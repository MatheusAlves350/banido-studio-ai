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


def test_single_pass_filter_graph_and_prep_missing_file(tmp_path):
    from app.audio_pipeline import SegmentedAudioExtractor
    from app.lossless_media import prepare_single_pass_norm

    # Arquivo inexistente deve retornar should_run=False sem lançar exceção
    prep = prepare_single_pass_norm(tmp_path / "nao_existe.mkv")
    assert prep["should_run"] is False
    assert prep["already_normalized"] is False
    assert "não encontrado" in prep["error"].lower()

    # Verificar grafo Single-Pass multi-faixa (6 faixas) e faixa única (1 faixa)
    ext_multi = SegmentedAudioExtractor.__new__(SegmentedAudioExtractor)
    ext_multi.stream_count = 6
    fc_multi = ext_multi._build_single_pass_filter_complex()
    assert "[0:a:2]asplit=2[disc_wh_in][disc_yt_in]" in fc_multi
    assert "[disc_wh]" in fc_multi
    assert "[aout]" in fc_multi

    ext_single = SegmentedAudioExtractor.__new__(SegmentedAudioExtractor)
    ext_single.stream_count = 1
    fc_single = ext_single._build_single_pass_filter_complex()
    assert "[0:a:0]asplit=2[disc_wh_in][yt_in]" in fc_single
    assert "[disc_wh]" in fc_single
    assert "[aout]" in fc_single


def _catalog_isolado(tmp_path, monkeypatch):
    import app.model_catalog as mc
    monkeypatch.setattr(mc, "CACHE_FILE", tmp_path / "models_cache.json")
    mc.clear_cache()
    return mc


def test_model_catalog_default_without_key(tmp_path, monkeypatch):
    mc = _catalog_isolado(tmp_path, monkeypatch)
    res = mc.list_models("gemini", "")
    assert res["source"] == "default"
    assert res["models"][0]["value"] == mc.DEFAULT_GOOGLE_MODEL
    assert mc.normalize_provider("xyz") is None


def test_model_catalog_uses_api_and_filters_fallback(tmp_path, monkeypatch):
    mc = _catalog_isolado(tmp_path, monkeypatch)
    chamadas = []

    def fake_get(url, headers):
        chamadas.append(url)
        return {"models": [
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-9-novo", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["generateContent"]},
        ]}

    monkeypatch.setattr(mc, "_http_get_json", fake_get)
    res = mc.list_models("gemini", "chave-x")
    valores = [m["value"] for m in res["models"]]
    assert res["source"] == "api"
    assert valores == ["gemini-2.5-flash", "gemini-9-novo"]  # padrão conhecido primeiro, novos depois
    # segunda chamada vem do cache (24 h)
    mc.list_models("gemini", "chave-x")
    assert len(chamadas) == 1
    # fallback só usa o que a API confirmou
    assert mc.get_fallback_models("gemini") == ["gemini-2.5-flash"]


def test_model_catalog_api_failure_falls_back_to_default(tmp_path, monkeypatch):
    mc = _catalog_isolado(tmp_path, monkeypatch)

    def boom(url, headers):
        raise OSError("sem internet")

    monkeypatch.setattr(mc, "_http_get_json", boom)
    res = mc.list_models("nvidia", "chave-x")
    assert res["source"] == "default"
    assert len(mc.get_fallback_models("nvidia")) >= 3


def test_nvidia_models_filter_non_chat(tmp_path, monkeypatch):
    mc = _catalog_isolado(tmp_path, monkeypatch)
    monkeypatch.setattr(mc, "_http_get_json", lambda u, h: {"data": [
        {"id": "z-ai/glm-5.3-flash"}, {"id": "nvidia/nv-embed-v1"}, {"id": "nvidia/rerank-x"},
    ]})
    ids = [m["value"] for m in mc.list_models("nvidia", "k")["models"]]
    assert ids == ["z-ai/glm-5.3-flash"]


def test_api_models_endpoint_requires_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.database as db
    import app.main as m
    _catalog_isolado(tmp_path, monkeypatch)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "SETTINGS_JSON_PATH", tmp_path / "s.json")
    db.init_db()
    client = TestClient(m.app)
    token = re.search(r'<meta name="banido-token" content="([^"]+)">', client.get("/").text).group(1)
    assert client.get("/api/models/gemini").status_code == 403
    r = client.get("/api/models/gemini", headers={"X-Banido-Token": token})
    assert r.status_code == 200 and r.json()["source"] == "default"
    assert client.get("/api/models/outro", headers={"X-Banido-Token": token}).status_code == 404


def test_whisper_hallucination_phrases_and_variants():
    from app.transcriber_v3 import _is_whisper_hallucination as h

    for fantasma in [
        "Inscreva-se no canal, ative o sininho....",
        "Ative o sininho",
        "Deixe seu like",
        "Até o próximo vídeo!",
        "Se inscreva e ative as notificações",
        "Legendas pela comunidade Amara.org",
        "",
    ]:
        assert h(fantasma, 2.0), fantasma


def test_whisper_hallucination_keeps_real_speech():
    from app.transcriber_v3 import _is_whisper_hallucination as h

    assert not h("Sim, bora jogar mais uma partida", 2.0)
    assert not h("Não", 0.6)
    longa = "cara eu falei pra galera que se inscreva no servidor do discord pra ver a partida de hoje e depois a gente vai jogar"
    assert not h(longa, 6.0)


def test_whisper_hallucination_confidence_metrics():
    from app.transcriber_v3 import _is_whisper_hallucination as h

    assert h("hmm tá bom então", 1.5, no_speech_prob=0.9, avg_logprob=-1.4)
    assert not h("hmm tá bom então", 1.5, no_speech_prob=0.9, avg_logprob=-0.3)
    assert h("blá blá", 1.0, compression_ratio=3.1)
    assert h("e aí " * 10, 5.0)


def _fake_seg(start, end, text):
    from types import SimpleNamespace as NS
    return NS(start=start, end=end, text=text, words=[], no_speech_prob=0.0, avg_logprob=-0.2, compression_ratio=1.2)


class _FakeWhisper:
    """Devolve falas diferentes conforme o áudio recebido (mic = 1.0, discord = 2.0)."""
    def __init__(self, by_marker):
        self.by_marker = by_marker
        self.calls = 0

    def transcribe(self, audio, **kw):
        import numpy as np
        self.calls += 1
        marker = float(np.max(audio))
        return iter(self.by_marker.get(marker, [])), None


def _dyn(has_iso, mic_active=True):
    import numpy as np
    n = 100  # 10 s
    mic = np.full(n, -30.0 if mic_active else -90.0)
    disc = np.full(n, -30.0)
    return {"has_isolated_mic": has_iso, "mic_rms": mic, "discord_rms": disc}


def test_separate_tracks_keep_overlapping_speech():
    import numpy as np
    from app import transcriber_v3 as t

    model = _FakeWhisper({
        1.0: [_fake_seg(1.0, 4.0, "eu acho que vai dar certo")],
        2.0: [_fake_seg(3.0, 6.0, "não cara, vamos pelo outro lado")],
    })
    mic = np.full(160000, 1.0, dtype=np.float32)
    disc = np.full(160000, 2.0, dtype=np.float32)
    segs = t._transcribe_block(model, disc, mic, disc, 10.0, _dyn(True), "")
    assert model.calls == 2
    assert [s["speaker"] for s in segs] == [t.HOST_SPEAKER_NAME, "Discord"]
    assert [s["start"] for s in segs] == [11.0, 13.0]  # offset do bloco aplicado
    assert all(s.get("overlap") for s in segs)  # 13.0-14.0 se sobrepõem


def test_separate_tracks_drop_mic_echo_and_silent_mic():
    import numpy as np
    from app import transcriber_v3 as t

    eco = {
        1.0: [_fake_seg(1.0, 4.0, "Vamos pelo outro lado, pessoal!")],
        2.0: [_fake_seg(1.1, 4.0, "vamos pelo outro lado pessoal")],
    }
    mic = np.full(160000, 1.0, dtype=np.float32)
    disc = np.full(160000, 2.0, dtype=np.float32)
    segs = t._transcribe_block(_FakeWhisper(eco), disc, mic, disc, 0.0, _dyn(True), "")
    assert [s["speaker"] for s in segs] == ["Discord"]  # eco do Discord no mic removido

    model = _FakeWhisper(eco)
    segs = t._transcribe_block(model, disc, mic, disc, 0.0, _dyn(True, mic_active=False), "")
    assert model.calls == 1  # mic mudo: Whisper roda só na faixa Discord
    assert [s["speaker"] for s in segs] == ["Discord"]


def test_single_mix_when_no_isolated_mic():
    import numpy as np
    from app import transcriber_v3 as t

    model = _FakeWhisper({2.0: [_fake_seg(0.0, 2.0, "fala qualquer")]})
    disc = np.full(160000, 2.0, dtype=np.float32)
    segs = t._transcribe_block(model, disc, None, disc, 0.0, _dyn(False), "")
    assert model.calls == 1 and len(segs) == 1


def _client_rede(tmp_path, monkeypatch, host="0.0.0.0", password="segredo-forte"):
    from fastapi.testclient import TestClient
    import app.database as db
    import app.main as m
    from app import security
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "net.db")
    monkeypatch.setattr(db, "SETTINGS_JSON_PATH", tmp_path / "net.json")
    db.init_db()
    monkeypatch.setattr(m, "SERVER_HOST", host)
    monkeypatch.setattr(m, "ACCESS_PASSWORD", password)
    security._FAILURES.clear()
    return TestClient(m.app, follow_redirects=False)


def test_network_mode_requires_password_and_login(tmp_path, monkeypatch):
    c = _client_rede(tmp_path, monkeypatch)
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/status").status_code == 401
    assert "banido-token" not in c.get("/login").text  # o token não vaza antes do login

    assert c.post("/login", content="password=errada",
                  headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 401
    ok = c.post("/login", content="password=segredo-forte",
                headers={"content-type": "application/x-www-form-urlencoded"})
    assert ok.status_code == 303
    set_cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie

    assert c.get("/").status_code == 200  # cookie guardado pelo cliente
    assert c.get("/api/status").status_code == 200


def test_network_mode_without_password_blocks_everything(tmp_path, monkeypatch):
    c = _client_rede(tmp_path, monkeypatch, password="")
    assert c.get("/").status_code == 403
    assert c.get("/login").status_code == 403
    assert c.get("/api/status").status_code == 403


def test_login_rate_limit(tmp_path, monkeypatch):
    c = _client_rede(tmp_path, monkeypatch)
    h = {"content-type": "application/x-www-form-urlencoded"}
    for _ in range(5):
        assert c.post("/login", content="password=x", headers=h).status_code == 401
    assert c.post("/login", content="password=segredo-forte", headers=h).status_code == 429


def test_local_mode_unchanged_and_preview_gets_need_token(tmp_path, monkeypatch):
    c = _client_rede(tmp_path, monkeypatch, host="127.0.0.1", password="")
    assert c.get("/").status_code == 200  # uso local não pede senha
    assert c.get("/api/audio/preview/1").status_code == 403
    assert c.get("/api/speakers/preview/1/Voz_1").status_code == 403


def test_prune_directory_removes_oldest(tmp_path):
    from app.security import prune_directory
    for i in range(6):
        f = tmp_path / f"p{i}.wav"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (1000 + i, 1000 + i))
    removed = prune_directory(tmp_path, max_bytes=3000, keep_newest=2)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert removed == 3 and left == ["p3.wav", "p4.wav", "p5.wav"]


def test_model_catalog_disk_cache_preserves_other_provider(tmp_path, monkeypatch):
    mc = _catalog_isolado(tmp_path, monkeypatch)
    monkeypatch.setattr(mc, "_http_get_json", lambda u, h: (
        {"models": [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]}]}
        if "googleapis" in u
        else {"data": [{"id": "z-ai/glm-5.3-flash"}]}
    ))
    mc.list_models("gemini", "k-google")
    # Simula reinício do processo (memória limpa, mas disco ainda tem google)
    mc._CACHE.clear()
    mc.list_models("nvidia", "k-nvidia")
    disk = mc._read_disk_cache()
    assert "google" in disk and "nvidia" in disk


def test_network_mode_logout_clears_session(tmp_path, monkeypatch):
    c = _client_rede(tmp_path, monkeypatch)
    ok = c.post("/login", content="password=segredo-forte",
                headers={"content-type": "application/x-www-form-urlencoded"})
    assert ok.status_code == 303
    assert c.get("/").status_code == 200
    out = c.post("/logout")
    assert out.status_code == 303 and out.headers["location"] == "/login"
    assert c.get("/").status_code == 303


def test_separate_tracks_preserve_short_simultaneous_reactions():
    from app.transcriber_v3 import merge_track_segments
    host = [{"start": 10.0, "end": 11.2, "text": "Boa!", "speaker": "Host"}]
    disc = [{"start": 10.1, "end": 11.3, "text": "Boa!", "speaker": "Discord"}]
    merged = merge_track_segments(host, disc)
    assert len(merged) == 2
    assert {s["speaker"] for s in merged} == {"Host", "Discord"}


def test_whisper_hallucination_preserves_high_confidence_laughter():
    from app.transcriber_v3 import _is_whisper_hallucination as h
    assert not h("kkkkk corre corre", 1.8, no_speech_prob=0.05, avg_logprob=-0.2, compression_ratio=2.6)
    assert h("kkkkk corre corre", 1.8, no_speech_prob=0.5, avg_logprob=-0.8, compression_ratio=2.6)


def test_transcribe_block_respects_separate_tracks_override_and_callback():
    import numpy as np
    from app import transcriber_v3 as t

    mic = np.full(160000, 1.0, dtype=np.float32)
    disc = np.full(160000, 2.0, dtype=np.float32)
    model_single = _FakeWhisper({2.0: [_fake_seg(1.0, 2.5, "fala mix")]})
    segs = t._transcribe_block(model_single, disc, mic, disc, 0.0, _dyn(True), "", separate_tracks=False)
    assert model_single.calls == 1 and len(segs) == 1

    labels = []
    model_dual = _FakeWhisper({
        1.0: [_fake_seg(1.0, 2.5, "fala host")],
        2.0: [_fake_seg(2.0, 3.5, "fala discord")],
    })
    segs2 = t._transcribe_block(
        model_dual, disc, mic, disc, 0.0, _dyn(True), "",
        separate_tracks=True, track_status_cb=labels.append,
    )
    assert model_dual.calls == 2 and len(segs2) == 2
    assert len(labels) == 2


def test_export_lossless_highlight_exact_cut_uses_reencode_and_accurate_seek(tmp_path, monkeypatch):
    import app.lossless_media as lm

    captured = []
    monkeypatch.setattr(lm, "CLIPS_DIR", tmp_path / "cortes")
    monkeypatch.setattr(lm, "inspect_mkv_audio_norm", lambda p: {"audio_streams": 3, "is_normalized": True})
    monkeypatch.setattr(lm.subprocess, "run", lambda cmd, **kw: captured.append(cmd))

    res = lm.export_lossless_highlight(
        tmp_path / "2026-11-20 20-00-00.mkv", 100.0, 115.0, "Jogada Insana",
        boost_discord=True, keep_all_tracks=False, exact_cut=True,
    )
    assert res["success"] is True and res["exact_cut"] is True
    cmd = captured[0]
    assert "-c:v" in cmd and "libx264" in cmd
    assert cmd.count("-ss") == 2  # seek rápido + seek preciso após -i


def test_original_mix_backup_detection_and_finalize(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    import app.lossless_media as lm

    fake_probe = {
        "format": {"duration": "120.0", "tags": {"BANIDO_AUDIO_NORM": lm.YOUTUBE_AUDIO_NORM_TAG}},
        "streams": [
            {"codec_type": "video"},
            {"codec_type": "audio", "tags": {"title": "Track1 (YouTube Gold)"}},
            {"codec_type": "audio", "tags": {"title": "Track2 (Mic)"}},
            {"codec_type": "audio", "tags": {"title": "Track3 (Discord)"}},
            {"codec_type": "audio", "tags": {"title": "Track1 (Original Mix Backup)", "BANIDO_ORIGINAL_MIX": "1"}},
        ],
    }
    monkeypatch.setattr(
        lm.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(fake_probe), stderr=""),
    )
    info = lm.inspect_mkv_audio_norm(tmp_path / "video.mkv")
    assert info["is_normalized"] is True
    assert info["has_original_backup"] is True
    assert info["original_backup_index"] == 3

    orig = tmp_path / "orig.mkv"
    temp = tmp_path / ".orig.audio_norm_tmp.mkv"
    orig.write_bytes(b"x" * 1000)
    temp.write_bytes(b"y" * 1050)
    res = lm.finalize_normalized_mkv(
        mkv_path=orig,
        temp_mkv=temp,
        orig_info={"audio_streams": 3, "video_streams": 1, "duration": 120.0, "has_original_backup": False},
    )
    assert res["success"] is True
    assert res["audio_streams"] == 4
    assert res["has_original_backup"] is True



