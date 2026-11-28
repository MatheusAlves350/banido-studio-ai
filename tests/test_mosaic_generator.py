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
    assert cmd.count("-an") == 3
    assert cmd.count("-sn") == 3
    assert cmd.count("-dn") == 3
    assert "-ss" in cmd and cmd.index("-ss") < cmd.index("-i")  # salto rápido
    assert "20.50" in cmd
    assert all(str(f) in cmd for f in frames)
    assert "-hwaccel" not in mg.build_batch_command(Path("v.mkv"), ts, frames, 560, 315, use_cuda=False)


def test_extract_frames_fallback_cuda_para_cpu_loga_motivo(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_run(cmd, capture_output=True, check=False):
        calls.append(cmd)
        if "-hwaccel" in cmd:
            return subprocess.CompletedProcess(cmd, 1, b"", b"CUDA_ERROR_OUT_OF_MEMORY\nCannot load cuvid\n")
        for arg in cmd:
            if arg.endswith(".jpg"):
                Path(arg).write_bytes(b"x" * 2048)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(mg.subprocess, "run", fake_run)
    frames, used_cuda = mg.extract_frames(Path("v.mkv"), [10.0, 20.0], tmp_path, 560, 315)

    assert used_cuda is False
    assert len(calls) == 2
    assert all(f is not None and f.exists() for f in frames)
    out = capsys.readouterr().out
    assert "lote CUDA incompleto/falhou" in out
    assert "CUDA_ERROR_OUT_OF_MEMORY" in out


def test_extract_frames_preserva_quadros_validos_em_falha_parcial(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, capture_output=True, check=False):
        calls.append(cmd)
        jpgs = [Path(arg) for arg in cmd if arg.endswith(".jpg")]
        if "-hwaccel" in cmd:
            # Simula lote CUDA gerando apenas 2 dos 3 quadros
            jpgs[0].write_bytes(b"a" * 2048)
            jpgs[2].write_bytes(b"c" * 2048)
            return subprocess.CompletedProcess(cmd, 1, b"", b"partial error\n")
        # Lote CPU deve receber apenas o quadro faltante (frame_1.jpg)
        for p in jpgs:
            p.write_bytes(b"b" * 2048)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(mg.subprocess, "run", fake_run)
    frames, used_cuda = mg.extract_frames(Path("v.mkv"), [10.0, 20.0, 30.0], tmp_path, 560, 315)

    assert used_cuda is False
    assert len(calls) == 2
    assert calls[0].count("-i") == 3
    assert calls[1].count("-i") == 1
    assert "20.00" in calls[1]
    assert frames[0].read_bytes() == b"a" * 2048
    assert frames[1].read_bytes() == b"b" * 2048
    assert frames[2].read_bytes() == b"c" * 2048


def test_extract_frames_fallback_quadro_a_quadro_apenas_para_faltantes(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_run(cmd, capture_output=True, check=False):
        calls.append(cmd)
        jpgs = [Path(arg) for arg in cmd if arg.endswith(".jpg")]
        if len(calls) == 1:
            # Lote CUDA gera só o primeiro quadro
            jpgs[0].write_bytes(b"a" * 2048)
            return subprocess.CompletedProcess(cmd, 1, b"", b"cuda batch failed\n")
        if len(calls) == 2:
            # Lote CPU falha para o quadro restante
            return subprocess.CompletedProcess(cmd, 1, b"", b"cpu batch failed\n")
        if "-hwaccel" in cmd:
            # Fallback individual CUDA falha
            return subprocess.CompletedProcess(cmd, 1, b"", b"single cuda failed\n")
        # Fallback individual CPU extrai o quadro faltante
        jpgs[0].write_bytes(b"b" * 2048)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(mg.subprocess, "run", fake_run)
    frames, used_cuda = mg.extract_frames(Path("v.mkv"), [10.0, 20.0], tmp_path, 560, 315)

    assert used_cuda is False
    assert len(calls) == 4  # batch CUDA, batch CPU (1 frame), single CUDA (1 frame), single CPU (1 frame)
    assert calls[2].count("-i") == 1 and "20.00" in calls[2]
    assert calls[3].count("-i") == 1 and "20.00" in calls[3]
    assert frames[0].read_bytes() == b"a" * 2048
    assert frames[1].read_bytes() == b"b" * 2048
    out = capsys.readouterr().out
    assert "extração quadro a quadro" in out


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
