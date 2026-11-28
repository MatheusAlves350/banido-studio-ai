"""C1: prévia leve do trecho."""
import shutil
import subprocess

import pytest

from app.clip_preview import MAX_PREVIEW_SEC, build_preview_proxy, clamp_window


def test_janela_respeita_limites():
    assert clamp_window(-5, 3) == (0.0, 3.0)
    s, e = clamp_window(10, 11)           # mínimo de 2 s
    assert (s, e) == (10.0, 12.0)
    s, e = clamp_window(0, 1000)          # máximo de 2 min
    assert e - s == MAX_PREVIEW_SEC
    s, e = clamp_window(95, 200, total_duration=100)  # não passa do fim do vídeo
    assert e <= 100 and e - s >= 2


def test_original_inexistente(tmp_path):
    assert build_preview_proxy(tmp_path / "x.mkv", 0, 5, tmp_path / "p.mp4") is False


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")
def test_gera_proxy_menor_sem_alterar_original(tmp_path):
    src = tmp_path / "src.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=10:duration=10",
         "-f", "lavfi", "-i", "sine=duration=10", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
    antes = src.read_bytes()
    out = tmp_path / "previews" / "p.mp4"
    assert build_preview_proxy(src, 2, 6, out)
    assert out.stat().st_size > 1000
    assert src.read_bytes() == antes
    assert not list(out.parent.glob("*.part.mp4"))
