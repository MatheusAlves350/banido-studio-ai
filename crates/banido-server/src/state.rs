//! Thread-safe shared application state for the Axum server.
//!
//! Replaces the unsynchronized global `ACTIVE_PROCESSES` dict in
//! `app/worker_control.py` with `Arc<RwLock<HashMap<i64, ActiveWorker>>>`.

use banido_core::config::AppConfig;
use banido_core::database::StudioDb;
use banido_core::security::SecurityManager;
use serde::Serialize;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

/// Metadata for an active background processing job.
#[derive(Debug, Clone, Serialize)]
pub struct ActiveWorker {
    /// Associated video ID (`0` for batch normalization).
    pub video_id: i64,
    /// SQLite `job_history` identifier.
    pub job_id: i64,
    /// Worker mode (`full`, `ai_only`, `normalize_audio`, `normalize_batch`).
    pub mode: String,
    /// OS process identifier (`0` if in-process).
    pub pid: u32,
}

/// Shared immutable/synchronized application state injected into every Axum handler.
#[derive(Clone)]
pub struct AppState {
    /// Instance configuration loaded from `.env` and process environment.
    pub config: Arc<AppConfig>,
    /// SQLite WAL database handle.
    pub db: Arc<StudioDb>,
    /// Revocable session, CSRF token, and login rate-limiter manager.
    pub security: Arc<SecurityManager>,
    /// Thread-safe registry of running jobs keyed by `video_id`.
    pub active_workers: Arc<RwLock<HashMap<i64, ActiveWorker>>>,
}

impl AppState {
    /// Initializes directories, SQLite schema, and security state for an instance.
    pub fn from_config(config: AppConfig, explicit_api_token: Option<String>) -> Self {
        let _ = config.ensure_directories();
        let db = StudioDb::new(
            config.db_path.clone(),
            config.settings_json_path.clone(),
            config.logs_dir.clone(),
        );
        let _ = db.init_db();
        Self {
            config: Arc::new(config),
            db: Arc::new(db),
            security: Arc::new(SecurityManager::new(explicit_api_token)),
            active_workers: Arc::new(RwLock::new(HashMap::new())),
        }
    }
}

/// Masks a secret API key as `"prefix...suffix"` so full credentials never leave the server.
///
/// # Examples
/// ```
/// use banido_server::state::mask_secret_key;
///
/// assert_eq!(mask_secret_key("fake-demo-secret-1234567890"), "fake...7890");
/// assert_eq!(mask_secret_key(""), "");
/// ```
pub fn mask_secret_key(raw: &str) -> String {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    let chars: Vec<char> = trimmed.chars().collect();
    if chars.len() <= 8 {
        return "****".to_string();
    }
    let head: String = chars[..4].iter().collect();
    let tail: String = chars[chars.len() - 4..].iter().collect();
    format!("{head}...{tail}")
}
