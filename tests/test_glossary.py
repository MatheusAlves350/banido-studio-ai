"""Glossário do grupo (A5): arquivo, prompt do Whisper e rotas."""
from fastapi.testclient import TestClient

from app.transcription import glossary
from app.transcription.prompt import MAX_PROMPT_CHARS, build_whisper_initial_prompt


def test_normaliza_termos():
    termos = glossary.normalize_terms(["  Zé  Pequeno ", "zé pequeno", "", "Bob, o Construtor", "x" * 100])
    assert termos[0] == "Zé Pequeno"
    assert termos[1] == "Bob o Construtor"
    assert len(termos) == 3 and len(termos[2]) == glossary.MAX_TERM_LEN


def test_limite_de_termos():
    termos = glossary.normalize_terms([f"termo{i}" for i in range(500)])
    assert len(termos) == glossary.MAX_TERMS


def test_salvar_e_carregar(tmp_path):
    arq = tmp_path / "pasta" / "glossario.txt"
    assert glossary.load_glossary(arq) == []
    assert glossary.save_glossary(["Banido", "banido", "Gaules"], arq) == ["Banido", "Gaules"]
    assert glossary.load_glossary(arq) == ["Banido", "Gaules"]
    assert not arq.with_suffix(".tmp").exists()


def test_carregar_ignora_bom_do_bloco_de_notas(tmp_path):
    arq = tmp_path / "glossario.txt"
    arq.write_bytes("\ufeffBanido\r\nGaules\r\n".encode("utf-8"))
    assert glossary.load_glossary(arq) == ["Banido", "Gaules"]


def test_prompt_inclui_glossario(monkeypatch):
    monkeypatch.setattr("app.transcription.prompt.list_known_voice_names", lambda exclude_host=False: [])
    p = build_whisper_initial_prompt(manual_game="Valorant", glossary_terms=["Jett", "Sova"])
    assert "Termos do grupo: Jett, Sova." in p
    assert "Jogo: Valorant." in p


def test_prompt_sem_glossario_igual_ao_antigo(monkeypatch):
    monkeypatch.setattr("app.transcription.prompt.list_known_voice_names", lambda exclude_host=False: [])
    p = build_whisper_initial_prompt(manual_game="Valorant", glossary_terms=[])
    assert "Termos do grupo" not in p


def test_prompt_respeita_limite_e_nao_corta_termo(monkeypatch):
    monkeypatch.setattr("app.transcription.prompt.list_known_voice_names", lambda exclude_host=False: [])
    termos = [f"Personagem{i:03d}" for i in range(200)]
    p = build_whisper_initial_prompt(manual_game="Valorant", extra_context="x" * 400, glossary_terms=termos)
    assert len(p) <= MAX_PROMPT_CHARS
    assert "Contexto e termos:" in p
    grupo = p.split("Termos do grupo: ")[1].split(". Contexto")[0]
    assert all(t in termos for t in grupo.split(", "))


def test_rotas(tmp_path, monkeypatch):
    from app.main import app, API_AUTH_TOKEN
    from app.routers import glossary as rota
    monkeypatch.setattr(glossary, "GLOSSARY_PATH", tmp_path / "glossario.txt")
    cliente = TestClient(app)
    token = {"X-Banido-Token": API_AUTH_TOKEN}
    r = cliente.get("/api/glossary", headers=token)
    assert r.status_code == 200 and r.json()["terms"] == []
    r = cliente.post("/api/glossary", json={"text": "Jett\nSova\njett\n"}, headers=token)
    assert r.json()["terms"] == ["Jett", "Sova"]
    assert cliente.get("/api/glossary", headers=token).json()["count"] == 2
    assert rota.MAX_TERMS == 200
