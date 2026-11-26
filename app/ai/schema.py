"""Esquema da resposta da IA (B7): confere a estrutura antes de aceitar e define as temperaturas.

Analogia: o contador (capítulos, tempos, JSON) trabalha com a calculadora ao lado; o título criativo
fica para outra etapa. Aqui ficam a calculadora (temperatura baixa) e o conferente da folha (esquema).
"""
import os
from typing import Any, List

# Capítulos, tempos e estrutura pedem precisão. Títulos criativos usam TEMPERATURE_CREATIVE (B9).
TEMPERATURE_STRUCTURE = 0.2
TEMPERATURE_CREATIVE = 0.8
# Modelos de raciocínio (NVIDIA) rendem melhor perto de 0,6; mantido como antes.
TEMPERATURE_REASONING = 0.6

ENV_TEMPERATURE = "BANIDO_AI_TEMPERATURE"


class AISchemaError(ValueError):
    """A resposta da IA é um JSON válido, mas não tem o formato esperado."""

    def __init__(self, problems: List[str], raw_text: str = "", duration_sec: Any = None):
        super().__init__("Resposta da IA fora do formato esperado: " + "; ".join(problems))
        self.problems = problems
        self.raw_text = raw_text
        self.duration_sec = duration_sec


def get_structure_temperature() -> float:
    """Temperatura da análise principal; BANIDO_AI_TEMPERATURE no .env sobrescreve (0 a 1,5)."""
    raw = os.getenv(ENV_TEMPERATURE, "").strip().replace(",", ".")
    if raw:
        try:
            value = float(raw)
            if 0.0 <= value <= 1.5:
                return value
        except ValueError:
            pass
    return TEMPERATURE_STRUCTURE


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_ai_schema(data: Any) -> List[str]:
    """Devolve a lista de problemas de estrutura (vazia = resposta aceita). Não olha o conteúdo."""
    if not isinstance(data, dict):
        return ["a resposta não é um objeto JSON { ... }"]

    problems: List[str] = []

    if not _has_text(data.get("main_game")):
        problems.append("'main_game' ausente ou vazio")
    if not _has_text(data.get("session_summary")):
        problems.append("'session_summary' ausente ou vazio")

    titles = data.get("title_options")
    if not isinstance(titles, list) or not any(_has_text(t) for t in titles):
        problems.append("'title_options' precisa ser uma lista de textos")

    chapters = data.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        problems.append("'chapters' precisa ser uma lista com pelo menos um capítulo")
    else:
        bad = [c for c in chapters if not (isinstance(c, dict) and _has_text(c.get("timestamp")) and _has_text(c.get("title")))]
        if bad:
            problems.append("cada item de 'chapters' precisa de 'timestamp' e 'title'")

    topics = data.get("topics")
    if not isinstance(topics, list):
        problems.append("'topics' precisa ser uma lista")

    highlights = data.get("highlights")
    if not isinstance(highlights, list):
        problems.append("'highlights' precisa ser uma lista")
    else:
        for h in highlights:
            has_start = isinstance(h, dict) and (h.get("start") is not None or h.get("start_time") is not None)
            has_end = isinstance(h, dict) and (h.get("end") is not None or h.get("end_time") is not None)
            if not (has_start and has_end):
                problems.append("cada item de 'highlights' precisa de início e fim ('start'/'end' ou 'start_time'/'end_time')")
                break

    if "speaker_mapping" in data and not isinstance(data.get("speaker_mapping"), dict):
        problems.append("'speaker_mapping' precisa ser um objeto")

    return problems


def build_correction_note(problems: List[str]) -> str:
    """Texto curto anexado ao pedido quando a primeira resposta veio fora do formato."""
    lista = "\n".join(f"- {p}" for p in problems)
    return (
        "ATENÇÃO: sua resposta anterior não seguiu o formato exigido:\n"
        f"{lista}\n"
        "Responda novamente com o objeto JSON COMPLETO, no formato pedido, sem texto fora do JSON."
    )
