"""Cortes entre blocos e refinamento de segmentos do Whisper."""
import numpy as np
import soundfile as sf
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from app.config import (
    HOST_SPEAKER_NAME
)
from app.audio_pipeline import (
    is_host_speaking
)
from app.transcription.hallucination import (
    _is_whisper_hallucination,
)


def _find_chunk_boundaries(
    wav_path: Path,
    target_chunk_sec: float = 300.0,
    search_window_sec: float = 20.0
) -> Tuple[List[Tuple[int, int]], int]:
    """
    Divide áudios longos em blocos de ~5 minutos escolhendo o ponto mais silencioso
    próximo à fronteira (janela de 400ms com menor RMS), evitando cortar falas no meio
    e mantendo o pico de RAM do STFT do faster-whisper em ~80 MiB mesmo em vídeos de 8+ horas.
    """
    with sf.SoundFile(str(wav_path)) as f:
        sr = f.samplerate
        total_frames = len(f)

    chunk_frames = int(target_chunk_sec * sr)
    if total_frames <= int(chunk_frames * 1.2):
        return [(0, total_frames)], sr

    win_frames = int(search_window_sec * sr)
    step_frames = max(1, int(0.4 * sr))
    boundaries: List[Tuple[int, int]] = []
    cur = 0

    with sf.SoundFile(str(wav_path)) as f:
        while cur < total_frames:
            ideal_end = cur + chunk_frames
            if ideal_end >= total_frames - int(60 * sr):
                boundaries.append((cur, total_frames))
                break

            search_start = max(cur + sr * 30, ideal_end - win_frames)
            search_end = min(total_frames, ideal_end + win_frames)
            f.seek(search_start)
            seg_audio = f.read(search_end - search_start, dtype="float32")
            if seg_audio.ndim > 1:
                seg_audio = seg_audio.mean(axis=1)

            n_steps = len(seg_audio) // step_frames
            if n_steps > 0:
                mat = seg_audio[:n_steps * step_frames].reshape(n_steps, step_frames)
                rms = np.sqrt(np.mean(mat ** 2, axis=1))
                quiet_idx = int(np.argmin(rms))
                split_frame = search_start + quiet_idx * step_frames + (step_frames // 2)
            else:
                split_frame = ideal_end

            boundaries.append((cur, split_frame))
            cur = split_frame

    return boundaries, sr


def _find_tail_silence_split(
    mix_audio: np.ndarray,
    sample_rate: int = 16000,
    tail_window_sec: float = 18.0,
    step_sec: float = 0.25
) -> int:
    """
    Localiza o ponto mais silencioso dentro dos últimos `tail_window_sec` segundos de um bloco,
    evitando cortar palavras ou frases ao meio na fronteira dos blocos de 5 minutos.
    """
    total_len = len(mix_audio)
    min_len = int(45.0 * sample_rate)
    if total_len <= min_len:
        return total_len

    win_samples = int(tail_window_sec * sample_rate)
    search_start = max(int(30.0 * sample_rate), total_len - win_samples)
    tail_slice = mix_audio[search_start:]
    step = max(1, int(step_sec * sample_rate))
    n_steps = len(tail_slice) // step
    if n_steps <= 0:
        return total_len

    mat = tail_slice[:n_steps * step].reshape(n_steps, step)
    rms = np.sqrt(np.mean(mat * mat, axis=1))
    quiet_idx = int(np.argmin(rms))
    split_sample = search_start + quiet_idx * step + (step // 2)
    # Garantir que o carryover não fique minúsculo (< 0.2s) nem gigantesco
    if total_len - split_sample < int(0.2 * sample_rate):
        return total_len
    return split_sample


def _refine_whisper_segment(
    seg_obj: Any,
    chunk_offset_sec: float,
    dynamics: Dict[str, Any],
    use_local_dynamics: bool = True,
    max_chars: int = 92,
    max_dur_sec: float = 8.5,
    forced_speaker: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Usa `word_timestamps` do Whisper large-v3 para:
    1. Quebrar falas muito longas (> 8.5s ou > 92 chars) em pausas naturais ou pontuação,
       deixando a legenda .SRT no padrão limpo de leitura do YouTube.
    2. Separar trocas rápidas de locutor (Anfitrião <-> Discord) dentro do mesmo segmento
       quando o microfone isolado está ativo.
    Não salva arrays `words` no SQLite para manter o banco leve.
    """
    full_text = str(getattr(seg_obj, "text", "") or "").strip()
    seg_start = float(getattr(seg_obj, "start", 0.0))
    seg_end = float(getattr(seg_obj, "end", seg_start))
    seg_dur = max(0.0, seg_end - seg_start)
    seg_metrics = {
        "no_speech_prob": getattr(seg_obj, "no_speech_prob", None),
        "avg_logprob": getattr(seg_obj, "avg_logprob", None),
        "compression_ratio": getattr(seg_obj, "compression_ratio", None),
    }
    if _is_whisper_hallucination(full_text, seg_dur, **seg_metrics):
        return []

    words = getattr(seg_obj, "words", None) or []
    has_iso = bool(dynamics.get("has_isolated_mic", True))

    def _eval_speaker(loc_s: float, loc_e: float) -> str:
        if forced_speaker is not None:
            return forced_speaker  # transcrição por faixa: o locutor é a própria faixa
        t_s = loc_s if use_local_dynamics else (loc_s + chunk_offset_sec)
        t_e = loc_e if use_local_dynamics else (loc_e + chunk_offset_sec)
        return HOST_SPEAKER_NAME if is_host_speaking(t_s, t_e, dynamics) else "Discord"

    # Se for curto e não tiver palavras suficientes para quebrar, retorna direto
    if len(words) < 3 or (seg_dur <= max_dur_sec and len(full_text) <= max_chars and not has_iso):
        return [{
            "start": round(seg_start + chunk_offset_sec, 2),
            "end": round(seg_end + chunk_offset_sec, 2),
            "text": full_text,
            "speaker": _eval_speaker(seg_start, seg_end)
        }]

    # Classificar cada palavra quando temos microfone isolado para detectar troca de locutor no meio da frase
    word_entries: List[Tuple[float, float, str, Optional[str]]] = []
    for w in words:
        wt = str(getattr(w, "word", "") or "")
        if not wt.strip():
            continue
        ws = float(getattr(w, "start", seg_start))
        we = float(getattr(w, "end", ws))
        w_spk = None
        if has_iso and (we - ws) >= 0.12:
            w_spk = _eval_speaker(ws, we)
        word_entries.append((ws, we, wt, w_spk))

    if len(word_entries) < 2:
        return [{
            "start": round(seg_start + chunk_offset_sec, 2),
            "end": round(seg_end + chunk_offset_sec, 2),
            "text": full_text,
            "speaker": _eval_speaker(seg_start, seg_end)
        }]

    # Se não houver troca de locutor e o segmento já for conciso, evita fragmentar sem necessidade
    spk_set = {e[3] for e in word_entries if e[3] is not None}
    has_speaker_switch = len(spk_set) > 1
    if not has_speaker_switch and seg_dur <= max_dur_sec and len(full_text) <= max_chars:
        return [{
            "start": round(seg_start + chunk_offset_sec, 2),
            "end": round(seg_end + chunk_offset_sec, 2),
            "text": full_text,
            "speaker": _eval_speaker(seg_start, seg_end)
        }]

    # Agrupar palavras em sub-segmentos naturais (pontuação, pausa >= 0.45s, limite de tamanho ou troca consistente de locutor)
    sub_groups: List[List[Tuple[float, float, str, Optional[str]]]] = []
    cur_group: List[Tuple[float, float, str, Optional[str]]] = []
    cur_chars = 0

    for idx_w, entry in enumerate(word_entries):
        ws, we, wt, w_spk = entry
        if cur_group:
            prev_ws, prev_we, prev_wt, prev_spk = cur_group[-1]
            gap = ws - prev_we
            group_dur = we - cur_group[0][0]
            group_word_count = len(cur_group)
            ends_sentence = prev_wt.strip().endswith((".", "?", "!"))
            ends_clause = prev_wt.strip().endswith((",", ";", ":", "..."))

            # Verificar troca consistente de locutor (evita flicker de 1 única palavra)
            speaker_flipped = False
            if has_speaker_switch and prev_spk and w_spk and prev_spk != w_spk and group_word_count >= 2:
                next_spk = word_entries[idx_w + 1][3] if (idx_w + 1) < len(word_entries) else w_spk
                if next_spk == w_spk or gap >= 0.20:
                    speaker_flipped = True

            should_split = False
            if speaker_flipped:
                should_split = True
            elif (cur_chars >= max_chars or group_dur >= max_dur_sec) and group_word_count >= 2:
                should_split = True
            elif ends_sentence and (cur_chars >= 42 or group_dur >= 4.0):
                should_split = True
            elif ends_clause and (cur_chars >= 64 or group_dur >= 6.0):
                should_split = True
            elif gap >= 0.55 and (cur_chars >= 32 or group_dur >= 3.0):
                should_split = True

            if should_split:
                sub_groups.append(cur_group)
                cur_group = []
                cur_chars = 0

        cur_group.append(entry)
        cur_chars += len(wt)

    if cur_group:
        sub_groups.append(cur_group)

    # Evitar sub-segmento final órfão de 1 única palavra curta (junta ao anterior se mesmo locutor)
    if len(sub_groups) >= 2 and len(sub_groups[-1]) == 1 and len(sub_groups[-1][0][2].strip()) <= 10:
        last_s = sub_groups[-1][0][0]
        last_e = sub_groups[-1][0][1]
        prev_s = sub_groups[-2][0][0]
        prev_e = sub_groups[-2][-1][1]
        if _eval_speaker(last_s, last_e) == _eval_speaker(prev_s, prev_e) and (last_e - prev_s) <= max_dur_sec + 2.0:
            sub_groups[-2].extend(sub_groups.pop())

    refined: List[Dict[str, Any]] = []
    for grp in sub_groups:
        g_start = grp[0][0]
        g_end = max(g_start + 0.05, grp[-1][1])
        g_text = "".join(item[2] for item in grp).strip()
        g_text = " ".join(g_text.split())
        if _is_whisper_hallucination(g_text, g_end - g_start, **seg_metrics):
            continue
        refined.append({
            "start": round(g_start + chunk_offset_sec, 2),
            "end": round(g_end + chunk_offset_sec, 2),
            "text": g_text,
            "speaker": _eval_speaker(g_start, g_end)
        })

    return refined
