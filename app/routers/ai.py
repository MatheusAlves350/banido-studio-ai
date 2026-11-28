"""Rotas do fluxo manual com o Claude Web (prompt e importação da resposta)."""
from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel

from app.database import (
    get_video_by_id, save_video_result
)
from app.ai_director import (
    generate_claude_web_prompt, parse_claude_web_response,
    build_youtube_description
)
from app.worker_control import (
    _ensure_not_busy,
)

router = APIRouter()


@router.get("/api/ai/estimate/{video_id}")
def estimate_ai_tokens(video_id: int, engine: str = "gemini", model: str = ""):
    """Estimativa (aproximada) de tokens do envio à IA, antes de enviar (B10)."""
    from app.ai.tokens import estimate_request
    from app.config import DATABASE_DIR
    video = get_video_by_id(video_id)
    if not video or "data" not in video:
        raise HTTPException(status_code=404, detail="Dados de transcrição não encontrados")
    transcript = video["data"].get("raw_transcript_prompt", "")
    mosaics = list((DATABASE_DIR / "mosaics").glob(f"{video_id}_mosaic*.jpg"))
    return estimate_request(transcript, engine, model, mosaic_count=len(mosaics))


@router.get("/api/claude-prompt/{video_id}")
def get_claude_prompt(video_id: int):
    video = get_video_by_id(video_id)
    if not video or "data" not in video:
        raise HTTPException(status_code=404, detail="Dados de transcrição não encontrados")
    raw_prompt = video["data"].get("raw_transcript_prompt", "")
    full_prompt = generate_claude_web_prompt(raw_prompt)
    return {"prompt": full_prompt}


class ClaudeImportRequest(BaseModel):
    claude_response: str


@router.post("/api/claude-import/{video_id}")
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
