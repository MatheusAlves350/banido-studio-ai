"""Estimativa de tokens antes do envio à IA (B10).

Analogia: ver o preço da corrida antes de entrar no táxi. A conta é aproximada (cerca de 4 caracteres
por token) e vem com margem de segurança, porque o número real varia com o idioma.
"""
import math
import os
from typing import Any, Dict

from app.model_catalog import get_context_limit
from app.ai.prompt import SYSTEM_PROMPT

CHARS_PER_TOKEN = 4
# Um mosaico 4x4 é uma imagem; o Gemini cobra por blocos de imagem. Valor médio conservador.
TOKENS_PER_MOSAIC = 1500
# Espaço reservado para a resposta JSON da IA.
OUTPUT_RESERVE_TOKENS = 8000
DEFAULT_MARGIN_PCT = 20
ENV_MARGIN = "BANIDO_AI_TOKEN_MARGIN_PCT"


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text or "") / CHARS_PER_TOKEN)


def get_margin_pct() -> int:
    """Margem de segurança em % do limite (padrão 20; BANIDO_AI_TOKEN_MARGIN_PCT sobrescreve, 0 a 60)."""
    raw = os.getenv(ENV_MARGIN, "").strip()
    try:
        value = int(raw)
        if 0 <= value <= 60:
            return value
    except ValueError:
        pass
    return DEFAULT_MARGIN_PCT


def estimate_request(transcript_text: str, engine: str, model: str = "", mosaic_count: int = 0) -> Dict[str, Any]:
    """
    Estima o tamanho do envio e compara com o limite do modelo.
    nivel: 'ok' (verde), 'atencao' (amarelo, passou de 60% do limite) ou 'estoura' (vermelho, passou do
    limite menos a margem de segurança).
    """
    engine = engine or "gemini"
    sends_image = engine in ("gemini", "google")
    transcript_tokens = estimate_tokens(transcript_text)
    mosaic_tokens = TOKENS_PER_MOSAIC * max(0, mosaic_count) if sends_image else 0
    prompt_tokens = estimate_tokens(SYSTEM_PROMPT)
    total = transcript_tokens + mosaic_tokens + prompt_tokens + OUTPUT_RESERVE_TOKENS
    limit = get_context_limit(engine, model)
    margin = get_margin_pct()
    safe_limit = int(limit * (100 - margin) / 100)

    if total > safe_limit:
        nivel = "estoura"
    elif total > limit * 0.6:
        nivel = "atencao"
    else:
        nivel = "ok"

    sugestao = ""
    if nivel == "estoura":
        sugestao = (
            "Pode passar do limite deste modelo. Use um modelo com limite maior"
            + (" ou envie menos mosaicos" if mosaic_tokens else "")
            + "."
        )
    elif nivel == "atencao":
        sugestao = "Está perto do limite deste modelo. Vale conferir antes de enviar."

    return {
        "engine": engine,
        "model": model,
        "transcript_tokens": transcript_tokens,
        "mosaic_tokens": mosaic_tokens,
        "instructions_tokens": prompt_tokens,
        "reserve_tokens": OUTPUT_RESERVE_TOKENS,
        "total_tokens": total,
        "limit_tokens": limit,
        "margin_pct": margin,
        "level": nivel,
        "suggestion": sugestao,
        "approximate": True,
    }
