"""C3: fila de cortes."""
import time

import pytest

from app import cut_batch


@pytest.fixture(autouse=True)
def _limpa(monkeypatch):
    cut_batch._JOBS.clear()
    monkeypatch.setattr(cut_batch, "check_free_space", lambda *a, **k: None)
    yield
    cut_batch._JOBS.clear()


def _espera(job_id, tempo=5):
    fim = time.time() + tempo
    while time.time() < fim:
        j = cut_batch.get_job(job_id)
        if j["state"] != "running":
            return j
        time.sleep(0.02)
    raise AssertionError("fila não terminou")


def test_fila_em_ordem_cronologica_e_continua_apos_falha(monkeypatch, tmp_path):
    chamados = []

    def falso(mkv, start, end, title, **kw):
        chamados.append(title)
        if title == "ruim":
            return {"success": False, "error": "boom"}
        return {"success": True, "filename": f"{title}.mkv", "clip_path": str(tmp_path / title), "duration": end - start,
                "verification": {"status": "ok", "message": "Corte conferido."}}

    monkeypatch.setattr(cut_batch, "export_lossless_highlight", falso)
    jid = cut_batch.start_batch(1, tmp_path / "v.mkv", [
        {"start_sec": 100, "end_sec": 130, "title": "c"},
        {"start_sec": 10, "end_sec": 40, "title": "ruim"},
        {"start_sec": 50, "end_sec": 80, "title": "b"},
    ], {})
    job = _espera(jid)
    assert chamados == ["ruim", "b", "c"]  # cronológica
    assert job["state"] == "done"
    assert (job["total"], job["ok"], job["failed"], job["finished"]) == (3, 2, 1, 3)
    assert job["items"][0]["error"] == "boom"
    assert job["items"][1]["result"]["verification"]["status"] == "ok"


def test_so_uma_fila_por_vez(monkeypatch, tmp_path):
    monkeypatch.setattr(cut_batch, "export_lossless_highlight",
                        lambda *a, **k: (time.sleep(0.3), {"success": True})[1])
    cut_batch.start_batch(1, tmp_path / "v.mkv", [{"start_sec": 0, "end_sec": 5, "title": "a"}], {})
    with pytest.raises(RuntimeError):
        cut_batch.start_batch(1, tmp_path / "v.mkv", [{"start_sec": 0, "end_sec": 5, "title": "a"}], {})


def test_cancelar_pula_itens_restantes(monkeypatch, tmp_path):
    def lento(*a, **k):
        time.sleep(0.3)
        return {"success": True}

    monkeypatch.setattr(cut_batch, "export_lossless_highlight", lento)
    jid = cut_batch.start_batch(1, tmp_path / "v.mkv", [
        {"start_sec": 0, "end_sec": 5, "title": "a"}, {"start_sec": 10, "end_sec": 15, "title": "b"}], {})
    assert cut_batch.cancel_job(jid)
    job = _espera(jid)
    assert job["state"] == "cancelled"
    assert job["items"][1]["status"] == "skipped"


def test_validacoes(tmp_path):
    with pytest.raises(ValueError):
        cut_batch.start_batch(1, tmp_path / "v.mkv", [], {})
    with pytest.raises(ValueError):
        cut_batch.start_batch(1, tmp_path / "v.mkv", [{"start_sec": 9, "end_sec": 3, "title": "x"}], {})


def test_sem_espaco_nao_inicia(monkeypatch, tmp_path):
    monkeypatch.setattr(cut_batch, "check_free_space", lambda *a, **k: "Espaço insuficiente")
    with pytest.raises(RuntimeError):
        cut_batch.start_batch(1, tmp_path / "v.mkv", [{"start_sec": 0, "end_sec": 5, "title": "a"}], {})


def test_video_ocupado_no_meio_da_fila(monkeypatch, tmp_path):
    monkeypatch.setattr(cut_batch, "export_lossless_highlight", lambda *a, **k: {"success": True})

    def ocupado():
        raise RuntimeError("em processamento")

    jid = cut_batch.start_batch(1, tmp_path / "v.mkv", [{"start_sec": 0, "end_sec": 5, "title": "a"}], {}, busy_check=ocupado)
    job = _espera(jid)
    assert job["items"][0]["status"] == "error"


def test_estimativa_e_espaco():
    assert cut_batch.estimate_batch_bytes([{"start_sec": 0, "end_sec": 10}], 1000, 100) == int(10 * 10 * cut_batch.SIZE_SAFETY)
    assert cut_batch.estimate_batch_bytes([{"start_sec": 0, "end_sec": 10}], 0, 0) == 0
