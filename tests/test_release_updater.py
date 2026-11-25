from pathlib import Path

import pytest

from app import release_updater as ru
from app import updater


def _release(version="0.2.0", asset="https://github.com/o/r/releases/download/v0.2.0/BanidoStudio-Setup.exe"):
    return {
        "tag": f"v{version}", "version": version, "name": f"Versão {version}", "body": "Novidades",
        "published_at": "2026-11-25T00:00:00Z", "html_url": f"https://github.com/o/r/releases/tag/v{version}",
        "asset_url": asset, "asset_size": 123,
    }


def test_parse_and_compare_versions():
    assert ru.is_newer("0.1.2", "0.1.1")
    assert ru.is_newer("v0.2", "0.1.9")
    assert ru.is_newer("1.0.0", "0.9.9")
    assert not ru.is_newer("0.1.1", "0.1.1")
    assert not ru.is_newer("0.1.0", "0.1.1")
    assert ru.is_newer("0.1.0", "0.0.0-dev")


def test_read_installed_version(tmp_path: Path):
    assert ru.read_installed_version(tmp_path) == ""
    (tmp_path / "VERSION").write_text("v0.3.1\n", encoding="utf-8")
    assert ru.read_installed_version(tmp_path) == "0.3.1"


def test_check_update_available():
    out = ru.check("o/r", "0.1.0", fetch=lambda repo: _release(), windows=True)
    assert out["update_available"] is True
    assert out["can_apply"] is True
    assert out["latest_version"] == "0.2.0"
    assert out["commits"][0]["sha"] == "v0.2.0"


def test_check_up_to_date():
    out = ru.check("o/r", "0.2.0", fetch=lambda repo: _release(), windows=True)
    assert out["update_available"] is False
    assert out["can_apply"] is False
    assert "mais recente" in out["reason"]


def test_check_without_installer_asset_or_not_windows():
    out = ru.check("o/r", "0.1.0", fetch=lambda repo: _release(asset=""), windows=True)
    assert out["update_available"] is True and out["can_apply"] is False
    out = ru.check("o/r", "0.1.0", fetch=lambda repo: _release(), windows=False)
    assert out["update_available"] is True and out["can_apply"] is False


def test_check_network_error():
    def boom(repo):
        raise OSError("sem rede")
    out = ru.check("o/r", "0.1.0", fetch=boom, windows=True)
    assert out["error"] and out["update_available"] is False


def test_download_rejects_foreign_url():
    with pytest.raises(ValueError):
        ru.download_installer("https://example.com/BanidoStudio-Setup.exe", "o/r")


def test_updater_uses_release_mode_for_installer_copy(tmp_path: Path, monkeypatch):
    (tmp_path / "VERSION").write_text("0.1.0", encoding="utf-8")
    monkeypatch.setattr(ru, "fetch_latest_release", lambda repo: _release())
    assert updater.is_installer_install(tmp_path) is True
    info = updater.check_for_updates(root=tmp_path)
    assert info["local"]["mode"] == "installed"
    assert info["local"]["version"] == "0.1.0"
    assert info["update_available"] is True
    assert info["latest_version"] == "0.2.0"


def test_zip_copy_without_version_is_not_installer_install(tmp_path: Path):
    assert updater.is_installer_install(tmp_path) is False
    assert updater.get_local_version(tmp_path)["mode"] == "zip"
