import os
import re
import time


def test_sanitize_filename_and_unique_path(tmp_path):
    from app.lossless_media import sanitize_filename, _unique_path

    assert sanitize_filename("[GTA V] O Golpe: Parte 1?") == "GTA V - O Golpe Parte 1"
    assert sanitize_filename("Overwatch 2 - [Highlight] Final") == "Overwatch 2 - Highlight Final"

    f1 = tmp_path / "video.mkv"
    assert _unique_path(f1) == f1
    f1.write_bytes(b"a")
    f2 = _unique_path(f1)
    assert f2.name == "video (2).mkv"
    f2.write_bytes(b"b")
    f3 = _unique_path(f1)
    assert f3.name == "video (3).mkv"


def test_timestamp_and_chapter_entries():
    from app.lossless_media import _parse_ts_sec, _build_chapter_entries

    assert _parse_ts_sec("00:01:30") == 90.0
    assert _parse_ts_sec("02:15") == 135.0
    assert _parse_ts_sec(45.5) == 45.5
    assert _parse_ts_sec("invalid") is None

    entries = _build_chapter_entries(
        chapters=[{"timestamp": "00:00:00", "title": "Intro"}, {"timestamp": "00:05:00", "title": "Partida"}],
        highlights=[{"start": 120.0, "end": 180.0, "score": 95, "title": "Jogada"}],
        total_duration_sec=600.0,
        mode="both"
    )
    assert len(entries) == 3
    assert entries[0]["start_ms"] == 0
    assert entries[1]["start_ms"] == 120000
    assert entries[1]["end_ms"] == 180000
    assert entries[2]["start_ms"] == 300000


def test_ai_director_helpers():
    from app.ai_director import (
        normalize_youtube_chapters,
        parse_ai_json_response,
        extract_video_participants,
        _is_non_retryable_ai_error,
    )
    from app.config import HOST_SPEAKER_NAME

    ch = normalize_youtube_chapters([{"timestamp": "00:02:00", "title": "Ação"}])
    assert ch[0]["timestamp"] == "00:00:00"

    raw = '<think>planejando</think>```json\n{"main_game": "CS2", "title_options": ["[CS2] Clutch Épico"], "chapters": [{"timestamp": "00:00:00", "title": "Começo"}], "highlights": [{"start": 10, "end": 40, "score": 90, "title": "Clutch"}]}\n```'
    parsed = parse_ai_json_response(raw, duration_sec=300.0)
    assert parsed["main_game"] == "CS2"
    assert parsed["title_options"][0] == "CS2 - Clutch Épico"

    parts = extract_video_participants({"segments": [{"speaker": "Carlos"}, {"speaker": HOST_SPEAKER_NAME}]})
    assert parts[0] == HOST_SPEAKER_NAME
    assert "Carlos" in parts

    assert _is_non_retryable_ai_error(RuntimeError("401 API_KEY_INVALID")) is True
    assert _is_non_retryable_ai_error(RuntimeError("503 Service Unavailable")) is False


def test_worker_extract_clean_deduced_name():
    from app.worker import extract_clean_deduced_name

    assert extract_clean_deduced_name("Pedro (chamado pelo nome)") == "Pedro"
    assert extract_clean_deduced_name("Desconhecido (sem pistas)") is None
    assert extract_clean_deduced_name("Host") is None


def test_voice_bank_path_traversal_blocked():
    from app.voice_bank import resolve_canonical_friend_name

    assert resolve_canonical_friend_name("..") == ""
    assert resolve_canonical_friend_name(".") == ""
    assert resolve_canonical_friend_name("...") == ""
    assert resolve_canonical_friend_name("../escape") != ".."
    assert resolve_canonical_friend_name("CON") == ""
    assert resolve_canonical_friend_name("NUL") == ""
    assert resolve_canonical_friend_name("COM1") == ""
    assert resolve_canonical_friend_name("joao pedro") == "Joao Pedro"


def test_obs_fixer_merge_filters_preserves_existing():
    from app.obs_fixer import _merge_obs_filters, _make_obs_filter

    existing = [_make_obs_filter("Supressão de Ruído (RNNoise)", "noise_suppress_filter_v2", {})]
    desired = [
        _make_obs_filter("Compressor (Voz)", "compressor_filter", {}),
        _make_obs_filter("Supressão de Ruído (RNNoise)", "noise_suppress_filter_v2", {}),
    ]
    merged = _merge_obs_filters(existing, desired)
    assert len(merged) == 2
    names = [f["name"] for f in merged]
    assert "Supressão de Ruído (RNNoise)" in names
    assert "Compressor (Voz)" in names


def test_sync_obs_videos_soft_delete_reassociate_and_active_skip(tmp_path, monkeypatch):
    import app.database as db

    db_file = tmp_path / "test_studio.db"
    settings_file = tmp_path / "settings.json"
    obs_dir = tmp_path / "obs"
    obs_dir.mkdir()

    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setattr(db, "SETTINGS_JSON_PATH", settings_file)
    db.init_db()

    # 1. Arquivo recém-modificado (simulando OBS gravando) deve ser ignorado quando min_stable_age_sec > 0
    recording = obs_dir / "gravando_agora.mkv"
    recording.write_bytes(b"x" * 1024)
    db.sync_obs_videos(obs_dir, min_stable_age_sec=120.0)
    assert db.get_all_videos() == []

    # 2. Envelhecer arquivo -> deve ser cadastrado
    old_ts = time.time() - 300
    os.utime(recording, (old_ts, old_ts))
    db.sync_obs_videos(obs_dir, min_stable_age_sec=60.0)
    vids = db.get_all_videos()
    assert len(vids) == 1
    orig_id = vids[0]["id"]
    db.save_video_result(orig_id, "CS2", 120.0, "", "", {"main_game": "CS2", "segments": []})

    # 3. Renomear fora do app -> soft-delete + reassociação preserva id e análise!
    renamed = obs_dir / "renomeado_no_explorer.mkv"
    recording.rename(renamed)
    os.utime(renamed, (old_ts, old_ts))
    db.sync_obs_videos(obs_dir, min_stable_age_sec=60.0)

    vids_after = db.get_all_videos()
    assert len(vids_after) == 1
    assert vids_after[0]["id"] == orig_id
    assert vids_after[0]["filename"] == "renomeado_no_explorer.mkv"
    assert vids_after[0]["game_name"] == "CS2"


def test_fastapi_csrf_and_masked_settings(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.database as db
    import app.main as m

    db_file = tmp_path / "api_test.db"
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setattr(db, "SETTINGS_JSON_PATH", settings_file)
    db.init_db()

    client = TestClient(m.app)

    # GET / injeta meta banido-token
    home = client.get("/")
    assert home.status_code == 200
    match = re.search(r'<meta name="banido-token" content="([^"]+)">', home.text)
    assert match is not None
    token = match.group(1)

    # Sem token -> 403 em POST e GET /api/settings
    assert client.get("/api/settings").status_code == 403
    assert client.post("/api/games", json={"name": "Hades"}).status_code == 403

    # Com token -> 200 e chaves mascaradas
    headers = {"X-Banido-Token": token}
    r_save = client.post(
        "/api/settings",
        json={"google_api_key": "fake-demo-secret-1234567890", "ai_provider": "gemini"},
        headers=headers
    )
    assert r_save.status_code == 200
    body = r_save.json()
    assert body["has_google_api_key"] is True
    assert body["google_api_key_masked"] == "fake...7890"
    assert "google_api_key" not in body
    assert "fake-demo-secret-1234567890" not in str(body)

    # Salvar outra config com chave vazia NÃO apaga chave existente
    r_keep = client.post("/api/settings", json={"ai_provider": "nvidia"}, headers=headers)
    assert r_keep.status_code == 200
    assert r_keep.json()["has_google_api_key"] is True

    # Limpar explicitamente apaga a chave
    r_clear = client.post("/api/settings", json={"clear_google_key": True}, headers=headers)
    assert r_clear.status_code == 200
    assert r_clear.json()["has_google_api_key"] is False
