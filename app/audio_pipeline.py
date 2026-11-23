import subprocess
import json
import shutil
import time
import threading
import numpy as np
import soundfile as sf
from pathlib import Path
from typing import List, Dict, Tuple, Any, Callable, Optional

DISCORD_NORM_FILTER = "dynaudnorm=f=150:g=15:p=0.90:m=20"
# Reamostra para 16 kHz mono ANTES do dynaudnorm (~6x menos amostras no filtro, mesma qualidade vocal!)
DISCORD_NORM_FILTER_16K = f"aresample=16000,aformat=channel_layouts=mono,{DISCORD_NORM_FILTER}"

YOUTUBE_AUDIO_NORM_TAG = "youtube_gold_v1"

# Cadeia calibrada nos MKVs reais do OBS (aprovada em testes A/B):
# - Alvo: -15.5 a -16.6 LUFS (padrão YouTube -14 LUFS sem redução automática), True Peak <= -1.0 dBTP
# - Equilíbrio Mic (Anfitrião) vs Discord: < 1 dB de diferença | Silêncio nas pausas ~ -78 dB (zero chiado)
# - Estéreo 48 kHz AAC 320 kbps com channel coupling (c=1)
YOUTUBE_GOLD_FILTER_MULTI = (
    "[0:a:0]highpass=f=75,"
    "dynaudnorm=f=250:g=21:p=0.82:m=12:c=1:b=1:t=0.004,"
    "acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=9dB[a1];"
    "[0:a:2]highpass=f=85,"
    "dynaudnorm=f=200:g=15:p=0.78:m=75:c=1:b=1:t=0.0008,"
    "acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=7.5dB[a3];"
    "[a1][a3]amix=inputs=2:duration=longest:normalize=0:weights=1.0 0.80,"
    "volume=5.0dB,"
    "alimiter=limit=0.87:attack=5:release=50:level=false,"
    "aformat=sample_rates=48000:channel_layouts=stereo[aout]"
)

YOUTUBE_GOLD_FILTER_SINGLE = (
    "[0:a:0]highpass=f=75,"
    "dynaudnorm=f=220:g=15:p=0.80:m=45:c=1:b=1:t=0.001,"
    "acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=8.5dB,"
    "volume=4.5dB,"
    "alimiter=limit=0.87:attack=5:release=50:level=false,"
    "aformat=sample_rates=48000:channel_layouts=stereo[aout]"
)


def parse_time_to_seconds(time_str: str) -> float:
    try:
        parts = time_str.split(":")
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except Exception:
        pass
    return 0.0


def get_audio_stream_count_and_duration(video_path: Path) -> Tuple[int, float]:
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(video_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    audio_count = len([s for s in data.get("streams", []) if s.get("codec_type") == "audio"])
    dur = float(data.get("format", {}).get("duration", 0.0))
    return audio_count, dur


def extract_audio_tracks(
    mkv_path: Path,
    temp_dir: Path,
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> Dict[str, Any]:
    temp_dir.mkdir(parents=True, exist_ok=True)
    mic_wav = temp_dir / "track2_mic.wav"
    discord_wav = temp_dir / "track3_discord.wav"
    soundpad_wav = temp_dir / "track6_soundpad.wav"
    stderr_log = temp_dir / "ffmpeg_extract.err"

    stream_count, total_dur = get_audio_stream_count_and_duration(mkv_path)
    if stream_count == 0:
        raise RuntimeError(f"O vídeo '{mkv_path.name}' não possui nenhuma faixa de áudio.")

    hrs = int(total_dur // 3600)
    mins = int((total_dur % 3600) // 60)
    secs = int(total_dur % 60)
    total_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"

    print(f"[FFMPEG] Vídeo possui {stream_count} faixa(s) de áudio | Duração total: {total_str} ({total_dur:.1f}s)", flush=True)

    if progress_callback:
        progress_callback(f"Extraindo faixas de áudio ({stream_count} faixas) [00:00:00 / {total_str}]...", 0.06)

    cmd = ["ffmpeg", "-y", "-i", str(mkv_path)]
    has_isolated_mic = True
    has_soundpad = False

    if stream_count >= 3:
        cmd.extend([
            "-map", "0:a:1", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(mic_wav),
            "-map", "0:a:2", "-vn", "-af", DISCORD_NORM_FILTER, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(discord_wav)
        ])
        if stream_count >= 6:
            has_soundpad = True
            cmd.extend(["-map", "0:a:5", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(soundpad_wav)])
    elif stream_count == 2:
        print("[FFMPEG] Aviso: gravação antiga com 2 faixas de áudio (0:a:1 como mic e 0:a:0 como mix/Discord).", flush=True)
        cmd.extend([
            "-map", "0:a:1", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(mic_wav),
            "-map", "0:a:0", "-vn", "-af", DISCORD_NORM_FILTER, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(discord_wav)
        ])
    else:
        print("[FFMPEG] Aviso: gravação com apenas 1 faixa de áudio (0:a:0). Isolamento físico de microfone desativado; usando diarização por voz.", flush=True)
        has_isolated_mic = False
        cmd.extend([
            "-map", "0:a:0", "-vn", "-af", DISCORD_NORM_FILTER, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(discord_wav)
        ])

    cmd.extend(["-progress", "pipe:1", "-nostats"])

    with open(stderr_log, "w", encoding="utf-8", errors="replace") as err_file:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_file, text=True, bufsize=1)

        last_update = -5.0
        last_log = -15.0
        current_speed = ""
        if proc.stdout:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("speed="):
                    current_speed = line.split("=", 1)[1].strip()
                elif line.startswith("out_time="):
                    curr_str = line.split("=", 1)[1][:8]
                    curr_sec = parse_time_to_seconds(curr_str)
                    if total_dur > 0:
                        pct = 0.06 + min(0.19, (curr_sec / total_dur) * 0.19)
                        if progress_callback and (curr_sec - last_update) >= 3.0:
                            last_update = curr_sec
                            spd_txt = f" ({current_speed})" if current_speed else ""
                            progress_callback(f"Extraindo e normalizando áudio [{curr_str} / {total_str}]{spd_txt}...", pct)
                        if (curr_sec - last_log) >= 20.0:
                            last_log = curr_sec
                            spd_txt = f" | Velocidade: {current_speed}" if current_speed else ""
                            print(f"[FFMPEG] Progresso de áudio: {curr_str} / {total_str} ({int((curr_sec/total_dur)*100)}%){spd_txt}", flush=True)

        proc.wait()

    if proc.returncode != 0:
        err_tail = ""
        if stderr_log.exists():
            try:
                lines = stderr_log.read_text(encoding="utf-8", errors="replace").splitlines()
                err_tail = "\n".join(lines[-15:])
            except Exception:
                pass
        print(f"[ERRO FFMPEG] Código de saída {proc.returncode}:\n{err_tail}", flush=True)
        raise RuntimeError(f"FFmpeg falhou ao extrair faixas (código {proc.returncode}): {err_tail[-300:] if err_tail else mkv_path.name}")

    if not has_isolated_mic and discord_wav.exists() and not mic_wav.exists():
        shutil.copyfile(str(discord_wav), str(mic_wav))

    print("[FFMPEG] Extração de áudio concluída com sucesso!", flush=True)
    return {
        "mic": mic_wav,
        "discord": discord_wav,
        "soundpad": soundpad_wav if has_soundpad and soundpad_wav.exists() else None,
        "has_isolated_mic": has_isolated_mic,
        "stream_count": stream_count
    }


def analyze_audio_dynamics(
    mic_wav: Path,
    discord_wav: Path,
    soundpad_wav: Optional[Path] = None,
    has_isolated_mic: bool = True,
    sample_rate: int = 16000,
    stream_count: int = 6
) -> Dict[str, Any]:
    mic_data, _ = sf.read(str(mic_wav), dtype="float32")
    discord_data, _ = sf.read(str(discord_wav), dtype="float32")
    soundpad_data = None
    if soundpad_wav and soundpad_wav.exists():
        soundpad_data, _ = sf.read(str(soundpad_wav), dtype="float32")

    min_len = min(len(mic_data), len(discord_data))
    mic_data = mic_data[:min_len]
    discord_data = discord_data[:min_len]
    if soundpad_data is not None:
        soundpad_data = soundpad_data[:min_len]

    # Cálculo vetorizado ultra-rápido em janelas de 100ms (1600 samples)
    frame_size_100ms = int(sample_rate * 0.1)
    num_frames = min_len // frame_size_100ms

    if num_frames > 0:
        trimmed_len = num_frames * frame_size_100ms
        m_mat = mic_data[:trimmed_len].reshape(num_frames, frame_size_100ms)
        d_mat = discord_data[:trimmed_len].reshape(num_frames, frame_size_100ms)

        m_rms_lin = np.maximum(np.sqrt(np.mean(m_mat ** 2, axis=1)), 1e-6)
        d_rms_lin = np.maximum(np.sqrt(np.mean(d_mat ** 2, axis=1)), 1e-6)

        mic_rms = 20.0 * np.log10(m_rms_lin)
        discord_rms = 20.0 * np.log10(d_rms_lin)
    else:
        mic_rms = np.zeros(1, dtype=np.float32)
        discord_rms = np.zeros(1, dtype=np.float32)

    # Em gravações antigas de 2 faixas onde ambas carregam o mesmo áudio duplicado,
    # desativa o isolamento físico para evitar falso-positivo na identificação do anfitrião.
    if has_isolated_mic and stream_count == 2 and num_frames >= 20:
        if float(np.std(mic_rms)) > 0.1 and float(np.std(discord_rms)) > 0.1:
            corr = float(np.corrcoef(mic_rms, discord_rms)[0, 1])
            if corr > 0.92:
                print(
                    f"[TELEMETRIA] Gravação antiga com 2 faixas idênticas (correlação {corr:.2f}); "
                    f"isolamento físico de microfone desativado para usar diarização acústica.",
                    flush=True
                )
                has_isolated_mic = False

    # Referências adaptativas de fala ativa para pontuação de Hype robusta a qualquer ganho do OBS
    m_abs = np.abs(mic_data)
    d_abs = np.abs(discord_data)
    m_active_samples = m_abs[m_abs > 0.004]
    d_active_samples = d_abs[d_abs > 0.004]
    m_ref = float(np.percentile(m_active_samples, 90)) if len(m_active_samples) > 200 else 0.08
    d_ref = float(np.percentile(d_active_samples, 90)) if len(d_active_samples) > 200 else 0.08
    m_ref = max(m_ref, 0.015)
    d_ref = max(d_ref, 0.015)

    # Janela de 5 segundos para cálculo de Melhores Momentos (Hype Score 0-100)
    window_5s = int(sample_rate * 5.0)
    frames_per_5s = 50  # 50 janelas de 100ms = 5s
    num_windows_5s = min_len // window_5s
    hype_scores = []
    high_hype_count = 0

    for w in range(num_windows_5s):
        s = w * window_5s
        e = s + window_5s
        t_start = w * 5.0
        t_end = t_start + 5.0

        m_sub = m_abs[s:e]
        d_sub = d_abs[s:e]

        # 1. Intensidade relativa ao nível normal de fala desta gravação (0-40)
        m_rel_peak = float(np.percentile(m_sub, 98)) / m_ref if has_isolated_mic and len(m_sub) > 0 else 0.0
        d_rel_peak = float(np.percentile(d_sub, 98)) / d_ref if len(d_sub) > 0 else 0.0
        peak_score = min(40.0, max(m_rel_peak, d_rel_peak) * 22.0)

        # 2. Explosão / cadência de risadas em 20 blocos de 250ms (0-30)
        d_chunks = np.mean(d_sub.reshape(20, window_5s // 20), axis=1)
        d_burst = float(np.std(d_chunks)) / d_ref
        if has_isolated_mic:
            m_chunks = np.mean(m_sub.reshape(20, window_5s // 20), axis=1)
            m_burst = float(np.std(m_chunks)) / m_ref
        else:
            m_burst = 0.0
        burst_score = min(30.0, max(d_burst, m_burst) * 45.0)

        # 3. Caos simultâneo: Anfitrião + Discord ativos na mesma janela (0-20)
        f_start = w * frames_per_5s
        f_end = min(len(mic_rms), f_start + frames_per_5s)
        both_active = 0
        if has_isolated_mic and f_end > f_start:
            m_win_p90 = float(np.percentile(mic_rms[f_start:f_end], 90))
            d_win_p90 = float(np.percentile(discord_rms[f_start:f_end], 90))
            if m_win_p90 > -45.0 and d_win_p90 > -45.0:
                both_active = 20

        # 4. Efeitos sonoros no Soundpad (0-15)
        soundpad_score = 0
        if soundpad_data is not None and len(soundpad_data) >= e:
            sp_sub = soundpad_data[s:e]
            if float(np.max(np.abs(sp_sub))) > 0.015:
                soundpad_score = 15

        total_score = int(min(100, peak_score + burst_score + both_active + soundpad_score))
        if total_score >= 75:
            high_hype_count += 1

        hype_scores.append({
            "start": round(t_start, 2),
            "end": round(t_end, 2),
            "score": total_score,
            "soundpad": bool(soundpad_score > 0),
            "is_chaos": bool(both_active > 0)
        })

    print(
        f"[TELEMETRIA] Dinâmica analisada em {min_len / sample_rate:.1f}s de áudio | "
        f"Picos de risada/hype (>=75): {high_hype_count}",
        flush=True
    )

    duration_out = min_len / sample_rate
    del mic_data, discord_data, soundpad_data, m_abs, d_abs, m_active_samples, d_active_samples
    import gc
    gc.collect()

    return {
        "duration": duration_out,
        "mic_rms": mic_rms,
        "discord_rms": discord_rms,
        "has_isolated_mic": has_isolated_mic,
        "hype_scores": hype_scores
    }


def is_host_speaking(start_sec: float, end_sec: float, dynamics: Dict[str, Any]) -> bool:
    if not dynamics.get("has_isolated_mic", True):
        return False

    mic_rms = dynamics["mic_rms"]
    discord_rms = dynamics["discord_rms"]

    s_idx = max(0, int(start_sec * 10))
    e_idx = int(end_sec * 10)

    if s_idx >= len(mic_rms):
        return False
    e_idx = min(e_idx, len(mic_rms))
    if s_idx >= e_idx:
        e_idx = min(len(mic_rms), s_idx + 1)

    m_slice = mic_rms[s_idx:e_idx]
    d_slice = discord_rms[s_idx:e_idx]
    if len(m_slice) == 0:
        return False

    # Como a faixa 2 do OBS é o microfone isolado (silêncio ~ -90 dB com noise gate)
    # e a faixa 3 (Discord) foi normalizada com dynaudnorm, comparamos atividade e energia P75.
    m_active = float(np.mean(m_slice > -48.0))
    d_active = float(np.mean(d_slice > -48.0))
    m_p75 = float(np.percentile(m_slice, 75))
    d_p75 = float(np.percentile(d_slice, 75))

    if m_p75 <= -46.0 or m_active < 0.20:
        return False

    # Caso 1: Só o microfone do anfitrião está claramente ativo neste trecho
    if m_active >= 0.25 and d_active < 0.15:
        return True

    # Caso 2: Ambos têm sinal; o anfitrião vence se domina em tempo ativo ou energia
    if m_active >= d_active * 1.15 and m_p75 >= d_p75 - 4.0:
        return True
    if m_p75 >= d_p75 - 1.5 and m_active >= d_active * 0.75:
        return True

    return False


# ==============================================================================
# PIPELINE PARALELO: EXTRAÇÃO SEGMENTADA EM SSD + PRÉ-BUFFER DE SEGURANÇA
# ==============================================================================

def compute_rms_100ms(audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Calcula RMS em dB por janela de 100 ms de forma vetorizada."""
    frame_size = int(sample_rate * 0.1)
    num_frames = len(audio) // frame_size
    if num_frames <= 0:
        return np.zeros(1, dtype=np.float32)
    mat = audio[:num_frames * frame_size].reshape(num_frames, frame_size)
    rms_lin = np.maximum(np.sqrt(np.mean(mat * mat, axis=1, dtype=np.float32)), 1e-6)
    return (20.0 * np.log10(rms_lin)).astype(np.float32)


def build_balanced_mix_array(
    mic_audio: Optional[np.ndarray],
    discord_audio: np.ndarray,
    has_isolated_mic: bool = True
) -> np.ndarray:
    """
    Combina faixa 2 (microfone do anfitrião) + faixa 3 (Discord normalizado) em memória (float32)
    com soft-limiter suave em 0.95 (equivalente a amix + alimiter=0.95, leva <0.02s/bloco!).
    """
    if not has_isolated_mic or mic_audio is None or len(mic_audio) == 0:
        return discord_audio.astype(np.float32, copy=False)

    min_len = min(len(mic_audio), len(discord_audio))
    mix = mic_audio[:min_len].astype(np.float32, copy=False) + discord_audio[:min_len].astype(np.float32, copy=False)
    abs_m = np.abs(mix)
    over = abs_m > 0.85
    if np.any(over):
        mix = np.where(
            over,
            np.sign(mix) * (0.85 + 0.10 * np.tanh((abs_m - 0.85) / 0.15)),
            mix
        ).astype(np.float32)
    return mix


class SegmentedAudioExtractor:
    """
    Extrai faixas de áudio do MKV em blocos sequenciais de 5 minutos (300s) direto no SSD
    enquanto o Whisper já transcreve os blocos anteriores em paralelo na RTX 4060.
    Um bloco k só é considerado 100% pronto quando o bloco k+1 já existe em TODOS os streams
    ativos (garantindo que o FFmpeg já fechou k) ou quando o FFmpeg terminou com sucesso.
    """

    def __init__(
        self,
        mkv_path: Path,
        seg_dir: Path,
        segment_sec: int = 300,
        norm_output_mkv: Optional[Path] = None
    ):
        self.mkv_path = mkv_path
        self.seg_dir = seg_dir
        self.segment_sec = int(segment_sec)
        self.norm_output_mkv: Optional[Path] = Path(norm_output_mkv) if norm_output_mkv else None
        self.stream_count, self.total_dur = get_audio_stream_count_and_duration(mkv_path)
        if self.stream_count == 0:
            raise RuntimeError(f"O vídeo '{mkv_path.name}' não possui nenhuma faixa de áudio.")

        self.has_isolated_mic = self.stream_count >= 2
        self.has_soundpad = self.stream_count >= 6
        self.active_prefixes = ["disc"]
        if self.has_isolated_mic:
            self.active_prefixes.append("mic")
        if self.has_soundpad:
            self.active_prefixes.append("sp")

        self.proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self.done_event = threading.Event()
        self.returncode: Optional[int] = None
        self.error_tail: str = ""
        self.current_sec: float = 0.0
        self.current_speed: str = ""
        self.stderr_log = self.seg_dir.parent / "ffmpeg_extract.err"
        self.t_start: float = 0.0

    def _fmt_hhmmss(self, sec: float) -> str:
        s = max(0, int(sec))
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    def _build_single_pass_filter_complex(self) -> str:
        """
        Grafo unificado para Passada Única: decodifica o áudio do MKV UMA ÚNICA VEZ e alimenta
        simultaneamente os blocos 16 kHz mono do Whisper ([disc_wh]) e a Faixa 1 YouTube Gold
        48 kHz Estéreo ([aout]) para o remux lossless do MKV.
        """
        if self.stream_count >= 3:
            return (
                "[0:a:0]highpass=f=75,"
                "dynaudnorm=f=250:g=21:p=0.82:m=12:c=1:b=1:t=0.004,"
                "acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=9dB[a1];"
                "[0:a:2]asplit=2[disc_wh_in][disc_yt_in];"
                f"[disc_wh_in]{DISCORD_NORM_FILTER_16K}[disc_wh];"
                "[disc_yt_in]highpass=f=85,"
                "dynaudnorm=f=200:g=15:p=0.78:m=75:c=1:b=1:t=0.0008,"
                "acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=7.5dB[a3];"
                "[a1][a3]amix=inputs=2:duration=longest:normalize=0:weights=1.0 0.80,"
                "volume=5.0dB,"
                "alimiter=limit=0.87:attack=5:release=50:level=false,"
                "aformat=sample_rates=48000:channel_layouts=stereo[aout]"
            )
        return (
            "[0:a:0]asplit=2[disc_wh_in][yt_in];"
            f"[disc_wh_in]{DISCORD_NORM_FILTER_16K}[disc_wh];"
            "[yt_in]highpass=f=75,"
            "dynaudnorm=f=220:g=15:p=0.80:m=45:c=1:b=1:t=0.001,"
            "acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=8.5dB,"
            "volume=4.5dB,"
            "alimiter=limit=0.87:attack=5:release=50:level=false,"
            "aformat=sample_rates=48000:channel_layouts=stereo[aout]"
        )

    def start(self):
        self.seg_dir.mkdir(parents=True, exist_ok=True)
        # Limpar eventuais blocos parciais de execução interrompida antes de extrair novamente
        for old in self.seg_dir.glob("*.wav"):
            try:
                old.unlink()
            except Exception:
                pass
        if self.norm_output_mkv and self.norm_output_mkv.exists():
            try:
                self.norm_output_mkv.unlink()
            except Exception:
                pass

        total_str = self._fmt_hhmmss(self.total_dur)
        sp_tag = " + Normalização YouTube Gold (PASSADA ÚNICA)" if self.norm_output_mkv else ""
        print(
            f"[FFMPEG] Extração paralela{sp_tag} iniciada -> {self.seg_dir} | "
            f"{self.stream_count} faixa(s) | Duração: {total_str} | Blocos de {self.segment_sec}s",
            flush=True
        )

        seg_opts = ["-f", "segment", "-segment_time", str(self.segment_sec), "-reset_timestamps", "1"]
        cmd = ["ffmpeg", "-y", "-i", str(self.mkv_path)]

        if self.norm_output_mkv:
            # PASSADA ÚNICA: extrai blocos WAV no SSD + gera MKV normalizado lossless na mesma leitura!
            cmd.extend(["-filter_complex", self._build_single_pass_filter_complex()])
            if self.stream_count >= 2:
                if self.stream_count == 2:
                    print("[FFMPEG] Aviso: gravação antiga com 2 faixas (0:a:1 mic, 0:a:0 mix/Discord).", flush=True)
                cmd.extend([
                    "-map", "0:a:1", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                    *seg_opts, str(self.seg_dir / "mic_%04d.wav"),
                ])
            else:
                print("[FFMPEG] Aviso: gravação com apenas 1 faixa (0:a:0). Usando diarização por voz.", flush=True)

            cmd.extend([
                "-map", "[disc_wh]", "-vn", "-c:a", "pcm_s16le",
                *seg_opts, str(self.seg_dir / "disc_%04d.wav"),
            ])
            if self.has_soundpad:
                cmd.extend([
                    "-map", "0:a:5", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                    *seg_opts, str(self.seg_dir / "sp_%04d.wav"),
                ])

            # Saída MKV normalizada (vídeo 100% lossless -c:v copy + Faixa 1 YouTube Gold + Faixas 2..N copy)
            cmd.extend([
                "-map", "0:v:0",
                "-map", "[aout]",
            ])
            for a_idx in range(1, self.stream_count):
                cmd.extend(["-map", f"0:a:{a_idx}"])
            cmd.extend([
                "-map_metadata", "0",
                "-map_chapters", "0",
                "-c:v", "copy",
                "-c:a:0", "aac", "-b:a:0", "320k",
            ])
            for a_idx in range(1, self.stream_count):
                cmd.extend([f"-c:a:{a_idx}", "copy"])
            cmd.extend([
                "-disposition:a:0", "default",
                "-metadata", f"BANIDO_AUDIO_NORM={YOUTUBE_AUDIO_NORM_TAG}",
                "-metadata:s:a:0", "title=Track1 (YouTube Gold)",
                "-metadata:s:a:0", f"BANIDO_AUDIO_NORM={YOUTUBE_AUDIO_NORM_TAG}",
                str(self.norm_output_mkv)
            ])
        elif self.stream_count >= 3:
            cmd.extend([
                "-map", "0:a:1", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                *seg_opts, str(self.seg_dir / "mic_%04d.wav"),
                "-map", "0:a:2", "-vn", "-af", DISCORD_NORM_FILTER_16K, "-c:a", "pcm_s16le",
                *seg_opts, str(self.seg_dir / "disc_%04d.wav"),
            ])
            if self.has_soundpad:
                cmd.extend([
                    "-map", "0:a:5", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                    *seg_opts, str(self.seg_dir / "sp_%04d.wav"),
                ])
        elif self.stream_count == 2:
            print("[FFMPEG] Aviso: gravação antiga com 2 faixas (0:a:1 mic, 0:a:0 mix/Discord).", flush=True)
            cmd.extend([
                "-map", "0:a:1", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                *seg_opts, str(self.seg_dir / "mic_%04d.wav"),
                "-map", "0:a:0", "-vn", "-af", DISCORD_NORM_FILTER_16K, "-c:a", "pcm_s16le",
                *seg_opts, str(self.seg_dir / "disc_%04d.wav"),
            ])
        else:
            print("[FFMPEG] Aviso: gravação com apenas 1 faixa (0:a:0). Usando diarização por voz.", flush=True)
            cmd.extend([
                "-map", "0:a:0", "-vn", "-af", DISCORD_NORM_FILTER_16K, "-c:a", "pcm_s16le",
                *seg_opts, str(self.seg_dir / "disc_%04d.wav"),
            ])

        cmd.extend(["-progress", "pipe:1", "-nostats"])
        self.t_start = time.time()
        err_fh = open(self.stderr_log, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_fh, text=True, bufsize=1)

        def _monitor():
            last_log_t = 0.0
            try:
                if self.proc and self.proc.stdout:
                    for line in self.proc.stdout:
                        line = line.strip()
                        if line.startswith("speed="):
                            self.current_speed = line.split("=", 1)[1].strip()
                        elif line.startswith("out_time="):
                            curr_str = line.split("=", 1)[1][:8]
                            self.current_sec = parse_time_to_seconds(curr_str)
                            now_t = time.time()
                            if (now_t - last_log_t) >= 15.0 and self.total_dur > 0:
                                last_log_t = now_t
                                pct = min(100, int((self.current_sec / self.total_dur) * 100))
                                spd = f" | Velocidade: {self.current_speed}" if self.current_speed else ""
                                print(
                                    f"[FFMPEG] Progresso de áudio (SSD): {curr_str} / {total_str} ({pct}%){spd}",
                                    flush=True
                                )
                if self.proc:
                    self.returncode = self.proc.wait()
            finally:
                try:
                    err_fh.close()
                except Exception:
                    pass
                if self.returncode not in (0, None) and self.stderr_log.exists():
                    try:
                        lines = self.stderr_log.read_text(encoding="utf-8", errors="replace").splitlines()
                        self.error_tail = "\n".join(lines[-15:])
                    except Exception:
                        pass
                elif self.returncode == 0:
                    elapsed = time.time() - self.t_start
                    spd_real = self.total_dur / max(0.1, elapsed)
                    sp_done = " + MKV YouTube Gold pronto p/ troca instantânea!" if self.norm_output_mkv else ""
                    print(
                        f"[FFMPEG] ✅ Extração segmentada 100% concluída em {elapsed:.1f}s ({spd_real:.1f}x tempo real){sp_done}",
                        flush=True
                    )
                self.done_event.set()

        self._reader_thread = threading.Thread(target=_monitor, daemon=True)
        self._reader_thread.start()

    def check_error(self):
        if self.done_event.is_set() and self.returncode not in (0, None):
            raise RuntimeError(
                f"FFmpeg falhou ao extrair faixas (código {self.returncode}): "
                f"{self.error_tail[-300:] if self.error_tail else self.mkv_path.name}"
            )

    def is_chunk_ready(self, idx: int) -> bool:
        """Retorna True somente quando o bloco idx está 100% fechado em TODOS os streams ativos."""
        disc_cur = self.seg_dir / f"disc_{idx:04d}.wav"
        if not disc_cur.exists():
            return False
        if self.done_event.is_set():
            self.check_error()
            return True
        # Só libera bloco idx depois que idx+1 foi aberto em todos os streams (garante flush/close de idx)
        nxt = idx + 1
        for pref in self.active_prefixes:
            if not (self.seg_dir / f"{pref}_{nxt:04d}.wav").exists():
                return False
        return True

    def ready_chunk_count(self) -> int:
        count = 0
        while self.is_chunk_ready(count):
            count += 1
        return count

    def wait_for_prebuffer(
        self,
        min_chunks: int = 3,
        progress_callback: Optional[Callable[[str, float], None]] = None
    ):
        """
        Aguarda o pré-buffer de segurança (padrão: 3 blocos = 15 minutos de áudio já extraídos
        e fechados no SSD) — ou o término completo do FFmpeg em vídeos menores que 15 min.
        """
        total_str = self._fmt_hhmmss(self.total_dur)
        est_total_chunks = max(1, int(np.ceil(self.total_dur / float(self.segment_sec))))
        target = min(min_chunks, est_total_chunks)
        print(
            f"[PIPELINE] Aguardando pré-buffer de segurança ({target} bloco(s) = ~{target * self.segment_sec // 60} min no SSD) "
            f"antes de acionar o Whisper...",
            flush=True
        )
        last_cb = 0.0
        while True:
            self.check_error()
            ready = self.ready_chunk_count()
            if self.done_event.is_set():
                print(f"[PIPELINE] FFmpeg concluiu o vídeo inteiro ({ready} bloco(s)) antes do Whisper iniciar!", flush=True)
                return
            if ready >= target:
                print(
                    f"[PIPELINE] ✅ Pré-buffer atingido ({ready} bloco(s) = ~{ready * self.segment_sec // 60} min prontos no SSD | "
                    f"FFmpeg segue em paralelo a {self.current_speed or '...'})! Acionando Whisper...",
                    flush=True
                )
                return
            now_t = time.time()
            if progress_callback and (now_t - last_cb) >= 1.5:
                last_cb = now_t
                curr_str = self._fmt_hhmmss(self.current_sec)
                spd = f" ({self.current_speed})" if self.current_speed else ""
                pct = 0.06 + min(0.24, (ready / max(1, target)) * 0.14 + (self.current_sec / max(1.0, self.total_dur)) * 0.10)
                progress_callback(
                    f"Pré-buffer de áudio no SSD ({ready}/{target} blocos prontos) [{curr_str} / {total_str}]{spd}...",
                    pct
                )
            time.sleep(0.25)

    def wait_for_chunk(self, idx: int) -> bool:
        """
        Aguarda até o bloco idx estar 100% fechado. Retorna False quando idx ultrapassa o fim do vídeo.
        """
        waited_logged = False
        while True:
            self.check_error()
            if self.is_chunk_ready(idx):
                return True
            if self.done_event.is_set():
                # FFmpeg finalizou e não existe bloco idx -> fim do vídeo!
                return False
            if not waited_logged:
                waited_logged = True
                print(f"[PIPELINE] Aguardando FFmpeg fechar bloco #{idx + 1} no SSD...", flush=True)
            time.sleep(0.25)

    def wait_finished(self):
        self.done_event.wait()
        self.check_error()

    def terminate(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if self.norm_output_mkv and self.norm_output_mkv.exists():
            if not self.done_event.is_set() or self.returncode != 0:
                try:
                    self.norm_output_mkv.unlink()
                except Exception:
                    pass


def analyze_segmented_hype(
    seg_dir: Path,
    num_chunks: int,
    chunk_offsets: List[float],
    has_isolated_mic: bool = True,
    has_soundpad: bool = False,
    sample_rate: int = 16000
) -> List[Dict[str, Any]]:
    """
    Calcula hype_scores global lendo os blocos segmentados do SSD 1 bloco por vez
    (RAM constante ~20 MB mesmo em vídeos de 10h, com referência adaptativa global!).
    """
    raw_windows = []
    block_m_p90s = []
    block_d_p90s = []
    window_5s = int(sample_rate * 5.0)
    frames_per_5s = 50
    total_samples_read = 0

    for idx in range(num_chunks):
        disc_p = seg_dir / f"disc_{idx:04d}.wav"
        mic_p = seg_dir / f"mic_{idx:04d}.wav"
        sp_p = seg_dir / f"sp_{idx:04d}.wav"
        if not disc_p.exists():
            continue

        d_data, _ = sf.read(str(disc_p), dtype="float32")
        if d_data.ndim > 1:
            d_data = d_data.mean(axis=1)

        if has_isolated_mic and mic_p.exists():
            m_data, _ = sf.read(str(mic_p), dtype="float32")
            if m_data.ndim > 1:
                m_data = m_data.mean(axis=1)
            min_len = min(len(m_data), len(d_data))
            m_data = m_data[:min_len]
            d_data = d_data[:min_len]
        else:
            m_data = None
            min_len = len(d_data)

        sp_data = None
        if has_soundpad and sp_p.exists():
            sp_data, _ = sf.read(str(sp_p), dtype="float32")
            if sp_data.ndim > 1:
                sp_data = sp_data.mean(axis=1)
            sp_data = sp_data[:min_len]

        total_samples_read += min_len
        d_abs = np.abs(d_data)
        d_act = d_abs[d_abs > 0.004]
        if len(d_act) > 200:
            block_d_p90s.append(float(np.percentile(d_act, 90)))

        d_rms = compute_rms_100ms(d_data, sample_rate)
        if m_data is not None:
            m_abs = np.abs(m_data)
            m_act = m_abs[m_abs > 0.004]
            if len(m_act) > 200:
                block_m_p90s.append(float(np.percentile(m_act, 90)))
            m_rms = compute_rms_100ms(m_data, sample_rate)
        else:
            m_abs = None
            m_rms = None

        base_t = chunk_offsets[idx] if idx < len(chunk_offsets) else (idx * 300.0)
        num_win = min_len // window_5s
        for w in range(num_win):
            s = w * window_5s
            e = s + window_5s
            d_sub = d_abs[s:e]
            d_p98 = float(np.percentile(d_sub, 98))
            d_chunks = np.mean(d_sub.reshape(20, window_5s // 20), axis=1)
            d_burst_raw = float(np.std(d_chunks))

            m_p98 = 0.0
            m_burst_raw = 0.0
            both_active = False
            if m_abs is not None and m_rms is not None:
                m_sub = m_abs[s:e]
                m_p98 = float(np.percentile(m_sub, 98))
                m_chunks = np.mean(m_sub.reshape(20, window_5s // 20), axis=1)
                m_burst_raw = float(np.std(m_chunks))
                fs = w * frames_per_5s
                fe = min(len(m_rms), fs + frames_per_5s)
                if fe > fs:
                    if float(np.percentile(m_rms[fs:fe], 90)) > -45.0 and float(np.percentile(d_rms[fs:fe], 90)) > -45.0:
                        both_active = True

            sp_hit = False
            if sp_data is not None and len(sp_data) >= e:
                if float(np.max(np.abs(sp_data[s:e]))) > 0.015:
                    sp_hit = True

            t_start = base_t + w * 5.0
            raw_windows.append((t_start, t_start + 5.0, m_p98, d_p98, m_burst_raw, d_burst_raw, both_active, sp_hit))

    m_ref = max(0.015, float(np.median(block_m_p90s)) if block_m_p90s else 0.08)
    d_ref = max(0.015, float(np.median(block_d_p90s)) if block_d_p90s else 0.08)

    hype_scores = []
    high_hype_count = 0
    for t_start, t_end, m_p98, d_p98, m_burst_raw, d_burst_raw, both_active, sp_hit in raw_windows:
        peak_score = min(40.0, max(m_p98 / m_ref, d_p98 / d_ref) * 22.0)
        burst_score = min(30.0, max(m_burst_raw / m_ref, d_burst_raw / d_ref) * 45.0)
        chaos_score = 20 if both_active else 0
        sp_score = 15 if sp_hit else 0
        total_score = int(min(100, peak_score + burst_score + chaos_score + sp_score))
        if total_score >= 75:
            high_hype_count += 1
        hype_scores.append({
            "start": round(t_start, 2),
            "end": round(t_end, 2),
            "score": total_score,
            "soundpad": sp_hit,
            "is_chaos": both_active
        })

    print(
        f"[TELEMETRIA] Dinâmica segmentada analisada em {total_samples_read / sample_rate:.1f}s de áudio (SSD) | "
        f"Picos de risada/hype (>=75): {high_hype_count}",
        flush=True
    )
    return hype_scores
