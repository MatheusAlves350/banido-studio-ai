def test_setup_check_endpoint():
    from fastapi.testclient import TestClient
    from app import main as app_main

    client = TestClient(app_main.app)
    assert client.get("/api/setup/check").status_code == 403
    r = client.get("/api/setup/check", headers={"X-Banido-Token": app_main.API_AUTH_TOKEN})
    assert r.status_code == 200
    data = r.json()
    keys = {i["key"] for i in data["items"]}
    assert {"ffmpeg", "obs_folder", "ai_key", "gpu", "disk"} <= keys
    for item in data["items"]:
        assert item["status"] in ("ok", "warn", "error")
        assert item["title"]
    assert data["ready"] == (data["errors"] == 0)
