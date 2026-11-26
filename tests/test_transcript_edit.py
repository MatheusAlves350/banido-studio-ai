"""Editor de transcrição (A6): arquivo de edições, sugestões para o glossário e rotas."""
import os
import time

from fastapi.testclient import TestClient

from app.transcription import edits as ed


def test_salvar_carregar_e_apagar(tmp_path):
    assert ed.load_edits(7, tmp_path) == {}
    ed.save_edits(7, {"0": {"start": 1.0, "original": "a", "text": "b", "edited_at": "x"}}, tmp_path)
    assert ed.load_edits(7, tmp_path)["0"]["text"] == "b"
    ed.save_edits(7, {}, tmp_path)
    assert not (tmp_path / "7.json").exists()


def test_reaplica_pelo_instante_de_inicio():
    segs = [{"start": 0.0, "text": "oi"}, {"start": 10.2, "text": "e ai jett"}]
    edits = {"5": {"start": 10.0, "original": "e ai jett", "text": "E aí, Jett"}, "9": {"start": 99.0, "original": "x", "text": "y"}}
    assert ed.apply_edits(segs, edits) == 1
    assert segs[1]["text"] == "E aí, Jett" and segs[0]["text"] == "oi"


def test_palavras_corrigidas_e_sugestoes():
    assert ed.corrected_words("o jet foi la", "o Jett foi la") == ["Jett"]
    assert ed.corrected_words("oi tudo bem", "oi tudo bem agora") == []
    edits = {
        "1": {"original": "o jet foi", "text": "o Jett foi"},
        "2": {"original": "manda jet", "text": "manda Jett"},
        "3": {"original": "sova aqui", "text": "Sova aqui"},
    }
    assert ed.suggest_glossary_terms(edits, []) == ["Jett"]
    assert ed.suggest_glossary_terms(edits, ["jett"]) == []


def test_rotas_editar_e_desfazer(tmp_path, monkeypatch):
    from app import database as db
    from app import main as m
    from app.transcription import glossary

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "SETTINGS_JSON_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(ed, "EDITS_DIR", tmp_path / "edicoes")
    monkeypatch.setattr(glossary, "GLOSSARY_PATH", tmp_path / "glossario.txt")
    monkeypatch.setattr("app.routers.transcript.list_known_voice_names", lambda exclude_host=False: [])
    db.init_db()

    obs = tmp_path / "obs"
    obs.mkdir()
    mkv = obs / "sessao.mkv"
    mkv.write_bytes(b"x")
    os.utime(mkv, (time.time() - 3600, time.time() - 3600))  # arquivo "estável" para o sincronizador
    db.sync_obs_videos(obs)
    video = db.get_all_videos()[0]
    vid = video["id"]
    segs = [
        {"start": 0.0, "end": 2.0, "speaker": "Ana", "text": "oi gente", "cluster_tag": "A"},
        {"start": 2.0, "end": 4.0, "speaker": "Beto", "text": "o jet chegou", "cluster_tag": "B"},
    ]
    db.save_video_result(vid, "Valorant", 4.0, "", str(mkv.with_suffix(".srt")), {"segments": segs, "hype_scores": []})

    client = TestClient(m.app)
    h = {"X-Banido-Token": m.API_AUTH_TOKEN}
    url = f"/api/video/{vid}/transcript"

    r = client.post(f"{url}/edit", json={"index": 1, "start": 2.0, "text": "o Jett chegou"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["edits"]["1"]["original"] == "o jet chegou"
    assert "o Jett chegou" in mkv.with_suffix(".srt").read_text(encoding="utf-8")
    assert db.get_video_by_id(vid)["data"]["segments"][1]["text"] == "o Jett chegou"
    assert client.get(f"/api/video/{vid}", headers=h).json()["transcript_edits"]["1"]["text"] == "o Jett chegou"

    # segunda edição mantém o original da primeira
    r = client.post(f"{url}/edit", json={"index": 1, "start": 2.0, "text": "o Jett chegou logo"}, headers=h)
    assert r.json()["edits"]["1"]["original"] == "o jet chegou"

    r = client.post(f"{url}/undo", json={"index": 1, "start": 2.0}, headers=h)
    assert r.json()["edits"] == {}
    assert "o jet chegou" in mkv.with_suffix(".srt").read_text(encoding="utf-8")

    assert client.post(f"{url}/undo", json={"index": 1, "start": 2.0}, headers=h).status_code == 404
    assert client.post(f"{url}/edit", json={"index": 1, "start": 2.0, "text": "  "}, headers=h).status_code == 400
    assert client.post(f"{url}/edit", json={"index": 1, "start": 50.0, "text": "x"}, headers=h).status_code == 409
