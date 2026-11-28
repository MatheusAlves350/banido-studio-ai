//! Multi-instance configuration and `.env` loader for Banido Studio AI.
//!
//! Resolves directory layouts, Whisper/audio flags, and network bind policies
//! cleanly without mutating global process state or crashing on missing paths.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

/// Values treated as boolean `false` consistently across runtime and UI.
///
/// Fixes legacy drift between `app/config.py` and `app/env_settings.py`
/// where `"off"` was accepted by the UI but ignored by the runtime.
const FALSE_TOKENS: &[&str] = &["0", "false", "no", "nao", "não", "off"];

/// Immutable runtime configuration for a Banido Studio AI instance.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    /// Repository or installation root directory.
    pub project_root: PathBuf,
    /// Root data directory (`database/`, `Cortes/`, `vozes_amigos/`, `models/`).
    pub base_dir: PathBuf,
    /// Directory monitored for OBS `.mkv` recordings.
    pub obs_recordings_dir: PathBuf,
    /// Directory storing downloaded ML models (Whisper, ECAPA-TDNN ONNX).
    pub models_dir: PathBuf,
    /// Directory storing confirmed speaker voice profiles (`embedding.npy`).
    pub voice_bank_dir: PathBuf,
    /// Directory where exported lossless clips are written.
    pub clips_dir: PathBuf,
    /// Temporary working directory (ideally on an NVMe SSD).
    pub temp_dir: PathBuf,
    /// Legacy fallback temp directory under `base_dir/temp`.
    pub legacy_temp_dir: PathBuf,
    /// SQLite database directory (`base_dir/database`).
    pub database_dir: PathBuf,
    /// Job log directory (`base_dir/database/logs`).
    pub logs_dir: PathBuf,
    /// Path to `banido_studio.db`.
    pub db_path: PathBuf,
    /// Path to `settings.json`.
    pub settings_json_path: PathBuf,
    /// Display label for the host's isolated microphone track.
    pub host_speaker_name: String,
    /// Channel or group display name used in metadata.
    pub group_name: String,
    /// HTTP bind address (e.g. `127.0.0.1` or `0.0.0.0`).
    pub server_host: String,
    /// HTTP bind port.
    pub server_port: u16,
    /// Optional password required whenever `server_host` is non-local.
    pub access_password: String,
    /// Explicitly allowed HTTP `Host` headers.
    pub allowed_hosts: Vec<String>,
    /// Whisper model identifier (e.g. `large-v3`, `medium`).
    pub whisper_model_size: String,
    /// Whisper execution device (`auto`, `cuda`, `cpu`).
    pub whisper_device: String,
    /// Whisper compute quantization (`auto`, `float16`, `int8`, `int8_float16`).
    pub whisper_compute_type: String,
    /// Optional forced Whisper language (`None` = auto-detect).
    pub whisper_language: Option<String>,
    /// Whether to transcribe host mic and Discord tracks separately.
    pub separate_track_transcription: bool,
    /// Whether to preserve Track 1 original mix as lossless backup.
    pub keep_orig_audio_backup: bool,
    /// Whether to pause transcription while OBS is actively recording.
    pub auto_pause_on_recording: bool,
    /// Additional process names that trigger auto-pause.
    pub pause_processes: Vec<String>,
    /// GitHub repository slug used for self-updates.
    pub update_repo: String,
    /// Git branch monitored for updates.
    pub update_branch: String,
}

/// Parses a `.env` file into a key-value map using last-write-wins semantics.
///
/// Strips optional `export ` prefixes and matching surrounding quotes (`"` or `'`).
///
/// # Examples
/// ```
/// use banido_core::config::parse_dotenv_str;
///
/// let map = parse_dotenv_str("# comment\nexport BANIDO_PORT=9000\nBANIDO_HOST_NAME=\"Zé\"\n");
/// assert_eq!(map.get("BANIDO_PORT").map(String::as_str), Some("9000"));
/// assert_eq!(map.get("BANIDO_HOST_NAME").map(String::as_str), Some("Zé"));
/// ```
pub fn parse_dotenv_str(content: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    for raw in content.lines() {
        let mut line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(rest) = line.strip_prefix("export ") {
            line = rest.trim();
        }
        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        let key = k.trim();
        if key.is_empty() {
            continue;
        }
        let val = strip_quotes(v.trim());
        out.insert(key.to_string(), val);
    }
    out
}

/// Strips matching surrounding single or double quotes from `val`.
pub fn strip_quotes(val: &str) -> String {
    let bytes = val.as_bytes();
    if bytes.len() >= 2
        && ((bytes[0] == b'"' && bytes[bytes.len() - 1] == b'"')
            || (bytes[0] == b'\'' && bytes[bytes.len() - 1] == b'\''))
    {
        val[1..val.len() - 1].to_string()
    } else {
        val.to_string()
    }
}

/// Parses a configuration boolean string using consistent truthy/falsy rules.
pub fn parse_bool_flag(raw: Option<&str>, default: bool) -> bool {
    match raw.map(str::trim).filter(|s| !s.is_empty()) {
        None => default,
        Some(v) => {
            let low = v.to_lowercase();
            !FALSE_TOKENS.contains(&low.as_str())
        }
    }
}

/// Resolves allowed `Host` header entries for a given bind host and CSV list.
pub fn build_allowed_hosts(server_host: &str, extra_csv: &str) -> Vec<String> {
    let extra: Vec<String> = extra_csv
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToOwned::to_owned)
        .collect();
    if extra.iter().any(|h| h == "*") {
        return vec!["*".to_string()];
    }
    let mut hosts = vec![
        "localhost".to_string(),
        "127.0.0.1".to_string(),
        "[::1]".to_string(),
        "testserver".to_string(),
    ];
    if server_host != "0.0.0.0" && !hosts.iter().any(|h| h == server_host) {
        hosts.push(server_host.to_string());
    }
    for h in extra {
        if !hosts.iter().any(|x| x == &h) {
            hosts.push(h);
        }
    }
    hosts
}

impl AppConfig {
    /// Builds an [`AppConfig`] from a project root, optional `.env` file, and
    /// explicit environment map (process env takes precedence over `.env`).
    pub fn load_with_env(project_root: PathBuf, env_vars: &HashMap<String, String>) -> Self {
        let dotenv_map = fs::read_to_string(project_root.join(".env"))
            .map(|c| parse_dotenv_str(&c))
            .unwrap_or_default();

        let get = |key: &str| -> Option<String> {
            env_vars
                .get(key)
                .or_else(|| dotenv_map.get(key))
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
        };

        let resolve_path = |key: &str, default_path: PathBuf| -> PathBuf {
            match get(key) {
                None => default_path,
                Some(raw) => {
                    let p = PathBuf::from(raw);
                    if p.is_absolute() {
                        p
                    } else {
                        project_root.join(p)
                    }
                }
            }
        };

        let base_dir = resolve_path("BANIDO_BASE_DIR", project_root.join("data"));
        let obs_recordings_dir = resolve_path("BANIDO_OBS_DIR", base_dir.join("obs_recordings"));
        let models_dir = resolve_path("BANIDO_MODELS_DIR", base_dir.join("models"));
        let voice_bank_dir = resolve_path("BANIDO_VOICE_BANK_DIR", base_dir.join("vozes_amigos"));
        let clips_dir = resolve_path("BANIDO_CLIPS_DIR", base_dir.join("Cortes"));
        let legacy_temp_dir = base_dir.join("temp");
        let temp_dir = resolve_path(
            "BANIDO_TEMP_DIR",
            std::env::temp_dir().join("BanidoStudioTemp"),
        );

        let database_dir = base_dir.join("database");
        let logs_dir = database_dir.join("logs");
        let db_path = database_dir.join("banido_studio.db");
        let settings_json_path = database_dir.join("settings.json");

        let server_host = get("BANIDO_HOST").unwrap_or_else(|| "127.0.0.1".to_string());
        let server_port = get("BANIDO_PORT")
            .and_then(|s| s.parse::<u16>().ok())
            .unwrap_or(8765);
        let extra_hosts = get("BANIDO_ALLOWED_HOSTS").unwrap_or_default();
        let allowed_hosts = build_allowed_hosts(&server_host, &extra_hosts);

        let whisper_language = match get("BANIDO_WHISPER_LANGUAGE") {
            Some(ref lang) if matches!(lang.to_lowercase().as_str(), "auto" | "none") => None,
            Some(lang) => Some(lang),
            None => Some("pt".to_string()),
        };

        let pause_processes = get("BANIDO_PAUSE_PROCESSES")
            .unwrap_or_default()
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned)
            .collect();

        Self {
            project_root,
            base_dir,
            obs_recordings_dir,
            models_dir,
            voice_bank_dir,
            clips_dir,
            temp_dir,
            legacy_temp_dir,
            database_dir,
            logs_dir,
            db_path,
            settings_json_path,
            host_speaker_name: get("BANIDO_HOST_NAME").unwrap_or_else(|| "Host".to_string()),
            group_name: get("BANIDO_GROUP_NAME").unwrap_or_else(|| "Banido Studio".to_string()),
            server_host,
            server_port,
            access_password: get("BANIDO_ACCESS_PASSWORD").unwrap_or_default(),
            allowed_hosts,
            whisper_model_size: get("BANIDO_WHISPER_MODEL")
                .unwrap_or_else(|| "large-v3".to_string()),
            whisper_device: get("BANIDO_WHISPER_DEVICE")
                .map(|s| s.to_lowercase())
                .unwrap_or_else(|| "auto".to_string()),
            whisper_compute_type: get("BANIDO_WHISPER_COMPUTE_TYPE")
                .map(|s| s.to_lowercase())
                .unwrap_or_else(|| "auto".to_string()),
            whisper_language,
            separate_track_transcription: parse_bool_flag(
                get("BANIDO_SEPARATE_TRACKS").as_deref(),
                true,
            ),
            keep_orig_audio_backup: parse_bool_flag(
                get("BANIDO_KEEP_ORIG_AUDIO_BACKUP").as_deref(),
                true,
            ),
            auto_pause_on_recording: parse_bool_flag(
                get("BANIDO_AUTO_PAUSE_ON_RECORDING").as_deref(),
                true,
            ),
            pause_processes,
            update_repo: get("BANIDO_UPDATE_REPO")
                .unwrap_or_else(|| "MatheusAlves350/banido-studio-ai".to_string()),
            update_branch: get("BANIDO_UPDATE_BRANCH").unwrap_or_else(|| "main".to_string()),
        }
    }

    /// Loads configuration using the live process environment variables.
    pub fn load_from_process(project_root: PathBuf) -> Self {
        let env_vars: HashMap<String, String> = std::env::vars().collect();
        Self::load_with_env(project_root, &env_vars)
    }

    /// Creates all required instance directories idempotently.
    pub fn ensure_directories(&self) -> std::io::Result<()> {
        let dirs: [&Path; 9] = [
            &self.base_dir,
            &self.obs_recordings_dir,
            &self.models_dir,
            &self.voice_bank_dir,
            &self.clips_dir,
            &self.legacy_temp_dir,
            &self.temp_dir,
            &self.database_dir,
            &self.logs_dir,
        ];
        for d in dirs {
            fs::create_dir_all(d)?;
        }
        Ok(())
    }
}
