"""Item E4: PC acordado durante o job e avisos ao terminar."""
import pytest
from fastapi.testclient import TestClient

from app import keep_awake, notifications
from app.database import create_job_record, finish_job_record, get_setting, save_setting
from app.main import app, API_AUTH_TOKEN

WEBHOOK = "https://discord.com/api/webhooks/123456789/AbC-dEf_123"


def test_stay_awake_liga_e_libera():
    chamadas = []
    with keep_awake.stay_awake(setter=lambda f: chamadas.append(f) or 1) as ativo:
        assert ativo is True
        assert chamadas == [keep_awake.ES_CONTINUOUS | keep_awake.ES_SYSTEM_REQUIRED]
    assert chamadas[-1] == keep_awake.ES_CONTINUOUS  # só ES_CONTINUOUS: o Windows volta ao normal


def test_stay_awake_libera_mesmo_com_erro_no_job():
    chamadas = []
    with pytest.raises(RuntimeError):
        with keep_awake.stay_awake(setter=lambda f: chamadas.append(f) or 1):
            raise RuntimeError("job falhou")
    assert chamadas[-1] == keep_awake.ES_CONTINUOUS


def test_stay_awake_nao_quebra_se_o_windows_recusar():
    def recusa(_flags):
        raise OSError("sem permissão")
    with keep_awake.stay_awake(setter=recusa) as ativo:
        assert ativo is False


def test_webhook_so_aceita_links_do_discord():
    assert notifications.is_valid_webhook(WEBHOOK)
    assert not notifications.is_valid_webhook("https://exemplo.com/api/webhooks/1/abc")
    assert not notifications.is_valid_webhook("http://discord.com/api/webhooks/1/abc")
    assert not notifications.is_valid_webhook("")


def test_texto_do_aviso_de_sucesso_e_de_falha():
    ok = notifications.describe_job_end({"status": "completed", "filename": "a.mkv", "elapsed_sec": 3700})
    assert ok["kind"] == "ok" and "a.mkv" in ok["text"] and "1h 01min" in ok["text"]
    falha = notifications.describe_job_end(
        {"status": "error", "filename": "a.mkv", "error_detail": "CUDA out of memory", "elapsed_sec": 5}
    )
    assert falha["kind"] == "error" and "placa de vídeo ficou sem memória" in falha["text"]
    assert notifications.describe_job_end({"status": "cancelled", "filename": "a.mkv"}) is None
    assert notifications.describe_job_end(None) is None


def test_notify_job_end_manda_aviso_do_job(monkeypatch):
    enviados = []
    monkeypatch.setattr(notifications, "notify", lambda t, x: enviados.append((t, x)) or {})
    job = create_job_record(video_id=0, filename="noite.mkv", engine="gemini", model="m", manual_game="")
    finish_job_record(job["job_id"], status="completed")
    notifications.notify_job_end(job["job_id"], wait=True)
    assert len(enviados) == 1 and "noite.mkv" in enviados[0][1]


def test_notify_envia_ao_discord_so_se_houver_link(monkeypatch):
    discord = []
    monkeypatch.setattr(notifications, "send_windows_toast", lambda t, x: True)
    monkeypatch.setattr(notifications, "send_discord", lambda url, txt: discord.append(url) or True)
    save_setting(notifications.WEBHOOK_SETTING, "")
    assert notifications.notify("t", "x") == {"windows": True, "discord": False}
    save_setting(notifications.WEBHOOK_SETTING, WEBHOOK)
    assert notifications.notify("t", "x")["discord"] is True
    assert discord == [WEBHOOK]
    save_setting(notifications.WEBHOOK_SETTING, "")


def test_falha_do_discord_nao_vaza_o_link(monkeypatch):
    def explode(*a, **k):
        raise OSError(f"falha em {WEBHOOK}")
    monkeypatch.setattr(notifications.urllib.request, "urlopen", explode)
    assert notifications.send_discord(WEBHOOK, "oi") is False


def test_api_do_webhook_mascara_valida_e_remove():
    client = TestClient(app, headers={"X-Banido-Token": API_AUTH_TOKEN})
    ruim = client.post("/api/notify/webhook", json={"url": "https://exemplo.com/x"})
    assert ruim.status_code == 400
    vazio = client.post("/api/notify/webhook", json={"url": " "})
    assert vazio.status_code == 400

    r = client.post("/api/notify/webhook", json={"url": WEBHOOK}).json()
    assert r["has_discord_webhook"] is True
    assert WEBHOOK not in str(r) and "..." in r["discord_webhook_masked"]
    assert WEBHOOK not in str(client.get("/api/notify").json())
    assert get_setting(notifications.WEBHOOK_SETTING, "") == WEBHOOK

    r = client.post("/api/notify/webhook", json={"clear": True}).json()
    assert r["has_discord_webhook"] is False


def test_aviso_de_teste(monkeypatch):
    monkeypatch.setattr(notifications, "send_windows_toast", lambda t, x: False)
    client = TestClient(app, headers={"X-Banido-Token": API_AUTH_TOKEN})
    r = client.post("/api/notify/test").json()
    assert r["success"] is True and r["discord_configured"] is False
