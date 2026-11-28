"""B6: sugestão de miniatura."""
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from app import thumbnails as th


def test_candidatos_nos_picos_afastados_entre_si():
    d = {"hype_scores": [
        {"start": 100, "end": 105, "score": 95}, {"start": 110, "end": 115, "score": 90},   # perto do primeiro
        {"start": 500, "end": 505, "score": 80}, {"start": 900, "end": 905, "score": 60}], "highlights": []}
    t = th.candidate_times(d, 2000)
    assert 102.5 in t and 502.5 in t and 112.5 not in t and 902.5 not in t


def test_sem_telemetria_usa_melhores_momentos_e_depois_fracoes():
    d = {"hype_scores": [], "highlights": [{"start": 100, "end": 140, "score": 90}]}
    t = th.candidate_times(d, 1000)
    assert 120 in t and len(t) >= 5
    assert th.candidate_times({}, 0) == []


def _img(kind):
    im = Image.new("RGB", (320, 180), (120, 120, 120))
    d = ImageDraw.Draw(im)
    if kind == "nitido":
        for x in range(0, 320, 8):
            d.rectangle([x, 0, x + 3, 179], fill=(250, 250, 250) if (x // 8) % 2 else (10, 10, 10))
    elif kind == "preto":
        im = Image.new("RGB", (320, 180), (2, 2, 2))
    elif kind == "liso":
        pass
    return im


def test_qualidade_rejeita_escuro_e_tela_lisa_e_aceita_nitido():
    assert th.is_usable(th.frame_quality(_img("nitido")))
    assert not th.is_usable(th.frame_quality(_img("preto")))
    assert not th.is_usable(th.frame_quality(_img("liso")))


def test_pick_best_mais_nitidos_e_nunca_vazio():
    bons = [(0, {"brightness": 100, "contrast": 50, "sharpness": s}) for s in (50, 10, 90, 30, 70, 20, 40)]
    assert th.pick_best(bons) == [2, 4, 0, 6, 3]
    ruins = [(0, {"brightness": 1, "contrast": 1, "sharpness": s}) for s in (1, 2, 3, 4)]
    assert len(th.pick_best(ruins)) == th.MIN_PICK


def test_texto_curto_sem_colchetes():
    assert th.clean_thumbnail_text("1. [Que isso] gente, olha só isso aqui!") == "Que isso gente, olha"
    assert th.clean_thumbnail_text("") == ""
    out = th.suggest_thumbnail_texts({"main_game": "GTA V"}, lambda s, u, t: "NÃO ACREDITO\n- Deu ruim demais\nnão acredito\nterceiro texto")
    assert out == ["NÃO ACREDITO", "Deu ruim demais"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")
def test_gera_miniaturas_em_video_sintetico(tmp_path):
    video = tmp_path / "v.mkv"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=60",
                    "-c:v", "libx264", "-g", "10", str(video)], check=True)
    d = {"hype_scores": [{"start": 10, "end": 15, "score": 90}, {"start": 40, "end": 45, "score": 85}], "highlights": []}
    out = th.generate_thumbnails(video, tmp_path / "m", 3, d, 60.0)
    assert 3 <= len(out) <= 5
    for f in out:
        with Image.open(tmp_path / "m" / f["name"]) as im:
            assert im.size == (1280, 720)
    assert not list((tmp_path / "m").glob("temp_thumb_*"))
