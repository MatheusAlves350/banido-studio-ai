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


@router.get("/api/validate-metadata/{video_id}")
def validate_metadata(video_id: int, title: str = ""):
    """Avisos sobre título, descrição, tags e capítulos antes de publicar (B4)."""
    from pathlib import Path
    from app.ai.validator import validate_youtube_metadata, probe_video_format
    video = get_video_by_id(video_id)
    if not video or "data" not in video:
        raise HTTPException(status_code=404, detail="Dados de transcrição não encontrados")
    ai_data = video["data"]
    if not ai_data.get("video_info") and video.get("file_path"):
        ai_data["video_info"] = probe_video_format(Path(video["file_path"]))
    return validate_youtube_metadata(ai_data, title=title)


class RegenerateTitleRequest(BaseModel):
    index: int
    mode: str = "other_tone"  # "shorter" ou "other_tone"
    engine: str = "gemini"
    model: str = ""


@router.post("/api/titles/regenerate/{video_id}")
def regenerate_video_title(video_id: int, req: RegenerateTitleRequest):
    """Refaz um título só ("Mais curto" ou "Outro tom") com uma chamada curta à IA (B9)."""
    from app.ai.titles import regenerate_title, make_generator
    from app.database import get_setting
    from app.model_catalog import DEFAULT_GOOGLE_MODEL, DEFAULT_NVIDIA_MODEL

    video = get_video_by_id(video_id)
    if not video or "data" not in video:
        raise HTTPException(status_code=404, detail="Dados de transcrição não encontrados")
    ai_data = video["data"]

    if req.engine == "gemini":
        key = get_setting("google_api_key", "")
        model = req.model or get_setting("google_model", DEFAULT_GOOGLE_MODEL)
    elif req.engine == "nvidia":
        key = get_setting("nvidia_api_key", "")
        model = req.model or get_setting("nvidia_model", DEFAULT_NVIDIA_MODEL)
    else:
        raise HTTPException(status_code=400, detail="Para refazer um título, escolha Gemini ou NVIDIA (o Claude Web é manual).")
    if not key:
        raise HTTPException(status_code=400, detail="Configure a chave da IA escolhida para refazer títulos.")

    try:
        new_title = regenerate_title(ai_data, req.index, req.mode, make_generator(req.engine, key, model))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"A IA não respondeu: {e}")

    ai_data["title_options"][req.index] = new_title
    save_video_result(
        video_id=video_id,
        game_name=ai_data.get("main_game", video.get("game_name", "")),
        duration=video.get("duration_sec", 0.0),
        mosaic_path=video.get("mosaic_path", ""),
        srt_path=video.get("srt_path", ""),
        ai_data=ai_data,
    )
    return {"success": True, "title": new_title, "title_options": ai_data["title_options"]}


@router.get("/api/mosaics/{video_id}")
def list_video_mosaics(video_id: int):
    """Mosaicos do vídeo (principal, por hora e de mudanças de cena), para anexar no Claude Web (B8)."""
    import re
    from app.config import DATABASE_DIR
    folder = DATABASE_DIR / "mosaics"
    files = []
    for path in sorted(folder.glob(f"{video_id}_mosaic*.jpg")):
        suffix = path.stem[len(f"{video_id}_mosaic"):]
        if not suffix:
            label = "Sessão inteira (16 cenas)"
        elif suffix == "_scenes":
            label = "Mudanças de cena"
        else:
            m = re.match(r"_h(\d+)$", suffix)
            label = f"Hora {int(m.group(1))}" if m else suffix.strip("_")
        files.append({"name": path.name, "url": f"/mosaics/{path.name}", "label": label})
    return {"files": files}


class ThumbnailRequest(BaseModel):
    engine: str = ""   # "gemini" ou "nvidia" para sugerir os textos; vazio = só os quadros
    model: str = ""


@router.get("/api/thumbnails/{video_id}")
def get_thumbnails(video_id: int):
    """Última sugestão de miniaturas salva para o vídeo (B6)."""
    video = get_video_by_id(video_id)
    if not video or "data" not in video:
        raise HTTPException(status_code=404, detail="Dados de transcrição não encontrados")
    return video["data"].get("thumbnails") or {"frames": [], "texts": []}


@router.post("/api/thumbnails/{video_id}")
def suggest_thumbnails(video_id: int, req: ThumbnailRequest):
    """Escolhe 3 a 5 quadros nítidos nos picos de hype (1280x720) e sugere 2 textos curtos (B6)."""
    from pathlib import Path
    from app.config import DATABASE_DIR
    from app.database import get_setting
    from app.model_catalog import DEFAULT_GOOGLE_MODEL, DEFAULT_NVIDIA_MODEL
    from app.thumbnails import generate_thumbnails, suggest_thumbnail_texts
    from app.ai.titles import make_generator

    _ensure_not_busy(video_id, "gerar miniaturas neste vídeo")
    video = get_video_by_id(video_id)
    if not video or "data" not in video:
        raise HTTPException(status_code=404, detail="Dados de transcrição não encontrados")
    if not video.get("file_path") or not Path(video["file_path"]).exists():
        raise HTTPException(status_code=404, detail="O arquivo de vídeo não foi encontrado no disco.")
    ai_data = video["data"]
    duration = float(video.get("duration_sec") or 0.0)

    frames = generate_thumbnails(Path(video["file_path"]), DATABASE_DIR / "mosaics", video_id, ai_data, duration)
    if not frames:
        raise HTTPException(status_code=500, detail="Não foi possível extrair quadros deste vídeo.")

    texts, note = [], ""
    if req.engine in ("gemini", "nvidia"):
        key = get_setting("google_api_key" if req.engine == "gemini" else "nvidia_api_key", "")
        model = req.model or get_setting(
            "google_model" if req.engine == "gemini" else "nvidia_model",
            DEFAULT_GOOGLE_MODEL if req.engine == "gemini" else DEFAULT_NVIDIA_MODEL,
        )
        if key:
            try:
                texts = suggest_thumbnail_texts(ai_data, make_generator(req.engine, key, model))
            except Exception as e:
                note = f"Quadros prontos, mas a IA não sugeriu textos: {e}"
        else:
            note = "Quadros prontos. Configure a chave da IA para receber sugestões de texto."

    ai_data["thumbnails"] = {"frames": frames, "texts": texts}
    save_video_result(
        video_id=video_id,
        game_name=ai_data.get("main_game", video.get("game_name", "")),
        duration=duration,
        mosaic_path=video.get("mosaic_path", ""),
        srt_path=video.get("srt_path", ""),
        ai_data=ai_data,
    )
    return {"frames": frames, "texts": texts, "note": note}


@router.get("/api/ollama/status")
def get_ollama_status():
    """O Ollama (IA local) está aberto e quais modelos já foram baixados (B11)."""
    from app.ai.ollama import ollama_status
    return ollama_status()


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
        from app.ai.highlight_check import check_highlights
        from app.ai.ranking import rank_highlights
        check_highlights(ai_data, video.get("duration_sec"))
        rank_highlights(ai_data)
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
