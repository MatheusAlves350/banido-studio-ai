"""Prompt inicial enviado ao Whisper, com o nome do jogo e contexto extra."""
from typing import List, Optional

from app.config import (
    HOST_SPEAKER_NAME
)
from app.transcription.glossary import load_glossary
from app.voice_bank import list_known_voice_names

# Limite seguro (~224 tokens do initial_prompt do Whisper ≈ 700 chars em PT-BR)
MAX_PROMPT_CHARS = 680


def build_whisper_initial_prompt(manual_game: str = "", extra_context: str = "",
                                 glossary_terms: Optional[List[str]] = None) -> str:
    """
    Constrói um initial_prompt dinâmico para o Whisper large-v3 contendo os nomes oficiais
    do Banco de Vozes, o jogo escolhido e o contexto extra digitado pelo usuário.
    Isso melhora a precisão ortográfica de nomes próprios, mapas e termos do jogo.
    Os termos do glossário do grupo (A5) usam o espaço que sobrar; os que não cabem inteiros ficam de fora.
    """
    parts = ["Conversa informal, engraçada e descontraída entre amigos jogando no Discord."]
    try:
        known = list_known_voice_names(exclude_host=False)
    except Exception:
        known = []

    names_clean = []
    seen = set()
    for n in [HOST_SPEAKER_NAME] + list(known):
        clean = str(n or "").strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            names_clean.append(clean)
    if names_clean:
        parts.append(f"Participantes: {', '.join(names_clean)}.")

    game_clean = str(manual_game or "").strip()
    if game_clean and game_clean.lower() not in ("gameplay", "videogame"):
        parts.append(f"Jogo: {game_clean}.")

    ctx_clean = " ".join(str(extra_context or "").strip().split())
    ctx_part = f"Contexto e termos: {ctx_clean[:260]}." if ctx_clean else ""

    if glossary_terms is None:
        try:
            glossary_terms = load_glossary()
        except Exception:
            glossary_terms = []
    fixed = " ".join(parts + ([ctx_part] if ctx_part else []))
    chosen: List[str] = []
    prefix = "Termos do grupo: "
    room = MAX_PROMPT_CHARS - len(fixed) - len(prefix) - 2  # espaço e ponto final
    used = 0
    for term in glossary_terms:
        cost = len(term) + (2 if chosen else 0)  # ", "
        if used + cost > room:
            break
        chosen.append(term)
        used += cost
    if chosen:
        parts.append(f"{prefix}{', '.join(chosen)}.")
    if ctx_part:
        parts.append(ctx_part)

    return " ".join(parts)[:MAX_PROMPT_CHARS]
