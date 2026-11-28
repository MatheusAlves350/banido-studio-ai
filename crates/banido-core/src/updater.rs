//! SemVer release comparison and secure GitHub installer URL validation.
//!
//! Fixes two issues found in `app/release_updater.py`:
//! 1. Preserves pre-release precedence (`1.0.0` > `1.0.0-beta1` > `0.9.9`).
//! 2. Rejects path-traversal (`..`) segments in release download URLs.

use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::fs;
use std::path::Path;

/// Parsed semantic version with optional pre-release tag.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SemVer {
    /// Major version number.
    pub major: u64,
    /// Minor version number.
    pub minor: u64,
    /// Patch version number.
    pub patch: u64,
    /// Optional pre-release tag (e.g. `dev`, `beta1`). `None` = stable release.
    pub pre: Option<String>,
}

impl Ord for SemVer {
    fn cmp(&self, other: &Self) -> Ordering {
        (self.major, self.minor, self.patch)
            .cmp(&(other.major, other.minor, other.patch))
            .then_with(|| match (&self.pre, &other.pre) {
                (None, None) => Ordering::Equal,
                (None, Some(_)) => Ordering::Greater,
                (Some(_), None) => Ordering::Less,
                (Some(a), Some(b)) => a.cmp(b),
            })
    }
}

impl PartialOrd for SemVer {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Parses a version string such as `"v0.3.1"`, `"0.2"`, or `"0.0.0-dev"`.
pub fn parse_version(raw: &str) -> SemVer {
    let trimmed = raw.trim().trim_start_matches(['v', 'V']);
    let (core, pre) = match trimmed.split_once('-') {
        Some((c, p)) => (c, Some(p.trim().to_string()).filter(|s| !s.is_empty())),
        None => (trimmed, None),
    };
    let mut parts = core.split('.').map(|p| p.parse::<u64>().unwrap_or(0));
    SemVer {
        major: parts.next().unwrap_or(0),
        minor: parts.next().unwrap_or(0),
        patch: parts.next().unwrap_or(0),
        pre,
    }
}

/// Returns `true` if `candidate` is strictly newer than `current`.
///
/// # Examples
/// ```
/// use banido_core::updater::is_newer;
///
/// assert!(is_newer("0.1.2", "0.1.1"));
/// assert!(is_newer("1.0.0", "1.0.0-beta1"));
/// assert!(!is_newer("0.1.1", "0.1.1"));
/// ```
pub fn is_newer(candidate: &str, current: &str) -> bool {
    parse_version(candidate) > parse_version(current)
}

/// Reads the installed version from `<root>/VERSION`, returning `""` if absent.
pub fn read_installed_version(root: &Path) -> String {
    fs::read_to_string(root.join("VERSION"))
        .map(|s| s.trim().trim_start_matches(['v', 'V']).to_string())
        .unwrap_or_default()
}

/// Validates that an installer download URL belongs strictly to the expected
/// GitHub repository's `/releases/download/` path without `..` traversal.
pub fn validate_installer_url(url: &str, repo: &str) -> Result<(), String> {
    let expected_prefix = format!("https://github.com/{repo}/releases/download/");
    if !url.starts_with(&expected_prefix) {
        return Err("URL do instalador rejeitada: origem fora do repositório oficial.".into());
    }
    if url.contains("..") || url.contains('\\') || url.contains('@') {
        return Err("URL do instalador rejeitada: contém sequência inválida.".into());
    }
    Ok(())
}

/// Status payload returned by `/api/update/status` and `/api/update/check`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UpdateStatus {
    /// Whether a newer version is available.
    pub update_available: bool,
    /// Whether the panel can apply the update automatically.
    pub can_apply: bool,
    /// Installed version string.
    pub current_version: String,
    /// Latest available version string.
    pub latest_version: String,
    /// Human-readable status explanation.
    pub reason: String,
    /// Non-empty when an error occurred while checking.
    pub error: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version_ordering_and_prerelease() {
        assert!(is_newer("0.1.2", "0.1.1"));
        assert!(is_newer("v0.2", "0.1.9"));
        assert!(is_newer("1.0.0", "0.9.9"));
        assert!(is_newer("1.0.0", "1.0.0-beta1"));
        assert!(is_newer("0.1.0", "0.0.0-dev"));
        assert!(!is_newer("0.1.1", "0.1.1"));
        assert!(!is_newer("0.1.0", "0.1.1"));
    }

    #[test]
    fn test_validate_installer_url_rejects_foreign_and_traversal() {
        assert!(validate_installer_url(
            "https://github.com/o/r/releases/download/v0.2.0/BanidoStudio-Setup.exe",
            "o/r"
        )
        .is_ok());
        assert!(validate_installer_url(
            "https://example.com/BanidoStudio-Setup.exe",
            "o/r"
        )
        .is_err());
        assert!(validate_installer_url(
            "https://github.com/o/r/releases/download/../../evil/a.exe",
            "o/r"
        )
        .is_err());
    }
}
