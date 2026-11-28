"""Controle dos processos worker: estado em memória, início, encerramento e limpeza."""
import os
import sys
import json
import signal
import shutil
import secrets
import tempfile
import threading
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List
from fastapi import HTTPException

from app.config import (
    PROJECT_ROOT, OBS_RECORDINGS_DIR, TEMP_DIR, LOGS_DIR, VENV_PYTHON, HOST_SPEAKER_NAME
)
from app.disk_space import BLOCKED as SPACE_BLOCKED, WARNING as SPACE_WARNING, check_process_space
from app.database import (
    get_video_by_id, update_video_progress, get_setting, has_video_data, create_job_record,
    set_job_pid, finish_job_record, get_job_by_id, now_str,
    update_video_game_and_context
)
from app import crash_capture
from app.ai_director import (
    DEFAULT_GOOGLE_MODEL, DEFAULT_NVIDIA_MODEL
)


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


def _record_worker_crash(info: Dict[str, Any], ret: int) -> str:
    """F1: junta pilha, versões, RAM/VRAM e módulo que falhou; anexa ao log do job e devolve um resumo curto."""
    try:
        job_id = info["job_id"]
        pid = getattr(info.get("proc"), "pid", 0) or 0
        stage = crash_capture.last_known_stage(job_id, LOGS_DIR)
        crash = crash_capture.collect_crash_info(job_id, ret, LOGS_DIR, pid=pid, stage=stage)
        job = get_job_by_id(job_id)
        if job and job.get("log_file"):
            with open(job["log_file"], "a", encoding="utf-8") as lf:
                lf.write(f"\n[{now_str()}] ❌ [QUEDA DO PROCESSO]\n{crash['report_text']}\n")
        return crash["summary"]
    except Exception:
        return ""


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
            crash_summary = ""
            # ret == 1 com arquivos brutos ainda no disco = encerramento abrupto (ex.: Gerenciador de Tarefas no Windows)
            if ret != 0 and (ret != 1 or crash_capture.has_pending_crash_files(job_id, LOGS_DIR)):
                crash_summary = _record_worker_crash(info, ret)
            if vid == 0:
                job = get_job_by_id(job_id)
                if job and job.get("status") == "running":
                    st = "completed" if ret == 0 else "error"
                    finish_job_record(
                        job_id, status=st,
                        error_detail="" if ret == 0 else f"Lote encerrou ({crash_summary or f'código {ret}'})",
                    )
                if ret != 0:
                    _cleanup_orphan_audio_norm_temps()
                continue

            video = get_video_by_id(vid)
            if video and video.get("status") == "processing":
                err_msg = f"Processo worker encerrou abruptamente ({crash_summary or f'código {ret}'}). Verifique o log."
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
                # O worker caiu sem avisar ninguém: o aviso de falha sai daqui (E4).
                from app.notifications import notify_job_end
                notify_job_end(job_id)
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



def launch_worker_subprocess(
    video_id: int,
    mode: str,
    manual_game: str,
    engine: str,
    custom_model: str,
    extra_context: str = "",
    normalize_audio: bool = False,
    separate_tracks: Optional[bool] = None,
    notify_end: bool = True,
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

    # F8: confere o espaço em disco antes de começar (a normalização avulsa já faz a própria checagem)
    space_warning = ""
    if mode == "full":
        will_normalize = bool(normalize_audio) and not video.get("audio_normalized")
        space = check_process_space(video, will_normalize, TEMP_DIR, OBS_RECORDINGS_DIR)
        if space["level"] == SPACE_BLOCKED:
            raise HTTPException(status_code=507, detail=space["message"])
        if space["level"] == SPACE_WARNING:
            space_warning = space["message"]

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
        elif engine == "ollama":
            effective_model = get_setting("ollama_model", "") or "ollama-local"
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
    if mode == "full" and separate_tracks is not None:
        cmd.append("--separate-tracks" if separate_tracks else "--no-separate-tracks")
    if not notify_end:
        cmd.append("--no-notify")  # a fila manda um aviso só, no fim de tudo

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
        "message": f"Processamento iniciado (Job #{job_id}, PID {proc.pid})",
        "space_warning": space_warning,
    }
