"""C1: prévia leve (proxy) de um trecho do vídeo para o usuário conferir o corte antes de exportar.

Analogia: é provar a roupa antes de comprar. O arquivo original é só lido; a prévia é uma cópia
pequena (360p) gerada sob demanda em ``TEMP_DIR/previews`` e apagada automaticamente quando o
limite de espaço da pasta é atingido (``security.prune_directory``).
"""
import subprocess
from pathlib import Path

MAX_PREVIEW_SEC = 120.0   # trecho máximo por prévia
MIN_PREVIEW_SEC = 2.0
PREVIEW_HEIGHT = 360


def clamp_window(start: float, end: float, total_duration: float = 0.0):
    """Normaliza o trecho pedido: início >= 0, duração entre 2 s e 120 s, fim dentro do vídeo."""
    s = max(0.0, float(start))
    e = float(end)
    if total_duration and total_duration > 0:
        s = min(s, max(0.0, total_duration - MIN_PREVIEW_SEC))
        e = min(e, total_duration)
    e = max(s + MIN_PREVIEW_SEC, min(s + MAX_PREVIEW_SEC, e))
    return s, e


def build_preview_proxy(mkv_path: Path, start: float, end: float, out_mp4: Path) -> bool:
    """Gera um MP4 pequeno do trecho [start, end]. Lê o original sem alterá-lo."""
    if not Path(mkv_path).exists():
        return False
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_mp4.with_suffix(".part.mp4")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(mkv_path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale=-2:{PREVIEW_HEIGHT}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-movflags", "+faststart", "-f", "mp4", str(tmp),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=600)
        tmp.replace(out_mp4)
        return out_mp4.stat().st_size > 1000
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
