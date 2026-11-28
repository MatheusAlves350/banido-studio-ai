//! Data models for SQLite persistence (`videos`, `game_history`, `job_history`).

use serde::{Deserialize, Serialize};

/// Default games seeded into `game_history` on first run.
pub const DEFAULT_GAMES_HISTORY: &[&str] = &["Overwatch 2", "Forza Horizon 5", "Counter-Strike"];

/// Summary row from the `videos` table.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VideoRecord {
    /// Primary key in SQLite.
    pub id: i64,
    /// Basename of the `.mkv` file.
    pub filename: String,
    /// Full path on disk.
    pub file_path: String,
    /// File size in gigabytes.
    pub size_gb: f64,
    /// Exact file size in bytes (used for rename reassociation).
    pub file_size_bytes: i64,
    /// Video duration in seconds.
    pub duration_sec: f64,
    /// Last modification timestamp (UNIX epoch seconds).
    pub mtime: f64,
    /// Processing status (`unprocessed`, `processing`, `completed`, `error`).
    pub status: String,
    /// Human-readable status detail.
    pub progress_msg: String,
    /// Normalized progress `[0.0, 1.0]`.
    pub progress_pct: f64,
    /// Detected or user-chosen game name.
    pub game_name: String,
    /// Optional user-provided context for the session.
    pub session_context: String,
    /// Chosen video title.
    pub chosen_title: String,
    /// Path to generated 4x4 mosaic JPEG.
    pub mosaic_path: String,
    /// Path to exported `.srt` subtitle file.
    pub srt_path: String,
    /// Whether Track 1 received YouTube Gold audio normalization.
    pub audio_normalized: bool,
    /// Soft-delete flag when recording is temporarily unmounted/missing.
    pub is_missing: bool,
}

/// Row from `game_history`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GameHistoryEntry {
    /// Canonical game title.
    pub name: String,
    /// Usage frequency counter.
    pub use_count: i64,
    /// Timestamp string of last usage.
    pub last_used_at: String,
    /// Default prompt context saved for this game.
    pub default_context: String,
}

/// Row from `job_history`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobRecord {
    /// Job identifier.
    pub id: i64,
    /// Associated video ID (`0` for batch normalization).
    pub video_id: i64,
    /// Associated filename.
    pub filename: String,
    /// AI/processing engine identifier.
    pub engine: String,
    /// Model name used.
    pub model: String,
    /// Job status (`running`, `completed`, `error`, `cancelled`, `interrupted`).
    pub status: String,
    /// Last reported pipeline step.
    pub last_step: String,
    /// Progress fraction `[0.0, 1.0]`.
    pub progress_pct: f64,
    /// Start timestamp string.
    pub started_at: String,
    /// Finish timestamp string.
    pub finished_at: String,
    /// Total elapsed wall time in seconds.
    pub elapsed_sec: f64,
    /// Error message if failed.
    pub error_detail: String,
    /// Log file path on disk.
    pub log_file: String,
}

/// Maps a `videos` SQLite row into [`VideoRecord`].
pub(crate) fn map_video_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<VideoRecord> {
    Ok(VideoRecord {
        id: r.get(0)?,
        filename: r.get(1)?,
        file_path: r.get(2)?,
        size_gb: r.get(3)?,
        file_size_bytes: r.get(4)?,
        duration_sec: r.get(5)?,
        mtime: r.get(6)?,
        status: r.get(7)?,
        progress_msg: r.get(8)?,
        progress_pct: r.get(9)?,
        game_name: r.get(10)?,
        session_context: r.get(11)?,
        chosen_title: r.get(12)?,
        mosaic_path: r.get(13)?,
        srt_path: r.get(14)?,
        audio_normalized: r.get::<_, i32>(15)? != 0,
        is_missing: r.get::<_, i32>(16)? != 0,
    })
}

/// Maps a `job_history` SQLite row into [`JobRecord`].
pub(crate) fn map_job_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<JobRecord> {
    Ok(JobRecord {
        id: r.get(0)?,
        video_id: r.get(1)?,
        filename: r.get(2)?,
        engine: r.get(3)?,
        model: r.get(4)?,
        status: r.get(5)?,
        last_step: r.get(6)?,
        progress_pct: r.get(7)?,
        started_at: r.get(8)?,
        finished_at: r.get(9)?,
        elapsed_sec: r.get(10)?,
        error_detail: r.get(11)?,
        log_file: r.get(12)?,
    })
}
