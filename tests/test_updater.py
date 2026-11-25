import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git não instalado")


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-c", "user.name=Teste", "-c", "user.email=teste@example.com", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return res.stdout.strip()


@pytest.fixture()
def repos(tmp_path):
    """Cria um 'GitHub' falso (repositório bare) + uma instalação local clonada dele."""
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    local = tmp_path / "local"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "init", "-b", "main", str(seed))
    (seed / "README.md").write_text("v1\n", encoding="utf-8")
    (seed / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "Versão inicial")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-q", "origin", "main")
    _git(tmp_path, "clone", "-q", str(remote), str(local))
    return {"remote": remote, "seed": seed, "local": local}


def _publish(seed: Path, filename: str, content: str, message: str):
    (seed / filename).write_text(content, encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", message)
    _git(seed, "push", "-q", "origin", "main")


def test_up_to_date(repos):
    from app import updater
    info = updater.check_for_updates(root=repos["local"])
    assert info["error"] == ""
    assert info["local"]["mode"] == "git"
    assert info["update_available"] is False
    assert info["can_apply"] is False
    assert "mais recente" in info["reason"]


def test_detects_and_applies_update(repos):
    from app import updater
    _publish(repos["seed"], "README.md", "v2\n", "Melhora a interface")

    info = updater.check_for_updates(root=repos["local"])
    assert info["update_available"] is True
    assert info["behind"] == 1
    assert info["can_apply"] is True
    assert info["requirements_changed"] is False
    assert info["commits"][0]["subject"] == "Melhora a interface"

    updater.start_apply(root=repos["local"], background=False)
    st = updater.get_apply_state()
    assert st["state"] == "done", st
    assert st["needs_restart"] is True
    assert (repos["local"] / "README.md").read_text(encoding="utf-8") == "v2\n"
    assert updater.check_for_updates(root=repos["local"])["update_available"] is False


def test_local_changes_block_update(repos):
    from app import updater
    _publish(repos["seed"], "README.md", "v2\n", "Atualização")
    (repos["local"] / "README.md").write_text("editado pelo usuário\n", encoding="utf-8")

    info = updater.check_for_updates(root=repos["local"])
    assert info["update_available"] is True
    assert info["can_apply"] is False
    assert "README.md" in info["dirty_files"]

    updater.start_apply(root=repos["local"], background=False)
    st = updater.get_apply_state()
    assert st["state"] == "error"
    # O arquivo do usuário continua intacto
    assert (repos["local"] / "README.md").read_text(encoding="utf-8") == "editado pelo usuário\n"


def test_requirements_change_is_flagged(repos):
    from app import updater
    _publish(repos["seed"], "requirements.txt", "fastapi\nhttpx\n", "Nova dependência")
    info = updater.check_for_updates(root=repos["local"])
    assert info["requirements_changed"] is True


def test_zip_install_without_git(tmp_path, monkeypatch):
    from app import updater
    monkeypatch.setattr(updater, "_fetch_github_latest_commit", lambda: {"sha": "abc1234", "date": "", "subject": "x"})
    info = updater.check_for_updates(root=tmp_path)
    assert info["local"]["mode"] == "zip"
    assert info["can_apply"] is False
    assert info["remote"]["sha"] == "abc1234"


def test_update_endpoints_require_token_and_idle(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main as app_main

    monkeypatch.setattr(app_main.updater, "check_for_updates", lambda force=False: {"update_available": False, "local": {}})
    client = TestClient(app_main.app)

    # Desde a correção da issue #7, o GET de status também exige o token (ele pode disparar git fetch)
    assert client.get("/api/update/status").status_code == 403
    headers = {"X-Banido-Token": app_main.API_AUTH_TOKEN}
    r = client.get("/api/update/status", headers=headers)
    assert r.status_code == 200
    assert "apply" in r.json()

    assert client.post("/api/update/apply").status_code == 403
    assert client.post("/api/update/restart").status_code == 403

    monkeypatch.delenv("BANIDO_LAUNCHER", raising=False)
    r = client.post("/api/update/restart", headers=headers)
    assert r.status_code == 200
    assert r.json()["restart_supported"] is False
