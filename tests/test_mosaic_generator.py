import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app import mosaic_generator as mg


def test_batch_command_abre_o_video_uma_vez_por_quadro_sem_decodificar_tudo(tmp_path):
    ts = [10.0, 20.5, 30.0]
    frames = [tmp_path / f"f{i}.jpg" for i in range(3)]
    cmd = mg.build_batch_command(Path("v.mkv"), ts, frames, 560, 315, use_cuda=True)
    assert cmd[0] == "ffmpeg"
    assert cmd.count("-i") == 3
    assert cmd.count("-ss") == 3
    assert cmd.count("-hwaccel") == 3
    assert "-ss" in cmd and cmd.index("-ss") < cmd.index("-i")  # salto rápido
    assert "20.50" in cmd
    assert all(str(f) in cmd for f in frames)
    assert "-hwaccel" not in mg.build_batch_command(Path("v.mkv"), ts, frames, 560, 315, use_cuda=False)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg ausente")
def test_mosaico_completo_em_video_sintetico(tmp_path):
    video = tmp_path / "teste.mkv"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=40",
         "-c:v", "libx264", "-g", "10", str(video)],
        capture_output=True, check=True,
    )
    out = tmp_path / "mosaico.jpg"
    assert mg.generate_video_mosaic(video, out) is True
    with Image.open(out) as img:
        assert img.size == (2240, 1260)
    assert not (tmp_path / "temp_mosaic_mosaico").exists()
