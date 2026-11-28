import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from app import security

from app.config import (
    OBS_RECORDINGS_DIR, DATABASE_DIR, VOICE_BANK_DIR, CLIPS_DIR, SERVER_HOST,
    SERVER_PORT, ALLOWED_HOSTS, ACCESS_PASSWORD
)
from app.database import (
    init_db, startup_maintenance, sync_obs_videos
)
from app.worker_control import (
    ACTIVE_PROCESSES,
    API_AUTH_TOKEN,
    AUDIO_NORM_CHECK_CACHE,
    _cleanup_boot_log,
    kill_process_tree,
)
from app import queue_manager
from app import updater  # noqa: F401 (testes acessam app.main.updater)
from app.routers import (
    videos, processing, media, voices, ai, settings, setup, update, diagnostics, env_settings, glossary, transcript,
    backup as backup_routes, notify, queue,
)
from app import backup


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_maintenance()
    if security.is_network_mode(SERVER_HOST):
        if ACCESS_PASSWORD:
            print(f"[SEGURANÇA] Servidor aberto na rede ({SERVER_HOST}): acesso protegido por senha.", flush=True)
        else:
            print(f"[SEGURANÇA] {security.NO_PASSWORD_MESSAGE}", flush=True)
    AUDIO_NORM_CHECK_CACHE.clear()
    sync_obs_videos(OBS_RECORDINGS_DIR)
    # F4: backup automático semanal, em segundo plano para não atrasar a abertura
    threading.Thread(target=backup.maybe_auto_backup, daemon=True, name="auto-backup").start()
    queue_manager.start_scheduler()
    yield
    # No encerramento do servidor, mata workers ativos para não deixar processos zumbis
    for vid, info in list(ACTIVE_PROCESSES.items()):
        proc = info.get("proc")
        if proc and proc.poll() is None:
            kill_process_tree(proc.pid)
        _cleanup_boot_log(info, append_to_job_log=False)
    ACTIVE_PROCESSES.clear()


init_db()
app = FastAPI(title="Banido Studio AI", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.middleware("http")
async def csrf_and_token_guard(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()
    needs_token = (
        (path.startswith("/api/") and method in ("POST", "PUT", "PATCH", "DELETE"))
        or (path in ("/api/settings", "/api/setup/check") and method == "GET")
        or (path.startswith("/api/models/") and method == "GET")
        # GETs que executam FFmpeg/rede ou gravam/expõem logs locais: também exigem o token
        or (path.startswith(("/api/audio/preview/", "/api/video/preview/", "/api/speakers/preview/", "/api/update/", "/api/diagnostics/", "/api/backup/")) and method == "GET")
    )
    if needs_token:
        provided = request.headers.get("X-Banido-Token", "")
        if not provided or not secrets.compare_digest(provided, API_AUTH_TOKEN):
            return JSONResponse(
                status_code=403,
                content={"detail": "Token de sessão/CSRF inválido ou ausente. Recarregue a página."}
            )
    return await call_next(request)


@app.middleware("http")
async def network_access_guard(request: Request, call_next):
    """Quando o servidor escuta na rede (BANIDO_HOST não local), exige senha de acesso."""
    if not security.is_network_mode(SERVER_HOST):
        return await call_next(request)

    path = request.url.path
    if not ACCESS_PASSWORD:
        return JSONResponse(status_code=403, content={"detail": security.NO_PASSWORD_MESSAGE})

    if path in ("/login", "/logout"):
        return await call_next(request)
    if security.is_valid_session(request.cookies.get(security.SESSION_COOKIE)):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "Acesso negado. Faça login em /login."})
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/login", status_code=303)


@app.get("/login")
def login_page():
    return HTMLResponse(content=security.login_page_html(), headers={"Cache-Control": "no-store"})


@app.post("/login")
async def login_submit(request: Request):
    from fastapi.responses import RedirectResponse
    client_id = request.client.host if request.client else "?"
    if security.login_blocked(client_id):
        return HTMLResponse(
            content=security.login_page_html("Muitas tentativas. Aguarde 1 minuto."),
            status_code=429
        )
    password = security.parse_login_body(await request.body())
    if not security.check_password(password, ACCESS_PASSWORD):
        security.register_login_failure(client_id)
        return HTMLResponse(content=security.login_page_html("Senha incorreta."), status_code=401)

    security.clear_login_failures(client_id)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        security.SESSION_COOKIE, security.make_session_value(),
        max_age=security.SESSION_MAX_AGE_SEC, httponly=True, samesite="strict", path="/"
    )
    return resp


@app.api_route("/logout", methods=["GET", "POST"])
def logout():
    from fastapi.responses import RedirectResponse
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(security.SESSION_COOKIE, path="/")
    return resp


# Montar diretórios de arquivos estáticos
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

mosaics_dir = DATABASE_DIR / "mosaics"
mosaics_dir.mkdir(parents=True, exist_ok=True)
app.mount("/mosaics", StaticFiles(directory=str(mosaics_dir)), name="mosaics")
app.mount("/vozes_amigos", StaticFiles(directory=str(VOICE_BANK_DIR)), name="vozes_amigos")
app.mount("/cortes", StaticFiles(directory=str(CLIPS_DIR)), name="cortes")


@app.get("/")
def get_index():
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    meta_tag = f'<meta name="banido-token" content="{API_AUTH_TOKEN}">'
    if "<head>" in html:
        html = html.replace("<head>", f"<head>\n    {meta_tag}", 1)
    else:
        html = meta_tag + html
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


# As rotas da API ficam em app/routers/. A ordem abaixo é a mesma de antes da separação.
for _modulo in (videos, processing, media, voices, ai, settings, setup, update, diagnostics, env_settings, glossary, transcript, backup_routes, notify, queue):
    app.include_router(_modulo.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=SERVER_HOST, port=SERVER_PORT)
