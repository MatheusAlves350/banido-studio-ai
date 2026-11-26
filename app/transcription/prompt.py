"""Prompt inicial enviado ao Whisper, com o nome do jogo e contexto extra."""
from app.config import (
    HOST_SPEAKER_NAME
)
from app.voice_bank import list_known_voice_names


def build_whisper_initial_prompt(manual_game: str = "", extra_context: str = "") -> str:
    """
    Constrói um initial_prompt dinâmico para o Whisper large-v3 contendo os nomes oficiais
    do Banco de Vozes, o jogo escolhido e o contexto extra digitado pelo usuário.
    Isso melhora a precisão ortográfica de nomes próprios, mapas e termos do jogo.
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
    if ctx_clean:
        parts.append(f"Contexto e termos: {ctx_clean[:260]}.")

    # Limite seguro (~224 tokens do initial_prompt do Whisper ≈ 700 chars em PT-BR)
    prompt = " ".join(parts)
    return prompt[:680]
