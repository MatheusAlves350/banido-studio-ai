"""B8: mosaicos adicionais por hora e em mudanças de cena."""
from pathlib import Path

from PIL import Image

from app import extra_mosaics as em


def test_video_de_ate_uma_hora_nao_ganha_mosaico_por_hora():
    assert em.plan_hourly_windows(3000, 11) == []
    assert em.plan_hourly_windows(3600, 11) == []


def test_janelas_por_hora_e_resto_curto_ignorado():
    assert em.plan_hourly_windows(3 * 3600 + 120, 11) == [(0, 3600), (3600, 7200), (7200, 10800)]
    assert em.plan_hourly_windows(3600 + 900, 11) == [(0, 3600), (3600, 4500)]
    assert len(em.plan_hourly_windows(20 * 3600, 5)) == 5


def test_instantes_ficam_entre_3_e_97_por_cento_da_janela():
    ts = em.window_timestamps(3600, 7200)
    assert len(ts) == 16
    assert abs(ts[0] - (3600 + 3600 * 0.03)) < 1e-6 and abs(ts[-1] - (3600 + 3600 * 0.97)) < 1e-6


def test_escolha_dos_saltos_de_cena_pega_os_maiores_em_ordem():
    amostras = [(300, 0.1), (600, 0.6), (900, 0.3), (1200, 0.9), (1500, 0.2)]
    assert em.pick_scene_times(amostras, 0.25) == [600, 900, 1200]
    assert em.pick_scene_times(amostras, 0.25, limit=2) == [600, 1200]
    assert em.pick_scene_times(amostras, 0.95) == []


def test_limites_por_env(monkeypatch):
    monkeypatch.setenv("BANIDO_MAX_MOSAICS", "5")
    monkeypatch.setenv("BANIDO_SCENE_THRESHOLD", "0,4")
    assert em.get_max_mosaics() == 5 and em.get_scene_threshold() == 0.4
    monkeypatch.setenv("BANIDO_MAX_MOSAICS", "999")
    monkeypatch.setenv("BANIDO_SCENE_THRESHOLD", "x")
    assert em.get_max_mosaics() == 12 and em.get_scene_threshold() == 0.25


def test_ensure_gera_por_hora_respeita_limite_e_reaproveita(tmp_path, monkeypatch):
    chamadas = []

    def fake_mosaic(video, out, num_cols=4, num_rows=4, timestamps=None):
        chamadas.append((out.name, len(timestamps)))
        Image.new("RGB", (8, 8)).save(out)
        return True

    monkeypatch.setattr(em, "generate_video_mosaic", fake_mosaic)
    monkeypatch.setattr(em, "detect_scene_changes", lambda *a, **k: [(300 * i, 0.5) for i in range(1, 8)])
    out = em.ensure_extra_mosaics(Path("v.mkv"), tmp_path, 7, duration=2.5 * 3600)
    nomes = [p.name for p in out]
    assert nomes == ["7_mosaic_h01.jpg", "7_mosaic_h02.jpg", "7_mosaic_h03.jpg", "7_mosaic_scenes.jpg"]
    n = len(chamadas)
    em.ensure_extra_mosaics(Path("v.mkv"), tmp_path, 7, duration=2.5 * 3600)
    assert len(chamadas) == n  # já existem: não gera de novo

    monkeypatch.setenv("BANIDO_MAX_MOSAICS", "3")
    out2 = em.ensure_extra_mosaics(Path("v.mkv"), tmp_path, 8, duration=5 * 3600)
    assert len(out2) <= 2


def test_cena_sem_saltos_suficientes_nao_gera_mosaico(tmp_path, monkeypatch):
    monkeypatch.setattr(em, "generate_video_mosaic", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria gerar")))
    monkeypatch.setattr(em, "detect_scene_changes", lambda *a, **k: [(300, 0.5), (600, 0.1)])
    assert em.ensure_extra_mosaics(Path("v.mkv"), tmp_path, 1, duration=1800) == []


def test_falha_nunca_levanta(tmp_path, monkeypatch):
    monkeypatch.setattr(em, "get_video_duration", lambda p: (_ for _ in ()).throw(RuntimeError("ffprobe")))
    assert em.ensure_extra_mosaics(Path("v.mkv"), tmp_path, 1) == []


def test_diferenca_entre_quadros(tmp_path):
    a, b, c = tmp_path / "a.jpg", tmp_path / "b.jpg", tmp_path / "c.jpg"
    Image.new("RGB", (64, 36), (0, 0, 0)).save(a)
    Image.new("RGB", (64, 36), (255, 255, 255)).save(b)
    Image.new("RGB", (64, 36), (0, 0, 0)).save(c)
    assert em._frame_difference(a, b) > 0.9
    assert em._frame_difference(a, c) < 0.01
