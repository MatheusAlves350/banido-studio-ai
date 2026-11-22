import os
import sqlite3
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.config import DB_PATH, SETTINGS_JSON_PATH, LOGS_DIR


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


DEFAULT_GAMES_HISTORY = [
    "Overwatch 2",
    "Forza Horizon 5",
    "Counter-Strike"
]


def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT UNIQUE,
        file_path TEXT,
        size_gb REAL,
        duration_sec REAL,
        mtime REAL,
        status TEXT DEFAULT 'unprocessed',
        progress_msg TEXT DEFAULT '',
        progress_pct REAL DEFAULT 0.0,
        game_name TEXT DEFAULT '',
        session_context TEXT DEFAULT '',
        chosen_title TEXT DEFAULT '',
        mosaic_path TEXT DEFAULT '',
        srt_path TEXT DEFAULT '',
        audio_normalized INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # Migração automática para bancos existentes sem colunas mais novas
    c.execute("PRAGMA table_info(videos)")
    existing_cols = {row["name"] for row in c.fetchall()}
    if "audio_normalized" not in existing_cols:
        c.execute("ALTER TABLE videos ADD COLUMN audio_normalized INTEGER DEFAULT 0")
    if "session_context" not in existing_cols:
        c.execute("ALTER TABLE videos ADD COLUMN session_context TEXT DEFAULT ''")
    c.execute("""
    CREATE TABLE IF NOT EXISTS video_data (
        video_id INTEGER PRIMARY KEY,
        data_json TEXT,
        FOREIGN KEY(video_id) REFERENCES videos(id)
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS game_history (
        name TEXT PRIMARY KEY,
        use_count INTEGER DEFAULT 1,
        last_used_at TEXT DEFAULT '',
        default_context TEXT DEFAULT ''
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS job_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER,
        filename TEXT,
        engine TEXT DEFAULT 'gemini',
        model TEXT DEFAULT '',
        manual_game TEXT DEFAULT '',
        pid INTEGER DEFAULT 0,
        status TEXT DEFAULT 'running',
        last_step TEXT DEFAULT '',
        progress_pct REAL DEFAULT 0.0,
        started_at TEXT,
        started_ts REAL DEFAULT 0.0,
        finished_at TEXT DEFAULT '',
        elapsed_sec REAL DEFAULT 0.0,
        error_detail TEXT DEFAULT '',
        log_file TEXT DEFAULT ''
    )
    """)

    # Popular histórico de jogos com padrões + jogos já presentes na tabela videos
    now = now_str()
    for g in DEFAULT_GAMES_HISTORY:
        c.execute(
            "INSERT OR IGNORE INTO game_history (name, use_count, last_used_at, default_context) VALUES (?, 1, ?, '')",
            (g, now)
        )
    c.execute("SELECT DISTINCT game_name FROM videos WHERE game_name IS NOT NULL AND TRIM(game_name) != ''")
    for r in c.fetchall():
        gn = str(r["game_name"]).strip()
        if gn and gn.lower() not in ("gameplay", "videogame"):
            c.execute(
                "INSERT OR IGNORE INTO game_history (name, use_count, last_used_at, default_context) VALUES (?, 1, ?, '')",
                (gn, now)
            )

    conn.commit()
    conn.close()


def startup_maintenance():
    """
    Executado APENAS UMA VEZ quando o servidor inicia (startup).
    Inicializa tabelas, sincroniza settings.json e limpa status órfãos
    de sessões anteriores em que a janela foi fechada.
    """
    init_db()
    sync_settings_from_file()
    reset_interrupted_tasks()


def sync_settings_from_file():
    if SETTINGS_JSON_PATH.exists():
        try:
            with open(SETTINGS_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            conn = get_connection()
            c = conn.cursor()
            for k, v in data.items():
                if v is not None and str(v) != "":
                    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Erro ao ler settings.json: {e}")


def save_setting(key: str, value: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()

    c.execute("SELECT key, value FROM settings")
    rows = c.fetchall()
    all_settings = {r["key"]: r["value"] for r in rows}
    conn.close()

    try:
        with open(SETTINGS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(all_settings, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Erro ao salvar settings.json: {e}")


def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()

    if row and row["value"]:
        return row["value"]

    if SETTINGS_JSON_PATH.exists():
        try:
            with open(SETTINGS_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if key in data and data[key]:
                return data[key]
        except Exception:
            pass

    env_map = {
        "google_api_key": "GOOGLE_API_KEY",
        "nvidia_api_key": "NVIDIA_API_KEY",
        "google_model": "BANIDO_GOOGLE_MODEL",
        "nvidia_model": "BANIDO_NVIDIA_MODEL",
        "ai_provider": "BANIDO_AI_PROVIDER",
    }
    env_name = env_map.get(key)
    if env_name:
        env_val = (os.getenv(env_name) or "").strip()
        if env_val:
            return env_val

    return default


def reset_interrupted_tasks():
    """
    Chamado SOMENTE no boot do servidor para limpar jobs que ficaram órfãos
    quando o terminal foi fechado.
    Se o vídeo já tiver transcrição salva em video_data (checkpoint pré-IA),
    restaura como 'completed' para nunca perder o trabalho do Whisper/SpeechBrain!
    """
    conn = get_connection()
    c = conn.cursor()

    c.execute("SELECT id FROM videos WHERE status = 'processing'")
    proc_rows = c.fetchall()
    for pr in proc_rows:
        vid = pr["id"]
        c.execute("SELECT data_json FROM video_data WHERE video_id = ?", (vid,))
        d_row = c.fetchone()
        has_segments = False
        if d_row and d_row["data_json"]:
            try:
                parsed = json.loads(d_row["data_json"])
                has_segments = bool(parsed.get("segments"))
            except Exception:
                has_segments = False

        if has_segments:
            c.execute("""
            UPDATE videos
            SET status = 'completed',
                progress_msg = 'Análise da IA interrompida (transcrição, vozes e .SRT preservados! Clique em Re-analisar só com IA).',
                progress_pct = 1.0
            WHERE id = ?
            """, (vid,))
        else:
            c.execute("""
            UPDATE videos
            SET status = 'unprocessed',
                progress_msg = 'Processamento anterior interrompido (checkpoint em disco preservado). Clique em Processar para retomar.',
                progress_pct = 0.0
            WHERE id = ?
            """, (vid,))

    now = now_str()
    now_epoch = time.time()
    c.execute("SELECT id, started_ts, log_file FROM job_history WHERE status = 'running'")
    orphan_jobs = c.fetchall()
    for j in orphan_jobs:
        elapsed = round(max(0.0, now_epoch - (j["started_ts"] or now_epoch)), 1)
        c.execute("""
        UPDATE job_history
        SET status = 'interrupted',
            finished_at = ?,
            elapsed_sec = ?,
            error_detail = 'Servidor foi fechado durante a execução.'
        WHERE id = ?
        """, (now, elapsed, j["id"]))
        if j["log_file"]:
            try:
                with open(j["log_file"], "a", encoding="utf-8") as lf:
                    lf.write(f"\n[{now}] [SISTEMA] ⚠️ Execução interrompida porque o servidor foi fechado.\n")
            except Exception:
                pass

    conn.commit()
    conn.close()


def has_video_data(video_id: int) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT 1 FROM video_data WHERE video_id = ? AND data_json IS NOT NULL AND data_json != ''", (video_id,))
    row = c.fetchone()
    conn.close()
    return row is not None


def reset_video_status(video_id: int, msg: str = "", restore_completed: bool = False):
    conn = get_connection()
    c = conn.cursor()
    if restore_completed:
        c.execute("""
        UPDATE videos
        SET status = 'completed',
            progress_msg = ?,
            progress_pct = 1.0
        WHERE id = ?
        """, (msg or "Concluído!", video_id))
    else:
        c.execute("""
        UPDATE videos
        SET status = 'unprocessed',
            progress_msg = ?,
            progress_pct = 0.0
        WHERE id = ?
        """, (msg, video_id))
    conn.commit()
    conn.close()


def sync_obs_videos(obs_dir: Path):
    """
    Sincroniza a pasta de gravações OBS configurada sem NUNCA resetar vídeos em processamento
    e sem apagar o banco caso o diretório esteja temporariamente indisponível.
    """
    if not obs_dir.exists():
        print(f"[AVISO] Diretório OBS indisponível ({obs_dir}); sincronização ignorada para proteger o banco.")
        return

    conn = get_connection()
    c = conn.cursor()

    current_files = list(obs_dir.glob("*.mkv"))
    current_filenames = {f.name: f for f in current_files}

    # 1. Apagar do banco vídeos que não existem mais no disco (exceto se estiver processando)
    c.execute("SELECT id, filename, status FROM videos")
    existing_rows = c.fetchall()
    for row in existing_rows:
        if row["filename"] not in current_filenames and row["status"] != "processing":
            c.execute("DELETE FROM video_data WHERE video_id = ?", (row["id"],))
            c.execute("DELETE FROM videos WHERE id = ?", (row["id"],))

    # 2. Inserir novos arquivos
    for name, fpath in current_filenames.items():
        try:
            stat = fpath.stat()
            size_gb = round(stat.st_size / (1024 ** 3), 2)
            mtime = stat.st_mtime
        except OSError:
            continue

        c.execute("SELECT id FROM videos WHERE filename = ?", (name,))
        exists = c.fetchone()
        if not exists:
            srt_candidate = fpath.with_suffix(".srt")
            srt_str = str(srt_candidate) if srt_candidate.exists() else ""
            c.execute("""
            INSERT INTO videos (filename, file_path, size_gb, duration_sec, mtime, status, srt_path)
            VALUES (?, ?, ?, 0.0, ?, 'unprocessed', ?)
            """, (name, str(fpath), size_gb, mtime, srt_str))

    conn.commit()
    conn.close()


def record_game_history(game_name: str, default_context: Optional[str] = None, increment: bool = True):
    clean = " ".join(str(game_name or "").strip().split())
    if not clean or clean.lower() in ("gameplay", "videogame", "desconhecido"):
        return
    conn = get_connection()
    c = conn.cursor()
    # Verificar existência sem diferenciar maiúsculas/minúsculas para evitar duplicatas ("far cry 4" vs "Far Cry 4")
    c.execute("SELECT name, use_count, default_context FROM game_history WHERE LOWER(name) = LOWER(?)", (clean,))
    row = c.fetchone()
    now = now_str()
    if row:
        canonical = row["name"]
        new_count = int(row["use_count"] or 0) + (1 if increment else 0)
        new_ctx = default_context.strip() if default_context is not None else (row["default_context"] or "")
        c.execute(
            "UPDATE game_history SET use_count = ?, last_used_at = ?, default_context = ? WHERE name = ?",
            (new_count, now, new_ctx, canonical)
        )
    else:
        ctx_val = (default_context or "").strip()
        c.execute(
            "INSERT INTO game_history (name, use_count, last_used_at, default_context) VALUES (?, 1, ?, ?)",
            (clean, now, ctx_val)
        )
    conn.commit()
    conn.close()


def get_game_history(limit: int = 30) -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT name, use_count, last_used_at, default_context
    FROM game_history
    ORDER BY last_used_at DESC, use_count DESC, LOWER(name) ASC
    LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def delete_game_from_history(game_name: str) -> bool:
    clean = str(game_name or "").strip()
    if not clean:
        return False
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM game_history WHERE LOWER(name) = LOWER(?)", (clean,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def update_video_game_and_context(
    video_id: int,
    game_name: Optional[str] = None,
    session_context: Optional[str] = None,
    save_default_context: bool = False
):
    conn = get_connection()
    c = conn.cursor()
    clean_game = " ".join(str(game_name or "").strip().split()) if game_name is not None else None
    clean_ctx = str(session_context or "").strip() if session_context is not None else None

    if clean_game is not None and clean_ctx is not None:
        c.execute(
            "UPDATE videos SET game_name = ?, session_context = ? WHERE id = ?",
            (clean_game, clean_ctx, video_id)
        )
    elif clean_game is not None:
        c.execute("UPDATE videos SET game_name = ? WHERE id = ?", (clean_game, video_id))
    elif clean_ctx is not None:
        c.execute("UPDATE videos SET session_context = ? WHERE id = ?", (clean_ctx, video_id))
    conn.commit()
    conn.close()

    if clean_game:
        record_game_history(
            clean_game,
            default_context=clean_ctx if (save_default_context and clean_ctx is not None) else None,
            increment=True
        )


def update_video_paths_after_rename(
    video_id: int,
    new_filename: str,
    new_file_path: str,
    new_srt_path: Optional[str],
    chosen_title: str = "",
    game_name: Optional[str] = None
):
    clean_game = " ".join(str(game_name or "").strip().split()) if game_name else ""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    UPDATE videos
    SET filename = ?,
        file_path = ?,
        srt_path = COALESCE(?, srt_path),
        chosen_title = ?,
        game_name = COALESCE(NULLIF(?, ''), game_name)
    WHERE id = ?
    """, (new_filename, new_file_path, new_srt_path, chosen_title, clean_game, video_id))
    conn.commit()
    conn.close()
    if clean_game:
        record_game_history(clean_game, increment=True)


def get_all_videos() -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM videos ORDER BY mtime DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_video_by_id(video_id: int, include_data: bool = True) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    res = dict(row)

    if include_data:
        c.execute("SELECT data_json FROM video_data WHERE video_id = ?", (video_id,))
        d_row = c.fetchone()
        res["data"] = json.loads(d_row["data_json"]) if d_row and d_row["data_json"] else {}
    else:
        res["data"] = None

    # Incluir resumo do último job desse vídeo
    c.execute("""
    SELECT id, status, engine, model, pid, started_at, started_ts, finished_at, elapsed_sec, last_step, error_detail
    FROM job_history
    WHERE video_id = ?
    ORDER BY id DESC LIMIT 1
    """, (video_id,))
    j_row = c.fetchone()
    if j_row:
        jd = dict(j_row)
        if jd["status"] == "running" and jd.get("started_ts"):
            jd["elapsed_sec"] = round(max(0.0, time.time() - jd["started_ts"]), 1)
        res["latest_job"] = jd
    else:
        res["latest_job"] = None

    conn.close()
    return res


def update_video_progress(video_id: int, msg: str, pct: float, status: str = "processing"):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE videos SET progress_msg = ?, progress_pct = ?, status = ? WHERE id = ?",
        (msg, pct, status, video_id)
    )
    conn.commit()
    conn.close()


def set_video_audio_normalized(
    video_id: int,
    normalized: bool = True,
    size_gb: Optional[float] = None,
    mtime: Optional[float] = None
):
    conn = get_connection()
    c = conn.cursor()
    val = 1 if normalized else 0
    if size_gb is not None and mtime is not None:
        c.execute(
            "UPDATE videos SET audio_normalized = ?, size_gb = ?, mtime = ? WHERE id = ?",
            (val, size_gb, mtime, video_id)
        )
    elif size_gb is not None:
        c.execute(
            "UPDATE videos SET audio_normalized = ?, size_gb = ? WHERE id = ?",
            (val, size_gb, video_id)
        )
    else:
        c.execute(
            "UPDATE videos SET audio_normalized = ? WHERE id = ?",
            (val, video_id)
        )
    conn.commit()
    conn.close()


def save_video_transcription_checkpoint(
    video_id: int,
    game_name: str,
    duration: float,
    mosaic_path: str,
    srt_path: str,
    ai_data: Dict[str, Any],
    progress_msg: str = "Transcrição e vozes salvas! Consultando IA...",
    progress_pct: float = 0.90
):
    """
    Persiste imediatamente a transcrição, telemetria, amostras de vozes, mosaico e .SRT
    no SQLite ANTES de chamar a API da IA (mantendo status='processing').
    Assim, mesmo se a API falhar, der timeout ou o PC reiniciar, NADA se perde.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    UPDATE videos SET
        progress_msg = ?,
        progress_pct = ?,
        game_name = ?,
        duration_sec = ?,
        mosaic_path = ?,
        srt_path = ?
    WHERE id = ?
    """, (progress_msg, progress_pct, game_name, duration, mosaic_path, srt_path, video_id))

    data_str = json.dumps(ai_data, ensure_ascii=False)
    c.execute("INSERT OR REPLACE INTO video_data (video_id, data_json) VALUES (?, ?)", (video_id, data_str))
    conn.commit()
    conn.close()

    if game_name:
        record_game_history(game_name, increment=True)
    for og in (ai_data.get("other_games") or []):
        if isinstance(og, str) and og.strip():
            record_game_history(og.strip(), increment=False)


def save_video_result(
    video_id: int,
    game_name: str,
    duration: float,
    mosaic_path: str,
    srt_path: str,
    ai_data: Dict[str, Any],
    status_msg: str = "Concluído!"
):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    UPDATE videos SET
        status = 'completed',
        progress_msg = ?,
        progress_pct = 1.0,
        game_name = ?,
        duration_sec = ?,
        mosaic_path = ?,
        srt_path = ?
    WHERE id = ?
    """, (status_msg, game_name, duration, mosaic_path, srt_path, video_id))

    data_str = json.dumps(ai_data, ensure_ascii=False)
    c.execute("INSERT OR REPLACE INTO video_data (video_id, data_json) VALUES (?, ?)", (video_id, data_str))
    conn.commit()
    conn.close()

    if game_name:
        record_game_history(game_name, increment=True)
    for og in (ai_data.get("other_games") or []):
        if isinstance(og, str) and og.strip():
            record_game_history(og.strip(), increment=False)


# ==============================================================================
# HISTÓRICO DE EXECUÇÕES E LOGS (job_history)
# ==============================================================================

def create_job_record(
    video_id: int,
    filename: str,
    engine: str,
    model: str,
    manual_game: str = ""
) -> Dict[str, Any]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    started_at = now_str()
    started_ts = time.time()

    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    INSERT INTO job_history (
        video_id, filename, engine, model, manual_game,
        status, last_step, progress_pct, started_at, started_ts
    ) VALUES (?, ?, ?, ?, ?, 'running', 'Inicializando worker...', 0.01, ?, ?)
    """, (video_id, filename, engine, model, manual_game, started_at, started_ts))
    job_id = c.lastrowid
    log_path = LOGS_DIR / f"job_{job_id}.log"
    c.execute("UPDATE job_history SET log_file = ? WHERE id = ?", (str(log_path), job_id))
    conn.commit()
    conn.close()

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("========================================================================\n")
        f.write(f"🎬 BANIDO STUDIO AI - LOG DE EXECUÇÃO (JOB #{job_id})\n")
        f.write(f"Arquivo : {filename} (Vídeo ID #{video_id})\n")
        f.write(f"Início  : {started_at}\n")
        f.write(f"Motor IA: {engine} | Modelo: {model or 'padrão'}\n")
        if manual_game:
            f.write(f"Jogo inf: {manual_game}\n")
        f.write("========================================================================\n")

    return {
        "job_id": job_id,
        "log_file": str(log_path),
        "started_at": started_at
    }


def set_job_pid(job_id: int, pid: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE job_history SET pid = ? WHERE id = ?", (pid, job_id))
    conn.commit()
    conn.close()


def update_job_step(job_id: int, msg: str, pct: float):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT started_ts FROM job_history WHERE id = ?", (job_id,))
    row = c.fetchone()
    elapsed = round(max(0.0, time.time() - (row["started_ts"] if row and row["started_ts"] else time.time())), 1)
    c.execute("""
    UPDATE job_history
    SET last_step = ?, progress_pct = ?, elapsed_sec = ?
    WHERE id = ?
    """, (msg, pct, elapsed, job_id))
    conn.commit()
    conn.close()


def finish_job_record(job_id: int, status: str, last_step: str = "", error_detail: str = ""):
    finished_at = now_str()
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT started_ts FROM job_history WHERE id = ?", (job_id,))
    row = c.fetchone()
    elapsed = round(max(0.0, time.time() - (row["started_ts"] if row and row["started_ts"] else time.time())), 1)
    c.execute("""
    UPDATE job_history
    SET status = ?,
        last_step = COALESCE(NULLIF(?, ''), last_step),
        progress_pct = CASE WHEN ? = 'completed' THEN 1.0 ELSE progress_pct END,
        finished_at = ?,
        elapsed_sec = ?,
        error_detail = ?
    WHERE id = ?
    """, (status, last_step, status, finished_at, elapsed, error_detail, job_id))
    conn.commit()
    conn.close()


def get_job_count() -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS cnt FROM job_history")
    row = c.fetchone()
    conn.close()
    return int(row["cnt"]) if row else 0


def get_job_history(limit: int = 50, video_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    if video_id is not None:
        c.execute("SELECT * FROM job_history WHERE video_id = ? ORDER BY id DESC LIMIT ?", (video_id, limit))
    else:
        c.execute("SELECT * FROM job_history ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    now_epoch = time.time()
    for r in rows:
        if r["status"] == "running" and r.get("started_ts"):
            r["elapsed_sec"] = round(max(0.0, now_epoch - r["started_ts"]), 1)
    return rows


def get_job_by_id(job_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM job_history WHERE id = ?", (job_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    res = dict(row)
    if res["status"] == "running" and res.get("started_ts"):
        res["elapsed_sec"] = round(max(0.0, time.time() - res["started_ts"]), 1)
    return res


def get_latest_job_for_video(video_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM job_history WHERE video_id = ? ORDER BY id DESC LIMIT 1", (video_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    res = dict(row)
    if res["status"] == "running" and res.get("started_ts"):
        res["elapsed_sec"] = round(max(0.0, time.time() - res["started_ts"]), 1)
    return res
