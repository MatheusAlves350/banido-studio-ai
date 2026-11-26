"""Tratamento das faixas de áudio: mix balanceado, detecção de fala e remoção de eco do microfone."""
import difflib
import shutil
import subprocess
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional
from app.config import (
    HOST_SPEAKER_NAME
)
from app.transcription.hallucination import (
    _normalize_for_hallucination,
)


def create_balanced_mix_audio(mic_wav: Path, discord_wav: Path, output_mix_wav: Path, has_isolated_mic: bool = True):
    """
    Cria uma mixagem mono equilibrada para garantir que o Whisper escute tanto
    o microfone do anfitrião quanto as vozes amplificadas do Discord com nitidez perfeita.
    """
    if not has_isolated_mic:
        shutil.copyfile(str(discord_wav), str(output_mix_wav))
        return

    print(f"[FFMPEG] Gerando mixagem balanceada ({HOST_SPEAKER_NAME} + Discord normalizado) para o Whisper...", flush=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(mic_wav),
        "-i", str(discord_wav),
        "-filter_complex", "amix=inputs=2:duration=longest:weights=1.0 1.2:normalize=0,alimiter=limit=0.95",
        "-ac", "1", "-ar", "16000",
        str(output_mix_wav)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Erro ao criar mix para Whisper: {res.stderr[-300:]}")


def _rms_slice(rms: np.ndarray, start_sec: float, end_sec: float) -> np.ndarray:
    s_idx = max(0, int(start_sec * 10))
    e_idx = max(s_idx + 1, int(end_sec * 10))
    return rms[s_idx:min(e_idx, len(rms))]


def _mic_segment_is_real(mic_rms: np.ndarray, start_sec: float, end_sec: float) -> bool:
    """Confirma que o microfone realmente estava ativo no trecho (descarta ruído e eco)."""
    sl = _rms_slice(mic_rms, start_sec, end_sec)
    if len(sl) == 0:
        return False
    return float(np.mean(sl > -48.0)) >= 0.20 and float(np.percentile(sl, 75)) > -46.0


def _track_has_speech(rms: Optional[np.ndarray], min_active_frac: float = 0.02) -> bool:
    """Evita rodar o Whisper numa faixa praticamente muda (economiza GPU)."""
    if rms is None or len(rms) == 0:
        return False
    return float(np.mean(rms > -48.0)) >= min_active_frac


def _time_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    shorter = max(0.05, min(a["end"] - a["start"], b["end"] - b["start"]))
    return max(0.0, inter) / shorter


def _drop_mic_bleed(host_segs: List[Dict[str, Any]], discord_segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove falas do microfone que são só o eco do Discord saindo pelas caixas de som:
    mesmo trecho de tempo e texto praticamente igual ao da faixa Discord.
    Reações curtas simultâneas ("boa", "morri", "calma") são preservadas.
    """
    kept = []
    for h in host_segs:
        h_norm = _normalize_for_hallucination(h["text"])
        if len(h_norm) < 12 and len(h_norm.split()) < 3:
            kept.append(h)
            continue
        is_echo = False
        for d in discord_segs:
            if d["start"] > h["end"]:
                break
            if _time_overlap(h, d) < 0.5:
                continue
            ratio = difflib.SequenceMatcher(None, h_norm, _normalize_for_hallucination(d["text"])).ratio()
            if ratio >= 0.8:
                is_echo = True
                break
        if not is_echo:
            kept.append(h)
    return kept


def merge_track_segments(host_segs: List[Dict[str, Any]], discord_segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Junta as falas das duas faixas em ordem cronológica, preservando as que se sobrepõem."""
    discord_sorted = sorted(discord_segs, key=lambda x: (x["start"], x["end"]))
    host_clean = _drop_mic_bleed(sorted(host_segs, key=lambda x: (x["start"], x["end"])), discord_sorted)
    merged = host_clean + discord_sorted
    merged.sort(key=lambda x: (x["start"], x["end"]))
    overlapped_ids = set()
    for i, a in enumerate(merged):
        for b in merged[i + 1:]:
            if b["start"] >= a["end"]:
                break
            if a["speaker"] != b["speaker"]:
                overlapped_ids.update((id(a), id(b)))
    for seg in merged:
        if id(seg) in overlapped_ids:
            seg["overlap"] = True
    return merged
