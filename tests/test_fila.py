"""Item E3: fila de vários vídeos e processamento noturno (sem worker nem Windows de verdade)."""
import time
import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import queue_manager as qm
from app.database import create_job_record, finish_job_record, get_connection, save_setting
from app.main import app, API_AUTH_TOKEN


def _novo_video(tmp_path, status="unprocessed"):
    nome = f"fila_{uuid.uuid4().hex[:8]}.mkv"
    conn = get_connection()
    conn.execute("INSERT INTO videos (filename, file_path, status) VALUES (?, ?, ?)", (nome, str(tmp_path / nome), status))
    conn.commit()
    vid = conn.execute("SELECT id FROM videos WHERE filename=?", (nome,)).fetchone()[0]
    conn.close()
    return vid


@pytest.fixture(autouse=True)
def fila_limpa():
    qm.ensure_table()
    conn = get_connection()
    conn.execute("DELETE FROM process_queue")
    conn.commit()
    conn.close()
    for k in (qm.K_START_AT, qm.K_AFTER, qm.K_POWER_OK, qm.K_ACTIVE, qm.K_POWER_DEADLINE, qm.K_POWER_ACTION):
        save_setting(k, "")
    yield


class Lancador:
    """Substitui launch_worker_subprocess: cada chamada cria um job e fica 'rodando' até o teste terminar."""
    def __init__(self):
        self.chamadas = []

    def __call__(self, **kw):
        self.chamadas.append(kw)
        job = create_job_record(video_id=kw["video_id"], filename="x.mkv", engine="gemini", model="m", manual_game="")
        return {"success": True, "already_running": False, "job_id": job["job_id"]}


def _tick(lanc, ativos=(), **kw):
    avisos = kw.pop("notifier", lambda t, x: None)
    return qm.tick(launcher=lanc, active_video_ids=lambda: set(ativos), notifier=avisos, **kw)


def test_horario_vira_hoje_ou_amanha():
    agora = datetime(2026, 11, 26, 21, 0)
    assert qm.next_start_datetime("23:00", agora) == datetime(2026, 11, 26, 23, 0)
    assert qm.next_start_datetime("08:30", agora) == datetime(2026, 11, 27, 8, 30)
    with pytest.raises(HTTPException):
        qm.next_start_datetime("25:99", agora)
    with pytest.raises(HTTPException):
        qm.next_start_datetime("abc", agora)


def test_um_video_por_vez_e_na_ordem(tmp_path):
    a, b = _novo_video(tmp_path), _novo_video(tmp_path)
    qm.add_to_queue([a, b])
    lanc = Lancador()
    assert _tick(lanc) == "started"
    assert [c["video_id"] for c in lanc.chamadas] == [a]
    assert all(c["notify_end"] is False for c in lanc.chamadas)
    # enquanto o primeiro roda, a fila não começa o segundo
    assert _tick(lanc, ativos={a}) == "busy"
    assert len(lanc.chamadas) == 1


def test_falha_registrada_e_fila_segue_para_o_proximo(tmp_path):
    a, b = _novo_video(tmp_path), _novo_video(tmp_path)
    qm.add_to_queue([a, b])
    lanc = Lancador()
    _tick(lanc)
    job_a = qm.list_items()[0]["job_id"]
    finish_job_record(job_a, status="error", error_detail="CUDA out of memory")
    assert _tick(lanc) == "started"  # a terminou (falhou) -> começa b
    itens = qm.list_items()
    assert itens[0]["status"] == qm.FAILED and "CUDA" in itens[0]["error"]
    assert itens[1]["status"] == qm.RUNNING and lanc.chamadas[-1]["video_id"] == b
    snap = qm.snapshot()
    assert "placa de vídeo ficou sem memória" in snap["items"][0]["error_friendly"]


def test_horario_de_inicio_segura_a_fila(tmp_path):
    v = _novo_video(tmp_path)
    qm.add_to_queue([v], start_time="23:00", now=datetime(2026, 11, 26, 21, 0))
    lanc = Lancador()
    assert _tick(lanc, now=datetime(2026, 11, 26, 22, 59)) == "waiting_time"
    assert lanc.chamadas == []
    assert _tick(lanc, now=datetime(2026, 11, 26, 23, 0)) == "started"


def test_nao_duplica_video_na_fila(tmp_path):
    v = _novo_video(tmp_path)
    assert qm.add_to_queue([v])["added"] == [v]
    r = qm.add_to_queue([v])
    assert r["added"] == [] and r["skipped"][0]["reason"] == "já está na fila"
    em_proc = _novo_video(tmp_path, status="processing")
    assert qm.add_to_queue([em_proc])["skipped"][0]["reason"] == "já está sendo processada"


def test_reabrir_o_programa_devolve_o_que_rodava_para_a_fila(tmp_path):
    v = _novo_video(tmp_path)
    qm.add_to_queue([v])
    _tick(Lancador())
    assert qm.list_items()[0]["status"] == qm.RUNNING
    assert qm.recover_on_start() == 1
    item = qm.list_items()[0]
    assert item["status"] == qm.WAITING and item["job_id"] is None


def test_fim_da_fila_avisa_uma_vez_e_nao_desliga_por_padrao(tmp_path):
    v = _novo_video(tmp_path)
    qm.add_to_queue([v])
    lanc, avisos, poderes = Lancador(), [], []
    _tick(lanc)
    finish_job_record(qm.list_items()[0]["job_id"], status="completed")
    assert _tick(lanc, notifier=lambda t, x: avisos.append(x), power=lambda a: poderes.append(a)) == "finished"
    assert len(avisos) == 1 and "1 vídeo(s) concluído(s)" in avisos[0]
    assert qm.pending_power() is None and poderes == []
    assert _tick(lanc) == "idle"


def test_desligar_exige_confirmacao_na_primeira_vez(tmp_path):
    v = _novo_video(tmp_path)
    with pytest.raises(HTTPException) as e:
        qm.add_to_queue([v], after="shutdown")
    assert e.value.status_code == 400
    qm.add_to_queue([v], after="shutdown", confirm_power=True)
    # depois da primeira confirmação não pergunta de novo
    w = _novo_video(tmp_path)
    assert qm.add_to_queue([w], after="sleep")["added"] == [w]


def test_aviso_de_60_segundos_e_cancelamento(tmp_path):
    v = _novo_video(tmp_path)
    qm.add_to_queue([v], after="shutdown", confirm_power=True)
    lanc, poderes = Lancador(), []
    _tick(lanc)
    finish_job_record(qm.list_items()[0]["job_id"], status="completed")
    assert _tick(lanc, power=lambda a: poderes.append(a)) == "finished"
    pend = qm.pending_power()
    assert pend["action"] == "shutdown" and 55 <= pend["seconds_left"] <= 60
    assert _tick(lanc, power=lambda a: poderes.append(a)) == "waiting_power"
    assert poderes == []  # ainda dentro do prazo
    assert qm.cancel_power() is True
    assert qm.pending_power() is None
    assert _tick(lanc, power=lambda a: poderes.append(a)) == "idle"
    assert poderes == []


def test_passado_o_prazo_executa_a_acao(tmp_path):
    v = _novo_video(tmp_path)
    qm.add_to_queue([v], after="sleep", confirm_power=True)
    lanc, poderes = Lancador(), []
    _tick(lanc)
    finish_job_record(qm.list_items()[0]["job_id"], status="completed")
    _tick(lanc)
    save_setting(qm.K_POWER_DEADLINE, str(time.time() - 1))
    assert _tick(lanc, power=lambda a: poderes.append(a)) == "power"
    assert poderes == ["sleep"]
    assert qm.settings_snapshot()["after"] == "none"  # volta ao padrão: não desliga de novo


def test_trabalho_novo_durante_o_aviso_cancela_a_acao(tmp_path):
    v = _novo_video(tmp_path)
    qm.add_to_queue([v], after="shutdown", confirm_power=True)
    lanc, poderes = Lancador(), []
    _tick(lanc)
    finish_job_record(qm.list_items()[0]["job_id"], status="completed")
    _tick(lanc)
    qm.add_to_queue([_novo_video(tmp_path)])
    save_setting(qm.K_POWER_DEADLINE, str(time.time() - 1))
    assert _tick(lanc, power=lambda a: poderes.append(a)) == "busy"
    assert poderes == [] and qm.pending_power() is None


def test_falha_ao_iniciar_marca_o_item_e_segue(tmp_path):
    v = _novo_video(tmp_path)
    qm.add_to_queue([v])

    def quebra(**kw):
        raise RuntimeError("sem GPU")
    assert qm.tick(launcher=quebra, active_video_ids=lambda: set(), notifier=lambda t, x: None) == "started"
    assert qm.list_items()[0]["status"] == qm.FAILED


def test_api_da_fila(tmp_path, monkeypatch):
    client = TestClient(app, headers={"X-Banido-Token": API_AUTH_TOKEN})
    monkeypatch.setattr(qm, "tick", lambda *a, **k: "idle")
    v = _novo_video(tmp_path)
    assert client.post("/api/queue/add", json={"video_ids": []}).status_code == 400
    r = client.post("/api/queue/add", json={"video_ids": [v], "start_time": "23:00"}).json()
    assert r["added"] == [v] and r["start_at"]
    assert r["items"][0]["status"] == "waiting" and r["settings"]["start_at"] == r["start_at"]
    item_id = r["items"][0]["id"]
    assert client.post(f"/api/queue/remove/{item_id}").json()["items"] == []
    assert client.post(f"/api/queue/remove/{item_id}").status_code == 404
    assert client.post("/api/queue/add", json={"video_ids": [v], "after": "shutdown"}).status_code == 400
    assert client.post("/api/queue/cancel").json()["items"] == []
    assert client.get("/api/queue").json()["pending_power"] is None
