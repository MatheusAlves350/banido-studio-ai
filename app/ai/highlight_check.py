"""Conferir os destaques contra a fala (B2).

Analogia: hoje o revisor só confere se a página existe; aqui ele confere se o trecho citado está mesmo
no livro e se o corte não começa ou termina no meio de uma frase.

Os segmentos da transcrição já vêm quebrados por frase (a quebra por palavras do Whisper é feita na
transcrição e as palavras não são guardadas no banco), então os limites de cada segmento servem de
início e fim de frase.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.ai.youtube import _seconds_to_hhmmss

PAD_BEFORE = 0.5      # folga antes da primeira fala
PAD_AFTER = 1.0       # folga depois da última fala
MAX_SHIFT = 10.0      # ajuste máximo de cada ponta; acima disso, só marca para revisão
MIN_LEN = 15.0
MAX_LEN = 180.0
HYPE_PEAK = 75        # mesmo limiar usado na telemetria


def _has_speech(segments: List[Dict[str, Any]], start: float, end: float) -> bool:
    return any(float(s["end"]) > start and float(s["start"]) < end and str(s.get("text", "")).strip() for s in segments)


def _has_hype_peak(hype_scores: List[Dict[str, Any]], start: float, end: float) -> bool:
    return any(float(h["end"]) > start and float(h["start"]) < end and h.get("score", 0) >= HYPE_PEAK for h in hype_scores)


def _snap_start(segments: List[Dict[str, Any]], t: float) -> Optional[float]:
    """Início da frase que contém t; se t cai num intervalo, o início de frase mais próximo."""
    for s in segments:
        if float(s["start"]) <= t < float(s["end"]):
            return float(s["start"])
    starts = [float(s["start"]) for s in segments]
    return min(starts, key=lambda x: abs(x - t)) if starts else None


def _snap_end(segments: List[Dict[str, Any]], t: float) -> Optional[float]:
    """Fim da frase que contém t; se t cai num intervalo, o fim de frase mais próximo."""
    for s in segments:
        if float(s["start"]) < t <= float(s["end"]):
            return float(s["end"])
    ends = [float(s["end"]) for s in segments]
    return min(ends, key=lambda x: abs(x - t)) if ends else None


def refine_highlight(
    h: Dict[str, Any],
    segments: List[Dict[str, Any]],
    hype_scores: List[Dict[str, Any]],
    duration_sec: Optional[float],
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Devolve (destaque ajustado, motivo). Destaque None = descartado, com o motivo."""
    start, end = float(h["start"]), float(h["end"])
    if not _has_speech(segments, start, end) and not _has_hype_peak(hype_scores, start, end):
        return None, "sem fala nem pico de risada no trecho"

    out = dict(h)
    notes: List[str] = []
    review = False

    snapped_start = _snap_start(segments, start)
    if snapped_start is not None:
        new_start = max(0.0, snapped_start - PAD_BEFORE)
        if abs(new_start - start) > MAX_SHIFT:
            review = True
            notes.append(f"início ficou a mais de {int(MAX_SHIFT)}s da frase mais próxima")
            new_start = start
        elif abs(new_start - start) >= 0.5:
            notes.append(f"início ajustado de {_seconds_to_hhmmss(start)} para {_seconds_to_hhmmss(new_start)}")
        start_final = new_start
    else:
        start_final = start

    snapped_end = _snap_end(segments, end)
    if snapped_end is not None:
        new_end = snapped_end + PAD_AFTER
        if duration_sec and duration_sec > 0:
            new_end = min(new_end, float(duration_sec))
        if abs(new_end - end) > MAX_SHIFT:
            review = True
            notes.append(f"fim ficou a mais de {int(MAX_SHIFT)}s da frase mais próxima")
            new_end = end
        elif abs(new_end - end) >= 0.5:
            notes.append(f"fim ajustado de {_seconds_to_hhmmss(end)} para {_seconds_to_hhmmss(new_end)}")
        end_final = new_end
    else:
        end_final = end

    if end_final <= start_final:
        start_final, end_final = start, end
        review = True
        notes.append("ajuste cancelado (fim antes do início)")

    length = end_final - start_final
    if length > MAX_LEN:
        review = True
        notes.append(f"duração de {int(length)}s passa de {int(MAX_LEN)}s")
    elif length < MIN_LEN:
        review = True
        notes.append(f"duração de {int(length)}s é menor que {int(MIN_LEN)}s")

    out.update({
        "start": round(start_final, 2),
        "end": round(end_final, 2),
        "start_time": _seconds_to_hhmmss(start_final),
        "end_time": _seconds_to_hhmmss(end_final),
        "needs_review": review,
        "adjust_note": "; ".join(notes),
    })
    return out, ""


def check_highlights(ai_data: Dict[str, Any], duration_sec: Optional[float] = None) -> List[str]:
    """
    Ajusta ai_data["highlights"] em lugar, com base em ai_data["segments"] e ["hype_scores"].
    Devolve linhas de log (o que foi ajustado, marcado ou descartado). Sem segmentos, não mexe em nada.
    """
    segments = [s for s in (ai_data.get("segments") or []) if isinstance(s, dict) and "start" in s and "end" in s]
    highlights = ai_data.get("highlights") or []
    log: List[str] = []
    if not segments or not highlights:
        return log

    hype = ai_data.get("hype_scores") or []
    kept: List[Dict[str, Any]] = []
    for h in highlights:
        new_h, why = refine_highlight(h, segments, hype, duration_sec)
        title = h.get("title", "Melhor momento")
        if new_h is None:
            log.append(f"[DESTAQUES] Descartado \"{title}\": {why}.")
            continue
        if new_h.get("adjust_note"):
            flag = " (marcado para revisão)" if new_h.get("needs_review") else ""
            log.append(f"[DESTAQUES] \"{title}\": {new_h['adjust_note']}{flag}.")
        kept.append(new_h)
    kept.sort(key=lambda x: x["start"])
    ai_data["highlights"] = kept
    return log
