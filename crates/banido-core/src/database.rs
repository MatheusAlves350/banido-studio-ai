//! SQLite WAL persistence layer compatible with existing `banido_studio.db` files.
//!
//! Improvements over `app/database.py`:
//! - Enforces `PRAGMA foreign_keys = ON` and creates explicit indexes on
//!   `job_history(video_id)`, `job_history(status)`, and `videos(is_missing, mtime)`.
//! - Updates `file_size_bytes` inside [`StudioDb::set_video_audio_normalized`] so
//!   external file renames after audio normalization remain reassociable.
//! - Uses atomic temp-file writes when persisting `settings.json`.

pub use crate::database_models::{
    GameHistoryEntry, JobRecord, VideoRecord, DEFAULT_GAMES_HISTORY,
};
use crate::database_models::{map_job_row, map_video_row};
use crate::{CoreError, CoreResult};
use chrono::Local;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// Handle to the SQLite WAL database, `settings.json`, and log directory.
#[derive(Debug, Clone)]
pub struct StudioDb {
    db_path: PathBuf,
    settings_json_path: PathBuf,
    logs_dir: PathBuf,
}

impl StudioDb {
    /// Creates a new database handle without opening a connection yet.
    pub fn new(db_path: PathBuf, settings_json_path: PathBuf, logs_dir: PathBuf) -> Self {
        Self {
            db_path,
            settings_json_path,
            logs_dir,
        }
    }

    /// Opens a configured WAL connection with `foreign_keys = ON`.
    pub fn open(&self) -> CoreResult<Connection> {
        if let Some(parent) = self.db_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(&self.db_path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        conn.pragma_update(None, "busy_timeout", 30_000)?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        Ok(conn)
    }

    /// Initializes all 5 tables, idempotent column migrations, indexes, and seeds.
    pub fn init_db(&self) -> CoreResult<()> {
        let conn = self.open()?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE,
                file_path TEXT,
                size_gb REAL DEFAULT 0.0,
                duration_sec REAL DEFAULT 0.0,
                mtime REAL DEFAULT 0.0,
                status TEXT DEFAULT 'unprocessed',
                progress_msg TEXT DEFAULT '',
                progress_pct REAL DEFAULT 0.0,
                game_name TEXT DEFAULT '',
                session_context TEXT DEFAULT '',
                chosen_title TEXT DEFAULT '',
                mosaic_path TEXT DEFAULT '',
                srt_path TEXT DEFAULT '',
                audio_normalized INTEGER DEFAULT 0,
                is_missing INTEGER DEFAULT 0,
                file_size_bytes INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS video_data (
                video_id INTEGER PRIMARY KEY,
                data_json TEXT,
                FOREIGN KEY(video_id) REFERENCES videos(id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS game_history (
                name TEXT PRIMARY KEY,
                use_count INTEGER DEFAULT 1,
                last_used_at TEXT DEFAULT '',
                default_context TEXT DEFAULT ''
            );
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
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_video_id ON job_history(video_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON job_history(status);
            CREATE INDEX IF NOT EXISTS idx_videos_missing_mtime ON videos(is_missing, mtime DESC);",
        )?;

        let now = now_str();
        for game in DEFAULT_GAMES_HISTORY {
            conn.execute(
                "INSERT OR IGNORE INTO game_history (name, use_count, last_used_at, default_context) VALUES (?1, 1, ?2, '')",
                params![game, now],
            )?;
        }
        Ok(())
    }

    /// Saves a key-value setting in SQLite and atomically mirrors `settings.json`.
    pub fn save_setting(&self, key: &str, value: &str) -> CoreResult<()> {
        let conn = self.open()?;
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?1, ?2)",
            params![key, value],
        )?;
        let mut stmt = conn.prepare("SELECT key, value FROM settings")?;
        let mut map = HashMap::new();
        let rows = stmt.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?;
        for r in rows {
            let (k, v) = r?;
            map.insert(k, v);
        }
        if let Some(parent) = self.settings_json_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let tmp = self.settings_json_path.with_extension("json.tmp");
        fs::write(&tmp, serde_json::to_string_pretty(&map)?)?;
        fs::rename(&tmp, &self.settings_json_path)?;
        Ok(())
    }

    /// Reads a setting from SQLite with fallback to `settings.json` and `default`.
    pub fn get_setting(&self, key: &str, default: &str) -> CoreResult<String> {
        let conn = self.open()?;
        let val: Option<String> = conn
            .query_row(
                "SELECT value FROM settings WHERE key = ?1",
                params![key],
                |r| r.get(0),
            )
            .optional()?;
        if let Some(v) = val.filter(|s| !s.is_empty()) {
            return Ok(v);
        }
        if let Ok(raw) = fs::read_to_string(&self.settings_json_path) {
            if let Ok(map) = serde_json::from_str::<HashMap<String, Value>>(&raw) {
                if let Some(v) = map.get(key).and_then(Value::as_str).filter(|s| !s.is_empty()) {
                    return Ok(v.to_string());
                }
            }
        }
        Ok(default.to_string())
    }

    /// Synchronizes an OBS recordings directory with the `videos` table.
    ///
    /// Supports soft-deletion when files vanish, active-recording age filtering
    /// (`min_stable_age_sec`), and rename reassociation via `(mtime ±2s, file_size_bytes)`.
    pub fn sync_obs_videos(&self, obs_dir: &Path, min_stable_age_sec: f64) -> CoreResult<usize> {
        if !obs_dir.is_dir() {
            return Ok(0);
        }
        let conn = self.open()?;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);

        let mut disk_files: HashMap<String, (PathBuf, i64, f64)> = HashMap::new();
        for entry in fs::read_dir(obs_dir)?.flatten() {
            let p = entry.path();
            if !p.is_file() {
                continue;
            }
            let Some(ext) = p.extension().and_then(|e| e.to_str()) else {
                continue;
            };
            if !ext.eq_ignore_ascii_case("mkv") {
                continue;
            }
            let Some(name) = p.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            if name.starts_with('.') {
                continue;
            }
            let Ok(meta) = entry.metadata() else {
                continue;
            };
            let mtime = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            disk_files.insert(name.to_string(), (p, meta.len() as i64, mtime));
        }

        // Soft-delete rows missing on disk (preserving active processing status).
        let mut stmt = conn.prepare("SELECT id, filename, status FROM videos")?;
        let existing: Vec<(i64, String, String)> = stmt
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
            .collect::<Result<_, _>>()?;
        drop(stmt);

        let mut known_names: HashSet<String> = HashSet::new();
        for (id, fname, status) in &existing {
            known_names.insert(fname.clone());
            if !disk_files.contains_key(fname) && status != "processing" {
                conn.execute("UPDATE videos SET is_missing = 1 WHERE id = ?1", params![id])?;
            }
        }

        let mut synced = 0usize;
        for (fname, (path, size_bytes, mtime)) in &disk_files {
            if min_stable_age_sec > 0.0 && (now - *mtime) < min_stable_age_sec {
                continue;
            }
            let size_gb = (*size_bytes as f64) / (1024.0 * 1024.0 * 1024.0);
            let path_str = path.to_string_lossy().into_owned();
            if known_names.contains(fname) {
                conn.execute(
                    "UPDATE videos SET file_path = ?1, size_gb = ?2, file_size_bytes = ?3, mtime = ?4, is_missing = 0 WHERE filename = ?5",
                    params![path_str, size_gb, size_bytes, mtime, fname],
                )?;
                synced += 1;
                continue;
            }

            // Reassociate externally renamed file against soft-deleted rows.
            let candidate: Option<i64> = conn
                .query_row(
                    "SELECT id FROM videos WHERE is_missing = 1 AND file_size_bytes = ?1 AND ABS(mtime - ?2) <= 2.0 LIMIT 1",
                    params![size_bytes, mtime],
                    |r| r.get(0),
                )
                .optional()?;
            if let Some(old_id) = candidate {
                conn.execute(
                    "UPDATE videos SET filename = ?1, file_path = ?2, size_gb = ?3, file_size_bytes = ?4, mtime = ?5, is_missing = 0 WHERE id = ?6",
                    params![fname, path_str, size_gb, size_bytes, mtime, old_id],
                )?;
            } else {
                conn.execute(
                    "INSERT INTO videos (filename, file_path, size_gb, file_size_bytes, mtime, status, is_missing) VALUES (?1, ?2, ?3, ?4, ?5, 'unprocessed', 0)",
                    params![fname, path_str, size_gb, size_bytes, mtime],
                )?;
            }
            synced += 1;
        }
        Ok(synced)
    }

    /// Lists all non-missing videos ordered by newest modification time first.
    pub fn get_all_videos(&self) -> CoreResult<Vec<VideoRecord>> {
        let conn = self.open()?;
        let mut stmt = conn.prepare(
            "SELECT id, filename, file_path, size_gb, file_size_bytes, duration_sec, mtime,
                    status, progress_msg, progress_pct, game_name, session_context,
                    chosen_title, mosaic_path, srt_path, audio_normalized, is_missing
             FROM videos WHERE is_missing = 0 ORDER BY mtime DESC",
        )?;
        let rows = stmt
            .query_map([], map_video_row)?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    /// Fetches a single video row and its optional JSON analysis payload.
    pub fn get_video_by_id(&self, video_id: i64) -> CoreResult<Option<(VideoRecord, Option<Value>)>> {
        let conn = self.open()?;
        let row = conn
            .query_row(
                "SELECT id, filename, file_path, size_gb, file_size_bytes, duration_sec, mtime,
                        status, progress_msg, progress_pct, game_name, session_context,
                        chosen_title, mosaic_path, srt_path, audio_normalized, is_missing
                 FROM videos WHERE id = ?1",
                params![video_id],
                map_video_row,
            )
            .optional()?;
        let Some(video) = row else {
            return Ok(None);
        };
        let data_json: Option<String> = conn
            .query_row(
                "SELECT data_json FROM video_data WHERE video_id = ?1",
                params![video_id],
                |r| r.get(0),
            )
            .optional()?;
        let parsed = data_json.and_then(|s| serde_json::from_str::<Value>(&s).ok());
        Ok(Some((video, parsed)))
    }

    /// Persists full analysis results and marks the video `completed`.
    pub fn save_video_result(
        &self,
        video_id: i64,
        game_name: &str,
        duration_sec: f64,
        mosaic_path: &str,
        srt_path: &str,
        data: &Value,
    ) -> CoreResult<()> {
        let conn = self.open()?;
        conn.execute(
            "UPDATE videos SET status = 'completed', progress_pct = 1.0, game_name = ?1, duration_sec = ?2, mosaic_path = ?3, srt_path = ?4 WHERE id = ?5",
            params![game_name, duration_sec, mosaic_path, srt_path, video_id],
        )?;
        conn.execute(
            "INSERT OR REPLACE INTO video_data (video_id, data_json) VALUES (?1, ?2)",
            params![video_id, serde_json::to_string(data)?],
        )?;
        Ok(())
    }

    /// Updates audio normalization status AND keeps `file_size_bytes` synchronized.
    ///
    /// Fixes the legacy bug in `app/database.py:583` where `file_size_bytes`
    /// became stale after lossless remuxing.
    pub fn set_video_audio_normalized(
        &self,
        video_id: i64,
        normalized: bool,
        file_size_bytes: i64,
        mtime: f64,
    ) -> CoreResult<()> {
        let conn = self.open()?;
        let size_gb = (file_size_bytes as f64) / (1024.0 * 1024.0 * 1024.0);
        conn.execute(
            "UPDATE videos SET audio_normalized = ?1, size_gb = ?2, file_size_bytes = ?3, mtime = ?4 WHERE id = ?5",
            params![i32::from(normalized), size_gb, file_size_bytes, mtime, video_id],
        )?;
        Ok(())
    }

    /// Lists up to 40 games from `game_history`.
    pub fn get_game_history(&self) -> CoreResult<Vec<GameHistoryEntry>> {
        let conn = self.open()?;
        let mut stmt = conn.prepare(
            "SELECT name, use_count, last_used_at, default_context FROM game_history ORDER BY use_count DESC, last_used_at DESC LIMIT 40",
        )?;
        let rows = stmt
            .query_map([], |r| {
                Ok(GameHistoryEntry {
                    name: r.get(0)?,
                    use_count: r.get(1)?,
                    last_used_at: r.get(2)?,
                    default_context: r.get(3)?,
                })
            })?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    /// Inserts or increments a game in `game_history` case-insensitively.
    pub fn record_game_history(&self, name: &str, default_context: &str) -> CoreResult<()> {
        let trimmed = name.trim();
        if trimmed.is_empty() {
            return Err(CoreError::Validation("Nome do jogo vazio.".into()));
        }
        let conn = self.open()?;
        let now = now_str();
        let existing: Option<String> = conn
            .query_row(
                "SELECT name FROM game_history WHERE LOWER(name) = LOWER(?1) LIMIT 1",
                params![trimmed],
                |r| r.get(0),
            )
            .optional()?;
        if let Some(canon) = existing {
            conn.execute(
                "UPDATE game_history SET use_count = use_count + 1, last_used_at = ?1, default_context = CASE WHEN ?2 != '' THEN ?2 ELSE default_context END WHERE name = ?3",
                params![now, default_context, canon],
            )?;
        } else {
            conn.execute(
                "INSERT INTO game_history (name, use_count, last_used_at, default_context) VALUES (?1, 1, ?2, ?3)",
                params![trimmed, now, default_context],
            )?;
        }
        Ok(())
    }

    /// Deletes a game from `game_history` case-insensitively.
    pub fn delete_game_from_history(&self, name: &str) -> CoreResult<()> {
        let conn = self.open()?;
        conn.execute(
            "DELETE FROM game_history WHERE LOWER(name) = LOWER(?1)",
            params![name.trim()],
        )?;
        Ok(())
    }

    /// Creates a job record and its corresponding log file atomically.
    pub fn create_job_record(
        &self,
        video_id: i64,
        filename: &str,
        engine: &str,
        model: &str,
    ) -> CoreResult<JobRecord> {
        fs::create_dir_all(&self.logs_dir)?;
        let conn = self.open()?;
        let started_at = now_str();
        let started_ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        conn.execute(
            "INSERT INTO job_history (video_id, filename, engine, model, status, started_at, started_ts) VALUES (?1, ?2, ?3, ?4, 'running', ?5, ?6)",
            params![video_id, filename, engine, model, started_at, started_ts],
        )?;
        let job_id = conn.last_insert_rowid();
        let log_file = self.logs_dir.join(format!("job_{job_id}.log"));
        fs::write(&log_file, "")?;
        let log_str = log_file.to_string_lossy().into_owned();
        conn.execute(
            "UPDATE job_history SET log_file = ?1 WHERE id = ?2",
            params![log_str, job_id],
        )?;
        Ok(JobRecord {
            id: job_id,
            video_id,
            filename: filename.to_string(),
            engine: engine.to_string(),
            model: model.to_string(),
            status: "running".to_string(),
            last_step: String::new(),
            progress_pct: 0.0,
            started_at,
            finished_at: String::new(),
            elapsed_sec: 0.0,
            error_detail: String::new(),
            log_file: log_str,
        })
    }

    /// Returns total count of rows in `job_history`.
    pub fn get_job_count(&self) -> CoreResult<i64> {
        let conn = self.open()?;
        let cnt: i64 = conn.query_row("SELECT COUNT(*) FROM job_history", [], |r| r.get(0))?;
        Ok(cnt)
    }

    /// Lists recent jobs (optionally filtered by `video_id`).
    pub fn get_job_history(&self, limit: i64, video_id: Option<i64>) -> CoreResult<Vec<JobRecord>> {
        let conn = self.open()?;
        if let Some(vid) = video_id {
            let mut stmt = conn.prepare(
                "SELECT id, video_id, filename, engine, model, status, last_step, progress_pct,
                        started_at, finished_at, elapsed_sec, error_detail, log_file
                 FROM job_history WHERE video_id = ?1 ORDER BY id DESC LIMIT ?2",
            )?;
            let rows = stmt
                .query_map(params![vid, limit], map_job_row)?
                .collect::<Result<Vec<_>, _>>()?;
            Ok(rows)
        } else {
            let mut stmt = conn.prepare(
                "SELECT id, video_id, filename, engine, model, status, last_step, progress_pct,
                        started_at, finished_at, elapsed_sec, error_detail, log_file
                 FROM job_history ORDER BY id DESC LIMIT ?1",
            )?;
            let rows = stmt
                .query_map(params![limit], map_job_row)?
                .collect::<Result<Vec<_>, _>>()?;
            Ok(rows)
        }
    }
}

fn now_str() -> String {
    Local::now().format("%Y-%m-%d %H:%M:%S").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_sync_soft_delete_and_reassociate() {
        let dir = tempdir().unwrap();
        let obs = dir.path().join("obs");
        fs::create_dir_all(&obs).unwrap();
        let db = StudioDb::new(
            dir.path().join("studio.db"),
            dir.path().join("settings.json"),
            dir.path().join("logs"),
        );
        db.init_db().unwrap();

        let f1 = obs.join("rec.mkv");
        fs::write(&f1, vec![b'x'; 2048]).unwrap();
        // Active recording age check skips brand-new file
        db.sync_obs_videos(&obs, 120.0).unwrap();
        assert!(db.get_all_videos().unwrap().is_empty());

        // Sync with 0s stability registers the video
        db.sync_obs_videos(&obs, 0.0).unwrap();
        let vids = db.get_all_videos().unwrap();
        assert_eq!(vids.len(), 1);
        let orig_id = vids[0].id;
        db.save_video_result(orig_id, "CS2", 120.0, "", "", &serde_json::json!({"ok": true}))
            .unwrap();

        // Rename externally -> reassociated with same ID and game_name!
        let f2 = obs.join("renamed.mkv");
        fs::rename(&f1, &f2).unwrap();
        db.sync_obs_videos(&obs, 0.0).unwrap();
        let after = db.get_all_videos().unwrap();
        assert_eq!(after.len(), 1);
        assert_eq!(after[0].id, orig_id);
        assert_eq!(after[0].filename, "renamed.mkv");
        assert_eq!(after[0].game_name, "CS2");
    }
}
