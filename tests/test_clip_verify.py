"""C4: verificação automática dos cortes."""
import shutil
import subprocess

import pytest

from app import clip_verify
from app.clip_verify import evaluate_clip, verify_clip

PROBE_OK = {"duration": 30.0, "video_streams": 1, "audio_streams": 1, "video_start": 0.0, "audio_start": 0.0}


def test_corte_correto_fica_verde():
    r = evaluate_clip(PROBE_OK, 30.0, 1)
    assert r["status"] == "ok"
    assert r["message"] == "Corte conferido."


def test_diferenca_pequena_de_duracao_fica_amarela():
    r = evaluate_clip({**PROBE_OK, "duration": 32.5}, 30.0, 1)
    assert r["status"] == "warn"
    assert "2.5 segundos a mais" in r["message"]


def test_duracao_muito_diferente_fica_vermelha():
    assert evaluate_clip({**PROBE_OK, "duration": 5.0}, 30.0, 1)["status"] == "bad"
    assert evaluate_clip({**PROBE_OK, "duration": 45.0}, 30.0, 1)["status"] == "bad"


def test_faixa_de_audio_faltando_fica_vermelha():
    r = evaluate_clip(PROBE_OK, 30.0, 3)
    assert r["status"] == "bad"
    assert "3" in r["message"]


def test_sem_video_ou_ilegivel_fica_vermelho():
    assert evaluate_clip({**PROBE_OK, "video_streams": 0}, 30.0, 1)["status"] == "bad"
    assert evaluate_clip(PROBE_OK, 30.0, 1, readable=False)["status"] == "bad"


def test_dessincronia_de_inicio():
    assert evaluate_clip({**PROBE_OK, "audio_start": 0.8}, 30.0, 1)["status"] == "warn"
    assert evaluate_clip({**PROBE_OK, "audio_start": 4.0}, 30.0, 1)["status"] == "bad"


def test_arquivo_inexistente_nao_levanta_excecao(tmp_path):
    r = verify_clip(tmp_path / "nao-existe.mkv", 10.0, 1)
    assert r["status"] == "bad"


def test_manter_todas_as_faixas_depende_de_audio_esperado():
    probe = {**PROBE_OK, "audio_streams": 3}
    assert evaluate_clip(probe, 30.0, 3)["status"] == "ok"
    assert evaluate_clip(probe, 30.0, 1)["status"] == "ok"  # faixas a mais não são problema


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg ausente")
def test_verificacao_real_com_ffmpeg(tmp_path):
    clip = tmp_path / "clip.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-c:a", "aac", str(clip)],
        check=True,
    )
    assert verify_clip(clip, 4.0, 1)["status"] == "ok"
    assert verify_clip(clip, 4.0, 2)["status"] == "bad"
    assert verify_clip(clip, 6.0, 1)["status"] == "warn"

    truncado = tmp_path / "truncado.mkv"
    truncado.write_bytes(clip.read_bytes()[: clip.stat().st_size // 2])
    assert verify_clip(truncado, 4.0, 1)["status"] != "ok"


def test_cabecalho_que_promete_mais_que_o_arquivo_tem():
    r = evaluate_clip(PROBE_OK, 30.0, 1, measured_duration=8.0)
    assert r["status"] == "bad"


def test_limites_ficam_em_constantes():
    assert clip_verify.DURATION_WARN_SEC < clip_verify.DURATION_BAD_SEC


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg ausente")
def test_exportacao_real_devolve_verificacao(tmp_path, monkeypatch):
    from app import lossless_media

    monkeypatch.setattr(lossless_media, "CLIPS_DIR", tmp_path / "Cortes")
    src = tmp_path / "2026-01-01 10-00-00.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=20",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20", "-f", "lavfi", "-i", "sine=frequency=880:duration=20",
         "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-g", "10", "-c:a", "aac", str(src)],
        check=True,
    )
    res = lossless_media.export_lossless_highlight(src, 5.0, 12.0, "Teste", boost_discord=False, keep_all_tracks=True)
    assert res["success"], res
    assert res["verification"]["status"] in ("ok", "warn")
    assert res["verification"]["audio_streams"] == 2
