"""Rotas de status, lista de vídeos, jogos e detalhes do vídeo."""
from fastapi import APIRouter
from pathlib import Path
from typing import Optional
from fastapi import HTTPException
from pydantic import BaseModel

from app.config import (
    BASE_DIR, OBS_RECORDINGS_DIR, VOICE_BANK_DIR, CLIPS_DIR, TEMP_DIR,
    HOST_SPEAKER_NAME, GROUP_NAME, PC_SPECS
)
from app.database import (
    sync_obs_videos, get_all_videos, get_video_by_id, get_job_count, get_job_by_id,
    now_str, set_video_audio_normalized, get_game_history, record_game_history,
    delete_game_from_history, update_video_game_and_context
)
from app.ai_director import (
    build_youtube_description, build_youtube_tags_csv,
    sanitize_video_title, normalize_youtube_chapters
)
from app.lossless_media import (
    inspect_mkv_audio_norm
)
from app.transcription.edits import load_edits
from app.voice_bank import (
    list_known_voice_names
)
from app.friendly_errors import explain
from app.worker_control import (
    ACTIVE_PROCESSES,
    AUDIO_NORM_CHECK_CACHE,
    reconcile_worker_processes,
)

router = APIRouter()


@router.get("/api/status")
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


@router.get("/api/videos")
def list_videos(sync: bool = True):
    reconcile_worker_processes()
    if sync:
        AUDIO_NORM_CHECK_CACHE.clear()
        sync_obs_videos(OBS_RECORDINGS_DIR)
    videos = get_all_videos()
    return {"videos": videos}


@router.get("/api/games")
def list_games():
    return {"games": get_game_history(limit=40)}


class GameHistoryRequest(BaseModel):
    name: str
    default_context: Optional[str] = None


@router.post("/api/games")
def add_game_history(req: GameHistoryRequest):
    clean = " ".join(str(req.name or "").strip().split())
    if not clean:
        raise HTTPException(status_code=400, detail="Informe o nome do jogo.")
    record_game_history(clean, default_context=req.default_context, increment=True)
    return {"success": True, "games": get_game_history(limit=40)}


@router.delete("/api/games/{game_name:path}")
def remove_game_history(game_name: str):
    delete_game_from_history(game_name)
    return {"success": True, "games": get_game_history(limit=40)}


class VideoGameContextRequest(BaseModel):
    game_name: Optional[str] = ""
    session_context: Optional[str] = ""
    save_default_context: bool = False


@router.post("/api/video/{video_id}/game")
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


@router.get("/api/video/{video_id}")
def get_video_details(video_id: int, include_data: bool = True):
    reconcile_worker_processes()
    video = get_video_by_id(video_id, include_data=include_data)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")
    video["is_worker_alive"] = video_id in ACTIVE_PROCESSES
    msg = video.get("progress_msg") or ""
    if video.get("status") == "error" or (video.get("status") == "unprocessed" and "interrompido" in msg):
        video["error_info"] = explain(msg)
    if include_data:
        video["known_friends"] = list_known_voice_names(exclude_host=False)
        video["transcript_edits"] = load_edits(video_id)

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
