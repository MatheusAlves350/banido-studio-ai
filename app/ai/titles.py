"""Estilos de título e refazer um título só (B9).

Analogia: um cardápio com cinco pratos da mesma receita não dá escolha. Aqui os cinco títulos
têm sabores diferentes, e dá para pedir "mais curto" ou "outro tom" para um prato só.
"""
import re
from typing import Any, Callable, Dict, List, Optional

from app.ai.schema import TEMPERATURE_CREATIVE
from app.ai.youtube import sanitize_video_title

YT_TITLE_MAX = 100
SHORT_TARGET = 60

# Ordem fixa dos cinco títulos devolvidos pela IA (o prompt pede nesta ordem).
TITLE_STYLES: List[str] = ["Engraçado", "Curioso", "Direto", "Livre", "Livre"]

MODES = ("shorter", "other_tone")

TITLE_SYSTEM = (
    "Você escreve títulos de YouTube em português do Brasil para um canal de gameplay entre amigos. "
    "Responda SOMENTE com o título, uma linha, sem aspas, sem colchetes e sem explicação."
)


def build_title_request(ai_data: Dict[str, Any], current_title: str, mode: str, style: str = "") -> str:
    game = str(ai_data.get("main_game") or "Jogo").strip()
    summary = str(ai_data.get("session_summary") or "").strip()
    chapters = ", ".join(
        str(c.get("title", "")) for c in (ai_data.get("chapters") or [])[:10] if isinstance(c, dict)
    )
    base = (
        f"Jogo principal: {game}\nResumo da sessão: {summary}\nCapítulos: {chapters}\n"
        f"Título atual: {current_title}\n"
        f"Regra: começar com \"{game} - \" (nome do jogo e hífen), sem colchetes, no máximo {YT_TITLE_MAX} caracteres.\n"
    )
    if mode == "shorter":
        return base + f"Pedido: reescreva o título atual mais curto (até {SHORT_TARGET} caracteres), mantendo a graça e a ideia."
    tone = f" O tom atual é \"{style}\"; escolha um tom diferente." if style else ""
    return base + "Pedido: escreva um título novo, com outro tom e outra ideia do mesmo vídeo." + tone


def finalize_title(raw: str, main_game: str) -> str:
    """Primeira linha, sem aspas/colchetes, começando com 'Jogo - ' e com no máximo 100 caracteres."""
    first = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    title = sanitize_video_title(first.strip().strip("\"'“”"))
    game = (main_game or "").strip()
    if game and title and not title.lower().startswith(game.lower() + " -"):
        resto = re.sub(r"^[-–—:\s]+", "", title)
        title = f"{game} - {resto}"
    if len(title) > YT_TITLE_MAX:
        cut = title[:YT_TITLE_MAX].rsplit(" ", 1)[0].rstrip(" -,:;")
        title = cut or title[:YT_TITLE_MAX]
    return title


def regenerate_title(
    ai_data: Dict[str, Any],
    index: int,
    mode: str,
    generate: Callable[[str, str, float], str],
) -> str:
    """
    Refaz o título `index`. `generate(system, user, temperature)` faz a chamada ao provedor
    (injetada para permitir teste sem rede). Usa a temperatura criativa (0,8).
    """
    if mode not in MODES:
        raise ValueError("Modo inválido: use 'shorter' ou 'other_tone'.")
    titles = ai_data.get("title_options") or []
    if not (0 <= index < len(titles)):
        raise ValueError("Título inexistente.")
    style = TITLE_STYLES[index] if index < len(TITLE_STYLES) else ""
    user = build_title_request(ai_data, titles[index], mode, style)
    raw = generate(TITLE_SYSTEM, user, TEMPERATURE_CREATIVE)
    new_title = finalize_title(raw, str(ai_data.get("main_game") or ""))
    if not new_title:
        raise ValueError("A IA não devolveu um título utilizável.")
    return new_title


def make_generator(engine: str, api_key: str, model: str) -> Optional[Callable[[str, str, float], str]]:
    """Gerador de texto do provedor escolhido (None para Claude Web, que é manual)."""
    if engine == "gemini":
        from app.ai.gemini import generate_text_gemini
        return lambda system, user, temp: generate_text_gemini(api_key, model, system, user, temp)
    if engine == "nvidia":
        from app.ai.nvidia import generate_text_nvidia
        return lambda system, user, temp: generate_text_nvidia(api_key, model, system, user, temp)
    return None
