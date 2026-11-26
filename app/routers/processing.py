"""Rotas que iniciam, retomam e cancelam o processamento (worker), normalização de áudio, reset e histórico."""
from fastapi import APIRouter
import sys
from pathlib import Path
from typing import Optional
from fastapi import HTTPException
from pydantic import BaseModel

from app.config import (
    OBS_RECORDINGS_DIR, TEMP_DIR, LOGS_DIR, VENV_PYTHON
)
from app.database import (
    sync_obs_videos, get_all_videos, get_video_by_id, reset_video_status, has_video_data,
    create_job_record, set_job_pid, finish_job_record, get_job_history,
    get_job_by_id, get_latest_job_for_video, now_str
)
from app.worker_control import (
    ACTIVE_PROCESSES,
    AUDIO_NORM_CHECK_CACHE,
    _cleanup_boot_log,
    _cleanup_orphan_audio_norm_temps,
    _ensure_not_busy,
    _spawn_worker,
    kill_process_tree,
    launch_worker_subprocess,
    read_log_tail,
    reconcile_worker_processes,
)

router = APIRouter()


class ProcessRequest(BaseModel):
    manual_game: Optional[str] = ""
    extra_context: Optional[str] = ""
    engine: Optional[str] = "gemini"
    custom_model: Optional[str] = ""
    normalize_audio: bool = False
    separate_tracks: Optional[bool] = None



@router.post("/api/process/{video_id}")
def process_video(video_id: int, req: ProcessRequest):
    return launch_worker_subprocess(
        video_id=video_id,
        mode="full",
        manual_game=req.manual_game or "",
        engine=req.engine or "gemini",
        custom_model=req.custom_model or "",
        extra_context=req.extra_context or "",
        normalize_audio=bool(req.normalize_audio),
        separate_tracks=req.separate_tracks,
    )


@router.post("/api/process/{video_id}/resume-override")
def resume_paused_process(video_id: int):
    """Cria flag para retomar imediatamente um processamento pausado por gravação/jogo ativo."""
    work_dir = TEMP_DIR / f"video_{video_id}_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    flag_path = work_dir / "force_resume.flag"
    flag_path.write_text("1", encoding="utf-8")
    return {"success": True, "message": "Retomada solicitada — o processamento continuará em instantes."}


@router.post("/api/reanalyze-ai/{video_id}")
def reanalyze_video_ai(video_id: int, req: ProcessRequest):
    return launch_worker_subprocess(
        video_id=video_id,
        mode="ai_only",
        manual_game=req.manual_game or "",
        engine=req.engine or "gemini",
        custom_model=req.custom_model or "",
        extra_context=req.extra_context or ""
    )


@router.post("/api/normalize-audio/{video_id}")
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


@router.post("/api/audio/remove-backup/{video_id}")
def remove_video_audio_backup(video_id: int):
    """Remove a faixa extra de backup do mix original (BANIDO_ORIGINAL_MIX=1) via stream copy lossless."""
    from app.lossless_media import remove_original_mix_backup

    _ensure_not_busy(video_id, "remover o backup de áudio deste vídeo")
    video = get_video_by_id(video_id, include_data=False)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")

    mkv_path = Path(video["file_path"])
    res = remove_original_mix_backup(mkv_path)
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "Erro ao remover faixa de backup"))
    AUDIO_NORM_CHECK_CACHE.pop(video_id, None)
    return res


@router.post("/api/normalize-audio-batch")
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


@router.post("/api/normalize-audio-batch/cancel")
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


@router.post("/api/reset/{video_id}")
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


@router.get("/api/logs/video/{video_id}")
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


@router.get("/api/logs/job/{job_id}")
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


@router.get("/api/history")
def list_job_history(limit: int = 50, video_id: Optional[int] = None):
    reconcile_worker_processes()
    jobs = get_job_history(limit=limit, video_id=video_id)
    return {"jobs": jobs}
