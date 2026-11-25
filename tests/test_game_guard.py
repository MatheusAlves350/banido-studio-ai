import os
import re
import time


def test_detect_active_obs_recording_ignores_old_hidden_and_excluded(tmp_path):
    from app.game_guard import detect_active_obs_recording

    now = time.time()
    current = tmp_path / "atual.mkv"
    current.write_bytes(b"0")
    os.utime(current, (now, now))

    hidden = tmp_path / ".temp.mkv"
    hidden.write_bytes(b"0")
    os.utime(hidden, (now, now))

    old = tmp_path / "antiga.mkv"
    old.write_bytes(b"0")
    os.utime(old, (now - 120, now - 120))

    assert detect_active_obs_recording(tmp_path, exclude_path=current, recent_sec=15.0, now=now) is None

    active = tmp_path / "gravando_agora.mkv"
    active.write_bytes(b"0")
    os.utime(active, (now - 2, now - 2))
    assert detect_active_obs_recording(tmp_path, exclude_path=current, recent_sec=15.0, now=now) == "gravando_agora.mkv"


def test_detect_watched_process(monkeypatch):
    from app import game_guard as gg

    monkeypatch.setattr(gg, "_list_running_process_names", lambda: {"obs64.exe", "cs2.exe"})
    assert gg.detect_watched_process(["valorant.exe"]) is None
    assert gg.detect_watched_process(["valorant.exe", "CS2.exe"]) == "CS2.exe"


def test_wait_while_paused_resumes_on_flag_and_when_recording_stops(tmp_path, monkeypatch):
    from app import game_guard as gg

    flag = tmp_path / "force_resume.flag"
    msgs = []
    ticks = {"n": 0}

    monkeypatch.setattr(gg, "detect_pause_reason", lambda *a, **kw: "gravação ativa do OBS (rec.mkv)")

    def fake_sleep(_sec):
        ticks["n"] += 1
        if ticks["n"] == 2:
            flag.write_text("1", encoding="utf-8")

    monkeypatch.setattr(gg.time, "sleep", fake_sleep)
    paused = gg.wait_while_paused(tmp_path, flag, progress_cb=msgs.append, poll_sec=0.01)
    assert paused is True
    assert ticks["n"] == 2
    assert msgs and "Pausado" in msgs[0]
    # Com a flag já presente, não pausa de novo
    assert gg.wait_while_paused(tmp_path, flag, poll_sec=0.01) is False


def test_resume_override_endpoint_requires_token_and_creates_flag(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as m

    monkeypatch.setattr(m, "TEMP_DIR", tmp_path)
    client = TestClient(m.app)
    token = re.search(r'<meta name="banido-token" content="([^"]+)">', client.get("/").text).group(1)

    assert client.post("/api/process/42/resume-override").status_code == 403
    r = client.post("/api/process/42/resume-override", headers={"X-Banido-Token": token})
    assert r.status_code == 200 and r.json()["success"] is True
    assert (tmp_path / "video_42_work" / "force_resume.flag").exists()
