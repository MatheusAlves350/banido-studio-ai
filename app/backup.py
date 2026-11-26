"""F4: backup e restauração do banco de vozes e do banco de dados (SQLite).

Analogia: o banco de vozes é o caderno de contatos do grupo. Este módulo tira uma "cópia
do caderno" datada, guarda as últimas, e sabe devolver o caderno ao lugar sem rasgar o atual.

O que entra no .zip: `vozes_amigos/` (amostras .wav e embeddings), o SQLite (copiado pela API
de backup do próprio SQLite, que consolida o WAL) e as configurações, **sem nenhuma chave**.
Não entram: gravações .mkv, cortes e modelos (grandes e baixáveis de novo).

Restaurar nunca apaga nada: antes de trocar, cria um backup do estado atual; os arquivos
de voz do backup são copiados por cima (os que só existem no PC atual continuam lá).
"""
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config
from app.database import get_setting, save_setting

DB_NAME_IN_ZIP = "banido_studio.db"
SETTINGS_NAME_IN_ZIP = "settings.json"
VOICES_PREFIX = "vozes_amigos/"
MANIFEST = "manifest.json"
FORMAT_VERSION = 1

AUTO_PREFIX = "banido-backup-auto-"
MANUAL_PREFIX = "banido-backup-"
PRE_RESTORE_PREFIX = "banido-backup-antes-de-restaurar-"
IMPORT_PREFIX = "banido-backup-importado-"
AUTO_KEEP = 4
AUTO_EVERY = timedelta(days=7)

SECRET_WORDS = ("key", "token", "password", "secret", "webhook")
ALLOWED_VOICE_SUFFIXES = {".wav", ".npy", ".json", ".txt"}
BACKUP_DIR_SETTING = "backup_dir"
_NAME_RE = re.compile(r"^banido-backup[\w\-]*\.zip$")

PRIVACY_NOTICE = (
    "Este arquivo contém as vozes dos seus amigos. Guarde-o com cuidado e não o compartilhe em público. "
    "Chaves de API não são incluídas."
)


class BackupError(Exception):
    """Erro com mensagem pronta para mostrar ao usuário."""


def is_secret_key(key: str) -> bool:
    k = (key or "").lower()
    return any(w in k for w in SECRET_WORDS)


# ------------------------------------------------------------------ pasta dos backups

def default_backup_dir() -> Path:
    return config.BASE_DIR / "backups"


def get_backup_dir() -> Path:
    raw = (get_setting(BACKUP_DIR_SETTING, "") or "").strip()
    folder = Path(raw).expanduser() if raw else default_backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def set_backup_dir(folder: str) -> Path:
    folder = (folder or "").strip()
    if not folder:
        save_setting(BACKUP_DIR_SETTING, "")
        return get_backup_dir()
    p = Path(folder).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".banido_teste_escrita"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:
        raise BackupError(f"Não consegui usar essa pasta ({e}). Escolha outra pasta com permissão de escrita.")
    save_setting(BACKUP_DIR_SETTING, str(p))
    return p


def resolve_backup_file(name: str) -> Path:
    """Aceita só nomes simples de backups da pasta configurada (nada de caminhos)."""
    if not name or not _NAME_RE.match(name) or "/" in name or "\\" in name:
        raise BackupError("Nome de backup inválido.")
    p = get_backup_dir() / name
    if not p.is_file():
        raise BackupError("Esse backup não está mais na pasta de backups.")
    return p


# ------------------------------------------------------------------ criar

def _copy_db_without_secrets(dest: Path) -> int:
    """Copia o SQLite pela API de backup (consolida o WAL) e apaga as chaves da cópia."""
    src = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
        rows = dst.execute("SELECT key FROM settings").fetchall()
        for (k,) in rows:
            if is_secret_key(k):
                dst.execute("UPDATE settings SET value = '' WHERE key = ?", (k,))
        dst.commit()
        return int(dst.execute("SELECT COUNT(*) FROM videos").fetchone()[0])
    finally:
        dst.close()
        src.close()


def _settings_without_secrets() -> Dict[str, Any]:
    try:
        data = json.loads(config.SETTINGS_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: v for k, v in data.items() if not is_secret_key(k)}


def _voice_files() -> List[Path]:
    root = config.VOICE_BANK_DIR
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in ALLOWED_VOICE_SUFFIXES)


def create_backup(prefix: str = MANUAL_PREFIX, folder: Optional[Path] = None) -> Path:
    folder = folder or get_backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final = folder / f"{prefix}{stamp}.zip"
    tmp_zip = final.with_suffix(".zip.parcial")
    voices = _voice_files()
    friends = sorted({p.relative_to(config.VOICE_BANK_DIR).parts[0] for p in voices})
    with tempfile.TemporaryDirectory() as td:
        db_copy = Path(td) / DB_NAME_IN_ZIP
        videos = _copy_db_without_secrets(db_copy)
        manifest = {
            "formato": FORMAT_VERSION,
            "criado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "vozes": friends,
            "arquivos_de_voz": len(voices),
            "videos_no_banco": videos,
            "aviso": PRIVACY_NOTICE,
        }
        try:
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
                zf.write(db_copy, DB_NAME_IN_ZIP)
                zf.writestr(SETTINGS_NAME_IN_ZIP, json.dumps(_settings_without_secrets(), ensure_ascii=False, indent=2))
                for p in voices:
                    zf.write(p, VOICES_PREFIX + p.relative_to(config.VOICE_BANK_DIR).as_posix())
            tmp_zip.replace(final)
        finally:
            tmp_zip.unlink(missing_ok=True)
    return final


# ------------------------------------------------------------------ listar e verificar

def list_backups(folder: Optional[Path] = None) -> List[Dict[str, Any]]:
    folder = folder or get_backup_dir()
    out = []
    for p in folder.glob("banido-backup*.zip"):
        if not _NAME_RE.match(p.name):
            continue
        st = p.stat()
        kind = ("auto" if p.name.startswith(AUTO_PREFIX) else
                "antes_de_restaurar" if p.name.startswith(PRE_RESTORE_PREFIX) else
                "importado" if p.name.startswith(IMPORT_PREFIX) else "manual")
        out.append({
            "name": p.name,
            "kind": kind,
            "size_mb": round(st.st_size / (1024 * 1024), 2),
            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "_mtime": st.st_mtime,
        })
    out.sort(key=lambda d: d["_mtime"], reverse=True)
    for d in out:
        d.pop("_mtime")
    return out


def _safe_member(name: str) -> Optional[str]:
    """Aceita só os arquivos que o próprio backup cria; recusa qualquer caminho suspeito."""
    if name in (MANIFEST, DB_NAME_IN_ZIP, SETTINGS_NAME_IN_ZIP):
        return name
    if not name.startswith(VOICES_PREFIX):
        return None
    parts = name[len(VOICES_PREFIX):].split("/")
    if len(parts) < 2 or any(p in ("", ".", "..") or ":" in p or "\\" in p for p in parts):
        return None
    if Path(parts[-1]).suffix.lower() not in ALLOWED_VOICE_SUFFIXES:
        return None
    return name


def verify_backup(zip_path: Path) -> Dict[str, Any]:
    """Confere o .zip e o SQLite dentro dele e descreve o que a restauração vai fazer."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise BackupError("Este arquivo não é um .zip válido.")
    with zf:
        bad = zf.testzip()
        if bad:
            raise BackupError(f"O backup está corrompido (arquivo com defeito: {bad}).")
        names = zf.namelist()
        if MANIFEST not in names or DB_NAME_IN_ZIP not in names:
            raise BackupError("Este .zip não parece ser um backup do Banido Studio.")
        try:
            manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
        except Exception:
            raise BackupError("O backup está sem o registro de conteúdo (manifest) legível.")
        if manifest.get("formato", 0) > FORMAT_VERSION:
            raise BackupError("Este backup foi feito por uma versão mais nova do programa. Atualize antes de restaurar.")
        strange = [n for n in names if not n.endswith("/") and _safe_member(n) is None]
        if strange:
            raise BackupError("O backup contém arquivos inesperados e foi recusado por segurança.")
        with tempfile.TemporaryDirectory() as td:
            db_tmp = Path(td) / DB_NAME_IN_ZIP
            db_tmp.write_bytes(zf.read(DB_NAME_IN_ZIP))
            conn = sqlite3.connect(f"file:{db_tmp}?mode=ro", uri=True)
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("O banco de dados dentro do backup está corrompido.")
                videos = int(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0])
            except sqlite3.DatabaseError:
                raise BackupError("O banco de dados dentro do backup não pôde ser lido.")
            finally:
                conn.close()
        voice_names = [n for n in names if n.startswith(VOICES_PREFIX) and not n.endswith("/")]
    overwritten = [n for n in voice_names if (config.VOICE_BANK_DIR / n[len(VOICES_PREFIX):]).exists()]
    return {
        "name": zip_path.name,
        "criado_em": manifest.get("criado_em", "desconhecida"),
        "vozes": manifest.get("vozes", []),
        "arquivos_de_voz": len(voice_names),
        "videos_no_backup": videos,
        "arquivos_de_voz_que_serao_substituidos": len(overwritten),
        "banco_sera_substituido": True,
        "aviso": "O banco de dados atual será substituído pelo do backup. Um backup do estado atual é criado antes, "
                 "nenhum arquivo é apagado e suas chaves de API continuam como estão.",
    }


# ------------------------------------------------------------------ restaurar

def _current_preserved_settings() -> Dict[str, str]:
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()
    return {k: v for k, v in rows if is_secret_key(k) or k == BACKUP_DIR_SETTING}


def _rewrite_settings_json() -> None:
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
    try:
        data = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    finally:
        conn.close()
    config.SETTINGS_JSON_PATH.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


def restore_backup(zip_path: Path) -> Dict[str, Any]:
    """Verifica, guarda o estado atual e restaura. Devolve o resumo e o nome do backup de segurança."""
    info = verify_backup(zip_path)
    safety = create_backup(prefix=PRE_RESTORE_PREFIX)
    preserved = _current_preserved_settings()
    copied = 0
    with zipfile.ZipFile(zip_path) as zf, tempfile.TemporaryDirectory() as td:
        db_tmp = Path(td) / DB_NAME_IN_ZIP
        db_tmp.write_bytes(zf.read(DB_NAME_IN_ZIP))
        src = sqlite3.connect(str(db_tmp))
        dst = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
        try:
            src.backup(dst)
            for k, v in preserved.items():
                dst.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
            dst.commit()
        finally:
            dst.close()
            src.close()
        _rewrite_settings_json()
        root = config.VOICE_BANK_DIR.resolve()
        for name in zf.namelist():
            if not name.startswith(VOICES_PREFIX) or name.endswith("/") or _safe_member(name) is None:
                continue
            target = (config.VOICE_BANK_DIR / name[len(VOICES_PREFIX):]).resolve()
            if root not in target.parents:
                continue  # proteção extra contra caminhos fora da pasta de vozes
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as fsrc, open(target, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            copied += 1
    info["arquivos_de_voz_restaurados"] = copied
    info["backup_de_seguranca"] = safety.name
    return info


# ------------------------------------------------------------------ automático

def prune_auto_backups(folder: Optional[Path] = None, keep: int = AUTO_KEEP) -> List[str]:
    """Mantém só os `keep` backups automáticos mais novos. Manuais e de segurança nunca são apagados."""
    autos = [b for b in list_backups(folder) if b["kind"] == "auto"]
    removed = []
    for b in autos[keep:]:
        try:
            ((folder or get_backup_dir()) / b["name"]).unlink()
            removed.append(b["name"])
        except OSError:
            pass
    return removed


def maybe_auto_backup(now: Optional[datetime] = None) -> Optional[Path]:
    """Cria um backup automático se o último tiver mais de 7 dias. Nunca levanta erro."""
    try:
        now = now or datetime.now()
        folder = get_backup_dir()
        autos = [b for b in list_backups(folder) if b["kind"] == "auto"]
        if autos:
            last = datetime.strptime(autos[0]["modified"], "%Y-%m-%d %H:%M:%S")
            if now - last < AUTO_EVERY:
                return None
        created = create_backup(prefix=AUTO_PREFIX, folder=folder)
        prune_auto_backups(folder)
        return created
    except Exception as e:
        print(f"[BACKUP] Backup automático não foi feito: {e}", flush=True)
        return None
