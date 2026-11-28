"""Glossário do grupo para o Whisper (A5)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.transcription.glossary import (
    MAX_TERMS, MAX_TERM_LEN, load_glossary, parse_glossary_text, save_glossary,
)

router = APIRouter()


class GlossaryRequest(BaseModel):
    text: str = ""


def _response(terms):
    return {"terms": terms, "count": len(terms), "max_terms": MAX_TERMS, "max_term_len": MAX_TERM_LEN}


@router.get("/api/glossary")
def get_glossary():
    return _response(load_glossary())


@router.post("/api/glossary")
def update_glossary(req: GlossaryRequest):
    if len(req.text) > 20000:
        raise HTTPException(status_code=400, detail="Texto grande demais para um glossário.")
    try:
        saved = save_glossary(parse_glossary_text(req.text))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Não foi possível salvar o glossário: {e}")
    return _response(saved)
