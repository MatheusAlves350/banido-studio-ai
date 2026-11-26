"""Filtro de alucinações do Whisper (frases típicas de silêncio e repetição)."""
import re
from typing import Optional


WHISPER_HALLUCINATION_SUBSTRINGS = (
    "legenda por ",
    "legendas por ",
    "legendas pela comunidade",
    "amara.org",
    "traducao e legendas",
    "inscreva-se no canal",
    "obrigado por assistir",
    "subtitles by",
)


WHISPER_HALLUCINATION_PHRASES = (
    "inscreva se no canal",
    "inscreva se",
    "se inscreva",
    "ative o sininho",
    "ativa o sininho",
    "ative as notificacoes",
    "ative a notificacao",
    "deixe o like",
    "deixe seu like",
    "deixa o like",
    "deixe um like",
    "curta e compartilhe",
    "curta o video",
    "compartilhe este video",
    "ate o proximo video",
    "ate a proxima",
    "obrigado por assistir",
    "obrigada por assistir",
    "obrigado por ter assistido",
    "legendas pela comunidade",
    "traducao e legendas",
    "subtitles by",
    "thanks for watching",
    "please subscribe",
)


WHISPER_NO_SPEECH_PROB_LIMIT = 0.6


WHISPER_AVG_LOGPROB_LIMIT = -1.0


WHISPER_COMPRESSION_RATIO_LIMIT = 2.4


WHISPER_HALLUCINATION_SILENCE_THRESHOLD = 2.0


def _normalize_for_hallucination(text: str) -> str:
    """Minúsculas, sem acentos e sem pontuação (hífens viram espaço, espaços colapsados)."""
    import unicodedata
    low = unicodedata.normalize("NFKD", str(text or "").lower())
    low = "".join(ch for ch in low if not unicodedata.combining(ch))
    low = re.sub(r"[^a-z0-9\s]", " ", low)
    return " ".join(low.split())


def _is_whisper_hallucination(
    text: str,
    seg_dur: float,
    no_speech_prob: Optional[float] = None,
    avg_logprob: Optional[float] = None,
    compression_ratio: Optional[float] = None,
) -> bool:
    """
    Decide se um trecho transcrito é "legenda fantasma" do Whisper.
    Combina (1) frases clássicas de fim de vídeo, (2) métricas de confiança do próprio
    modelo e (3) repetições em laço. Falas reais curtas ("sim", "não") são preservadas.
    """
    low = str(text or "").strip().lower()
    if not low:
        return True
    norm = _normalize_for_hallucination(low)
    if not norm:
        return True

    padded = f" {norm} "
    if any(sub in low or sub in norm for sub in WHISPER_HALLUCINATION_SUBSTRINGS):
        return True
    # Frases de fim de vídeo só contam em trechos curtos: uma fala longa que apenas
    # menciona "se inscreva" no meio da conversa real é preservada.
    if len(norm.split()) <= 10 and any(f" {ph} " in padded for ph in WHISPER_HALLUCINATION_PHRASES):
        return True

    # Métricas de confiança: só descartam quando o modelo está inseguro em mais de um sinal,
    # para não perder falas reais de baixo volume.
    if (
        no_speech_prob is not None and avg_logprob is not None
        and no_speech_prob > WHISPER_NO_SPEECH_PROB_LIMIT
        and avg_logprob < WHISPER_AVG_LOGPROB_LIMIT
    ):
        return True
    if compression_ratio is not None:
        if compression_ratio > 3.0:
            return True
        if compression_ratio > WHISPER_COMPRESSION_RATIO_LIMIT:
            low_conf = (
                (avg_logprob is not None and avg_logprob < -0.5)
                or (no_speech_prob is not None and no_speech_prob > 0.35)
                or (avg_logprob is None and no_speech_prob is None)
            )
            if low_conf:
                return True

    # Laço de repetição ("e aí e aí e aí e aí e aí ...").
    toks = norm.split()
    if len(toks) >= 8 and len(set(toks)) <= max(2, len(toks) // 5):
        return True

    if seg_dur <= 0.05 and len(low) <= 40:
        return True
    return False
