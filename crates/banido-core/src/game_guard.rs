//! Auto-pause guard that detects active OBS recordings and watched game
//! processes so transcription never competes with live gameplay.

use std::collections::HashSet;
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

/// Detects whether an `.mkv` file in `obs_dir` was modified within `recent_sec`,
/// indicating an active OBS recording session.
///
/// Ignores hidden dotfiles (starting with `.`) and the optional `exclude_path`.
///
/// # Examples
/// ```
/// use banido_core::game_guard::detect_active_obs_recording;
/// use std::path::Path;
///
/// assert_eq!(detect_active_obs_recording(Path::new("/nonexistent"), None, 15.0, 1000.0), None);
/// ```
pub fn detect_active_obs_recording(
    obs_dir: &Path,
    exclude_path: Option<&Path>,
    recent_sec: f64,
    now_epoch_sec: f64,
) -> Option<String> {
    let entries = fs::read_dir(obs_dir).ok()?;
    for entry in entries.flatten() {
        let p = entry.path();
        if !p.is_file() {
            continue;
        }
        if let Some(ex) = exclude_path {
            if p == ex {
                continue;
            }
        }
        let name = p.file_name()?.to_str()?;
        if name.starts_with('.') {
            continue;
        }
        let ext = p.extension().and_then(|e| e.to_str())?;
        if !ext.eq_ignore_ascii_case("mkv") {
            continue;
        }
        let mtime = entry
            .metadata()
            .ok()?
            .modified()
            .ok()?
            .duration_since(UNIX_EPOCH)
            .ok()?
            .as_secs_f64();
        if (now_epoch_sec - mtime) <= recent_sec {
            return Some(name.to_string());
        }
    }
    None
}

/// Checks if any process name in `watched` is present in `running_lowercase`,
/// matching case-insensitively with or without the `.exe` suffix.
pub fn detect_watched_process(
    watched: &[String],
    running_lowercase: &HashSet<String>,
) -> Option<String> {
    for target in watched {
        let trimmed = target.trim();
        if trimmed.is_empty() {
            continue;
        }
        let low = trimmed.to_lowercase();
        let stem = low.strip_suffix(".exe").unwrap_or(&low);
        let with_exe = format!("{stem}.exe");
        if running_lowercase.contains(&low)
            || running_lowercase.contains(stem)
            || running_lowercase.contains(&with_exe)
        {
            return Some(trimmed.to_string());
        }
    }
    None
}

/// Returns `true` if a manual override flag file exists in either primary or
/// legacy work directories (fixes legacy mismatch in `routers/processing.py:60`).
pub fn has_resume_override(primary_flag: &Path, legacy_flag: Option<&Path>) -> bool {
    primary_flag.exists() || legacy_flag.is_some_and(Path::exists)
}

/// Returns current UNIX timestamp in seconds.
pub fn current_epoch_sec() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_detect_watched_process_case_and_exe_insensitive() {
        let running: HashSet<String> = ["obs64.exe".into(), "cs2.exe".into()].into_iter().collect();
        assert_eq!(detect_watched_process(&["valorant.exe".into()], &running), None);
        assert_eq!(
            detect_watched_process(&["valorant.exe".into(), "CS2.exe".into()], &running),
            Some("CS2.exe".into())
        );
        assert_eq!(
            detect_watched_process(&["cs2".into()], &running),
            Some("cs2".into())
        );
    }

    #[test]
    fn test_detect_active_obs_recording() {
        let dir = tempdir().unwrap();
        let current = dir.path().join("atual.mkv");
        let hidden = dir.path().join(".temp.mkv");
        let active = dir.path().join("gravando.mkv");
        fs::write(&current, b"0").unwrap();
        fs::write(&hidden, b"0").unwrap();
        let now = current_epoch_sec();
        assert_eq!(
            detect_active_obs_recording(dir.path(), Some(&current), 15.0, now),
            None
        );
        fs::write(&active, b"0").unwrap();
        assert_eq!(
            detect_active_obs_recording(dir.path(), Some(&current), 15.0, current_epoch_sec()),
            Some("gravando.mkv".into())
        );
    }
}
