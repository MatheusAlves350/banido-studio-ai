"""Rotas de mídia: renomear, prévia de áudio, cortes e capítulos."""
from fastapi import APIRouter
from pathlib import Path
from typing import Optional
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app import security

from app.config import (
    TEMP_DIR
)
from app.database import (
    get_video_by_id, update_video_paths_after_rename
)
from app.lossless_media import (
    rename_mkv_and_srt, export_lossless_highlight, embed_chapters_into_mkv
)
from app.clip_preview import build_preview_proxy, clamp_window
from app.voice_bank import (
    extract_highlight_preview_wav
)
from app.worker_control import (
    _ensure_not_busy,
)

router = APIRouter()


class RenameRequest(BaseModel):
    title: str
    game_name: Optional[str] = ""


@router.post("/api/rename/{video_id}")
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


@router.get("/api/audio/preview/{video_id}")
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


@router.get("/api/video/preview/{video_id}")
def preview_clip_video(video_id: int, start: float = 0.0, end: float = 15.0):
    """C1: prévia leve (360p, até 2 min) do trecho para conferir início e fim antes de exportar."""
    video = get_video_by_id(video_id, include_data=False)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")
    mkv_path = Path(video["file_path"])
    if not mkv_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo MKV não encontrado no disco")

    s, e = clamp_window(start, end, float(video.get("duration_sec") or 0.0))
    preview_dir = TEMP_DIR / "previews"
    preview_mp4 = preview_dir / f"clip_v{video_id}_{int(video.get('mtime') or 0)}_{int(s * 10)}_{int(e * 10)}.mp4"
    if not preview_mp4.exists() or preview_mp4.stat().st_size <= 1000:
        if not build_preview_proxy(mkv_path, s, e, preview_mp4):
            raise HTTPException(status_code=500, detail="Não foi possível gerar a prévia do trecho.")
        security.prune_directory(preview_dir)
    return FileResponse(str(preview_mp4), media_type="video/mp4", filename=preview_mp4.name)


class CutRequest(BaseModel):
    video_id: int
    start_sec: float
    end_sec: float
    title: str
    boost_discord: bool = True
    keep_all_tracks: bool = False
    exact_cut: bool = False


@router.post("/api/cut")
def cut_highlight(req: CutRequest):
    _ensure_not_busy(req.video_id, "exportar cortes deste vídeo")
    video = get_video_by_id(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")

    mkv_path = Path(video["file_path"])
    res = export_lossless_highlight(
        mkv_path, req.start_sec, req.end_sec, req.title,
        boost_discord=req.boost_discord,
        keep_all_tracks=req.keep_all_tracks,
        exact_cut=req.exact_cut,
    )
    if not res["success"]:
        raise HTTPException(status_code=500, detail=res.get("error", "Erro ao exportar corte"))
    return res


class EmbedChaptersRequest(BaseModel):
    mode: Optional[str] = "both"  # "both" | "highlights" | "chapters"


@router.post("/api/embed-chapters/{video_id}")
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
