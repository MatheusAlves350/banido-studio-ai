"""Ranking híbrido dos melhores momentos (B3): nota da IA + telemetria de risada e caos.

Analogia: escolher o melhor lance juntando a opinião do narrador (a IA) com o barulho da torcida
(risada, caos de vozes, Soundpad). Um dos dois sozinho engana.
"""
import os
from typing import Any, Dict, List, Tuple

DEFAULT_WEIGHT_AI = 60
DEFAULT_WEIGHT_TELEMETRY = 40
NO_PEAK_PENALTY = 5     # penalidade leve (em pontos) quando não há pico de hype no trecho
HYPE_PEAK = 75
ENV_WEIGHT_AI = "BANIDO_HIGHLIGHT_WEIGHT_AI"
ENV_WEIGHT_TEL = "BANIDO_HIGHLIGHT_WEIGHT_TELEMETRY"


def _env_weight(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
        if 0 <= value <= 100:
            return value
    except ValueError:
        pass
    return default


def get_weights() -> Tuple[float, float]:
    """Pesos normalizados (somam 1). Se ambos forem 0, volta ao padrão 60/40."""
    w_ai = _env_weight(ENV_WEIGHT_AI, DEFAULT_WEIGHT_AI)
    w_tel = _env_weight(ENV_WEIGHT_TEL, DEFAULT_WEIGHT_TELEMETRY)
    total = w_ai + w_tel
    if total == 0:
        w_ai, w_tel, total = DEFAULT_WEIGHT_AI, DEFAULT_WEIGHT_TELEMETRY, 100
    return w_ai / total, w_tel / total


def telemetry_score(start: float, end: float, hype_scores: List[Dict[str, Any]]) -> int:
    """Maior nota de hype (0 a 100) entre as janelas de 5 s que tocam o trecho; 0 se não houver."""
    best = 0
    for h in hype_scores:
        if float(h["end"]) > start and float(h["start"]) < end:
            best = max(best, int(h.get("score", 0)))
    return min(100, max(0, best))


def rank_highlights(ai_data: Dict[str, Any]) -> List[str]:
    """
    Calcula a nota final de cada destaque em lugar e guarda as duas notas separadas
    (`score_ai`, `score_hype`). Sem telemetria (hype_scores vazio) não mexe em nada.
    """
    hype = ai_data.get("hype_scores") or []
    highlights = ai_data.get("highlights") or []
    if not hype or not highlights:
        return []

    w_ai, w_tel = get_weights()
    log: List[str] = []
    for h in highlights:
        ai_score = int(h.get("score_ai", h.get("score", 0)) or 0)
        tel = telemetry_score(float(h["start"]), float(h["end"]), hype)
        final = w_ai * ai_score + w_tel * tel
        if tel < HYPE_PEAK:
            final -= NO_PEAK_PENALTY
        final = int(round(max(0, min(100, final))))
        h["score_ai"] = ai_score
        h["score_hype"] = tel
        h["score"] = final
        log.append(f"[DESTAQUES] \"{h.get('title', 'Melhor momento')}\": nota {final} (IA {ai_score}, risada/caos {tel}).")
    return log
