"""F8: alerta de espaço em disco antes de processar. Roda sem GPU, sem modelos e sem rede."""
from collections import namedtuple

import pytest

import app.disk_space as ds

Uso = namedtuple("Uso", "total used free")
GB = ds.GB


def _livre(monkeypatch, livre_bytes):
    monkeypatch.setattr(ds.shutil, "disk_usage", lambda p: Uso(10**13, 0, livre_bytes))


def _video(horas=2.0, gb=40.0):
    return {"duration_sec": horas * 3600, "size_gb": gb, "file_size_bytes": int(gb * GB)}


def test_estimativa_wav_e_mkv_normalizado():
    n = ds.estimate_process_needs(3600, 10 * GB, normalize_audio=True)
    assert n["temp"] == 3600 * 16000 * 2 * ds.WAV_TRACKS_WORST_CASE
    assert n["recordings"] == pytest.approx(10 * GB * 1.02)
    assert ds.estimate_process_needs(3600, 10 * GB, False)["recordings"] == 0


def test_duracao_desconhecida_usa_o_tamanho():
    n = ds.estimate_process_needs(None, 6_250_000 * 100, False)
    assert n["temp"] == pytest.approx(100 * 16000 * 2 * ds.WAV_TRACKS_WORST_CASE)


def test_margem_minima_5gb_ou_10_por_cento():
    assert ds.safety_margin(1 * GB) == 5 * GB
    assert ds.safety_margin(100 * GB) == pytest.approx(10 * GB)


def test_espaco_de_sobra_nao_avisa(monkeypatch, tmp_path):
    _livre(monkeypatch, 500 * GB)
    r = ds.check_process_space(_video(), True, tmp_path / "tmp", tmp_path / "obs")
    assert r["level"] == ds.OK and r["message"] == ""


def test_espaco_apertado_gera_aviso(monkeypatch, tmp_path):
    video = _video(horas=1, gb=10)
    needed = ds.estimate_process_needs(3600, 10 * GB, False)["temp"]
    _livre(monkeypatch, int(needed + 1 * GB))  # cabe, mas sem a margem de 5 GB
    r = ds.check_process_space(video, False, tmp_path / "tmp", tmp_path / "obs")
    assert r["level"] == ds.WARNING
    assert "Espaço apertado" in r["message"] and "GB" in r["message"]


def test_espaco_insuficiente_bloqueia(monkeypatch, tmp_path):
    _livre(monkeypatch, 1 * GB)
    r = ds.check_process_space(_video(gb=40), True, tmp_path / "tmp", tmp_path / "obs")
    assert r["level"] == ds.BLOCKED
    assert "Espaço insuficiente" in r["message"]


def test_pastas_no_mesmo_disco_somam_as_necessidades(monkeypatch, tmp_path):
    # Mesmo disco: temp + MKV normalizado precisam caber juntos.
    video = _video(horas=1, gb=30)
    n = ds.estimate_process_needs(3600, 30 * GB, True)
    _livre(monkeypatch, int(n["recordings"] + 100))  # só o MKV caberia sozinho
    r = ds.check_process_space(video, True, tmp_path / "tmp", tmp_path / "obs")
    assert len(r["items"]) == 1
    assert r["level"] == ds.BLOCKED


def test_sem_conseguir_medir_nao_bloqueia(monkeypatch, tmp_path):
    def falha(p):
        raise OSError("sem acesso")
    monkeypatch.setattr(ds.shutil, "disk_usage", falha)
    r = ds.check_process_space(_video(), True, tmp_path / "tmp", tmp_path / "obs")
    assert r["level"] == ds.OK


def test_iniciar_processamento_responde_507_quando_falta_espaco(monkeypatch, tmp_path):
    from fastapi import HTTPException
    import app.worker_control as wc

    monkeypatch.setattr(wc, "reconcile_worker_processes", lambda: None)
    monkeypatch.setattr(wc, "get_video_by_id", lambda vid, include_data=True: {"id": vid, "filename": "a.mkv", **_video(gb=40)})
    monkeypatch.setattr(wc, "create_job_record", lambda **k: pytest.fail("não deveria criar job sem espaço"))
    monkeypatch.setattr(ds, "free_bytes", lambda p: 1 * GB)
    with pytest.raises(HTTPException) as e:
        wc.launch_worker_subprocess(7, "full", "", "gemini", "", normalize_audio=True)
    assert e.value.status_code == 507
    assert "Espaço insuficiente" in e.value.detail
