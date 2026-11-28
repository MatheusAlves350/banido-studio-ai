"""Item E5: conferir e ajustar o OBS pela interface, com edição antes de aplicar."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from app import obs_fixer, obs_panel
from app.main import app, API_AUTH_TOKEN

CENA = {
    "AuxAudioDevice1": {"name": "Mic/Aux", "mixers": 255, "volume": 0.8, "filters": [{"name": "RNNoise", "id": "noise_suppress_filter"}]},
    "DesktopAudioDevice1": {"name": "Áudio do desktop", "mixers": 255, "volume": 1.0},
    "sources": [
        {"name": "Discord", "id": "wasapi_process_output_capture", "mixers": 255, "volume": 0.5},
        {"name": "Jogo Overwatch", "id": "wasapi_process_output_capture", "mixers": 255},
        {"name": "Soundpad", "id": "wasapi_output_capture", "mixers": 255},
        {"name": "Janela do navegador", "id": "window_capture", "mixers": 255},
        {"name": "Música", "id": "ffmpeg_source", "mixers": 255},
        {"name": "Webcam", "id": "dshow_input"},
    ],
}


@pytest.fixture
def obs_dir(tmp_path, monkeypatch):
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "Cenas_Teste.json").write_text(json.dumps(CENA, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(obs_fixer, "OBS_SCENES_DIR", scenes)
    monkeypatch.setattr(obs_fixer, "OBS_USER_INI", tmp_path / "nao_existe.ini")
    monkeypatch.setattr(obs_fixer, "OBS_BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(obs_fixer, "_is_obs_running", lambda: False)
    return scenes / "Cenas_Teste.json"


def _resumo(data):
    """Só o que importa para o roteamento: faixas, volume e nomes dos filtros de cada fonte."""
    itens = {k: v for k, v in data.items() if isinstance(v, dict)}
    itens.update({f"s{i}": s for i, s in enumerate(data["sources"])})
    return {k: (v.get("mixers"), v.get("volume"), sorted(f["name"] for f in v.get("filters", []))) for k, v in itens.items()}


def test_plano_padrao_e_igual_ao_que_o_obs_fixer_aplica(obs_dir):
    """O diff mostrado ao usuário tem que ser exatamente o que o obs_fixer faria."""
    # 1) resultado do obs_fixer, que continua como está
    assert obs_fixer.apply_obs_audio_routing()["success"] is True
    esperado = _resumo(json.loads(obs_dir.read_text(encoding="utf-8")))
    # 2) volta ao original e aplica pela camada nova, com as escolhas padrão
    obs_dir.write_text(json.dumps(CENA, ensure_ascii=False), encoding="utf-8")
    estado = obs_panel.read_state()
    r = obs_panel.apply("6", None, True, estado["base_hash"], confirm=True)
    assert r["changed"] is True
    assert _resumo(json.loads(obs_dir.read_text(encoding="utf-8"))) == esperado


def test_estado_descreve_fontes_e_funcoes_detectadas(obs_dir):
    estado = obs_panel.read_state()
    funcoes = {s["name"]: s["role"] for s in estado["sources"]}
    assert funcoes["Mic/Aux"] == "mic" and funcoes["Discord"] == "discord"
    assert funcoes["Jogo Overwatch"] == "game" and funcoes["Soundpad"] == "soundpad"
    assert funcoes["Janela do navegador"] == "desktop"
    assert funcoes["Música"] == "keep"          # fonte de áudio que o programa não mexe por padrão
    assert "Webcam" not in funcoes               # não é áudio
    assert estado["sources"][0]["tracks_now"] == [1, 2, 3, 4, 5, 6]
    assert estado["diff"]                        # há o que mudar no cenário de teste


def test_preview_nao_grava_nada(obs_dir):
    antes = obs_dir.read_text(encoding="utf-8")
    p = obs_panel.preview("6", None, True)
    assert p["diff"]
    assert obs_dir.read_text(encoding="utf-8") == antes
    assert not obs_fixer.OBS_BACKUP_DIR.exists()


def test_edicao_funcao_e_layout_mudam_o_plano(obs_dir):
    # "Música" passa a ser tratada como Soundpad e o layout é de 2 faixas
    chave = next(s["key"] for s in obs_panel.read_state()["sources"] if s["name"] == "Música")
    plano = obs_panel.build_plan(CENA, "2", {chave: "soundpad"}, False)
    por_nome = {p["name"]: p for p in plano}
    assert por_nome["Música"]["tracks_new"] == [1]
    assert por_nome["Mic/Aux"]["tracks_new"] == [1, 2]
    assert por_nome["Discord"]["tracks_new"] == [1]
    assert all(p["new_filters"] == [] for p in plano)


def test_nao_mexer_deixa_a_fonte_fora_do_plano_e_do_arquivo(obs_dir):
    chaves = {s["name"]: s["key"] for s in obs_panel.read_state()["sources"]}
    obs_panel.apply("6", {chaves["Discord"]: "keep"}, True, "", confirm=True)
    cena = json.loads(obs_dir.read_text(encoding="utf-8"))
    assert cena["sources"][0]["mixers"] == 255 and cena["sources"][0]["volume"] == 0.5


def test_nada_e_gravado_sem_confirmacao(obs_dir):
    antes = obs_dir.read_text(encoding="utf-8")
    with pytest.raises(obs_panel.ObsPanelError):
        obs_panel.apply("6", None, True, "", confirm=False)
    assert obs_dir.read_text(encoding="utf-8") == antes
    assert not obs_fixer.OBS_BACKUP_DIR.exists()


def test_obs_aberto_impede_gravar(obs_dir, monkeypatch):
    monkeypatch.setattr(obs_fixer, "_is_obs_running", lambda: True)
    with pytest.raises(obs_panel.ObsPanelError) as e:
        obs_panel.apply("6", None, True, "", confirm=True)
    assert e.value.status == 409
    assert obs_panel.read_state()["obs_running"] is True   # mas ler continua funcionando


def test_arquivo_mudou_depois_de_abrir_a_tela(obs_dir):
    estado = obs_panel.read_state()
    cena = json.loads(obs_dir.read_text(encoding="utf-8"))
    cena["AuxAudioDevice1"]["volume"] = 0.3                # alguém mexeu no OBS enquanto a tela estava aberta
    obs_dir.write_text(json.dumps(cena), encoding="utf-8")
    with pytest.raises(obs_panel.ObsPanelError) as e:
        obs_panel.apply("6", None, True, estado["base_hash"], confirm=True)
    assert e.value.status == 409 and "mudou" in str(e.value)


def test_backup_e_desfazer(obs_dir):
    original = obs_dir.read_text(encoding="utf-8")
    assert obs_panel.read_state()["can_undo"] is False
    r = obs_panel.apply("6", None, True, obs_panel.read_state()["base_hash"], confirm=True)
    assert (obs_fixer.OBS_BACKUP_DIR / r["backup"]).exists()
    assert obs_dir.read_text(encoding="utf-8") != original
    assert obs_panel.read_state()["can_undo"] is True
    with pytest.raises(obs_panel.ObsPanelError):
        obs_panel.undo(confirm=False)
    obs_panel.undo(confirm=True)
    assert obs_dir.read_text(encoding="utf-8") == original
    # o estado de antes de desfazer também foi guardado
    assert any(p.stem.endswith("_desfeito") for p in obs_fixer.OBS_BACKUP_DIR.glob("*.json"))


def test_aplicar_duas_vezes_nao_muda_mais_nada(obs_dir):
    obs_panel.apply("6", None, True, "", confirm=True)
    r = obs_panel.apply("6", None, True, "", confirm=True)
    assert r["changed"] is False


def test_nao_tem_configuracao_do_obs(tmp_path, monkeypatch):
    monkeypatch.setattr(obs_fixer, "OBS_SCENES_DIR", tmp_path / "vazio")
    monkeypatch.setattr(obs_fixer, "OBS_USER_INI", tmp_path / "x.ini")
    monkeypatch.setattr(obs_fixer, "_is_obs_running", lambda: False)
    estado = obs_panel.read_state()
    assert estado["found"] is False and "Abra o OBS uma vez" in estado["message"]


def test_api(obs_dir):
    client = TestClient(app, headers={"X-Banido-Token": API_AUTH_TOKEN})
    estado = client.get("/api/obs/state").json()
    assert estado["found"] and estado["sources"]
    prev = client.post("/api/obs/preview", json={"layout": "2", "roles": {}, "add_filters": False}).json()
    assert prev["diff"]
    assert client.post("/api/obs/preview", json={"layout": "9"}).status_code == 400
    assert client.post("/api/obs/preview", json={"roles": {"s0": "inventada"}}).status_code == 400
    sem = client.post("/api/obs/apply", json={"layout": "6", "confirm": False})
    assert sem.status_code == 400
    ok = client.post("/api/obs/apply", json={"layout": "6", "confirm": True, "base_hash": estado["base_hash"]}).json()
    assert ok["changed"] is True
    assert client.post("/api/obs/undo", json={"confirm": True}).json()["success"] is True
    assert copy.deepcopy(CENA)  # fixture não foi alterada em memória
