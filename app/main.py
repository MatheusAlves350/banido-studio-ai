import os
import re
import sys
import json
import signal
import shutil
import secrets
import tempfile
import threading
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from app import security

from app.config import (
    PROJECT_ROOT, BASE_DIR, OBS_RECORDINGS_DIR, DATABASE_DIR, VOICE_BANK_DIR,
    CLIPS_DIR, TEMP_DIR, LOGS_DIR, VENV_PYTHON,
    HOST_SPEAKER_NAME, GROUP_NAME, PC_SPECS, SERVER_HOST, SERVER_PORT, ALLOWED_HOSTS,
    ACCESS_PASSWORD
)
from app.database import (
    init_db, startup_maintenance, sync_obs_videos, get_all_videos, get_video_by_id,
    update_video_progress, save_video_result, save_setting, get_setting,
    reset_video_status, has_video_data, update_video_paths_after_rename,
    create_job_record, set_job_pid, finish_job_record, get_job_count,
    get_job_history, get_job_by_id, get_latest_job_for_video, now_str,
    set_video_audio_normalized, get_game_history, record_game_history,
    delete_game_from_history, update_video_game_and_context
)
from app.transcriber_v3 import export_srt_file
from app.ai_director import (
    DEFAULT_GOOGLE_MODEL, DEFAULT_NVIDIA_MODEL,
    format_transcript_for_ai, generate_claude_web_prompt, parse_claude_web_response,
    build_youtube_description, build_youtube_tags_csv, sanitize_video_title,
    normalize_youtube_chapters
)
from app.lossless_media import (
    rename_mkv_and_srt, export_lossless_highlight, embed_chapters_into_mkv,
    inspect_mkv_audio_norm
)
from app.voice_bank import (
    list_known_voice_names, resolve_canonical_friend_name,
    extract_speaker_preview_wav, extract_highlight_preview_wav
)
from app import updater
from app.setup_check import run_setup_check

# Mapa em memória dos processos worker ativos:
# video_id -> {"proc": Popen, "job_id": int, "mode": str, "boot_log": Path, "boot_fh": file}
ACTIVE_PROCESSES: Dict[int, Dict[str, Any]] = {}
SPEAKER_CONFIRM_LOCK = threading.Lock()
# Cache em memória para evitar rodar ffprobe a cada clique em vídeos não normalizados (video_id -> (mtime, is_norm))
AUDIO_NORM_CHECK_CACHE: Dict[int, tuple] = {}


# Token de sessão gerado a cada inicialização para proteção contra CSRF em chamadas locais
API_AUTH_TOKEN = (os.getenv("BANIDO_API_TOKEN") or "").strip() or secrets.token_urlsafe(32)


def _mask_secret(val: str) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}...{s[-4:]}"


def _build_worker_env() -> Dict[str, str]:
    worker_env = os.environ.copy()
    existing_pp = worker_env.get("PYTHONPATH", "")
    worker_env["PYTHONPATH"] = f"{PROJECT_ROOT}{os.pathsep}{existing_pp}" if existing_pp else str(PROJECT_ROOT)
    return worker_env


def _spawn_worker(cmd: List[str], boot_log: Path):
    boot_fh = open(boot_log, "w", encoding="utf-8", errors="replace")
    kwargs: Dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "env": _build_worker_env(),
        "stdout": subprocess.DEVNULL,
        "stderr": boot_fh,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    return proc, boot_fh


def _run_voice_harvest_subprocess(
    mode: str,
    mkv_path: Path,
    segments: List[Dict[str, Any]],
    speaker_tag: str = "",
    friend_name: str = "",
    max_samples: int = 3
) -> Dict[str, Any]:
    """
    Executa a extração de embeddings SpeechBrain em um subprocesso isolado de vida curta,
    evitando carregar PyTorch/CUDA na memória do servidor FastAPI.
    """
    python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    tmp_dir = Path(tempfile.mkdtemp(prefix="banido_harvest_ipc_"))
    seg_file = tmp_dir / "segments.json"
    try:
        seg_file.write_text(json.dumps(segments, ensure_ascii=False), encoding="utf-8")
        cmd = [
            python_exe, "-u", "-m", "app.voice_bank",
            "--mode", mode,
            "--mkv", str(mkv_path),
            "--segments-json", str(seg_file),
            "--max-samples", str(max_samples),
        ]
        if speaker_tag:
            cmd.extend(["--speaker-tag", speaker_tag])
        if friend_name:
            cmd.extend(["--friend-name", friend_name])

        run_kwargs: Dict[str, Any] = {
            "cwd": str(PROJECT_ROOT),
            "env": _build_worker_env(),
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 240,
        }
        if os.name == "nt":
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        res = subprocess.run(cmd, **run_kwargs)
        if res.returncode != 0:
            err_msg = (res.stderr or res.stdout or "").strip()[-400:]
            raise RuntimeError(f"Subprocesso de voz falhou (código {res.returncode}): {err_msg}")

        for line in reversed((res.stdout or "").splitlines()):
            if line.startswith("BANIDO_HARVEST_JSON:"):
                return json.loads(line.split("BANIDO_HARVEST_JSON:", 1)[1])
        return {"friend": friend_name or HOST_SPEAKER_NAME, "samples_saved": []}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _ensure_not_busy(video_id: Optional[int], action_desc: str):
    reconcile_worker_processes()
    if 0 in ACTIVE_PROCESSES:
        raise HTTPException(
            status_code=409,
            detail=f"Não é possível {action_desc} enquanto a normalização de áudio em lote está em execução."
        )
    if video_id is not None and video_id in ACTIVE_PROCESSES:
        raise HTTPException(
            status_code=409,
            detail=f"Não é possível {action_desc} enquanto este vídeo está sendo processado."
        )


def kill_process_tree(pid: int):
    if pid <= 0:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True
            )
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception:
                os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _cleanup_boot_log(info: Dict[str, Any], append_to_job_log: bool = False):
    boot_fh = info.get("boot_fh")
    if boot_fh:
        try:
            boot_fh.close()
        except Exception:
            pass
    boot_log: Optional[Path] = info.get("boot_log")
    if boot_log and boot_log.exists():
        if append_to_job_log:
            try:
                boot_txt = boot_log.read_text(encoding="utf-8", errors="replace").strip()
                if boot_txt:
                    job = get_job_by_id(info["job_id"])
                    if job and job.get("log_file"):
                        with open(job["log_file"], "a", encoding="utf-8") as lf:
                            lf.write(f"\n[{now_str()}] ❌ [ERRO DE INICIALIZAÇÃO DO WORKER]\n{boot_txt}\n")
            except Exception:
                pass
        try:
            boot_log.unlink()
        except Exception:
            pass


def _cleanup_orphan_audio_norm_temps(mkv_path: Optional[Path] = None):
    """Remove arquivos temporários .audio_norm_tmp.mkv caso um job seja cancelado."""
    targets = []
    if mkv_path is not None:
        targets.append(mkv_path.with_name(f".{mkv_path.stem}.audio_norm_tmp.mkv"))
        targets.append(mkv_path.with_name(f".{mkv_path.stem}.audio_norm_err.log"))
    elif OBS_RECORDINGS_DIR.exists():
        targets.extend(OBS_RECORDINGS_DIR.glob(".*.audio_norm_tmp.mkv"))
        targets.extend(OBS_RECORDINGS_DIR.glob(".*.audio_norm_err.log"))
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


def reconcile_worker_processes():
    """
    Verifica se algum subprocesso worker terminou e garante que o banco de dados
    nunca fique preso em 'processing' caso o subprocesso tenha morrido abruptamente.
    """
    finished_vids = []
    for vid, info in list(ACTIVE_PROCESSES.items()):
        proc: subprocess.Popen = info["proc"]
        job_id: int = info["job_id"]
        ret = proc.poll()
        if ret is not None:
            finished_vids.append(vid)
            _cleanup_boot_log(info, append_to_job_log=(ret != 0))
            if vid == 0:
                job = get_job_by_id(job_id)
                if job and job.get("status") == "running":
                    st = "completed" if ret == 0 else "error"
                    finish_job_record(job_id, status=st, error_detail="" if ret == 0 else f"Lote encerrou (código {ret})")
                if ret != 0:
                    _cleanup_orphan_audio_norm_temps()
                continue

            video = get_video_by_id(vid)
            if video and video.get("status") == "processing":
                err_msg = f"Processo worker encerrou abruptamente (código {ret}). Verifique o log."
                if has_video_data(vid):
                    update_video_progress(
                        vid,
                        f"Aviso (transcrição e .SRT preservados!): {err_msg}",
                        1.0,
                        "completed"
                    )
                else:
                    update_video_progress(vid, f"Erro: {err_msg}", 0.0, "error")
                finish_job_record(job_id, status="error", last_step="Processo encerrado abruptamente", error_detail=err_msg)
            else:
                job = get_job_by_id(job_id)
                if job and job.get("status") == "running":
                    st = "completed" if ret == 0 else "error"
                    finish_job_record(job_id, status=st)

    for vid in finished_vids:
        ACTIVE_PROCESSES.pop(vid, None)


def read_log_tail(log_path_str: str, max_lines: int = 250) -> str:
    if not log_path_str:
        return ""
    p = Path(log_path_str)
    if not p.exists():
        return ""
    try:
        file_size = p.stat().st_size
        # Para arquivos grandes e leitura em cauda, faz seek nos últimos ~192 KB em O(1) em vez de ler o arquivo inteiro
        byte_window = max(65536, max_lines * 320) if max_lines > 0 else 0
        if max_lines > 0 and file_size > byte_window:
            with open(p, "rb") as fb:
                fb.seek(max(0, file_size - byte_window))
                chunk = fb.read().decode("utf-8", errors="replace")
            lines = chunk.splitlines(keepends=True)
            # Descartar a primeira linha parcial gerada pelo seek no meio da linha
            if len(lines) > 1:
                lines = lines[1:]
            tail_lines = lines[-max_lines:]
            return "... (linhas anteriores omitidas — abra no Histórico Completo para ver tudo) ...\n" + "".join(tail_lines)

        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if max_lines > 0 and len(lines) > max_lines:
            omitted = len(lines) - max_lines
            return f"... ({omitted} linhas anteriores omitidas — abra no Histórico Completo para ver tudo) ...\n" + "".join(lines[-max_lines:])
        return "".join(lines)
    except Exception as e:
        return f"[Erro ao ler arquivo de log: {e}]"


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
        # GETs que executam FFmpeg/rede ou gravam arquivos: também exigem o token
        or (path.startswith(("/api/audio/preview/", "/api/speakers/preview/", "/api/update/")) and method == "GET")
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

    if path == "/login":
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


@app.get("/api/status")
def server_status():
    reconcile_worker_processes()
    active_jobs = []
    batch_job = None
    for vid, info in ACTIVE_PROCESSES.items():
        job = get_job_by_id(info["job_id"])
        if job:
            entry = {
                "video_id": vid,
                "job_id": job["id"],
                "pid": info["proc"].pid,
                "mode": info.get("mode", "full"),
                "filename": job["filename"],
                "last_step": job["last_step"],
                "progress_pct": job["progress_pct"],
                "elapsed_sec": job["elapsed_sec"]
            }
            active_jobs.append(entry)
            if vid == 0 and info.get("mode") == "normalize_batch":
                batch_job = entry
    return {
        "online": True,
        "server_time": now_str(),
        "active_count": len(active_jobs),
        "active_jobs": active_jobs,
        "batch_audio_active": batch_job is not None,
        "batch_job": batch_job,
        "total_jobs": get_job_count(),
        "config": {
            "host_name": HOST_SPEAKER_NAME,
            "group_name": GROUP_NAME,
            "base_dir": str(BASE_DIR),
            "obs_dir": str(OBS_RECORDINGS_DIR),
            "clips_dir": str(CLIPS_DIR),
            "voice_bank_dir": str(VOICE_BANK_DIR),
            "temp_dir": str(TEMP_DIR),
            "gpu": PC_SPECS.get("gpu", ""),
        }
    }


@app.get("/api/setup/check")
def setup_check():
    """Diagnóstico de primeiros passos (FFmpeg, pasta do OBS, chave de IA, GPU, disco...)."""
    return run_setup_check()


@app.get("/api/videos")
def list_videos(sync: bool = True):
    reconcile_worker_processes()
    if sync:
        AUDIO_NORM_CHECK_CACHE.clear()
        sync_obs_videos(OBS_RECORDINGS_DIR)
    videos = get_all_videos()
    return {"videos": videos}


@app.get("/api/games")
def list_games():
    return {"games": get_game_history(limit=40)}


class GameHistoryRequest(BaseModel):
    name: str
    default_context: Optional[str] = None


@app.post("/api/games")
def add_game_history(req: GameHistoryRequest):
    clean = " ".join(str(req.name or "").strip().split())
    if not clean:
        raise HTTPException(status_code=400, detail="Informe o nome do jogo.")
    record_game_history(clean, default_context=req.default_context, increment=True)
    return {"success": True, "games": get_game_history(limit=40)}


@app.delete("/api/games/{game_name:path}")
def remove_game_history(game_name: str):
    delete_game_from_history(game_name)
    return {"success": True, "games": get_game_history(limit=40)}


class VideoGameContextRequest(BaseModel):
    game_name: Optional[str] = ""
    session_context: Optional[str] = ""
    save_default_context: bool = False


@app.post("/api/video/{video_id}/game")
def save_video_game_and_context(video_id: int, req: VideoGameContextRequest):
    video = get_video_by_id(video_id, include_data=False)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")
    update_video_game_and_context(
        video_id=video_id,
        game_name=req.game_name,
        session_context=req.session_context,
        save_default_context=req.save_default_context
    )
    return {
        "success": True,
        "game_name": " ".join(str(req.game_name or "").strip().split()),
        "session_context": str(req.session_context or "").strip(),
        "games": get_game_history(limit=40)
    }


@app.get("/api/video/{video_id}")
def get_video_details(video_id: int, include_data: bool = True):
    reconcile_worker_processes()
    video = get_video_by_id(video_id, include_data=include_data)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")
    video["is_worker_alive"] = video_id in ACTIVE_PROCESSES
    if include_data:
        video["known_friends"] = list_known_voice_names(exclude_host=False)

    # Sincroniza status de normalização da tag do MKV com cache em memória (evita ffprobe a cada clique!)
    if not video.get("audio_normalized") and video.get("status") != "processing":
        try:
            mkv_p = Path(video["file_path"])
            if mkv_p.exists():
                cur_mtime = float(mkv_p.stat().st_mtime)
                cached = AUDIO_NORM_CHECK_CACHE.get(video_id)
                if cached is None or abs(cached[0] - cur_mtime) > 0.5:
                    insp = inspect_mkv_audio_norm(mkv_p)
                    is_norm = bool(insp.get("is_normalized"))
                    AUDIO_NORM_CHECK_CACHE[video_id] = (cur_mtime, is_norm)
                    if is_norm:
                        set_video_audio_normalized(video_id, normalized=True)
                        video["audio_normalized"] = 1
                elif cached[1]:
                    set_video_audio_normalized(video_id, normalized=True)
                    video["audio_normalized"] = 1
        except Exception:
            pass

    # Garantir que descrição, capítulos (regra 00:00:00 do YouTube), tags e títulos sigam sempre o formato mais recente
    data = video.get("data")
    if include_data and isinstance(data, dict) and data:
        if isinstance(data.get("title_options"), list):
            data["title_options"] = [sanitize_video_title(t) for t in data["title_options"] if str(t or "").strip()]
        if isinstance(data.get("chapters"), list) and data["chapters"]:
            data["chapters"] = normalize_youtube_chapters(data["chapters"], duration_sec=video.get("duration_sec"))
        data["youtube_tags_csv"] = build_youtube_tags_csv(data)
        data["youtube_description"] = build_youtube_description(data)
    return video


class ProcessRequest(BaseModel):
    manual_game: Optional[str] = ""
    extra_context: Optional[str] = ""
    engine: Optional[str] = "gemini"
    custom_model: Optional[str] = ""
    normalize_audio: bool = False


def launch_worker_subprocess(
    video_id: int,
    mode: str,
    manual_game: str,
    engine: str,
    custom_model: str,
    extra_context: str = "",
    normalize_audio: bool = False
) -> Dict[str, Any]:
    reconcile_worker_processes()
    if 0 in ACTIVE_PROCESSES:
        raise HTTPException(
            status_code=409,
            detail="Já existe uma normalização de áudio em lote em execução. Aguarde ou cancele o lote antes."
        )
    video = get_video_by_id(video_id, include_data=False)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")

    if video_id in ACTIVE_PROCESSES:
        existing = ACTIVE_PROCESSES[video_id]
        return {
            "already_running": True,
            "job_id": existing["job_id"],
            "pid": existing["proc"].pid,
            "message": f"Este vídeo já está em processamento ativo (Job #{existing['job_id']}, PID {existing['proc'].pid})."
        }

    # Salvar imediatamente o jogo e o contexto escolhidos no vídeo e no histórico de jogos
    if mode in ("full", "ai_only") and (manual_game.strip() or extra_context.strip()):
        update_video_game_and_context(
            video_id=video_id,
            game_name=manual_game.strip() if manual_game.strip() else None,
            session_context=extra_context.strip(),
            save_default_context=bool(manual_game.strip() and extra_context.strip())
        )
    if mode == "normalize_audio" or (mode == "full" and normalize_audio):
        AUDIO_NORM_CHECK_CACHE.pop(video_id, None)

    # Determinar modelo efetivo para registro claro no histórico
    effective_model = custom_model
    if not effective_model:
        if engine == "gemini":
            effective_model = get_setting("google_model", DEFAULT_GOOGLE_MODEL)
        elif engine == "nvidia":
            effective_model = get_setting("nvidia_model", DEFAULT_NVIDIA_MODEL)
        else:
            effective_model = "claude-web-manual"

    job_info = create_job_record(
        video_id=video_id,
        filename=video["filename"],
        engine=engine,
        model=effective_model,
        manual_game=manual_game
    )
    job_id = job_info["job_id"]

    if mode == "normalize_audio":
        start_msg = "Normalizando áudio YouTube Gold (Vídeo 4K 100% lossless)..."
    elif mode == "ai_only":
        start_msg = "Regerando análise criativa da IA..."
    else:
        start_msg = "Inicializando processo dedicado de transcrição/IA..."
    update_video_progress(video_id, start_msg, 0.02, "processing")

    python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    cmd = [
        python_exe, "-u", "-m", "app.worker",
        "--job-id", str(job_id),
        "--video-id", str(video_id),
        "--mode", mode,
        "--game", manual_game or "",
        "--context", extra_context or "",
        "--engine", engine or "gemini",
        "--model", effective_model or ""
    ]
    if mode == "full" and normalize_audio:
        cmd.append("--normalize-audio")

    # Capturar stderr de inicialização (ex: ImportError / DLL CUDA) para nunca falhar silenciosamente
    boot_log = LOGS_DIR / f"job_{job_id}_boot.log"
    proc, boot_fh = _spawn_worker(cmd, boot_log)
    set_job_pid(job_id, proc.pid)
    ACTIVE_PROCESSES[video_id] = {
        "proc": proc,
        "job_id": job_id,
        "mode": mode,
        "boot_log": boot_log,
        "boot_fh": boot_fh
    }

    return {
        "success": True,
        "already_running": False,
        "job_id": job_id,
        "pid": proc.pid,
        "message": f"Processamento iniciado (Job #{job_id}, PID {proc.pid})"
    }


@app.post("/api/process/{video_id}")
def process_video(video_id: int, req: ProcessRequest):
    return launch_worker_subprocess(
        video_id=video_id,
        mode="full",
        manual_game=req.manual_game or "",
        engine=req.engine or "gemini",
        custom_model=req.custom_model or "",
        extra_context=req.extra_context or "",
        normalize_audio=bool(req.normalize_audio)
    )


@app.post("/api/reanalyze-ai/{video_id}")
def reanalyze_video_ai(video_id: int, req: ProcessRequest):
    return launch_worker_subprocess(
        video_id=video_id,
        mode="ai_only",
        manual_game=req.manual_game or "",
        engine=req.engine or "gemini",
        custom_model=req.custom_model or "",
        extra_context=req.extra_context or ""
    )


@app.post("/api/normalize-audio/{video_id}")
def normalize_video_audio(video_id: int):
    reconcile_worker_processes()
    if 0 in ACTIVE_PROCESSES:
        raise HTTPException(
            status_code=409,
            detail="Já existe uma normalização de áudio em lote em execução. Aguarde ou cancele o lote antes."
        )
    return launch_worker_subprocess(
        video_id=video_id,
        mode="normalize_audio",
        manual_game="",
        engine="ffmpeg",
        custom_model="youtube_gold_v1"
    )


@app.post("/api/normalize-audio-batch")
def normalize_all_videos_audio():
    reconcile_worker_processes()
    if 0 in ACTIVE_PROCESSES:
        existing = ACTIVE_PROCESSES[0]
        return {
            "already_running": True,
            "job_id": existing["job_id"],
            "pid": existing["proc"].pid,
            "message": f"A normalização em lote já está em andamento (Job #{existing['job_id']}, PID {existing['proc'].pid})."
        }
    if ACTIVE_PROCESSES:
        raise HTTPException(
            status_code=409,
            detail="Há outro processamento de vídeo em andamento. Aguarde concluir antes de iniciar o lote de áudio."
        )

    AUDIO_NORM_CHECK_CACHE.clear()
    sync_obs_videos(OBS_RECORDINGS_DIR)
    job_info = create_job_record(
        video_id=0,
        filename="LOTE COMPLETO — Normalização YouTube Gold (OBS)",
        engine="ffmpeg",
        model="youtube_gold_v1",
        manual_game=""
    )
    job_id = job_info["job_id"]

    python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    cmd = [
        python_exe, "-u", "-m", "app.worker",
        "--job-id", str(job_id),
        "--video-id", "0",
        "--mode", "normalize_batch",
        "--engine", "ffmpeg",
        "--model", "youtube_gold_v1"
    ]
    boot_log = LOGS_DIR / f"job_{job_id}_boot.log"
    proc, boot_fh = _spawn_worker(cmd, boot_log)
    set_job_pid(job_id, proc.pid)
    ACTIVE_PROCESSES[0] = {
        "proc": proc,
        "job_id": job_id,
        "mode": "normalize_batch",
        "boot_log": boot_log,
        "boot_fh": boot_fh
    }
    return {
        "success": True,
        "already_running": False,
        "job_id": job_id,
        "pid": proc.pid,
        "message": f"Normalização em lote iniciada (Job #{job_id}, PID {proc.pid})."
    }


@app.post("/api/normalize-audio-batch/cancel")
def cancel_normalize_batch():
    reconcile_worker_processes()
    if 0 not in ACTIVE_PROCESSES:
        return {"success": True, "cancelled": False, "message": "Nenhum lote em execução."}

    info = ACTIVE_PROCESSES.pop(0)
    proc = info["proc"]
    job_id = info["job_id"]
    kill_process_tree(proc.pid)
    _cleanup_boot_log(info, append_to_job_log=False)
    _cleanup_orphan_audio_norm_temps()

    # Restaurar qualquer vídeo que estava marcado como processing pelo lote
    for v in get_all_videos():
        if v.get("status") == "processing" and v["id"] not in ACTIVE_PROCESSES:
            had_data = has_video_data(v["id"])
            reset_video_status(v["id"], "Lote de áudio cancelado pelo usuário.", restore_completed=had_data)

    job = get_job_by_id(job_id)
    if job and job.get("log_file"):
        try:
            with open(job["log_file"], "a", encoding="utf-8") as lf:
                lf.write(f"\n[{now_str()}] 🛑 [CANCELADO] Lote de normalização interrompido pelo usuário (PID {proc.pid} encerrado).\n")
        except Exception:
            pass

    finish_job_record(
        job_id,
        status="cancelled",
        last_step="Cancelado manualmente pelo usuário",
        error_detail="Cancelado pelo usuário"
    )
    return {"success": True, "cancelled": True, "job_id": job_id, "message": "Lote cancelado com segurança!"}


@app.post("/api/reset/{video_id}")
def reset_video(video_id: int):
    reconcile_worker_processes()
    cancelled_job_id = None
    cancelled_mode = "full"
    if video_id in ACTIVE_PROCESSES:
        info = ACTIVE_PROCESSES.pop(video_id)
        proc = info["proc"]
        cancelled_job_id = info["job_id"]
        cancelled_mode = info.get("mode", "full")
        kill_process_tree(proc.pid)
        _cleanup_boot_log(info, append_to_job_log=False)

        job = get_job_by_id(cancelled_job_id)
        if job and job.get("log_file"):
            try:
                with open(job["log_file"], "a", encoding="utf-8") as lf:
                    lf.write(f"\n[{now_str()}] 🛑 [CANCELADO] Processamento interrompido manualmente pelo usuário (PID {proc.pid} encerrado).\n")
            except Exception:
                pass
        finish_job_record(
            cancelled_job_id,
            status="cancelled",
            last_step="Cancelado manualmente pelo usuário",
            error_detail="Cancelado pelo usuário"
        )
        if cancelled_mode in ("normalize_audio", "full"):
            v_cur = get_video_by_id(video_id)
            if v_cur and v_cur.get("file_path"):
                _cleanup_orphan_audio_norm_temps(Path(v_cur["file_path"]))

    restore_prev = (cancelled_job_id is not None and has_video_data(video_id))
    reset_video_status(
        video_id,
        "Processamento cancelado (transcrição e .SRT já salvos foram preservados! Clique em Re-analisar só com IA)." if restore_prev else "",
        restore_completed=restore_prev
    )
    return {
        "success": True,
        "cancelled_job_id": cancelled_job_id,
        "message": "Processamento cancelado e status restaurado." if cancelled_job_id else "Status do vídeo resetado com sucesso."
    }


@app.get("/api/logs/video/{video_id}")
def get_video_logs(video_id: int, tail: int = 250):
    reconcile_worker_processes()
    job = get_latest_job_for_video(video_id)
    if not job:
        return {"has_job": False, "job": None, "log_text": ""}
    log_text = read_log_tail(job.get("log_file", ""), max_lines=tail)
    return {
        "has_job": True,
        "job": job,
        "is_running": video_id in ACTIVE_PROCESSES,
        "log_text": log_text
    }


@app.get("/api/logs/job/{job_id}")
def get_specific_job_logs(job_id: int, tail: int = 800):
    reconcile_worker_processes()
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado no histórico")
    log_text = read_log_tail(job.get("log_file", ""), max_lines=tail)
    return {
        "job": job,
        "log_text": log_text
    }


@app.get("/api/history")
def list_job_history(limit: int = 50, video_id: Optional[int] = None):
    reconcile_worker_processes()
    jobs = get_job_history(limit=limit, video_id=video_id)
    return {"jobs": jobs}


class RenameRequest(BaseModel):
    title: str
    game_name: Optional[str] = ""


@app.post("/api/rename/{video_id}")
def rename_video(video_id: int, req: RenameRequest):
    _ensure_not_busy(video_id, "renomear este vídeo")
    video = get_video_by_id(video_id, include_data=False)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")

    mkv_path = Path(video["file_path"])
    effective_game = (req.game_name or "").strip() or video.get("game_name", "")
    res = rename_mkv_and_srt(mkv_path, req.title, effective_game)
    if not res["success"]:
        raise HTTPException(status_code=500, detail=res["error"])

    update_video_paths_after_rename(
        video_id=video_id,
        new_filename=res["new_name"],
        new_file_path=res["new_path"],
        new_srt_path=res.get("srt_path"),
        chosen_title=req.title,
        game_name=effective_game
    )
    return res


@app.get("/api/audio/preview/{video_id}")
def preview_highlight_audio(video_id: int, start: float = 0.0, end: float = 15.0):
    """
    Extrai (ou reaproveita em cache) uma prévia de áudio de até 30s de um trecho do vídeo
    para o usuário conferir um Melhor Momento antes de exportar o corte 4K Lossless.
    """
    video = get_video_by_id(video_id, include_data=False)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")
    mkv_path = Path(video["file_path"])
    if not mkv_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo MKV não encontrado no disco")

    s = max(0.0, float(start))
    e = max(s + 2.0, min(s + 30.0, float(end)))
    preview_dir = TEMP_DIR / "previews"
    preview_wav = preview_dir / f"hl_v{video_id}_{int(video.get('mtime') or 0)}_{int(s)}_{int(e)}.wav"

    if not preview_wav.exists() or preview_wav.stat().st_size <= 1000:
        ok = extract_highlight_preview_wav(mkv_path, s, e, preview_wav, max_duration=30.0)
        if not ok:
            raise HTTPException(status_code=500, detail="Não foi possível extrair a prévia de áudio do MKV.")
        security.prune_directory(preview_dir)

    return FileResponse(str(preview_wav), media_type="audio/wav", filename=preview_wav.name)


class CutRequest(BaseModel):
    video_id: int
    start_sec: float
    end_sec: float
    title: str
    boost_discord: bool = True
    keep_all_tracks: bool = False


@app.post("/api/cut")
def cut_highlight(req: CutRequest):
    _ensure_not_busy(req.video_id, "exportar cortes deste vídeo")
    video = get_video_by_id(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")

    mkv_path = Path(video["file_path"])
    res = export_lossless_highlight(
        mkv_path, req.start_sec, req.end_sec, req.title,
        boost_discord=req.boost_discord,
        keep_all_tracks=req.keep_all_tracks
    )
    if not res["success"]:
        raise HTTPException(status_code=500, detail=res.get("error", "Erro ao exportar corte"))
    return res


class EmbedChaptersRequest(BaseModel):
    mode: Optional[str] = "both"  # "both" | "highlights" | "chapters"


@app.post("/api/embed-chapters/{video_id}")
def embed_chapters(video_id: int, req: EmbedChaptersRequest):
    _ensure_not_busy(video_id, "embutir capítulos neste MKV")
    video = get_video_by_id(video_id)
    if not video or not video.get("data"):
        raise HTTPException(status_code=404, detail="Vídeo ou dados de capítulos não encontrados")

    mode = (req.mode or "both").strip().lower()
    if mode not in ("both", "highlights", "chapters"):
        mode = "both"

    mkv_path = Path(video["file_path"])
    data = video["data"]
    res = embed_chapters_into_mkv(
        mkv_path=mkv_path,
        chapters=data.get("chapters") or [],
        highlights=data.get("highlights") or [],
        mode=mode
    )
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "Erro ao embutir capítulos no MKV"))
    return res


@app.get("/api/speakers/preview/{video_id}/{speaker_tag:path}")
def preview_speaker_audio(video_id: int, speaker_tag: str):
    """
    Extrai (ou reutiliza em cache) um trecho limpo de 3s-12s da voz diretamente do MKV
    para o usuário ouvir no navegador antes de confirmar e salvar no Banco de Vozes.
    """
    video = get_video_by_id(video_id)
    if not video or not video.get("data"):
        raise HTTPException(status_code=404, detail="Vídeo ou transcrição não encontrados")

    mkv_path = Path(video["file_path"])
    if not mkv_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo MKV não encontrado no disco")

    data = video["data"]
    samples = data.get("speaker_samples") or {}
    segments = data.get("segments") or []
    is_host = speaker_tag.strip().lower() == HOST_SPEAKER_NAME.strip().lower()

    start_sec = None
    end_sec = None

    if not is_host and speaker_tag in samples:
        info = samples[speaker_tag] or {}
        if info.get("start") is not None and info.get("end") is not None:
            start_sec = float(info["start"])
            end_sec = float(info["end"])

    if start_sec is None or end_sec is None:
        tag_low = speaker_tag.strip().lower()
        matching = [
            s for s in segments
            if str(s.get("cluster_tag", "")).strip().lower() == tag_low
            or str(s.get("speaker", "")).strip().lower() == tag_low
        ]
        if not matching:
            raise HTTPException(status_code=404, detail=f"Nenhum trecho de áudio encontrado para '{speaker_tag}'")
        best_seg = max(matching, key=lambda s: float(s.get("end", 0)) - float(s.get("start", 0)))
        start_sec = float(best_seg["start"])
        end_sec = float(best_seg["end"])

    safe_tag = re.sub(r"[^A-Za-z0-9_-]+", "_", speaker_tag.strip())[:40] or "voz"
    preview_dir = TEMP_DIR / "previews"
    preview_wav = preview_dir / f"v{video_id}_{int(video.get('mtime') or 0)}_{safe_tag}_{int(start_sec)}.wav"

    if not preview_wav.exists() or preview_wav.stat().st_size <= 1000:
        ok = extract_speaker_preview_wav(mkv_path, start_sec, end_sec, preview_wav, is_host=is_host)
        if not ok:
            raise HTTPException(status_code=500, detail="Não foi possível extrair o trecho de áudio do MKV.")
        security.prune_directory(preview_dir)

    return FileResponse(str(preview_wav), media_type="audio/wav", filename=preview_wav.name)


class SpeakerConfirmRequest(BaseModel):
    video_id: int
    speaker_tag: str
    friend_name: str


@app.post("/api/speakers/confirm")
def confirm_speaker(req: SpeakerConfirmRequest):
    _ensure_not_busy(req.video_id, "confirmar vozes neste vídeo")

    clean_name = resolve_canonical_friend_name(req.friend_name)
    if not clean_name:
        raise HTTPException(status_code=400, detail="Informe um nome válido para o participante.")

    # Lock protege cliques simultâneos em vários cards sem sobrescrever confirmações vizinhas
    with SPEAKER_CONFIRM_LOCK:
        video = get_video_by_id(req.video_id)
        if not video or "data" not in video:
            raise HTTPException(status_code=404, detail="Vídeo não encontrado")

        ai_data = video["data"]
        segments = ai_data.get("segments", [])
        tag_lower = req.speaker_tag.strip().lower()

        updated_count = 0
        for seg in segments:
            c_tag = str(seg.get("cluster_tag", "")).strip().lower()
            spk = str(seg.get("speaker", "")).strip().lower()
            if c_tag == tag_lower or spk == tag_lower:
                if not seg.get("cluster_tag"):
                    seg["cluster_tag"] = req.speaker_tag
                seg["speaker"] = clean_name
                updated_count += 1

        srt_path = Path(video["srt_path"]) if video.get("srt_path") else Path(video["file_path"]).with_suffix(".srt")
        export_srt_file(segments, srt_path)

        mkv_path = Path(video["file_path"])
        harvest_res: Dict[str, Any] = {}
        try:
            harvest_res = _run_voice_harvest_subprocess(
                mode="friend",
                mkv_path=mkv_path,
                segments=segments,
                speaker_tag=req.speaker_tag,
                friend_name=clean_name,
                max_samples=3
            )
        except Exception as e:
            print(f"[VOZES] Erro no harvest manual isolado: {e}")

        if not isinstance(ai_data.get("speaker_mapping"), dict):
            ai_data["speaker_mapping"] = {}
        ai_data["speaker_mapping"][req.speaker_tag] = f"{clean_name} (Confirmado manualmente)"

        # Atualizar imediatamente speaker_samples para o card da interface refletir ✅ Confirmado na hora
        samples_dict = ai_data.get("speaker_samples")
        if isinstance(samples_dict, dict) and req.speaker_tag in samples_dict and isinstance(samples_dict[req.speaker_tag], dict):
            samples_dict[req.speaker_tag]["known"] = True
            samples_dict[req.speaker_tag]["confirmed_manually"] = True
            samples_dict[req.speaker_tag]["confirmed_name"] = clean_name
            samples_dict[req.speaker_tag]["avg_confidence"] = 1.0

        ai_data["raw_transcript_prompt"] = format_transcript_for_ai(
            segments,
            ai_data.get("hype_scores", []),
            manual_game=ai_data.get("main_game", video.get("game_name", "")),
            known_friends=list_known_voice_names(exclude_host=False),
            extra_context=video.get("session_context", "")
        )
        ai_data["youtube_description"] = build_youtube_description(ai_data)

        save_video_result(
            video_id=req.video_id,
            game_name=video.get("game_name", ""),
            duration=video.get("duration_sec", 0.0),
            mosaic_path=video.get("mosaic_path", ""),
            srt_path=str(srt_path),
            ai_data=ai_data
        )

    return {
        "success": True,
        "friend_name": clean_name,
        "updated_segments": updated_count,
        "harvest": harvest_res
    }


@app.post("/api/speakers/harvest-host/{video_id}")
def harvest_host_samples(video_id: int):
    _ensure_not_busy(video_id, "salvar amostras vocais deste vídeo")

    with SPEAKER_CONFIRM_LOCK:
        video = get_video_by_id(video_id)
        if not video or not video.get("data"):
            raise HTTPException(status_code=404, detail="Vídeo não encontrado ou ainda não transcrito")

        segments = video["data"].get("segments", [])
        mkv_path = Path(video["file_path"])
        try:
            harvest_res = _run_voice_harvest_subprocess(
                mode="host",
                mkv_path=mkv_path,
                segments=segments,
                max_samples=3
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro ao salvar amostras de {HOST_SPEAKER_NAME}: {e}")

    return {"success": True, "harvest": harvest_res}


@app.get("/api/claude-prompt/{video_id}")
def get_claude_prompt(video_id: int):
    video = get_video_by_id(video_id)
    if not video or "data" not in video:
        raise HTTPException(status_code=404, detail="Dados de transcrição não encontrados")
    raw_prompt = video["data"].get("raw_transcript_prompt", "")
    full_prompt = generate_claude_web_prompt(raw_prompt)
    return {"prompt": full_prompt}


class ClaudeImportRequest(BaseModel):
    claude_response: str


@app.post("/api/claude-import/{video_id}")
def import_claude_response(video_id: int, req: ClaudeImportRequest):
    _ensure_not_busy(video_id, "importar análise do Claude neste vídeo")
    video = get_video_by_id(video_id)
    if not video or "data" not in video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")

    try:
        parsed = parse_claude_web_response(req.claude_response, duration_sec=video.get("duration_sec"))
        ai_data = video["data"]
        ai_data.update(parsed)
        ai_data["ai_warning"] = ""
        ai_data["youtube_description"] = build_youtube_description(ai_data)

        save_video_result(
            video_id=video_id,
            game_name=ai_data.get("main_game", video.get("game_name", "")),
            duration=video.get("duration_sec", 0.0),
            mosaic_path=video.get("mosaic_path", ""),
            srt_path=video.get("srt_path", ""),
            ai_data=ai_data
        )
        return {"success": True, "data": ai_data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao interpretar resposta do Claude: {e}")


class SettingsModel(BaseModel):
    google_api_key: Optional[str] = None
    nvidia_api_key: Optional[str] = None
    clear_google_key: bool = False
    clear_nvidia_key: bool = False
    ai_provider: Optional[str] = "gemini"
    google_model: Optional[str] = DEFAULT_GOOGLE_MODEL
    nvidia_model: Optional[str] = DEFAULT_NVIDIA_MODEL


def _build_safe_settings_response() -> Dict[str, Any]:
    g_raw = get_setting("google_api_key", "")
    n_raw = get_setting("nvidia_api_key", "")
    return {
        "has_google_api_key": bool(g_raw),
        "google_api_key_masked": _mask_secret(g_raw),
        "has_nvidia_api_key": bool(n_raw),
        "nvidia_api_key_masked": _mask_secret(n_raw),
        "ai_provider": get_setting("ai_provider", "gemini"),
        "google_model": get_setting("google_model", DEFAULT_GOOGLE_MODEL),
        "nvidia_model": get_setting("nvidia_model", DEFAULT_NVIDIA_MODEL)
    }


@app.get("/api/settings")
def get_settings():
    return _build_safe_settings_response()


@app.post("/api/settings")
def update_settings(req: SettingsModel):
    if req.clear_google_key:
        save_setting("google_api_key", "")
    elif req.google_api_key is not None:
        g_in = req.google_api_key.strip()
        if g_in and g_in != _mask_secret(get_setting("google_api_key", "")):
            save_setting("google_api_key", g_in)

    if req.clear_nvidia_key:
        save_setting("nvidia_api_key", "")
    elif req.nvidia_api_key is not None:
        n_in = req.nvidia_api_key.strip()
        if n_in and n_in != _mask_secret(get_setting("nvidia_api_key", "")):
            save_setting("nvidia_api_key", n_in)

    save_setting("ai_provider", req.ai_provider or "gemini")
    save_setting("google_model", req.google_model or DEFAULT_GOOGLE_MODEL)
    save_setting("nvidia_model", req.nvidia_model or DEFAULT_NVIDIA_MODEL)
    res = _build_safe_settings_response()
    res["success"] = True
    return res


@app.get("/api/models/{provider}")
def get_models(provider: str, refresh: bool = False):
    """Lista de modelos do provedor (Gemini ou NVIDIA), com cache de 24 h e lista padrão como reserva."""
    from app.model_catalog import list_models, normalize_provider
    prov = normalize_provider(provider)
    if prov is None:
        raise HTTPException(status_code=404, detail="Provedor desconhecido. Use 'gemini' ou 'nvidia'.")
    key = get_setting("google_api_key" if prov == "google" else "nvidia_api_key", "")
    return list_models(prov, key, force_refresh=refresh)


class TestKeyModel(BaseModel):
    key: Optional[str] = ""
    model: Optional[str] = ""


@app.post("/api/test-google-key")
def test_google_key(req: TestKeyModel):
    key = req.key.strip() if req.key else get_setting("google_api_key", "")
    model = req.model.strip() if req.model else get_setting("google_model", DEFAULT_GOOGLE_MODEL)
    if not key:
        return {"success": False, "error": "Nenhuma chave informada ou salva."}
    try:
        from google import genai
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=model,
            contents="Responda apenas: OK"
        )
        return {"success": True, "reply": (resp.text or "").strip(), "model": model}
    except Exception as e:
        return {"success": False, "error": str(e), "model": model}


@app.post("/api/test-nvidia-key")
def test_nvidia_key(req: TestKeyModel):
    key = req.key.strip() if req.key else get_setting("nvidia_api_key", "")
    model = req.model.strip() if req.model else get_setting("nvidia_model", DEFAULT_NVIDIA_MODEL)
    if not key:
        return {"success": False, "error": "Nenhuma chave NVIDIA informada ou salva."}
    try:
        from openai import OpenAI
        client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key, timeout=30.0)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Responda apenas: OK"}],
            max_tokens=128
        )
        reply_txt = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return {"success": True, "reply": reply_txt or "OK", "model": model}
    except Exception as e:
        return {"success": False, "error": str(e), "model": model}


# ==============================================================================
# ATUALIZAÇÕES (GitHub)
# ==============================================================================

@app.get("/api/update/status")
def update_status():
    """Versão instalada + novidades disponíveis no GitHub (cache de 10 min)."""
    info = updater.check_for_updates(force=False)
    return {**info, "apply": updater.get_apply_state(), "restart_supported": updater.launched_by_launcher()}


@app.post("/api/update/check")
def update_check_now():
    """Força uma nova consulta ao GitHub (botão "Verificar agora")."""
    info = updater.check_for_updates(force=True)
    return {**info, "apply": updater.get_apply_state(), "restart_supported": updater.launched_by_launcher()}


def _ensure_idle_for_update(action_desc: str):
    reconcile_worker_processes()
    if ACTIVE_PROCESSES:
        raise HTTPException(
            status_code=409,
            detail=f"Não é possível {action_desc} enquanto há {len(ACTIVE_PROCESSES)} processamento(s) em andamento. Aguarde terminar ou cancele."
        )


@app.post("/api/update/apply")
def update_apply():
    _ensure_idle_for_update("atualizar")
    python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    res = updater.start_apply(python_exe=python_exe)
    return {**res, "apply": updater.get_apply_state()}


@app.get("/api/update/progress")
def update_progress():
    return updater.get_apply_state()


@app.post("/api/update/restart")
def update_restart():
    _ensure_idle_for_update("reiniciar o servidor")
    if not updater.launched_by_launcher():
        return {
            "success": False,
            "restart_supported": False,
            "message": "Feche esta janela/terminal do servidor e abra o Banido Studio de novo pelo Iniciar_Banido_Studio.bat (ou ./start.sh)."
        }
    updater.schedule_restart()
    return {"success": True, "restart_supported": True, "message": "Reiniciando o servidor..."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=SERVER_HOST, port=SERVER_PORT)
