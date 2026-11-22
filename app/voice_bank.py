import os
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="speechbrain")
warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain")
warnings.filterwarnings("ignore", category=UserWarning, module="torchaudio")

import gc
import re
import subprocess
import tempfile
import shutil
import time
import unicodedata
import numpy as np
import soundfile as sf
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any, Callable
from collections import Counter
from app.config import VOICE_BANK_DIR, MODELS_DIR, HOST_SPEAKER_NAME
from app.audio_pipeline import DISCORD_NORM_FILTER, get_audio_stream_count_and_duration

_classifier = None


def _normalize_name_key(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(name or "").strip())
    no_accents = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", no_accents).strip().lower()


WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def list_known_voice_names(exclude_host: bool = False) -> List[str]:
    """
    Retorna os nomes exatos das pastas já existentes em vozes_amigos/
    para manter consistência de grafia (ex: 'João Pedro - JP' em vez de 'Joao').
    """
    if not VOICE_BANK_DIR.exists():
        return []
    names = []
    host_low = HOST_SPEAKER_NAME.strip().lower()
    for d in sorted(VOICE_BANK_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir():
            continue
        if exclude_host and d.name.strip().lower() == host_low:
            continue
        names.append(d.name)
    return names


def resolve_canonical_friend_name(raw_name: str) -> str:
    """
    Resolve o nome do amigo preservando EXATAMENTE a grafia da pasta já existente
    em vozes_amigos/ (comparação sem diferenciar maiúsculas/acentos).
    Bloqueia path traversal (., ..), caracteres inválidos e nomes reservados do Windows.
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", str(raw_name or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(". ")
    if not cleaned:
        return ""

    base_stem = cleaned.split(".")[0].strip().lower()
    if base_stem in WINDOWS_RESERVED_NAMES:
        return ""

    target_key = _normalize_name_key(cleaned)
    for existing in list_known_voice_names(exclude_host=False):
        if _normalize_name_key(existing) == target_key:
            return existing

    # Se o usuário digitou tudo em minúsculas, capitaliza; caso contrário preserva grafia digitada
    if cleaned == cleaned.lower():
        cleaned = cleaned.title()

    # Validação final contra path traversal
    try:
        base_resolved = VOICE_BANK_DIR.resolve()
        cand_resolved = (VOICE_BANK_DIR / cleaned).resolve()
        if cand_resolved.parent != base_resolved:
            return ""
    except Exception:
        return ""

    return cleaned


def extract_speaker_preview_wav(
    mkv_path: Path,
    start_sec: float,
    end_sec: float,
    out_wav: Path,
    is_host: bool = False
) -> bool:
    """
    Extrai um trecho curto (3s a 12s) direto do MKV com seek instantâneo do FFmpeg
    para o usuário ouvir a voz no navegador antes de confirmar/salvar no Banco de Vozes.
    """
    if not mkv_path.exists():
        return False
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    stream_count, _ = get_audio_stream_count_and_duration(mkv_path)
    if is_host:
        map_spec = "0:a:1" if stream_count >= 2 else "0:a:0"
    else:
        map_spec = "0:a:2" if stream_count >= 3 else "0:a:0"

    s = max(0.0, float(start_sec) - 0.35)
    raw_dur = max(2.5, float(end_sec) - float(start_sec) + 0.70)
    dur = min(12.0, raw_dur)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{s:.2f}",
        "-t", f"{dur:.2f}",
        "-i", str(mkv_path),
        "-map", map_spec,
        "-vn",
    ]
    if not is_host:
        cmd.extend(["-af", DISCORD_NORM_FILTER])
    cmd.extend(["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)])

    res = subprocess.run(cmd, capture_output=True)
    return res.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 1000


def extract_highlight_preview_wav(
    mkv_path: Path,
    start_sec: float,
    end_sec: float,
    out_wav: Path,
    max_duration: float = 30.0
) -> bool:
    """
    Extrai uma prévia de áudio (até 30s) de um Melhor Momento direto do MKV para o usuário
    ouvir no painel antes de exportar o corte 4K Lossless.
    """
    if not mkv_path.exists():
        return False
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    stream_count, _ = get_audio_stream_count_and_duration(mkv_path)

    s = max(0.0, float(start_sec))
    dur = max(2.0, min(float(max_duration), float(end_sec) - float(start_sec)))

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{s:.2f}",
        "-t", f"{dur:.2f}",
        "-i", str(mkv_path),
    ]
    if stream_count >= 3:
        cmd.extend([
            "-filter_complex",
            f"[0:a:0]volume=1.0[a1];[0:a:2]{DISCORD_NORM_FILTER}[a3];[a1][a3]amix=inputs=2:duration=longest:normalize=0:weights=1.0 0.9,alimiter=limit=0.92[aout]",
            "-map", "[aout]",
        ])
    else:
        cmd.extend(["-map", "0:a:0", "-af", "alimiter=limit=0.92"])

    cmd.extend(["-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(out_wav)])
    res = subprocess.run(cmd, capture_output=True)
    return res.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 1000


def _get_device_str() -> str:
    import torch
    return "cuda:0" if torch.cuda.is_available() else "cpu"


def get_classifier():
    global _classifier
    if _classifier is None:
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy
        model_dir = MODELS_DIR / "spkrec"
        model_dir.mkdir(parents=True, exist_ok=True)
        device_str = _get_device_str()
        print(f"[VOZES] Carregando modelo SpeechBrain ECAPA-TDNN ({device_str})...", flush=True)

        local_files_exist = (model_dir / "hyperparams.yaml").exists() and (model_dir / "embedding_model.ckpt").exists()
        source_target = str(model_dir) if local_files_exist else "speechbrain/spkrec-ecapa-voxceleb"

        try:
            _classifier = EncoderClassifier.from_hparams(
                source=source_target,
                savedir=str(model_dir),
                run_opts={"device": device_str},
                local_strategy=LocalStrategy.COPY
            )
            print(f"[VOZES] Modelo SpeechBrain pronto em {device_str}!", flush=True)
        except Exception as e:
            print(f"[VOZES] Aviso ao carregar modelo local ({e}). Tentando fallback online...", flush=True)
            _classifier = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=str(model_dir),
                run_opts={"device": device_str},
                local_strategy=LocalStrategy.COPY
            )
    return _classifier


def load_known_voice_profiles(exclude_host: bool = False) -> Dict[str, np.ndarray]:
    profiles = {}
    if not VOICE_BANK_DIR.exists():
        return profiles
    host_low = HOST_SPEAKER_NAME.strip().lower()
    for friend_dir in VOICE_BANK_DIR.iterdir():
        if friend_dir.is_dir():
            if exclude_host and friend_dir.name.strip().lower() == host_low:
                continue
            emb_file = friend_dir / "embedding.npy"
            if emb_file.exists():
                try:
                    vec = np.load(str(emb_file))
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        profiles[friend_dir.name] = vec / norm
                except Exception as e:
                    print(f"[VOZES] Erro ao carregar perfil de {friend_dir.name}: {e}", flush=True)
    return profiles


def _select_best_voice_window(audio_data: np.ndarray, sample_rate: int = 16000, max_sec: float = 8.0) -> np.ndarray:
    """
    Para falas muito longas (> 8s), seleciona a janela contígua de 8 segundos com maior
    energia vocal (RMS), reduzindo tempo/VRAM sem perder precisão do ECAPA-TDNN.
    """
    max_samples = int(sample_rate * max_sec)
    if len(audio_data) <= max_samples:
        return audio_data
    step = max(1, sample_rate)  # passo de 1s
    best_start = 0
    best_energy = -1.0
    for s in range(0, len(audio_data) - max_samples + 1, step):
        win = audio_data[s:s + max_samples]
        energy = float(np.mean(win * win))
        if energy > best_energy:
            best_energy = energy
            best_start = s
    return audio_data[best_start:best_start + max_samples]


def extract_embedding_for_segment(classifier, audio_data: np.ndarray, sample_rate: int = 16000) -> Optional[np.ndarray]:
    if len(audio_data) < sample_rate * 1.0:  # Mínimo 1 segundo
        return None
    trimmed = _select_best_voice_window(audio_data, sample_rate, max_sec=8.0)
    try:
        import torch
        device = next(classifier.parameters()).device
        with torch.inference_mode():
            tensor = torch.tensor(trimmed, dtype=torch.float32, device=device).unsqueeze(0)
            emb = classifier.encode_batch(tensor)
            vec = emb.squeeze().cpu().numpy()
            norm = np.linalg.norm(vec)
            if norm > 0:
                return vec / norm
    except Exception as e:
        print(f"[VOZES] Erro ao extrair embedding: {e}", flush=True)
    return None


def extract_embeddings_batch(
    classifier,
    items: List[Tuple[int, np.ndarray]],
    sample_rate: int = 16000,
    batch_size: int = 12
) -> Dict[int, np.ndarray]:
    """
    Extrai embeddings em mini-lotes na GPU usando wav_lens relativos do SpeechBrain
    (2x-3x mais rápido na RTX 4060). Em caso de falha num lote, faz fallback seguro 1 a 1.
    """
    if not items:
        return {}
    import torch
    device = next(classifier.parameters()).device
    results: Dict[int, np.ndarray] = {}

    # Ordenar por tamanho dentro do conjunto para minimizar padding entre itens do mesmo lote
    prepared: List[Tuple[int, np.ndarray]] = []
    for idx, arr in items:
        if len(arr) >= sample_rate * 1.0:
            prepared.append((idx, _select_best_voice_window(arr, sample_rate, max_sec=8.0)))
    prepared.sort(key=lambda x: len(x[1]))

    for b_start in range(0, len(prepared), batch_size):
        batch = prepared[b_start:b_start + batch_size]
        if len(batch) == 1:
            idx0, arr0 = batch[0]
            emb0 = extract_embedding_for_segment(classifier, arr0, sample_rate)
            if emb0 is not None:
                results[idx0] = emb0
            continue

        max_len = max(len(arr) for _, arr in batch)
        padded = np.zeros((len(batch), max_len), dtype=np.float32)
        rel_lens = np.zeros(len(batch), dtype=np.float32)
        for i, (_, arr) in enumerate(batch):
            L = len(arr)
            padded[i, :L] = arr
            rel_lens[i] = float(L) / float(max_len)

        try:
            with torch.inference_mode():
                wavs_t = torch.from_numpy(padded).to(device)
                lens_t = torch.from_numpy(rel_lens).to(device)
                embs = classifier.encode_batch(wavs_t, lens_t)
                embs_np = embs.squeeze(1).cpu().numpy()
                if embs_np.ndim == 1:
                    embs_np = embs_np.reshape(1, -1)
                for i, (seg_idx, _) in enumerate(batch):
                    vec = embs_np[i]
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        results[seg_idx] = vec / norm
        except Exception:
            # Fallback seguro item a item
            for seg_idx, arr in batch:
                emb = extract_embedding_for_segment(classifier, arr, sample_rate)
                if emb is not None:
                    results[seg_idx] = emb

    return results


def cluster_and_identify_speakers(
    discord_wav_path: Path,
    segments: List[Dict],
    known_profiles: Dict[str, np.ndarray],
    progress_callback: Optional[Callable[[str, float], None]] = None,
    chunk_timeline: Optional[List[Tuple[float, float, Path]]] = None,
    hype_scores: Optional[List[Dict[str, Any]]] = None
) -> Tuple[List[Dict], Dict[str, Dict[str, Any]]]:
    """
    segments: lista de dicts com 'start', 'end', 'text', etc.
    Suporta tanto um único arquivo WAV quanto blocos segmentados em SSD (chunk_timeline / diretório),
    consumindo apenas ~20-40 MB de RAM mesmo em gravações de 10 horas.
    """
    classifier = get_classifier()
    is_segmented = bool(chunk_timeline) or discord_wav_path.is_dir()

    audio_data = None
    sr = 16000
    timeline: List[Tuple[float, float, Path]] = []
    chunk_cache: Dict[int, np.ndarray] = {}

    if is_segmented:
        if chunk_timeline:
            timeline = chunk_timeline
        else:
            offset = 0.0
            for p in sorted(discord_wav_path.glob("disc_*.wav")):
                info = sf.info(str(p))
                sr = info.samplerate
                dur = info.frames / float(sr)
                timeline.append((offset, offset + dur, p))
                offset += dur
        print(f"[VOZES] Modo segmentado (SSD): lendo {len(timeline)} bloco(s) sob demanda (~20 MB RAM)...", flush=True)
    else:
        print(f"[VOZES] Carregando áudio do Discord: {discord_wav_path.name}...", flush=True)
        audio_data, sr = sf.read(str(discord_wav_path), dtype="float32")

    def _get_block(b_idx: int) -> np.ndarray:
        if b_idx not in chunk_cache:
            # Mantém no máximo 2 blocos em RAM (~38 MB) para tratar falas que cruzam fronteiras
            if len(chunk_cache) >= 2:
                oldest = next(iter(chunk_cache))
                del chunk_cache[oldest]
            arr, _ = sf.read(str(timeline[b_idx][2]), dtype="float32")
            if arr.ndim > 1:
                arr = arr.mean(axis=1)
            chunk_cache[b_idx] = arr
        return chunk_cache[b_idx]

    def _slice_audio(start_sec: float, end_sec: float) -> np.ndarray:
        if not is_segmented:
            s_sample = max(0, int(start_sec * sr))
            e_sample = min(len(audio_data), int(end_sec * sr))
            return audio_data[s_sample:e_sample]

        parts = []
        for b_idx, (b_start, b_end, _) in enumerate(timeline):
            if b_end <= start_sec:
                continue
            if b_start >= end_sec:
                break
            arr = _get_block(b_idx)
            loc_s = max(0, int((max(start_sec, b_start) - b_start) * sr))
            loc_e = min(len(arr), int((min(end_sec, b_end) - b_start) * sr))
            if loc_e > loc_s:
                parts.append(arr[loc_s:loc_e])
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    host_low = HOST_SPEAKER_NAME.strip().lower()
    discord_indices = [
        idx for idx, s in enumerate(segments)
        if str(s.get("speaker") or "").strip().lower() != host_low
    ]
    total_discord = len(discord_indices)
    print(f"[VOZES] Analisando timbres vocais em {total_discord} falas do Discord (GPU mini-batch, janela ótima 8s)...", flush=True)

    extracted_embs: Dict[int, np.ndarray] = {}
    last_log_t = time.time()
    t_start = time.time()

    # Coletar em grupos de até 48 segmentos por vez para manter RAM baixa e maximizar batching na GPU
    pending_items: List[Tuple[int, np.ndarray]] = []
    processed_count = 0

    def _flush_pending(force_log: bool = False):
        nonlocal last_log_t
        if pending_items:
            batch_res = extract_embeddings_batch(classifier, pending_items, sample_rate=sr, batch_size=12)
            extracted_embs.update(batch_res)
            pending_items.clear()
        now_t = time.time()
        if force_log or (now_t - last_log_t) >= 2.5:
            last_log_t = now_t
            pct = processed_count / max(1, total_discord)
            print(f"[VOZES] Processando timbres do Discord: {processed_count}/{total_discord} ({pct*100:.0f}%) | {len(extracted_embs)} embeddings...", flush=True)
            if progress_callback:
                current_pct = 0.80 + (pct * 0.06)
                progress_callback(f"Identificando timbres dos amigos no Discord [{processed_count}/{total_discord}]...", current_pct)

    for idx in discord_indices:
        processed_count += 1
        seg = segments[idx]
        dur = seg["end"] - seg["start"]
        if dur >= 1.0:
            chunk = _slice_audio(seg["start"], seg["end"])
            if len(chunk) >= sr * 1.0:
                pending_items.append((idx, chunk))
        if len(pending_items) >= 48:
            _flush_pending(force_log=False)

    _flush_pending(force_log=True)

    chunk_cache.clear()
    if audio_data is not None:
        del audio_data
    gc.collect()

    elapsed_extract = time.time() - t_start
    print(f"[VOZES] Extração concluída: {len(extracted_embs)} amostras de voz em {elapsed_extract:.1f}s.", flush=True)

    # 1. Comparar primeiro com perfis já conhecidos em vozes_amigos/
    unmatched_indices = []
    unmatched_embs = []
    matched_by_name: Dict[str, List[int]] = {}

    for idx in discord_indices:
        if idx in extracted_embs:
            emb = extracted_embs[idx]
            best_match = None
            best_sim = 0.0
            for name, ref_vec in known_profiles.items():
                sim = float(np.dot(emb, ref_vec))
                if sim > best_sim:
                    best_sim = sim
                    best_match = name

            if best_sim >= 0.65 and best_match:
                segments[idx]["speaker"] = best_match
                segments[idx]["cluster_tag"] = best_match
                segments[idx]["speaker_confidence"] = round(best_sim, 2)
                matched_by_name.setdefault(best_match, []).append(idx)
            else:
                unmatched_indices.append(idx)
                unmatched_embs.append(emb)
        else:
            segments[idx]["speaker"] = "Discord"

    # 2. Agrupamento por timbre das falas ainda não identificadas
    speaker_samples: Dict[str, Dict[str, Any]] = {}

    # Incluir amigos já reconhecidos automaticamente para permitir reforçar amostras ou corrigir no painel
    for known_name, idx_list in sorted(matched_by_name.items(), key=lambda kv: len(kv[1]), reverse=True):
        k_segs = [segments[i] for i in idx_list]
        best_seg = max(k_segs, key=lambda s: s["end"] - s["start"])
        avg_conf = round(sum(float(s.get("speaker_confidence", 0.7)) for s in k_segs) / max(1, len(k_segs)), 2)
        speaker_samples[known_name] = {
            "start": best_seg["start"],
            "end": best_seg["end"],
            "duration": round(best_seg["end"] - best_seg["start"], 2),
            "text": best_seg["text"],
            "known": True,
            "avg_confidence": avg_conf,
            "segment_count": len(k_segs)
        }

    # Filtrar trechos mais estáveis (>= 1.5s) para determinar os centróides dos amigos reais,
    # excluindo janelas com Soundpad ativo ou Caos de vozes (Anfitrião + Discord falando juntos)
    noisy_windows = set()
    if hype_scores:
        for h in hype_scores:
            if h.get("soundpad") or h.get("is_chaos"):
                noisy_windows.add(int(float(h.get("start", 0.0)) // 5))

    def _overlaps_noisy_window(seg: Dict[str, Any]) -> bool:
        if not noisy_windows:
            return False
        w_s = int(float(seg["start"]) // 5)
        w_e = int(float(seg["end"]) // 5)
        return any(w in noisy_windows for w in range(w_s, w_e + 1))

    all_stable_pairs = [
        (idx, emb) for idx, emb in zip(unmatched_indices, unmatched_embs)
        if (segments[idx]["end"] - segments[idx]["start"]) >= 1.5
    ]
    clean_stable_pairs = [
        pair for pair in all_stable_pairs
        if not _overlaps_noisy_window(segments[pair[0]])
    ]
    if len(clean_stable_pairs) >= 8 or (all_stable_pairs and len(clean_stable_pairs) >= len(all_stable_pairs) * 0.5):
        filtered_out = len(all_stable_pairs) - len(clean_stable_pairs)
        if filtered_out > 0:
            print(
                f"[VOZES] Filtro acústico: {filtered_out} trecho(s) com Soundpad/sobreposição removidos "
                f"do cálculo de centróides ({len(clean_stable_pairs)} trechos limpos mantidos).",
                flush=True
            )
        stable_pairs = clean_stable_pairs
    else:
        stable_pairs = all_stable_pairs

    if len(stable_pairs) >= 2:
        try:
            from sklearn.cluster import AgglomerativeClustering
            stable_vecs = [p[1] for p in stable_pairs]

            clustering = AgglomerativeClustering(
                n_clusters=None,
                metric="cosine",
                linkage="average",
                distance_threshold=0.68
            )
            raw_labels = clustering.fit_predict(stable_vecs)
            counts = Counter(raw_labels)

            # Em gravações curtas, aceita clusters menores; em longas, exige >= 3 trechos estáveis
            min_cluster_segs = 3 if len(stable_pairs) >= 30 else 1
            top_clusters = [c for c, cnt in counts.most_common(8) if cnt >= min_cluster_segs]
            if not top_clusters:
                top_clusters = [counts.most_common(1)[0][0]]

            # Calcular centróide normalizado de cada locutor
            centroids = {}
            for c in top_clusters:
                c_embs = [stable_vecs[i] for i, l in enumerate(raw_labels) if l == c]
                mean_vec = np.mean(c_embs, axis=0)
                norm = np.linalg.norm(mean_vec)
                if norm > 0:
                    centroids[c] = mean_vec / norm

            # Atribuir rótulos ordenados por quantidade de falas: Voz_1, Voz_2, etc.
            ordered_clusters = [c for c in top_clusters if c in centroids]
            tag_map = {c: f"Voz_{i+1}" for i, c in enumerate(ordered_clusters)}
            centroid_tags = {tag_map[c]: centroids[c] for c in ordered_clusters}

            # Classificar todas as falas do Discord não conhecidas pelo centróide mais próximo
            for idx in unmatched_indices:
                if idx in extracted_embs:
                    vec = extracted_embs[idx]
                    best_tag = None
                    best_sim = 0.0
                    for tag, c_vec in centroid_tags.items():
                        sim = float(np.dot(vec, c_vec))
                        if sim > best_sim:
                            best_sim = sim
                            best_tag = tag

                    if best_sim >= 0.45 and best_tag:
                        segments[idx]["speaker"] = best_tag
                        segments[idx]["cluster_tag"] = best_tag
                        segments[idx]["speaker_confidence"] = round(best_sim, 2)
                    else:
                        segments[idx]["speaker"] = "Discord"
                else:
                    segments[idx]["speaker"] = "Discord"

            # Coletar a melhor/mais longa amostra de áudio de cada voz para o painel web
            for tag in centroid_tags.keys():
                tag_segs = [segments[idx] for idx in unmatched_indices if segments[idx].get("cluster_tag") == tag]
                if tag_segs:
                    best_seg = max(tag_segs, key=lambda s: s["end"] - s["start"])
                    speaker_samples[tag] = {
                        "start": best_seg["start"],
                        "end": best_seg["end"],
                        "duration": round(best_seg["end"] - best_seg["start"], 2),
                        "text": best_seg["text"],
                        "known": False,
                        "segment_count": len(tag_segs)
                    }

        except Exception as e:
            print(f"[VOZES] Aviso no agrupamento: {e}. Mantendo como 'Discord'.", flush=True)
            for idx in unmatched_indices:
                segments[idx]["speaker"] = "Discord"

    elif len(unmatched_indices) > 0:
        for idx in unmatched_indices:
            segments[idx]["speaker"] = "Voz_1"
            segments[idx]["cluster_tag"] = "Voz_1"
        best_seg = max([segments[idx] for idx in unmatched_indices], key=lambda s: s["end"] - s["start"])
        speaker_samples["Voz_1"] = {
            "start": best_seg["start"],
            "end": best_seg["end"],
            "duration": round(best_seg["end"] - best_seg["start"], 2),
            "text": best_seg["text"],
            "known": False,
            "segment_count": len(unmatched_indices)
        }

    detected_tags = list(speaker_samples.keys())
    print(f"[VOZES] Agrupamento finalizado: {len(detected_tags)} vozes detectadas no Discord ({', '.join(detected_tags) or 'Nenhuma'}).", flush=True)

    return segments, speaker_samples


def _select_candidate_segments(
    segments: List[Dict],
    speaker_tag: str,
    friend_name: str,
    max_samples: int = 3
) -> List[Dict]:
    clean_name = resolve_canonical_friend_name(friend_name) or friend_name.strip()
    match_targets = {
        speaker_tag.strip().lower(),
        friend_name.strip().lower(),
        clean_name.lower()
    }

    def matches_speaker(seg: Dict) -> bool:
        c_tag = str(seg.get("cluster_tag", "")).strip().lower()
        spk = str(seg.get("speaker", "")).strip().lower()
        return (c_tag in match_targets) or (spk in match_targets)

    # Prioriza trechos >= 3.0s; se houver poucos, faz fallback para >= 1.5s
    long_segs = [s for s in segments if matches_speaker(s) and (s["end"] - s["start"]) >= 3.0]
    if len(long_segs) >= max_samples:
        candidate_segs = long_segs
    else:
        candidate_segs = [s for s in segments if matches_speaker(s) and (s["end"] - s["start"]) >= 1.5]

    candidate_segs.sort(key=lambda s: s["end"] - s["start"], reverse=True)
    return candidate_segs[:max_samples]


def _save_embeddings_to_bank(friend_dir: Path, clean_name: str, collected_embs: List[np.ndarray], saved_files: List[str]):
    if not collected_embs:
        return

    emb_file = friend_dir / "embedding.npy"
    meta_file = friend_dir / "profile_meta.json"

    old_vec = None
    old_count = 0
    if emb_file.exists():
        try:
            loaded = np.load(str(emb_file))
            old_norm = np.linalg.norm(loaded)
            if old_norm > 0:
                old_vec = loaded / old_norm
        except Exception:
            old_vec = None

        # Determinar quantidade real de amostras anteriores (via profile_meta.json ou contagem de .wav antigos)
        if meta_file.exists():
            try:
                import json
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                old_count = int(meta.get("sample_count", 0))
            except Exception:
                old_count = 0
        if old_count <= 0:
            saved_set = set(saved_files)
            existing_wavs = [p for p in friend_dir.glob("*.wav") if p.name not in saved_set]
            old_count = max(1, len(existing_wavs))

    # Se o perfil já tem >= 3 amostras consolidadas, filtrar outliers extremos (ex: risada muito distorcida)
    filtered_embs = list(collected_embs)
    if old_vec is not None and old_count >= 3:
        inliers = [e for e in collected_embs if float(np.dot(e, old_vec)) >= 0.35]
        if inliers:
            if len(inliers) < len(collected_embs):
                print(
                    f"[VOZES] Filtro de consistência para '{clean_name}': "
                    f"{len(collected_embs) - len(inliers)} amostra(s) atípica(s) descartada(s) do centróide.",
                    flush=True
                )
            filtered_embs = inliers

    new_count = len(filtered_embs)
    new_mean = np.mean(filtered_embs, axis=0)
    new_norm = np.linalg.norm(new_mean)
    if new_norm <= 0:
        return
    new_mean = new_mean / new_norm

    if old_vec is not None and old_count > 0:
        # Média ponderada real pelo número de amostras (limita peso máximo do histórico a 40 para manter adaptabilidade)
        eff_old = min(old_count, 40)
        combined = (old_vec * float(eff_old)) + (new_mean * float(new_count))
        comb_norm = np.linalg.norm(combined)
        final_vec = (combined / comb_norm) if comb_norm > 0 else new_mean
        total_count = old_count + new_count
    else:
        final_vec = new_mean
        total_count = new_count

    np.save(str(emb_file), final_vec)
    try:
        import json
        meta_file.write_text(
            json.dumps({"friend": clean_name, "sample_count": total_count, "updated_at": int(time.time())}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass
    print(
        f"[VOZES] Banco de Vozes atualizado para '{clean_name}': "
        f"+{len(saved_files)} amostra(s) salva(s) (total ponderado: {total_count}).",
        flush=True
    )


def harvest_friend_voice_samples(
    discord_wav_path: Path,
    segments: List[Dict],
    speaker_tag: str,
    friend_name: str,
    max_samples: int = 3
) -> Dict[str, Any]:
    """
    Recorta e salva trechos limpos em .wav a partir do track3_discord.wav já extraído
    e gera/atualiza o embedding.npy do amigo!
    """
    clean_name = resolve_canonical_friend_name(friend_name)
    if not clean_name:
        return {"friend": "", "samples_saved": [], "profile_path": ""}
    friend_dir = VOICE_BANK_DIR / clean_name
    friend_dir.mkdir(parents=True, exist_ok=True)

    selected = _select_candidate_segments(segments, speaker_tag, friend_name, max_samples=max_samples)
    if not selected:
        return {
            "friend": clean_name,
            "samples_saved": [],
            "profile_path": str(friend_dir / "embedding.npy")
        }

    classifier = get_classifier()
    audio_data, sr = sf.read(str(discord_wav_path), dtype="float32")

    saved_files = []
    collected_embs = []
    batch_ts = int(time.time())

    for seg in selected:
        s_sample = max(0, int(seg["start"] * sr))
        e_sample = min(len(audio_data), int(seg["end"] * sr))
        chunk = audio_data[s_sample:e_sample]
        if len(chunk) < sr * 1.0:
            continue

        ts_tag = int(seg["start"])
        out_wav = friend_dir / f"amostra_{batch_ts}_{ts_tag}s.wav"
        sf.write(str(out_wav), chunk, sr)
        saved_files.append(str(out_wav.name))

        emb = extract_embedding_for_segment(classifier, chunk, sr)
        if emb is not None:
            collected_embs.append(emb)

    _save_embeddings_to_bank(friend_dir, clean_name, collected_embs, saved_files)

    return {
        "friend": clean_name,
        "samples_saved": saved_files,
        "profile_path": str(friend_dir / "embedding.npy")
    }


def harvest_friend_voice_samples_from_mkv(
    mkv_path: Path,
    segments: List[Dict],
    speaker_tag: str,
    friend_name: str,
    max_samples: int = 3
) -> Dict[str, Any]:
    """
    Versão ultrarrápida para confirmação manual na interface web:
    faz seek direto com FFmpeg apenas nos 3 trechos escolhidos (~0.5s total),
    em vez de extrair horas de áudio de um MKV de 100+ GB.
    """
    clean_name = resolve_canonical_friend_name(friend_name)
    if not clean_name:
        return {"friend": "", "samples_saved": [], "profile_path": ""}
    friend_dir = VOICE_BANK_DIR / clean_name
    friend_dir.mkdir(parents=True, exist_ok=True)

    selected = _select_candidate_segments(segments, speaker_tag, friend_name, max_samples=max_samples)
    if not selected:
        return {
            "friend": clean_name,
            "samples_saved": [],
            "profile_path": str(friend_dir / "embedding.npy")
        }

    stream_count, _ = get_audio_stream_count_and_duration(mkv_path)
    map_spec = "0:a:2" if stream_count >= 3 else "0:a:0"

    classifier = get_classifier()
    saved_files = []
    collected_embs = []
    batch_ts = int(time.time())
    tmp_dir = Path(tempfile.mkdtemp(prefix="banido_harvest_"))

    try:
        for idx, seg in enumerate(selected):
            dur = max(1.0, float(seg["end"]) - float(seg["start"]))
            ts_tag = int(seg["start"])
            out_wav = friend_dir / f"amostra_{batch_ts}_{ts_tag}s.wav"
            tmp_wav = tmp_dir / f"clip_{idx}.wav"

            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{float(seg['start']):.2f}",
                "-t", f"{dur:.2f}",
                "-i", str(mkv_path),
                "-map", map_spec,
                "-vn", "-af", DISCORD_NORM_FILTER,
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                str(tmp_wav)
            ]
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode != 0 or not tmp_wav.exists():
                continue

            chunk, sr = sf.read(str(tmp_wav), dtype="float32")
            if len(chunk) < sr * 1.0:
                continue

            shutil.copyfile(str(tmp_wav), str(out_wav))
            saved_files.append(str(out_wav.name))

            emb = extract_embedding_for_segment(classifier, chunk, sr)
            if emb is not None:
                collected_embs.append(emb)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    _save_embeddings_to_bank(friend_dir, clean_name, collected_embs, saved_files)

    return {
        "friend": clean_name,
        "samples_saved": saved_files,
        "profile_path": str(friend_dir / "embedding.npy")
    }


def harvest_host_voice_samples(
    mic_wav_path: Path,
    segments: List[Dict],
    max_samples: int = 3
) -> Dict[str, Any]:
    """
    Salva amostras limpas da voz do anfitrião a partir da Faixa 2 (microfone isolado)
    em vozes_amigos/<Host>/ e atualiza o embedding.npy para uso futuro.
    """
    return harvest_friend_voice_samples(
        discord_wav_path=mic_wav_path,
        segments=segments,
        speaker_tag=HOST_SPEAKER_NAME,
        friend_name=HOST_SPEAKER_NAME,
        max_samples=max_samples
    )


def harvest_host_voice_samples_from_mkv(
    mkv_path: Path,
    segments: List[Dict],
    max_samples: int = 3
) -> Dict[str, Any]:
    """
    Versão ultrarrápida para salvar amostras da voz do anfitrião direto da Faixa 2 do MKV (0:a:1).
    """
    clean_name = resolve_canonical_friend_name(HOST_SPEAKER_NAME) or HOST_SPEAKER_NAME
    friend_dir = VOICE_BANK_DIR / clean_name
    friend_dir.mkdir(parents=True, exist_ok=True)

    selected = _select_candidate_segments(segments, HOST_SPEAKER_NAME, clean_name, max_samples=max_samples)
    if not selected:
        return {
            "friend": clean_name,
            "samples_saved": [],
            "profile_path": str(friend_dir / "embedding.npy")
        }

    stream_count, _ = get_audio_stream_count_and_duration(mkv_path)
    map_spec = "0:a:1" if stream_count >= 2 else "0:a:0"

    classifier = get_classifier()
    saved_files = []
    collected_embs = []
    batch_ts = int(time.time())
    tmp_dir = Path(tempfile.mkdtemp(prefix="banido_harvest_host_"))

    try:
        for idx, seg in enumerate(selected):
            dur = max(1.0, float(seg["end"]) - float(seg["start"]))
            ts_tag = int(seg["start"])
            out_wav = friend_dir / f"amostra_{batch_ts}_{ts_tag}s.wav"
            tmp_wav = tmp_dir / f"clip_{idx}.wav"

            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{float(seg['start']):.2f}",
                "-t", f"{dur:.2f}",
                "-i", str(mkv_path),
                "-map", map_spec,
                "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                str(tmp_wav)
            ]
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode != 0 or not tmp_wav.exists():
                continue

            chunk, sr = sf.read(str(tmp_wav), dtype="float32")
            if len(chunk) < sr * 1.0:
                continue

            shutil.copyfile(str(tmp_wav), str(out_wav))
            saved_files.append(str(out_wav.name))

            emb = extract_embedding_for_segment(classifier, chunk, sr)
            if emb is not None:
                collected_embs.append(emb)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    _save_embeddings_to_bank(friend_dir, clean_name, collected_embs, saved_files)

    return {
        "friend": clean_name,
        "samples_saved": saved_files,
        "profile_path": str(friend_dir / "embedding.npy")
    }


def _cli_main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Subprocesso isolado de coleta de amostras vocais (SpeechBrain)")
    parser.add_argument("--mode", choices=["friend", "host"], required=True)
    parser.add_argument("--mkv", required=True)
    parser.add_argument("--segments-json", required=True)
    parser.add_argument("--speaker-tag", default="")
    parser.add_argument("--friend-name", default="")
    parser.add_argument("--max-samples", type=int, default=3)
    args = parser.parse_args()

    segments = json.loads(Path(args.segments_json).read_text(encoding="utf-8"))
    if args.mode == "host":
        res = harvest_host_voice_samples_from_mkv(
            Path(args.mkv), segments, max_samples=args.max_samples
        )
    else:
        res = harvest_friend_voice_samples_from_mkv(
            Path(args.mkv), segments, args.speaker_tag, args.friend_name, max_samples=args.max_samples
        )
    print("BANIDO_HARVEST_JSON:" + json.dumps(res, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    _cli_main()
