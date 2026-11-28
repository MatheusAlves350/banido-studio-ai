"""
Avisos ao terminar um processamento (item E4 do roadmap).

Dois canais:
1. Notificação do Windows (balão), automática, sem configuração.
2. Mensagem em um canal do Discord por webhook, opcional. O link do webhook é segredo: fica no banco como as
   chaves de API, aparece mascarado e nunca vai para log nem para mensagem de erro.

Falha ao avisar nunca derruba o processamento: tudo aqui engole o erro e devolve False.
"""
import json
import os
import re
import subprocess
import threading
import urllib.request
from typing import Any, Dict, Optional

from app.database import get_job_by_id, get_setting
from app.friendly_errors import friendly_text

WEBHOOK_SETTING = "discord_webhook_url"
_WEBHOOK_RE = re.compile(r"^https://(?:(?:canary|ptb)\.)?(?:discord|discordapp)\.com/api/webhooks/\d+/[\w-]+/?$")

_TOAST_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
    "$n = New-Object System.Windows.Forms.NotifyIcon; "
    "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
    "$n.ShowBalloonTip(10000, $env:BANIDO_TOAST_TITLE, $env:BANIDO_TOAST_TEXT, "
    "[System.Windows.Forms.ToolTipIcon]::Info); Start-Sleep -Seconds 11; $n.Dispose()"
)


def is_valid_webhook(url: str) -> bool:
    return bool(_WEBHOOK_RE.match((url or "").strip()))


def _fmt_duration(sec: Any) -> str:
    try:
        s = max(0, int(float(sec or 0)))
    except (TypeError, ValueError):
        return ""
    h, rest = divmod(s, 3600)
    m, sc = divmod(rest, 60)
    if h:
        return f"{h}h {m:02d}min"
    if m:
        return f"{m}min {sc:02d}s"
    return f"{sc}s"


def describe_job_end(job: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Título e texto do aviso, em linguagem simples. None quando não há o que avisar (cancelado)."""
    if not job:
        return None
    status = job.get("status")
    nome = job.get("filename") or "gravação"
    tempo = _fmt_duration(job.get("elapsed_sec"))
    if status == "completed":
        texto = f"{nome} terminou" + (f" em {tempo}." if tempo else ".")
        return {"kind": "ok", "title": "Banido Studio: processamento concluído", "text": texto}
    if status == "error":
        motivo = friendly_text(job.get("error_detail") or job.get("last_step"))
        return {"kind": "error", "title": "Banido Studio: o processamento falhou", "text": f"{nome}. {motivo}"}
    return None


def send_windows_toast(title: str, text: str) -> bool:
    if os.name != "nt":
        return False
    try:
        env = os.environ.copy()
        env["BANIDO_TOAST_TITLE"] = title[:60]
        env["BANIDO_TOAST_TEXT"] = text[:250]
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", _TOAST_SCRIPT],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception:
        return False


def send_discord(webhook_url: str, text: str) -> bool:
    if not is_valid_webhook(webhook_url):
        return False
    try:
        req = urllib.request.Request(
            webhook_url.strip(),
            data=json.dumps({"content": text[:1800]}).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "BanidoStudio/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:
        # Nada de imprimir a exceção: ela pode trazer o link do webhook.
        return False


def notify(title: str, text: str) -> Dict[str, bool]:
    """Envia pelos dois canais. Devolve o que funcionou."""
    result = {"windows": send_windows_toast(title, text), "discord": False}
    hook = get_setting(WEBHOOK_SETTING, "")
    if hook:
        result["discord"] = send_discord(hook, f"**{title}**\n{text}")
    return result


def notify_job_end(job_id: int, wait: bool = False) -> None:
    """Avisa o resultado de um job. Com wait=False roda em segundo plano (uso no servidor)."""
    def _run():
        try:
            info = describe_job_end(get_job_by_id(job_id))
            if info:
                notify(info["title"], info["text"])
        except Exception:
            pass

    if wait:
        _run()
    else:
        threading.Thread(target=_run, daemon=True).start()
