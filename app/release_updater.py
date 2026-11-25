"""
Atualização para quem instalou pelo BanidoStudio-Setup.exe (sem pasta .git).

A versão instalada fica no arquivo VERSION (gravado pelo build da release). O painel consulta
`releases/latest` do GitHub, compara com a versão instalada e, se houver uma nova, baixa o
BanidoStudio-Setup.exe e o executa em modo silencioso. O instalador preserva o .env e a pasta de dados.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from app.config import PROJECT_ROOT

ASSET_NAME = "BanidoStudio-Setup.exe"
_UA = {"Accept": "application/vnd.github+json", "User-Agent": "banido-studio-updater"}


def read_installed_version(root: Optional[Path] = None) -> str:
    """Versão gravada em VERSION (ex.: '0.1.1'); vazio se este não for um build do instalador."""
    try:
        return (((root or PROJECT_ROOT) / "VERSION").read_text(encoding="utf-8").strip().lstrip("vV"))
    except Exception:
        return ""


def parse_version(text: str) -> Tuple[int, ...]:
    base = str(text or "").strip().lstrip("vV").split("-")[0]
    nums = [int(n) for n in re.findall(r"\d+", base)][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def is_newer(latest: str, installed: str) -> bool:
    return parse_version(latest) > parse_version(installed)


def fetch_latest_release(repo: str) -> Dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    asset_url, asset_size = "", 0
    for asset in data.get("assets") or []:
        if asset.get("name") == ASSET_NAME:
            asset_url = asset.get("browser_download_url") or ""
            asset_size = int(asset.get("size") or 0)
    tag = str(data.get("tag_name") or "")
    return {
        "tag": tag,
        "version": tag.lstrip("vV"),
        "name": data.get("name") or tag,
        "body": data.get("body") or "",
        "published_at": data.get("published_at") or "",
        "html_url": data.get("html_url") or "",
        "asset_url": asset_url,
        "asset_size": asset_size,
    }


def check(repo: str, installed: str, fetch: Optional[Callable[[str], Dict[str, Any]]] = None,
          windows: Optional[bool] = None) -> Dict[str, Any]:
    """Campos de resultado no mesmo formato do atualizador por Git (para reaproveitar a tela)."""
    fetch = fetch or fetch_latest_release
    windows = (sys.platform == "win32") if windows is None else windows
    out: Dict[str, Any] = {
        "update_available": False,
        "behind": 0,
        "commits": [],
        "can_apply": False,
        "reason": "",
        "error": "",
        "latest_version": "",
        "release_url": f"https://github.com/{repo}/releases/latest",
        "notes": "",
        "asset_url": "",
    }
    try:
        rel = fetch(repo)
    except Exception as e:
        out["error"] = f"Não foi possível consultar as versões no GitHub: {e}"
        return out

    out["latest_version"] = rel.get("version", "")
    out["release_url"] = rel.get("html_url") or out["release_url"]
    if not is_newer(rel.get("version", ""), installed):
        out["reason"] = "Você já está na versão mais recente."
        return out

    out.update({
        "update_available": True,
        "behind": 1,
        "notes": rel.get("body", ""),
        "asset_url": rel.get("asset_url", ""),
        "commits": [{
            "sha": rel.get("tag", ""),
            "date": rel.get("published_at", ""),
            "subject": rel.get("name") or f"Versão {rel.get('version', '')}",
        }],
    })
    if not windows:
        out["reason"] = "A atualização automática pelo instalador só funciona no Windows."
    elif not out["asset_url"]:
        out["reason"] = (
            f"A versão {out['latest_version']} ainda não tem o {ASSET_NAME} anexado. "
            "Tente de novo em alguns minutos ou baixe na página de versões."
        )
    else:
        out["can_apply"] = True
    return out


def download_installer(url: str, repo: str, progress: Optional[Callable[[str], None]] = None) -> Path:
    """Baixa o instalador para a pasta temporária. Só aceita arquivos das releases do repositório configurado."""
    prefix = f"https://github.com/{repo}/releases/download/"
    if not url.startswith(prefix):
        raise ValueError("Endereço de download fora das releases do repositório; atualização bloqueada.")
    dest_dir = Path(tempfile.gettempdir()) / "BanidoStudio-Update"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ASSET_NAME
    part = dest_dir / (ASSET_NAME + ".part")

    req = urllib.request.Request(url, headers={"User-Agent": _UA["User-Agent"]})
    done, last_bucket = 0, -1
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress and total:
                bucket = (done * 100 // total) // 25
                if bucket != last_bucket:
                    last_bucket = bucket
                    progress(f"Baixando o instalador... {done * 100 // total}%")

    with open(part, "rb") as fh:
        magic = fh.read(2)
    if done < 100_000 or magic != b"MZ":
        part.unlink(missing_ok=True)
        raise RuntimeError("O arquivo baixado não parece ser o instalador. Tente novamente.")
    os.replace(part, dest)
    return dest


def launch_installer(exe: Path) -> None:
    """Abre o instalador em modo silencioso (mostra só o progresso); /ATUALIZACAO=1 reabre o painel ao final."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(exe), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/ATUALIZACAO=1"],
        creationflags=flags,
        close_fds=True,
    )
