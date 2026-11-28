from fastapi.testclient import TestClient

from app import env_settings, first_run
from app.main import app, API_AUTH_TOKEN


def test_catalogo_nao_expoe_chaves_nem_senha():
    chaves = {f["key"] for f in env_settings.FIELDS}
    assert not any("API_KEY" in k or "PASSWORD" in k for k in chaves)
    assert "BANIDO_HOST" not in chaves and "BANIDO_PORT" not in chaves


def test_valores_atuais_usam_padrao_e_leem_o_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text('BANIDO_WHISPER_MODEL=medium\nBANIDO_SEPARATE_TRACKS=0\nBANIDO_HOST_NAME="Zé"\n', encoding="utf-8")
    vals = env_settings.current_values(env)
    assert vals["BANIDO_WHISPER_MODEL"] == "medium"
    assert vals["BANIDO_SEPARATE_TRACKS"] == "0"
    assert vals["BANIDO_HOST_NAME"] == "Zé"
    assert vals["BANIDO_WHISPER_LANGUAGE"] == "pt"
    assert vals["BANIDO_AUTO_PAUSE_ON_RECORDING"] == "1"


def test_validar_rejeita_valor_fora_da_lista_e_opcao_desconhecida():
    _, erros = env_settings.validate({"BANIDO_WHISPER_MODEL": "gigante", "BANIDO_X": "1", "BANIDO_SEPARATE_TRACKS": "talvez"})
    assert set(erros) == {"BANIDO_WHISPER_MODEL", "BANIDO_X", "BANIDO_SEPARATE_TRACKS"}


def test_validar_limpa_texto_e_cria_pasta(tmp_path):
    alvo = tmp_path / "Cortes"
    limpo, erros = env_settings.validate({
        "BANIDO_HOST_NAME": ' Ma"theus\n#',
        "BANIDO_CLIPS_DIR": str(alvo),
        "BANIDO_TEMP_DIR": "",
        "BANIDO_WHISPER_COMPUTE_TYPE": "int8_float16",
    })
    assert erros == {}
    assert limpo["BANIDO_HOST_NAME"] == "Ma theus"
    assert limpo["BANIDO_TEMP_DIR"] == ""
    assert alvo.is_dir()


def test_validar_exige_pasta_com_caminho_completo_e_nome_nao_vazio():
    _, erros = env_settings.validate({"BANIDO_OBS_DIR": "relativa", "BANIDO_HOST_NAME": "  "})
    assert set(erros) == {"BANIDO_OBS_DIR", "BANIDO_HOST_NAME"}


def test_so_grava_o_que_mudou():
    base = {"BANIDO_WHISPER_MODEL": "large-v3", "BANIDO_OBS_DIR": "/a/b"}
    out = env_settings.changed_only({"BANIDO_WHISPER_MODEL": "large-v3", "BANIDO_OBS_DIR": "/a/b/"}, base)
    assert out == {}
    out = env_settings.changed_only({"BANIDO_WHISPER_MODEL": "medium"}, base)
    assert out == {"BANIDO_WHISPER_MODEL": "medium"}


def test_api_get_e_post(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comentario\nBANIDO_PORT=8765\n", encoding="utf-8")
    monkeypatch.setattr(first_run, "ENV_PATH", env)
    client = TestClient(app, headers={"X-Banido-Token": API_AUTH_TOKEN})

    dados = client.get("/api/env-settings").json()
    assert dados["values"]["BANIDO_WHISPER_MODEL"] == "large-v3"
    assert any(f["key"] == "BANIDO_OBS_DIR" for f in dados["fields"])

    sem_mudanca = client.post("/api/env-settings", json={"values": {"BANIDO_WHISPER_MODEL": "large-v3"}}).json()
    assert sem_mudanca["restart_needed"] is False
    assert "BANIDO_WHISPER_MODEL" not in env.read_text(encoding="utf-8")

    r = client.post("/api/env-settings", json={"values": {"BANIDO_WHISPER_MODEL": "medium"}}).json()
    assert r["changed"] == ["BANIDO_WHISPER_MODEL"] and r["restart_needed"] is True
    linhas = env.read_text(encoding="utf-8").splitlines()
    assert "# comentario" in linhas and "BANIDO_PORT=8765" in linhas
    assert 'BANIDO_WHISPER_MODEL="medium"' in linhas

    ruim = client.post("/api/env-settings", json={"values": {"BANIDO_WHISPER_MODEL": "gigante"}})
    assert ruim.status_code == 400
