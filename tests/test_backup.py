"""F4: backup e restauração do banco de vozes e do SQLite. Sem GPU, sem modelos e sem rede."""
import re
import sqlite3
import zipfile
from datetime import datetime, timedelta

import pytest

import app.backup as bk
import app.database as db

CHAVE = "AIzaSyChaveSecretaDeTeste1234567890abcdef"


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    base = tmp_path / "base"
    voices = base / "vozes_amigos"
    (voices / "Ana").mkdir(parents=True)
    (voices / "Ana" / "embedding.npy").write_bytes(b"NPY-ANA")
    (voices / "Ana" / "amostra1.wav").write_bytes(b"WAV-ANA")
    (voices / "Bruno").mkdir()
    (voices / "Bruno" / "embedding.npy").write_bytes(b"NPY-BRUNO")
    dbdir = base / "database"
    dbdir.mkdir()
    for alvo in (db, bk.config):
        monkeypatch.setattr(alvo, "DB_PATH", dbdir / "banido_studio.db")
        monkeypatch.setattr(alvo, "SETTINGS_JSON_PATH", dbdir / "settings.json")
    monkeypatch.setattr(db, "LOGS_DIR", dbdir / "logs")
    monkeypatch.setattr(bk.config, "BASE_DIR", base)
    monkeypatch.setattr(bk.config, "VOICE_BANK_DIR", voices)
    (dbdir / "logs").mkdir()
    db.init_db()
    db.save_setting("google_api_key", CHAVE)
    db.save_setting("google_model", "gemini-2.5-flash")
    conn = sqlite3.connect(str(dbdir / "banido_studio.db"))
    conn.execute("INSERT INTO videos (filename, file_path, size_gb) VALUES ('a.mkv', 'x', 1.0)")
    conn.commit()
    conn.close()
    return {"base": base, "voices": voices, "db": dbdir / "banido_studio.db"}


def _nomes(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return zf.namelist()


def test_backup_tem_vozes_banco_e_configuracoes_sem_chaves(ambiente):
    z = bk.create_backup()
    nomes = _nomes(z)
    assert {"manifest.json", "banido_studio.db", "settings.json",
            "vozes_amigos/Ana/embedding.npy", "vozes_amigos/Ana/amostra1.wav",
            "vozes_amigos/Bruno/embedding.npy"} <= set(nomes)
    with zipfile.ZipFile(z) as zf:
        assert CHAVE.encode() not in zf.read("banido_studio.db")
        assert CHAVE not in zf.read("settings.json").decode()
        assert "gemini-2.5-flash" in zf.read("settings.json").decode()
    assert not any(n.endswith(".mkv") for n in nomes)


def test_verificacao_descreve_o_backup_e_pega_arquivo_ruim(ambiente, tmp_path):
    z = bk.create_backup()
    info = bk.verify_backup(z)
    assert info["vozes"] == ["Ana", "Bruno"] and info["videos_no_backup"] == 1
    assert info["arquivos_de_voz_que_serao_substituidos"] == 3
    falso = tmp_path / "banido-backup-falso.zip"
    falso.write_bytes(b"isso nao e zip")
    with pytest.raises(bk.BackupError):
        bk.verify_backup(falso)
    sem_manifest = tmp_path / "banido-backup-vazio.zip"
    with zipfile.ZipFile(sem_manifest, "w") as zf:
        zf.writestr("qualquer.txt", "x")
    with pytest.raises(bk.BackupError):
        bk.verify_backup(sem_manifest)


def test_recusa_caminhos_perigosos_no_zip(ambiente, tmp_path):
    z = bk.create_backup()
    ruim = tmp_path / "banido-backup-ruim.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(ruim, "w") as dst:
        for n in src.namelist():
            dst.writestr(n, src.read(n))
        dst.writestr("vozes_amigos/../../fora.wav", "x")
    with pytest.raises(bk.BackupError, match="inesperados"):
        bk.verify_backup(ruim)


def test_restaurar_volta_dados_cria_seguranca_e_preserva_chaves_e_extras(ambiente):
    z = bk.create_backup()
    # Depois do backup: muda o banco, apaga uma voz, adiciona voz nova e troca a chave
    conn = sqlite3.connect(str(ambiente["db"]))
    conn.execute("DELETE FROM videos")
    conn.commit()
    conn.close()
    (ambiente["voices"] / "Ana" / "embedding.npy").write_bytes(b"ALTERADO")
    (ambiente["voices"] / "Bruno" / "embedding.npy").unlink()
    (ambiente["voices"] / "Carla").mkdir()
    (ambiente["voices"] / "Carla" / "embedding.npy").write_bytes(b"NPY-CARLA")
    db.save_setting("google_api_key", "AIzaChaveNovaDepoisDoBackup1234567890")

    res = bk.restore_backup(z)

    conn = sqlite3.connect(str(ambiente["db"]))
    assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1
    conn.close()
    assert (ambiente["voices"] / "Ana" / "embedding.npy").read_bytes() == b"NPY-ANA"
    assert (ambiente["voices"] / "Bruno" / "embedding.npy").read_bytes() == b"NPY-BRUNO"
    assert (ambiente["voices"] / "Carla" / "embedding.npy").read_bytes() == b"NPY-CARLA"  # nada é apagado
    assert db.get_setting("google_api_key") == "AIzaChaveNovaDepoisDoBackup1234567890"  # chave atual mantida
    assert db.get_setting("google_model") == "gemini-2.5-flash"
    assert res["backup_de_seguranca"].startswith(bk.PRE_RESTORE_PREFIX)
    assert (bk.get_backup_dir() / res["backup_de_seguranca"]).is_file()
    # O backup de segurança guarda o estado de antes da restauração (sem vídeos, com a voz da Carla)
    seg = bk.get_backup_dir() / res["backup_de_seguranca"]
    assert "vozes_amigos/Carla/embedding.npy" in _nomes(seg)


def test_automatico_so_a_cada_7_dias_e_mantem_as_4_ultimas(ambiente):
    primeiro = bk.maybe_auto_backup()
    assert primeiro and primeiro.name.startswith(bk.AUTO_PREFIX)
    assert bk.maybe_auto_backup() is None  # ainda não passou uma semana
    pasta = bk.get_backup_dir()
    agora = datetime.now()
    for i in range(6):  # simula 6 automáticos antigos
        f = pasta / f"{bk.AUTO_PREFIX}2026010{i + 1}-000000.zip"
        f.write_bytes(b"x")
        t = (agora - timedelta(days=30 + i)).timestamp()
        import os
        os.utime(f, (t, t))
    manual = bk.create_backup()
    bk.prune_auto_backups()
    autos = [b for b in bk.list_backups() if b["kind"] == "auto"]
    assert len(autos) == bk.AUTO_KEEP
    assert manual.exists()  # manual nunca é apagado
    futuro = bk.maybe_auto_backup(now=agora + timedelta(days=8))
    assert futuro is not None


def test_nome_de_backup_nao_aceita_caminhos(ambiente):
    for ruim in ("../x.zip", "..\\x.zip", "qualquer.zip", "banido-backup-../x.zip", ""):
        with pytest.raises(bk.BackupError):
            bk.resolve_backup_file(ruim)


def test_pasta_dos_backups_configuravel(ambiente, tmp_path):
    destino = tmp_path / "OutroDisco" / "meus backups"
    bk.set_backup_dir(str(destino))
    z = bk.create_backup()
    assert z.parent == destino
    bk.set_backup_dir("")  # volta ao padrão
    assert bk.get_backup_dir() == ambiente["base"] / "backups"


def test_rotas_exigem_token_importam_e_restauram(ambiente):
    from fastapi.testclient import TestClient
    import app.main as m

    client = TestClient(m.app)
    token = re.search(r'<meta name="banido-token" content="([^"]+)">', client.get("/").text).group(1)
    h = {"X-Banido-Token": token}
    assert client.get("/api/backup/status").status_code == 403
    assert client.post("/api/backup/create").status_code == 403

    criado = client.post("/api/backup/create", headers=h).json()
    assert criado["success"] and criado["name"].endswith(".zip")
    status = client.get("/api/backup/status", headers=h).json()
    assert [b["name"] for b in status["backups"]] == [criado["name"]]

    prev = client.post("/api/backup/preview", json={"name": criado["name"]}, headers=h).json()
    assert prev["vozes"] == ["Ana", "Bruno"]

    baixado = client.post("/api/backup/download", json={"name": criado["name"]}, headers=h)
    assert baixado.status_code == 200 and baixado.content[:2] == b"PK"

    # Importar (corpo cru) e recusar lixo
    imp = client.post("/api/backup/import", content=baixado.content, headers=h)
    assert imp.status_code == 200 and imp.json()["name"].startswith(bk.IMPORT_PREFIX)
    assert client.post("/api/backup/import", content=b"lixo", headers=h).status_code == 400
    assert client.post("/api/backup/preview", json={"name": "../x.zip"}, headers=h).status_code == 400

    ok = client.post("/api/backup/restore", json={"name": criado["name"]}, headers=h)
    assert ok.status_code == 200 and ok.json()["success"]
    assert not list(bk.get_backup_dir().glob("*.parcial"))


def test_restaurar_e_recusado_com_processamento_ativo(ambiente, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as m
    import app.routers.backup as rb

    client = TestClient(m.app)
    token = re.search(r'<meta name="banido-token" content="([^"]+)">', client.get("/").text).group(1)
    z = bk.create_backup()
    monkeypatch.setattr(rb, "reconcile_worker_processes", lambda: None)
    monkeypatch.setattr(rb, "ACTIVE_PROCESSES", {1: {}})
    r = client.post("/api/backup/restore", json={"name": z.name}, headers={"X-Banido-Token": token})
    assert r.status_code == 409
