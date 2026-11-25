"""
Proteções de acesso do Banido Studio AI.

- Acesso pela rede (BANIDO_HOST diferente de 127.0.0.1/localhost): exige senha
  (BANIDO_ACCESS_PASSWORD) com cookie de sessão HttpOnly/SameSite=Strict. Sem senha, nega tudo.
- Limpeza da pasta de prévias de áudio (evita encher o disco).

O token X-Banido-Token (CSRF) continua separado, em app/main.py: ele protege contra outros
SITES; a senha protege contra outras PESSOAS da mesma rede.
"""
import hashlib
import hmac
import html
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs

LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
SESSION_COOKIE = "banido_session"
SESSION_MAX_AGE_SEC = 7 * 24 * 3600
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SEC = 60

# Segredo da sessão: novo a cada inicialização do servidor (reiniciar encerra as sessões).
_SESSION_SECRET = secrets.token_bytes(32)
_FAILURES: Dict[str, List[float]] = {}


def is_network_mode(server_host: str) -> bool:
    return str(server_host or "").strip().lower() not in LOCAL_BIND_HOSTS


def make_session_value() -> str:
    return hmac.new(_SESSION_SECRET, b"banido-session-v1", hashlib.sha256).hexdigest()


def is_valid_session(cookie_value: Optional[str]) -> bool:
    return bool(cookie_value) and hmac.compare_digest(cookie_value, make_session_value())


def check_password(provided: str, expected: str) -> bool:
    if not expected:
        return False
    a = hashlib.sha256(provided.encode("utf-8")).digest()
    b = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def login_blocked(client_id: str, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    recent = [t for t in _FAILURES.get(client_id, []) if now - t < LOGIN_WINDOW_SEC]
    _FAILURES[client_id] = recent
    return len(recent) >= LOGIN_MAX_FAILURES


def register_login_failure(client_id: str, now: Optional[float] = None) -> None:
    _FAILURES.setdefault(client_id, []).append(time.time() if now is None else now)


def clear_login_failures(client_id: str) -> None:
    _FAILURES.pop(client_id, None)


def parse_login_body(raw: bytes) -> str:
    try:
        return (parse_qs(raw.decode("utf-8")).get("password") or [""])[0]
    except Exception:
        return ""


def login_page_html(message: str = "") -> str:
    msg = f'<p class="err">{html.escape(message)}</p>' if message else ""
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Banido Studio AI - Acesso</title>
<style>
body{{font-family:system-ui,sans-serif;background:#111;color:#eee;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
form{{background:#1b1b1b;border:1px solid #333;border-radius:8px;padding:24px;width:min(340px,90vw)}}
h1{{font-size:18px;margin:0 0 6px}} p{{font-size:13px;color:#aaa;margin:0 0 14px}}
input,button{{width:100%;box-sizing:border-box;padding:10px;border-radius:6px;border:1px solid #444;font-size:14px}}
input{{background:#0e0e0e;color:#eee;margin-bottom:10px}} button{{background:#2d6cdf;border-color:#2d6cdf;color:#fff;cursor:pointer}}
.err{{color:#ff8a80}}
</style></head><body>
<form method="post" action="/login">
<h1>Banido Studio AI</h1>
<p>Este servidor está aberto na rede. Digite a senha definida em <code>BANIDO_ACCESS_PASSWORD</code>.</p>
{msg}
<input type="password" name="password" placeholder="Senha de acesso" autofocus required>
<button type="submit">Entrar</button>
</form></body></html>"""


NO_PASSWORD_MESSAGE = (
    "Acesso pela rede bloqueado: o servidor foi iniciado em um endereço de rede "
    "(BANIDO_HOST) sem senha. Defina BANIDO_ACCESS_PASSWORD no arquivo .env, ou use "
    "BANIDO_HOST=127.0.0.1 para uso apenas neste computador."
)


def prune_directory(directory: Path, max_bytes: int = 200 * 1024 * 1024, keep_newest: int = 10) -> int:
    """Apaga os arquivos mais antigos de `directory` até ficar abaixo de `max_bytes`. Retorna quantos apagou."""
    try:
        files = [p for p in directory.iterdir() if p.is_file()]
    except OSError:
        return 0
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    total = sum(p.stat().st_size for p in files)
    removed = 0
    for p in reversed(files[keep_newest:]):  # do mais antigo para o mais novo
        if total <= max_bytes:
            break
        try:
            size = p.stat().st_size
            p.unlink()
            total -= size
            removed += 1
        except OSError:
            pass
    return removed
