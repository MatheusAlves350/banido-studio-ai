"""Mosaicos adicionais para sessões longas (B8): um por hora e um com as mudanças de cena.

Analogia: 16 fotos para um dia inteiro de viagem deixam de fora a troca de cidade. Um álbum por hora,
mais um álbum só com os momentos em que a paisagem mudou, mostra melhor a história.

O mosaico principal ({id}_mosaic.jpg, 16 quadros na sessão inteira) continua igual. Os extras são
{id}_mosaic_h01.jpg, _h02.jpg... (um por hora, só em vídeos com mais de 1 hora) e {id}_mosaic_scenes.jpg.
"""
import os
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageChops, ImageStat

from app.mosaic_generator import extract_frames, generate_video_mosaic, get_video_duration

FRAMES_PER_MOSAIC = 16
HOUR = 3600.0
MIN_WINDOW_SEC = 600.0           # não faz mosaico de uma "hora" com menos de 10 minutos
SCENE_SAMPLE_STEP = 300.0        # uma amostra a cada 5 minutos (mínimo)
SCENE_MAX_SAMPLES = 48
SCENE_MIN_FRAMES = 4             # menos que isso não vale um mosaico
SCENE_DEFAULT_THRESHOLD = 0.25   # diferença média (0 a 1) entre amostras vizinhas; NÃO validado com gravações reais
DEFAULT_MAX_MOSAICS = 12


def get_max_mosaics() -> int:
    try:
        value = int(os.getenv("BANIDO_MAX_MOSAICS", "").strip())
        if 1 <= value <= 30:
            return value
    except ValueError:
        pass
    return DEFAULT_MAX_MOSAICS


def get_scene_threshold() -> float:
    try:
        value = float(os.getenv("BANIDO_SCENE_THRESHOLD", "").strip().replace(",", "."))
        if 0.02 <= value <= 0.9:
            return value
    except ValueError:
        pass
    return SCENE_DEFAULT_THRESHOLD


def plan_hourly_windows(duration: float, max_windows: int) -> List[Tuple[float, float]]:
    """Janelas de uma hora (início, fim). Vídeo de até 1 hora não ganha mosaicos por hora."""
    if duration <= HOUR or max_windows <= 0:
        return []
    windows = []
    start = 0.0
    while start < duration and len(windows) < max_windows:
        end = min(start + HOUR, duration)
        if end - start >= MIN_WINDOW_SEC:
            windows.append((start, end))
        start += HOUR
    return windows


def window_timestamps(start: float, end: float, n: int = FRAMES_PER_MOSAIC) -> List[float]:
    """n instantes entre 3% e 97% da janela (mesma regra do mosaico principal)."""
    length = end - start
    return [start + length * (0.03 + 0.94 * (i / max(1, n - 1))) for i in range(n)]


def pick_scene_times(samples: List[Tuple[float, float]], threshold: float, limit: int = FRAMES_PER_MOSAIC) -> List[float]:
    """
    `samples` = [(instante, diferença para a amostra anterior)]. Devolve até `limit` instantes cuja
    diferença passa do limiar (os maiores saltos), em ordem cronológica.
    """
    strong = [(t, d) for t, d in samples if d >= threshold]
    strong.sort(key=lambda x: x[1], reverse=True)
    return sorted(t for t, _ in strong[:limit])


def _frame_difference(a: Path, b: Path) -> float:
    with Image.open(a) as ia, Image.open(b) as ib:
        ga, gb = ia.convert("L"), ib.convert("L")
        return ImageStat.Stat(ImageChops.difference(ga, gb)).mean[0] / 255.0


def detect_scene_changes(video_path: Path, duration: float, work_dir: Path) -> List[Tuple[float, float]]:
    """
    Amostra quadros pequenos (64x36) em intervalos regulares, por saltos do FFmpeg (sem decodificar o
    vídeo inteiro), e mede a diferença entre amostras vizinhas. Devolve [(instante, diferença)].
    """
    step = max(SCENE_SAMPLE_STEP, duration / SCENE_MAX_SAMPLES)
    times = []
    t = step
    while t < duration:
        times.append(t)
        t += step
    if len(times) < 2:
        return []

    work_dir.mkdir(parents=True, exist_ok=True)
    frames: List[Optional[Path]] = []
    for i in range(0, len(times), FRAMES_PER_MOSAIC):
        batch_dir = work_dir / f"b{i}"
        batch_dir.mkdir(exist_ok=True)
        got, _ = extract_frames(video_path, times[i:i + FRAMES_PER_MOSAIC], batch_dir, 64, 36)
        frames.extend(got or [None] * len(times[i:i + FRAMES_PER_MOSAIC]))

    samples = []
    for k in range(1, len(times)):
        if frames[k] and frames[k - 1] and frames[k].exists() and frames[k - 1].exists():
            samples.append((times[k], _frame_difference(frames[k - 1], frames[k])))
    return samples


def ensure_extra_mosaics(video_path: Path, mosaics_dir: Path, video_id: int, duration: Optional[float] = None) -> List[Path]:
    """
    Gera (ou reaproveita) os mosaicos extras do vídeo e devolve os caminhos existentes, em ordem.
    Nunca levanta erro: se algo falhar, a análise segue só com o mosaico principal.
    """
    try:
        duration = duration or get_video_duration(video_path)
        budget = get_max_mosaics() - 1  # o principal já conta
        out: List[Path] = []

        for n, (start, end) in enumerate(plan_hourly_windows(duration, budget), start=1):
            path = mosaics_dir / f"{video_id}_mosaic_h{n:02d}.jpg"
            if not path.exists():
                generate_video_mosaic(video_path, path, timestamps=window_timestamps(start, end))
            if path.exists():
                out.append(path)

        if len(out) < budget and duration > 2 * SCENE_SAMPLE_STEP:
            scenes = mosaics_dir / f"{video_id}_mosaic_scenes.jpg"
            if not scenes.exists():
                work = mosaics_dir / f"temp_scene_{video_id}"
                try:
                    samples = detect_scene_changes(video_path, duration, work)
                finally:
                    shutil.rmtree(work, ignore_errors=True)
                times = pick_scene_times(samples, get_scene_threshold())
                if len(times) >= SCENE_MIN_FRAMES:
                    generate_video_mosaic(video_path, scenes, timestamps=times)
            if scenes.exists():
                out.append(scenes)
        return out
    except Exception as e:
        print(f"[MOSAICO] Aviso: mosaicos adicionais não gerados ({e}). Seguindo só com o principal.", flush=True)
        return []
