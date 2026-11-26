"""Editor de transcrição: corrigir o texto de uma fala e regenerar o .srt (A6)."""
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ai_director import build_youtube_description, format_transcript_for_ai
from app.database import get_video_by_id, now_str, save_video_result
from app.transcriber_v3 import export_srt_file
from app.transcription.edits import (
    clean_text, load_edits, save_edits, segment_matches, suggest_glossary_terms,
)
from app.transcription.glossary import load_glossary
from app.voice_bank import list_known_voice_names
from app.worker_control import SPEAKER_CONFIRM_LOCK, _ensure_not_busy

router = APIRouter()


class TranscriptEditRequest(BaseModel):
    index: int
    start: float
    text: str = ""


class TranscriptUndoRequest(BaseModel):
    index: int
    start: float


def _load_segment(video_id: int, index: int, start: float):
    video = get_video_by_id(video_id)
    if not video or not video.get("data"):
        raise HTTPException(status_code=404, detail="Vídeo ou transcrição não encontrados")
    segments = video["data"].get("segments") or []
    if index < 0 or index >= len(segments) or not segment_matches(segments[index], start):
        raise HTTPException(status_code=409, detail="A legenda mudou desde que foi aberta. Recarregue o vídeo e tente de novo.")
    return video, segments


def _persist(video: Dict[str, Any], segments: list) -> None:
    """Regrava o .srt e atualiza texto da IA e descrição do YouTube, como na confirmação de vozes."""
    ai_data = video["data"]
    srt_path = Path(video["srt_path"]) if video.get("srt_path") else Path(video["file_path"]).with_suffix(".srt")
    export_srt_file(segments, srt_path)
    ai_data["raw_transcript_prompt"] = format_transcript_for_ai(
        segments,
        ai_data.get("hype_scores", []),
        manual_game=ai_data.get("main_game", video.get("game_name", "")),
        known_friends=list_known_voice_names(exclude_host=False),
        extra_context=video.get("session_context", ""),
    )
    ai_data["youtube_description"] = build_youtube_description(ai_data)
    save_video_result(
        video_id=video["id"],
        game_name=video.get("game_name", ""),
        duration=video.get("duration_sec", 0.0),
        mosaic_path=video.get("mosaic_path", ""),
        srt_path=str(srt_path),
        ai_data=ai_data,
    )


def _response(edits: dict) -> Dict[str, Any]:
    return {
        "success": True,
        "edits": edits,
        "glossary_suggestions": suggest_glossary_terms(edits, load_glossary()),
    }


@router.post("/api/video/{video_id}/transcript/edit")
def edit_transcript_segment(video_id: int, req: TranscriptEditRequest):
    _ensure_not_busy(video_id, "editar a legenda deste vídeo")
    text = clean_text(req.text)
    if not text:
        raise HTTPException(status_code=400, detail="O texto da fala não pode ficar vazio.")
    with SPEAKER_CONFIRM_LOCK:
        video, segments = _load_segment(video_id, req.index, req.start)
        seg = segments[req.index]
        edits = load_edits(video_id)
        key = str(req.index)
        original = edits[key]["original"] if key in edits else str(seg.get("text", "")).strip()
        if text == original:
            edits.pop(key, None)
        else:
            edits[key] = {"start": seg.get("start", 0.0), "original": original, "text": text, "edited_at": now_str()}
        seg["text"] = text
        _persist(video, segments)
        save_edits(video_id, edits)
    return _response(edits)


@router.post("/api/video/{video_id}/transcript/undo")
def undo_transcript_edit(video_id: int, req: TranscriptUndoRequest):
    _ensure_not_busy(video_id, "desfazer a edição da legenda deste vídeo")
    with SPEAKER_CONFIRM_LOCK:
        video, segments = _load_segment(video_id, req.index, req.start)
        edits = load_edits(video_id)
        edit = edits.pop(str(req.index), None)
        if not edit:
            raise HTTPException(status_code=404, detail="Esta fala não foi editada.")
        segments[req.index]["text"] = edit["original"]
        _persist(video, segments)
        save_edits(video_id, edits)
    return _response(edits)
