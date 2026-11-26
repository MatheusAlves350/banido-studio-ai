"""Pipeline de transcrição: processa cada bloco e junta o resultado."""
import os
import gc
import json
import time
import numpy as np
import soundfile as sf
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional, Tuple
from app.config import (
    HOST_SPEAKER_NAME, WHISPER_MODEL_SIZE,
    WHISPER_LANGUAGE, SEPARATE_TRACK_TRANSCRIPTION
)
from app.audio_pipeline import (
    extract_audio_tracks, analyze_audio_dynamics, compute_rms_100ms,
    build_balanced_mix_array,
    SegmentedAudioExtractor, analyze_segmented_hype
)
from app.voice_bank import load_known_voice_profiles, cluster_and_identify_speakers
from app.transcription.audio_tracks import (
    _mic_segment_is_real,
    _track_has_speech,
    create_balanced_mix_audio,
    merge_track_segments,
)
from app.transcription.chunking import (
    _find_chunk_boundaries,
    _find_tail_silence_split,
    _refine_whisper_segment,
)
from app.transcription.hallucination import (
    WHISPER_HALLUCINATION_SILENCE_THRESHOLD,
)
from app.transcription.prompt import (
    build_whisper_initial_prompt,
)
from app.transcription.runtime import (
    _log_memory_status,
    format_hhmmss,
    get_whisper_model,
    release_whisper_model,
)
from app.transcription.srt import (
    export_srt_file,
)


def _transcribe_block(
    model: Any,
    mix_chunk: np.ndarray,
    mic_work: Optional[np.ndarray],
    disc_work: np.ndarray,
    eff_offset_sec: float,
    block_dynamics: Dict[str, Any],
    initial_prompt: str,
    separate_tracks: Optional[bool] = None,
    track_status_cb: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Transcreve um bloco. Com microfone isolado (e separate_tracks ativo) roda o Whisper
    uma vez por faixa (Anfitrião e Discord), preservando falas simultâneas; sem mic isolado
    (1 faixa ou 2 faixas duplicadas) mantém o mix único com classificação acústica.
    """
    def _run(audio: np.ndarray, forced: Optional[str]) -> List[Dict[str, Any]]:
        gen, _ = model.transcribe(
            audio,
            language=WHISPER_LANGUAGE,
            beam_size=5,
            best_of=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=250),
            word_timestamps=True,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            hallucination_silence_threshold=WHISPER_HALLUCINATION_SILENCE_THRESHOLD,
            initial_prompt=initial_prompt
        )
        out: List[Dict[str, Any]] = []
        for s in gen:
            out.extend(_refine_whisper_segment(
                s, eff_offset_sec, block_dynamics, use_local_dynamics=True, forced_speaker=forced
            ))
        return out

    use_separate = SEPARATE_TRACK_TRANSCRIPTION if separate_tracks is None else bool(separate_tracks)
    separate = (
        use_separate
        and bool(block_dynamics.get("has_isolated_mic"))
        and mic_work is not None
        and len(mic_work) > 0
    )
    if not separate:
        return _run(mix_chunk, None)

    m_rms = block_dynamics["mic_rms"]
    d_rms = block_dynamics["discord_rms"]
    host_segs: List[Dict[str, Any]] = []
    discord_segs: List[Dict[str, Any]] = []
    ts_block = format_hhmmss(eff_offset_sec)
    if _track_has_speech(m_rms):
        print(f"[WHISPER] [{ts_block}] Transcrevendo faixa isolada ({HOST_SPEAKER_NAME})...", flush=True)
        if track_status_cb:
            track_status_cb(f"faixa {HOST_SPEAKER_NAME}")
        host_segs = [
            seg for seg in _run(mic_work, HOST_SPEAKER_NAME)
            if _mic_segment_is_real(m_rms, seg["start"] - eff_offset_sec, seg["end"] - eff_offset_sec)
        ]
    if _track_has_speech(d_rms):
        print(f"[WHISPER] [{ts_block}] Transcrevendo faixa Discord...", flush=True)
        if track_status_cb:
            track_status_cb("faixa Discord")
        discord_segs = _run(disc_work, "Discord")
    return merge_track_segments(host_segs, discord_segs)


def run_transcription_pipeline(
    mkv_path: Path,
    temp_dir: Path,
    output_srt_path: Path,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    manual_game: str = "",
    extra_context: str = "",
    norm_output_mkv: Optional[Path] = None,
    separate_tracks: Optional[bool] = None,
) -> Dict[str, Any]:
    temp_dir.mkdir(parents=True, exist_ok=True)
    stat = mkv_path.stat()
    mkv_sig = f"{mkv_path.name}|{stat.st_size}|{int(stat.st_mtime)}"
    extract_meta_path = temp_dir / "extract_meta.json"
    ckpt_path = temp_dir / "whisper_checkpoint.json"
    final_ckpt_path = temp_dir / "transcription_checkpoint.json"

    # 0. Verificar se transcrição + SpeechBrain já foram 100% concluídos em checkpoint anterior
    if final_ckpt_path.exists():
        try:
            fck = json.loads(final_ckpt_path.read_text(encoding="utf-8"))
            if fck.get("mkv_sig") == mkv_sig and fck.get("segments"):
                if not output_srt_path.exists():
                    export_srt_file(fck["segments"], output_srt_path)
                print(
                    f"[CHECKPOINT] Transcrição + SpeechBrain já concluídos no checkpoint "
                    f"({len(fck['segments'])} falas, {len(fck.get('speaker_samples', {}))} vozes)! "
                    f"Pulando direto para a análise da IA.",
                    flush=True
                )
                return {
                    "duration": float(fck.get("duration", 0.0)),
                    "segments": fck["segments"],
                    "hype_scores": fck.get("hype_scores", []),
                    "speaker_samples": fck.get("speaker_samples", {}),
                    "has_isolated_mic": bool(fck.get("has_isolated_mic", True)),
                    "srt_path": str(output_srt_path)
                }
        except Exception as fck_err:
            print(f"[CHECKPOINT] Aviso ao ler transcription_checkpoint ({fck_err}); continuando normalmente.", flush=True)

    mic_wav = temp_dir / "track2_mic.wav"
    discord_wav = temp_dir / "track3_discord.wav"
    soundpad_wav = temp_dir / "track6_soundpad.wav"
    mix_wav = temp_dir / "balanced_mix.wav"

    # Se existir checkpoint no formato antigo (WAV único + balanced_mix.wav), mantém compatibilidade;
    # caso contrário, usa o Pipeline Paralelo no SSD (extração segmentada + pré-buffer de 15 min + Whisper simultâneo)!
    use_legacy_cache = False
    if extract_meta_path.exists() and mic_wav.exists() and discord_wav.exists():
        try:
            meta = json.loads(extract_meta_path.read_text(encoding="utf-8"))
            if meta.get("mkv_sig") == mkv_sig and meta.get("mode") != "segmented":
                use_legacy_cache = True
        except Exception:
            use_legacy_cache = False

    if not use_legacy_cache:
        return _run_parallel_segmented_pipeline(
            mkv_path=mkv_path,
            mkv_sig=mkv_sig,
            temp_dir=temp_dir,
            output_srt_path=output_srt_path,
            progress_callback=progress_callback,
            manual_game=manual_game,
            extra_context=extra_context,
            norm_output_mkv=norm_output_mkv,
            separate_tracks=separate_tracks,
        )

    # 1. Caminho legado (apenas para retomar checkpoints antigos já extraídos como WAV único)
    reused_extract = False
    has_isolated_mic = True
    stream_count = 6
    if extract_meta_path.exists() and mic_wav.exists() and discord_wav.exists():
        try:
            meta = json.loads(extract_meta_path.read_text(encoding="utf-8"))
            if meta.get("mkv_sig") == mkv_sig:
                has_isolated_mic = bool(meta.get("has_isolated_mic", True))
                stream_count = int(meta.get("stream_count", 6))
                reused_extract = True
                print("[CHECKPOINT] Reaproveitando faixas de áudio já extraídas anteriormente!", flush=True)
        except Exception:
            reused_extract = False

    if not reused_extract:
        tracks = extract_audio_tracks(mkv_path, temp_dir, progress_callback=progress_callback)
        mic_wav = tracks["mic"]
        discord_wav = tracks["discord"]
        soundpad_wav = tracks["soundpad"]
        has_isolated_mic = tracks.get("has_isolated_mic", True)
        stream_count = tracks.get("stream_count", 6)
        extract_meta_path.write_text(json.dumps({
            "mkv_sig": mkv_sig,
            "has_isolated_mic": has_isolated_mic,
            "stream_count": stream_count
        }), encoding="utf-8")
    else:
        soundpad_wav = soundpad_wav if soundpad_wav.exists() else None

    if progress_callback:
        progress_callback("Analisando telemetria de energia, risadas e Soundpad...", 0.26)

    # 2. Análise de dinâmica e hype
    dynamics = analyze_audio_dynamics(
        mic_wav, discord_wav, soundpad_wav,
        has_isolated_mic=has_isolated_mic,
        stream_count=stream_count
    )
    has_isolated_mic = dynamics.get("has_isolated_mic", has_isolated_mic)
    gc.collect()

    # 3. Criar mix balanceado para o Whisper (reaproveita se já existir do checkpoint)
    if not (reused_extract and mix_wav.exists()):
        if progress_callback:
            progress_callback("Preparando mixagem de alta nitidez para transcrição...", 0.30)
        create_balanced_mix_audio(mic_wav, discord_wav, mix_wav, has_isolated_mic=has_isolated_mic)
    else:
        print("[CHECKPOINT] Reaproveitando balanced_mix.wav existente!", flush=True)

    # 4. Transcrição com Whisper large-v3 Full em blocos de ~5 min (~80 MiB RAM/bloco) + checkpoint automático
    total_duration = max(1.0, dynamics["duration"])
    total_hhmmss = format_hhmmss(total_duration)
    chunk_bounds, wav_sr = _find_chunk_boundaries(mix_wav, target_chunk_sec=300.0)
    num_chunks = len(chunk_bounds)

    # Tentar retomar checkpoint salvo se for o mesmo vídeo e mesma divisão de blocos
    raw_segments: List[Dict[str, Any]] = []
    host_count = 0
    discord_count = 0
    start_chunk_idx = 1

    if ckpt_path.exists():
        try:
            ck = json.loads(ckpt_path.read_text(encoding="utf-8"))
            if ck.get("mkv_sig") == mkv_sig and ck.get("chunk_bounds") == [list(b) for b in chunk_bounds]:
                last_done = int(ck.get("last_completed_chunk", 0))
                if 0 < last_done <= num_chunks:
                    raw_segments = ck.get("raw_segments", [])
                    host_count = int(ck.get("host_count", 0))
                    discord_count = int(ck.get("discord_count", 0))
                    start_chunk_idx = last_done + 1
                    print(
                        f"[CHECKPOINT] Retomando transcrição do bloco {start_chunk_idx}/{num_chunks} "
                        f"({len(raw_segments)} falas já preservadas)!",
                        flush=True
                    )
        except Exception as ck_err:
            print(f"[CHECKPOINT] Aviso ao ler checkpoint ({ck_err}); iniciando do bloco 1.", flush=True)

    model = None
    if start_chunk_idx > num_chunks:
        print(
            f"[CHECKPOINT] Todos os {num_chunks} blocos já foram transcritos ({len(raw_segments)} falas)! "
            f"Pulando carregamento do Whisper e indo direto para a identificação de vozes.",
            flush=True
        )
    else:
        if progress_callback:
            progress_callback(f"Carregando Whisper {WHISPER_MODEL_SIZE} (GPU/CPU)...", 0.34)

        _log_memory_status("[MEMÓRIA PRÉ-WHISPER]")

        model = get_whisper_model()
        initial_prompt = build_whisper_initial_prompt(manual_game=manual_game, extra_context=extra_context)
        print(f"[WHISPER] Prompt de contexto ativo: {initial_prompt}", flush=True)

        print(
            f"[WHISPER] Iniciando transcrição Beam=5 (VAD + refinamento por palavra) | Duração: {total_hhmmss} | "
            f"{num_chunks} bloco(s) de ~5 min (~80 MiB RAM/bloco)",
            flush=True
        )
        if progress_callback and start_chunk_idx == 1:
            progress_callback(f"Transcrevendo fala com Whisper large-v3 [00:00:00 / {total_hhmmss}]...", 0.36)

        t_trans_start = time.time()
        last_cb_wall = 0.0
        seg_counter = len(raw_segments)

        for c_idx, (start_frame, end_frame) in enumerate(chunk_bounds, 1):
            if c_idx < start_chunk_idx:
                continue

            chunk_offset_sec = start_frame / float(wav_sr)
            chunk_end_sec = end_frame / float(wav_sr)
            if num_chunks > 1:
                print(
                    f"[WHISPER] Processando bloco {c_idx}/{num_chunks} "
                    f"[{format_hhmmss(chunk_offset_sec)} -> {format_hhmmss(chunk_end_sec)}]...",
                    flush=True
                )

            with sf.SoundFile(str(mix_wav)) as f:
                f.seek(start_frame)
                chunk_audio = f.read(end_frame - start_frame, dtype="float32")
                if chunk_audio.ndim > 1:
                    chunk_audio = chunk_audio.mean(axis=1)

            segments_gen, info = model.transcribe(
                chunk_audio,
                language=WHISPER_LANGUAGE,
                beam_size=5,
                best_of=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=250),
                word_timestamps=True,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                hallucination_silence_threshold=WHISPER_HALLUCINATION_SILENCE_THRESHOLD,
                initial_prompt=initial_prompt
            )

            for s in segments_gen:
                refined_segs = _refine_whisper_segment(
                    s, chunk_offset_sec, dynamics, use_local_dynamics=False
                )
                for seg_dict in refined_segs:
                    seg_counter += 1
                    if seg_dict["speaker"] == HOST_SPEAKER_NAME:
                        host_count += 1
                    else:
                        discord_count += 1
                    raw_segments.append(seg_dict)

                    abs_start = seg_dict["start"]
                    abs_end = seg_dict["end"]
                    text_clean = seg_dict["text"]
                    ts_cur = format_hhmmss(abs_end)
                    preview = text_clean if len(text_clean) <= 85 else text_clean[:82] + "..."
                    print(f"[FALA #{seg_counter:04d}] [{format_hhmmss(abs_start)} -> {ts_cur}] [{seg_dict['speaker']}]: {preview}", flush=True)

                    now_w = time.time()
                    if progress_callback and (now_w - last_cb_wall) >= 1.5:
                        last_cb_wall = now_w
                        current_pct = 0.36 + min(0.42, (abs_end / total_duration) * 0.42)
                        progress_callback(
                            f"Transcrevendo bloco {c_idx}/{num_chunks} [{ts_cur} / {total_hhmmss}] ({len(raw_segments)} falas)...",
                            current_pct
                        )

            # Liberar explicitamente gerador, features internas e buffer ANTES do próximo bloco!
            del segments_gen, info, chunk_audio
            gc.collect()

            # Salvar checkpoint após cada bloco concluído (protege vídeos de 3–8h contra falhas)
            try:
                ckpt_tmp = ckpt_path.with_suffix(".tmp")
                ckpt_tmp.write_text(json.dumps({
                    "mkv_sig": mkv_sig,
                    "chunk_bounds": [list(b) for b in chunk_bounds],
                    "last_completed_chunk": c_idx,
                    "raw_segments": raw_segments,
                    "host_count": host_count,
                    "discord_count": discord_count
                }, ensure_ascii=False), encoding="utf-8")
                os.replace(str(ckpt_tmp), str(ckpt_path))
            except Exception:
                pass

        trans_elapsed = time.time() - t_trans_start
        speed_factor = total_duration / max(0.1, trans_elapsed)
        print(
            f"[WHISPER] Transcrição finalizada em {trans_elapsed:.1f}s ({speed_factor:.1f}x tempo real) | "
            f"Total: {len(raw_segments)} falas ({HOST_SPEAKER_NAME}: {host_count}, Discord: {discord_count})",
            flush=True
        )

    # Liberar Whisper da VRAM/RAM ANTES de importar PyTorch para o SpeechBrain
    if model is not None:
        del model
    release_whisper_model()
    gc.collect()
    _log_memory_status("[MEMÓRIA PÓS-WHISPER]")

    if progress_callback:
        progress_callback("Agrupando timbres de voz no Discord (SpeechBrain ECAPA-TDNN)...", 0.80)

    # 5. Agrupamento de vozes dos amigos no Discord (PyTorch só é carregado agora!)
    known_profiles = load_known_voice_profiles(exclude_host=has_isolated_mic)
    print(f"[VOZES] Perfis já conhecidos em vozes_amigos/: {list(known_profiles.keys()) or 'Nenhum ainda'}", flush=True)
    enriched_segments, speaker_samples = cluster_and_identify_speakers(
        discord_wav, raw_segments, known_profiles,
        progress_callback=progress_callback,
        hype_scores=dynamics["hype_scores"]
    )
    print(f"[VOZES] Vozes detectadas no Discord nesta sessão: {list(speaker_samples.keys()) or 'Nenhuma fala longa no Discord'}", flush=True)
    print("[VOZES] Amostras vocais NÃO são salvas automaticamente — revise/ouça os trechos no painel antes de salvar.", flush=True)

    if progress_callback:
        progress_callback("Exportando arquivo de legenda .SRT...", 0.88)

    # 6. Exportar arquivo .srt
    export_srt_file(enriched_segments, output_srt_path)

    # 7. Salvar checkpoint completo pós-SpeechBrain (protege contra falhas na API de IA)
    try:
        fck_tmp = final_ckpt_path.with_suffix(".tmp")
        fck_tmp.write_text(json.dumps({
            "mkv_sig": mkv_sig,
            "duration": total_duration,
            "segments": enriched_segments,
            "hype_scores": dynamics["hype_scores"],
            "speaker_samples": speaker_samples,
            "has_isolated_mic": has_isolated_mic
        }, ensure_ascii=False), encoding="utf-8")
        os.replace(str(fck_tmp), str(final_ckpt_path))
        print(f"[CHECKPOINT] Checkpoint completo pós-SpeechBrain salvo em {final_ckpt_path.name}.", flush=True)
    except Exception as fck_save_err:
        print(f"[CHECKPOINT] Aviso ao salvar transcription_checkpoint: {fck_save_err}", flush=True)

    return {
        "duration": total_duration,
        "segments": enriched_segments,
        "hype_scores": dynamics["hype_scores"],
        "speaker_samples": speaker_samples,
        "has_isolated_mic": has_isolated_mic,
        "srt_path": str(output_srt_path)
    }


def _run_parallel_segmented_pipeline(
    mkv_path: Path,
    mkv_sig: str,
    temp_dir: Path,
    output_srt_path: Path,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    segment_sec: int = 300,
    prebuffer_chunks: int = 3,
    manual_game: str = "",
    extra_context: str = "",
    norm_output_mkv: Optional[Path] = None,
    separate_tracks: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Pipeline Paralelo em Diretório Temporário (preferencialmente SSD separado do disco de gravações):
    1. FFmpeg extrai blocos de 5 min no diretório temporário configurado (~5x mais rápido em SSD!).
       Quando `norm_output_mkv` é informado, gera o MKV normalizado YouTube Gold na MESMA leitura (Passada Única!).
    2. Aguarda pré-buffer de segurança (3 blocos = 15 min de áudio fechados em disco).
    3. Aciona Whisper large-v3 Full (beam_size=5, 100% máxima qualidade) em paralelo enquanto
       o FFmpeg termina o restante do MKV em segundo plano, usando fronteira de silêncio
       (carryover) entre blocos para nunca cortar palavras ao meio!
    4. Calcula telemetria de hype e roda SpeechBrain em mini-lotes lendo bloco a bloco (~20 MB RAM).
    """
    seg_dir = temp_dir / "seg"
    seg_meta_path = temp_dir / "extract_meta.json"
    ckpt_path = temp_dir / "whisper_checkpoint.json"
    final_ckpt_path = temp_dir / "transcription_checkpoint.json"

    reused_seg_extract = False
    single_pass_norm_ready = False
    has_isolated_mic = True
    has_soundpad = False
    stream_count = 6
    total_duration = 0.0
    total_saved_chunks = 0

    if seg_meta_path.exists() and seg_dir.exists():
        try:
            meta = json.loads(seg_meta_path.read_text(encoding="utf-8"))
            if (
                meta.get("mkv_sig") == mkv_sig
                and meta.get("mode") == "segmented"
                and meta.get("extract_completed") is True
            ):
                disc_files = sorted(seg_dir.glob("disc_*.wav"))
                if disc_files and len(disc_files) == int(meta.get("num_chunks", 0)):
                    reused_seg_extract = True
                    has_isolated_mic = bool(meta.get("has_isolated_mic", True))
                    has_soundpad = bool(meta.get("has_soundpad", False))
                    stream_count = int(meta.get("stream_count", 6))
                    total_duration = float(meta.get("duration", 0.0))
                    total_saved_chunks = len(disc_files)
                    if (
                        norm_output_mkv
                        and meta.get("single_pass_norm_completed") is True
                        and norm_output_mkv.exists()
                    ):
                        single_pass_norm_ready = True
                    print(
                        f"[CHECKPOINT] Reaproveitando {total_saved_chunks} bloco(s) de áudio já extraídos no SSD!",
                        flush=True
                    )
        except Exception:
            reused_seg_extract = False

    extractor: Optional[SegmentedAudioExtractor] = None
    model = None
    try:
        if not reused_seg_extract:
            extractor = SegmentedAudioExtractor(
                mkv_path,
                seg_dir,
                segment_sec=segment_sec,
                norm_output_mkv=norm_output_mkv
            )
            has_isolated_mic = extractor.has_isolated_mic
            has_soundpad = extractor.has_soundpad
            stream_count = extractor.stream_count
            total_duration = max(1.0, extractor.total_dur)
            extractor.start()
            extractor.wait_for_prebuffer(min_chunks=prebuffer_chunks, progress_callback=progress_callback)

        total_duration = max(1.0, total_duration)
        total_hhmmss = format_hhmmss(total_duration)
        est_num_chunks = total_saved_chunks if reused_seg_extract else max(1, int(np.ceil(total_duration / float(segment_sec))))

        # Retomar checkpoint de blocos Whisper já concluídos, se houver
        raw_segments: List[Dict[str, Any]] = []
        host_count = 0
        discord_count = 0
        completed_chunks = 0
        saved_carryover_sec = 0.0

        if ckpt_path.exists():
            try:
                ck = json.loads(ckpt_path.read_text(encoding="utf-8"))
                if ck.get("mkv_sig") == mkv_sig and ck.get("mode") == "segmented":
                    last_done = int(ck.get("completed_chunks", 0))
                    if last_done > 0:
                        raw_segments = ck.get("raw_segments", [])
                        host_count = int(ck.get("host_count", 0))
                        discord_count = int(ck.get("discord_count", 0))
                        completed_chunks = last_done
                        saved_carryover_sec = float(ck.get("carryover_sec", 0.0) or 0.0)
                        print(
                            f"[CHECKPOINT] Retomando transcrição segmentada a partir do bloco {completed_chunks + 1} "
                            f"({len(raw_segments)} falas já preservadas)!",
                            flush=True
                        )
            except Exception as ck_err:
                print(f"[CHECKPOINT] Aviso ao ler checkpoint ({ck_err}); iniciando do bloco 1.", flush=True)

        skip_whisper = reused_seg_extract and completed_chunks >= total_saved_chunks
        initial_prompt = build_whisper_initial_prompt(manual_game=manual_game, extra_context=extra_context)
        if skip_whisper:
            print(
                f"[CHECKPOINT] Todos os {total_saved_chunks} blocos já estão transcritos ({len(raw_segments)} falas)! "
                f"Pulando carregamento do Whisper e indo direto para a identificação de vozes.",
                flush=True
            )
        else:
            if progress_callback:
                progress_callback(f"Carregando Whisper {WHISPER_MODEL_SIZE} (GPU/CPU)...", 0.32)
            _log_memory_status("[MEMÓRIA PRÉ-WHISPER]")
            model = get_whisper_model()
            print(f"[WHISPER] Prompt de contexto ativo: {initial_prompt}", flush=True)
            print(
                f"[WHISPER] Pipeline paralelo ativo | Beam=5 (VAD + corte por silêncio + refinamento por palavra) | "
                f"Duração: {total_hhmmss} | ~{est_num_chunks} bloco(s) de {segment_sec // 60} min no SSD",
                flush=True
            )
            if progress_callback and completed_chunks == 0:
                progress_callback(f"Transcrevendo em paralelo com Whisper large-v3 [00:00:00 / {total_hhmmss}]...", 0.34)

        t_trans_start = time.time()
        last_cb_wall = 0.0
        seg_counter = len(raw_segments)
        chunk_timeline: List[Tuple[float, float, Path]] = []
        chunk_offset_sec = 0.0
        checked_2track_dup = False
        carryover_disc: Optional[np.ndarray] = None
        carryover_mic: Optional[np.ndarray] = None
        restored_carryover = False
        idx = 0

        while True:
            if reused_seg_extract:
                if idx >= total_saved_chunks:
                    break
            else:
                if not extractor.wait_for_chunk(idx):
                    break

            disc_p = seg_dir / f"disc_{idx:04d}.wav"
            mic_p = seg_dir / f"mic_{idx:04d}.wav"
            info_wav = sf.info(str(disc_p))
            wav_sr = info_wav.samplerate
            chunk_dur = info_wav.frames / float(wav_sr)
            chunk_end_sec = chunk_offset_sec + chunk_dur
            chunk_timeline.append((chunk_offset_sec, chunk_end_sec, disc_p))

            if idx < completed_chunks:
                chunk_offset_sec = chunk_end_sec
                idx += 1
                continue

            # Ao retomar de checkpoint com carryover salvo, recuperar a sobra final do bloco anterior no SSD
            if not restored_carryover and idx == completed_chunks and idx > 0 and saved_carryover_sec > 0.0:
                restored_carryover = True
                prev_disc_p = seg_dir / f"disc_{idx - 1:04d}.wav"
                prev_mic_p = seg_dir / f"mic_{idx - 1:04d}.wav"
                try:
                    if prev_disc_p.exists():
                        p_disc, p_sr = sf.read(str(prev_disc_p), dtype="float32")
                        if p_disc.ndim > 1:
                            p_disc = p_disc.mean(axis=1)
                        n_carry = min(len(p_disc), int(round(saved_carryover_sec * p_sr)))
                        if n_carry > 0:
                            carryover_disc = p_disc[-n_carry:]
                            if has_isolated_mic and prev_mic_p.exists():
                                p_mic, _ = sf.read(str(prev_mic_p), dtype="float32")
                                if p_mic.ndim > 1:
                                    p_mic = p_mic.mean(axis=1)
                                carryover_mic = p_mic[-n_carry:] if len(p_mic) >= n_carry else np.zeros(n_carry, dtype=np.float32)
                except Exception:
                    carryover_disc = None
                    carryover_mic = None

            # Log de folga do FFmpeg paralelo em relação ao Whisper
            extra_buf_txt = ""
            if extractor is not None:
                if extractor.done_event.is_set():
                    extra_buf_txt = " | FFmpeg: 100% concluído"
                else:
                    ready_now = extractor.ready_chunk_count()
                    lead = max(0, ready_now - (idx + 1))
                    extra_buf_txt = f" | FFmpeg adiantado: +{lead} bloco(s) ({lead * segment_sec // 60} min de folga)"

            disc_audio, _ = sf.read(str(disc_p), dtype="float32")
            if disc_audio.ndim > 1:
                disc_audio = disc_audio.mean(axis=1)

            mic_audio = None
            if has_isolated_mic and mic_p.exists():
                mic_audio, _ = sf.read(str(mic_p), dtype="float32")
                if mic_audio.ndim > 1:
                    mic_audio = mic_audio.mean(axis=1)

            # Anexar sobra de silêncio (carryover) do bloco anterior para continuidade perfeita
            carry_dur = 0.0
            if carryover_disc is not None and len(carryover_disc) > 0:
                carry_dur = len(carryover_disc) / float(wav_sr)
                disc_audio = np.concatenate([carryover_disc, disc_audio])
                if mic_audio is not None:
                    c_mic = carryover_mic if (carryover_mic is not None and len(carryover_mic) == len(carryover_disc)) else np.zeros(len(carryover_disc), dtype=np.float32)
                    mic_audio = np.concatenate([c_mic, mic_audio])
                carryover_disc = None
                carryover_mic = None

            eff_offset_sec = max(0.0, chunk_offset_sec - carry_dur)

            # Determinar se existe próximo bloco para decidir se buscamos ponto de silêncio na cauda
            if reused_seg_extract:
                has_next_chunk = (idx + 1) < total_saved_chunks
            elif extractor is not None and extractor.done_event.is_set():
                has_next_chunk = (seg_dir / f"disc_{idx + 1:04d}.wav").exists()
            else:
                has_next_chunk = chunk_dur >= (segment_sec * 0.85)

            full_mix = build_balanced_mix_array(mic_audio, disc_audio, has_isolated_mic=has_isolated_mic)
            split_sample = len(full_mix)
            if has_next_chunk:
                split_sample = _find_tail_silence_split(full_mix, sample_rate=wav_sr, tail_window_sec=18.0)

            if split_sample < len(disc_audio):
                carryover_disc = disc_audio[split_sample:].copy()
                disc_work = disc_audio[:split_sample]
                if mic_audio is not None:
                    carryover_mic = mic_audio[split_sample:].copy() if split_sample < len(mic_audio) else np.zeros(len(carryover_disc), dtype=np.float32)
                    mic_work = mic_audio[:split_sample]
                else:
                    carryover_mic = None
                    mic_work = None
                mix_chunk = full_mix[:split_sample]
                next_carry_sec = len(carryover_disc) / float(wav_sr)
            else:
                disc_work = disc_audio
                mic_work = mic_audio
                mix_chunk = full_mix
                next_carry_sec = 0.0

            eff_end_sec = eff_offset_sec + (len(mix_chunk) / float(wav_sr))
            carry_note = f" | corte silêncio (-{next_carry_sec:.1f}s)" if next_carry_sec > 0 else ""
            print(
                f"[WHISPER] Processando bloco {idx + 1}/{est_num_chunks} "
                f"[{format_hhmmss(eff_offset_sec)} -> {format_hhmmss(eff_end_sec)}]{carry_note}{extra_buf_txt}...",
                flush=True
            )

            d_rms = compute_rms_100ms(disc_work, wav_sr)
            if mic_work is not None:
                m_rms = compute_rms_100ms(mic_work, wav_sr)
                # Proteção contra gravações antigas de 2 faixas com áudio duplicado
                if not checked_2track_dup and stream_count == 2 and len(m_rms) >= 20:
                    if float(np.std(m_rms)) > 0.1 and float(np.std(d_rms)) > 0.1:
                        checked_2track_dup = True
                        min_f = min(len(m_rms), len(d_rms))
                        corr = float(np.corrcoef(m_rms[:min_f], d_rms[:min_f])[0, 1])
                        if corr > 0.92:
                            print(
                                f"[TELEMETRIA] Gravação antiga com 2 faixas idênticas (correlação {corr:.2f}); "
                                f"isolamento físico de microfone desativado.",
                                flush=True
                            )
                            has_isolated_mic = False
            else:
                m_rms = np.zeros_like(d_rms)

            block_dynamics = {
                "has_isolated_mic": has_isolated_mic,
                "mic_rms": m_rms,
                "discord_rms": d_rms
            }

            from app.config import AUTO_PAUSE_ON_RECORDING, OBS_RECORDINGS_DIR, PAUSE_PROCESSES
            if AUTO_PAUSE_ON_RECORDING:
                from app.game_guard import wait_while_paused
                base_pause_pct = 0.34 + min(0.44, (eff_offset_sec / total_duration) * 0.44)
                wait_while_paused(
                    obs_dir=OBS_RECORDINGS_DIR,
                    resume_flag_path=temp_dir / "force_resume.flag",
                    exclude_path=mkv_path,
                    watched_processes=PAUSE_PROCESSES,
                    progress_cb=(lambda m: progress_callback(m, base_pause_pct)) if progress_callback else None,
                )

            def _track_cb(track_label: str):
                if progress_callback:
                    base_pct = 0.34 + min(0.44, (eff_offset_sec / total_duration) * 0.44)
                    progress_callback(
                        f"Transcrevendo bloco {idx + 1}/{est_num_chunks} ({track_label}) [{format_hhmmss(eff_offset_sec)} / {total_hhmmss}]...",
                        base_pct,
                    )

            block_segs = _transcribe_block(
                model, mix_chunk, mic_work, disc_work,
                eff_offset_sec, block_dynamics, initial_prompt,
                separate_tracks=separate_tracks,
                track_status_cb=_track_cb,
            )

            for seg_dict in block_segs:
                seg_counter += 1
                if seg_dict["speaker"] == HOST_SPEAKER_NAME:
                    host_count += 1
                else:
                    discord_count += 1
                raw_segments.append(seg_dict)

                abs_start = seg_dict["start"]
                abs_end = seg_dict["end"]
                text_clean = seg_dict["text"]
                ts_cur = format_hhmmss(abs_end)
                preview = text_clean if len(text_clean) <= 85 else text_clean[:82] + "..."
                print(f"[FALA #{seg_counter:04d}] [{format_hhmmss(abs_start)} -> {ts_cur}] [{seg_dict['speaker']}]: {preview}", flush=True)

                now_w = time.time()
                if progress_callback and (now_w - last_cb_wall) >= 1.5:
                    last_cb_wall = now_w
                    current_pct = 0.34 + min(0.44, (abs_end / total_duration) * 0.44)
                    progress_callback(
                        f"Transcrevendo bloco {idx + 1}/{est_num_chunks} [{ts_cur} / {total_hhmmss}] ({len(raw_segments)} falas)...",
                        current_pct
                    )

            del block_segs, full_mix, mix_chunk, disc_audio, mic_audio, disc_work, mic_work, m_rms, d_rms, block_dynamics
            gc.collect()

            completed_chunks = idx + 1
            try:
                ckpt_tmp = ckpt_path.with_suffix(".tmp")
                ckpt_tmp.write_text(json.dumps({
                    "mkv_sig": mkv_sig,
                    "mode": "segmented",
                    "completed_chunks": completed_chunks,
                    "carryover_sec": round(next_carry_sec, 4),
                    "raw_segments": raw_segments,
                    "host_count": host_count,
                    "discord_count": discord_count
                }, ensure_ascii=False), encoding="utf-8")
                os.replace(str(ckpt_tmp), str(ckpt_path))
            except Exception:
                pass

            chunk_offset_sec = chunk_end_sec
            idx += 1

        # Caso o vídeo tenha terminado exatamente num múltiplo de 300s e ainda haja sobra no carryover, transcrevê-la
        if not skip_whisper and model is not None and carryover_disc is not None and len(carryover_disc) >= 1600:
            wav_sr = 16000
            carry_dur = len(carryover_disc) / float(wav_sr)
            eff_offset_sec = max(0.0, chunk_offset_sec - carry_dur)
            d_rms = compute_rms_100ms(carryover_disc, wav_sr)
            m_rms = compute_rms_100ms(carryover_mic, wav_sr) if carryover_mic is not None else np.zeros_like(d_rms)
            block_dynamics = {"has_isolated_mic": has_isolated_mic, "mic_rms": m_rms, "discord_rms": d_rms}
            mix_tail = build_balanced_mix_array(carryover_mic, carryover_disc, has_isolated_mic=has_isolated_mic)
            tail_segs = _transcribe_block(
                model, mix_tail, carryover_mic, carryover_disc,
                eff_offset_sec, block_dynamics, initial_prompt,
                separate_tracks=separate_tracks,
            )
            for seg_dict in tail_segs:
                seg_counter += 1
                if seg_dict["speaker"] == HOST_SPEAKER_NAME:
                    host_count += 1
                else:
                    discord_count += 1
                raw_segments.append(seg_dict)
            carryover_disc = None
            carryover_mic = None

        # Garantir término limpo do FFmpeg e salvar metadados completos da extração
        if extractor is not None:
            extractor.wait_finished()
            single_pass_norm_ready = bool(
                norm_output_mkv
                and extractor.returncode == 0
                and norm_output_mkv.exists()
            )
            actual_duration = max(chunk_offset_sec, total_duration)
            seg_meta_path.write_text(json.dumps({
                "mkv_sig": mkv_sig,
                "mode": "segmented",
                "extract_completed": True,
                "single_pass_norm_completed": single_pass_norm_ready,
                "has_isolated_mic": has_isolated_mic,
                "has_soundpad": has_soundpad,
                "stream_count": stream_count,
                "num_chunks": len(chunk_timeline),
                "duration": actual_duration
            }, ensure_ascii=False), encoding="utf-8")
            total_duration = actual_duration
        else:
            total_duration = max(chunk_offset_sec, total_duration)

        if not skip_whisper:
            trans_elapsed = time.time() - t_trans_start
            speed_factor = total_duration / max(0.1, trans_elapsed)
            print(
                f"[WHISPER] Transcrição finalizada em {trans_elapsed:.1f}s ({speed_factor:.1f}x tempo real) | "
                f"Total: {len(raw_segments)} falas ({HOST_SPEAKER_NAME}: {host_count}, Discord: {discord_count})",
                flush=True
            )
    finally:
        if extractor is not None and not extractor.done_event.is_set():
            extractor.terminate()
        if model is not None:
            del model
        release_whisper_model()
        gc.collect()

    _log_memory_status("[MEMÓRIA PÓS-WHISPER]")

    # Calcular telemetria global de hype lendo os blocos direto do SSD (~3-5s para 10h, ~20 MB RAM)
    if progress_callback:
        progress_callback("Analisando telemetria de energia, risadas e Soundpad no SSD...", 0.79)
    hype_scores = analyze_segmented_hype(
        seg_dir=seg_dir,
        num_chunks=len(chunk_timeline),
        chunk_offsets=[t[0] for t in chunk_timeline],
        has_isolated_mic=has_isolated_mic,
        has_soundpad=has_soundpad
    )
    gc.collect()

    if progress_callback:
        progress_callback("Agrupando timbres de voz no Discord (SpeechBrain ECAPA-TDNN)...", 0.80)

    # Identificação de vozes dos amigos lendo bloco a bloco do SSD (~20-40 MB RAM!)
    known_profiles = load_known_voice_profiles(exclude_host=has_isolated_mic)
    print(f"[VOZES] Perfis já conhecidos em vozes_amigos/: {list(known_profiles.keys()) or 'Nenhum ainda'}", flush=True)
    enriched_segments, speaker_samples = cluster_and_identify_speakers(
        seg_dir, raw_segments, known_profiles,
        progress_callback=progress_callback,
        chunk_timeline=chunk_timeline,
        hype_scores=hype_scores
    )
    print(f"[VOZES] Vozes detectadas no Discord nesta sessão: {list(speaker_samples.keys()) or 'Nenhuma fala longa no Discord'}", flush=True)
    print("[VOZES] Amostras vocais NÃO são salvas automaticamente — revise/ouça os trechos no painel antes de salvar.", flush=True)

    if progress_callback:
        progress_callback("Exportando arquivo de legenda .SRT...", 0.88)

    export_srt_file(enriched_segments, output_srt_path)

    # Checkpoint completo pós-SpeechBrain
    try:
        fck_tmp = final_ckpt_path.with_suffix(".tmp")
        fck_tmp.write_text(json.dumps({
            "mkv_sig": mkv_sig,
            "duration": total_duration,
            "segments": enriched_segments,
            "hype_scores": hype_scores,
            "speaker_samples": speaker_samples,
            "has_isolated_mic": has_isolated_mic
        }, ensure_ascii=False), encoding="utf-8")
        os.replace(str(fck_tmp), str(final_ckpt_path))
        print(f"[CHECKPOINT] Checkpoint completo pós-SpeechBrain salvo em {final_ckpt_path.name}.", flush=True)
    except Exception as fck_save_err:
        print(f"[CHECKPOINT] Aviso ao salvar transcription_checkpoint: {fck_save_err}", flush=True)

    return {
        "duration": total_duration,
        "segments": enriched_segments,
        "hype_scores": hype_scores,
        "speaker_samples": speaker_samples,
        "has_isolated_mic": has_isolated_mic,
        "srt_path": str(output_srt_path),
        "single_pass_norm_mkv": str(norm_output_mkv) if single_pass_norm_ready else None
    }
