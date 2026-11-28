//! Network access control, revocable HMAC-SHA256 sessions, CSRF token
//! verification, brute-force rate limiting, and TOCTOU-safe cache pruning.

use hmac::{Hmac, Mac};
use rand::RngCore;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

type HmacSha256 = Hmac<Sha256>;

/// Local loopback hostnames that do not require network password authentication.
pub const LOCAL_BIND_HOSTS: &[&str] = &["127.0.0.1", "localhost", "::1", "[::1]"];

/// Name of the HTTP session cookie used in network mode.
pub const SESSION_COOKIE: &str = "banido_session";

/// Session lifetime in seconds (7 days).
pub const SESSION_MAX_AGE_SEC: u64 = 7 * 24 * 3600;

/// Maximum consecutive failed logins before temporary lockout.
pub const LOGIN_MAX_FAILURES: usize = 5;

/// Sliding window for login rate limiting (60 seconds).
pub const LOGIN_WINDOW_SEC: f64 = 60.0;

/// User-facing block message when server binds to LAN without a password.
pub const NO_PASSWORD_MESSAGE: &str = "Acesso pela rede bloqueado: o servidor foi iniciado em um endereço de rede (BANIDO_HOST) sem senha. Defina BANIDO_ACCESS_PASSWORD no arquivo .env, ou use BANIDO_HOST=127.0.0.1 para uso apenas neste computador.";

/// Returns `true` when `server_host` exposes the server beyond localhost.
///
/// # Examples
/// ```
/// use banido_core::security::is_network_mode;
///
/// assert!(!is_network_mode("127.0.0.1"));
/// assert!(!is_network_mode("localhost"));
/// assert!(is_network_mode("0.0.0.0"));
/// ```
pub fn is_network_mode(server_host: &str) -> bool {
    let norm = server_host.trim().to_lowercase();
    !LOCAL_BIND_HOSTS.contains(&norm.as_str())
}

/// Constant-time SHA-256 comparison of two secrets (prevents length oracles).
pub fn constant_time_eq_secret(provided: &str, expected: &str) -> bool {
    if expected.is_empty() {
        return false;
    }
    let a = Sha256::digest(provided.as_bytes());
    let b = Sha256::digest(expected.as_bytes());
    let mut diff = 0u8;
    for (&x, &y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

/// Generates a cryptographically random URL-safe hex token of `n_bytes` entropy.
pub fn generate_random_token(n_bytes: usize) -> String {
    let mut buf = vec![0u8; n_bytes];
    rand::thread_rng().fill_bytes(&mut buf);
    hex::encode(buf)
}

/// Returns current UNIX timestamp in seconds.
pub fn now_epoch_sec() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Thread-safe manager for revocable sessions, CSRF tokens, and login throttling.
///
/// Fixes two legacy issues in `app/security.py`:
/// 1. Each login issues a unique, server-revocable HMAC-signed session token
///    with server-side expiration (logout truly invalidates stolen cookies).
/// 2. The brute-force failure map prunes expired entries so memory cannot leak.
#[derive(Debug)]
pub struct SecurityManager {
    session_secret: [u8; 32],
    api_auth_token: String,
    active_sessions: Mutex<HashMap<String, f64>>,
    login_failures: Mutex<HashMap<String, Vec<f64>>>,
}

impl SecurityManager {
    /// Creates a new `SecurityManager` with fresh cryptographic randomness.
    pub fn new(explicit_api_token: Option<String>) -> Self {
        let mut session_secret = [0u8; 32];
        rand::thread_rng().fill_bytes(&mut session_secret);
        let api_auth_token = explicit_api_token
            .filter(|t| !t.trim().is_empty())
            .unwrap_or_else(|| generate_random_token(32));
        Self {
            session_secret,
            api_auth_token,
            active_sessions: Mutex::new(HashMap::new()),
            login_failures: Mutex::new(HashMap::new()),
        }
    }

    /// Returns the per-boot CSRF / API authentication token (`X-Banido-Token`).
    pub fn api_auth_token(&self) -> &str {
        &self.api_auth_token
    }

    /// Validates an `X-Banido-Token` header in constant time.
    pub fn verify_api_token(&self, provided: Option<&str>) -> bool {
        match provided {
            Some(tok) => constant_time_eq_secret(tok, &self.api_auth_token),
            None => false,
        }
    }

    /// Issues a newly minted, HMAC-signed, revocable session cookie value.
    pub fn create_session(&self) -> String {
        let nonce = generate_random_token(16);
        let mut mac =
            HmacSha256::new_from_slice(&self.session_secret).expect("HMAC accepts 32-byte key");
        mac.update(nonce.as_bytes());
        let sig = hex::encode(mac.finalize().into_bytes());
        let token = format!("{nonce}.{sig}");
        let expires_at = now_epoch_sec() + (SESSION_MAX_AGE_SEC as f64);
        if let Ok(mut guard) = self.active_sessions.lock() {
            guard.insert(token.clone(), expires_at);
        }
        token
    }

    /// Verifies that `cookie_value` is a live, non-expired, HMAC-valid session.
    pub fn is_valid_session(&self, cookie_value: Option<&str>) -> bool {
        let Some(val) = cookie_value.filter(|s| !s.is_empty()) else {
            return false;
        };
        let Some((nonce, sig_hex)) = val.split_once('.') else {
            return false;
        };
        let Ok(mut mac) = HmacSha256::new_from_slice(&self.session_secret) else {
            return false;
        };
        mac.update(nonce.as_bytes());
        let expected_sig = hex::encode(mac.finalize().into_bytes());
        if !constant_time_eq_secret(sig_hex, &expected_sig) {
            return false;
        }
        let now = now_epoch_sec();
        if let Ok(mut guard) = self.active_sessions.lock() {
            guard.retain(|_, exp| *exp > now);
            return guard.get(val).is_some_and(|exp| *exp > now);
        }
        false
    }

    /// Revokes a session token immediately on logout.
    pub fn revoke_session(&self, cookie_value: Option<&str>) {
        if let Some(val) = cookie_value {
            if let Ok(mut guard) = self.active_sessions.lock() {
                guard.remove(val);
            }
        }
    }

    /// Returns `true` if `client_id` has reached the login failure threshold.
    pub fn login_blocked(&self, client_id: &str, now_override: Option<f64>) -> bool {
        let now = now_override.unwrap_or_else(now_epoch_sec);
        let Ok(mut guard) = self.login_failures.lock() else {
            return false;
        };
        guard.retain(|_, times| {
            times.retain(|&t| now - t < LOGIN_WINDOW_SEC);
            !times.is_empty()
        });
        guard
            .get(client_id)
            .is_some_and(|times| times.len() >= LOGIN_MAX_FAILURES)
    }

    /// Records a failed login attempt for `client_id`.
    pub fn register_login_failure(&self, client_id: &str, now_override: Option<f64>) {
        let now = now_override.unwrap_or_else(now_epoch_sec);
        if let Ok(mut guard) = self.login_failures.lock() {
            guard.entry(client_id.to_string()).or_default().push(now);
        }
    }

    /// Clears recorded failures for `client_id` after a successful login.
    pub fn clear_login_failures(&self, client_id: &str) {
        if let Ok(mut guard) = self.login_failures.lock() {
            guard.remove(client_id);
        }
    }
}

/// Parses `password=...` from an `application/x-www-form-urlencoded` body.
pub fn parse_login_body(raw: &[u8]) -> String {
    let Ok(s) = std::str::from_utf8(raw) else {
        return String::new();
    };
    for pair in s.split('&') {
        if let Some((k, v)) = pair.split_once('=') {
            if k == "password" {
                let plus_decoded = v.replace('+', " ");
                return urlencoding::decode(&plus_decoded)
                    .map(|c| c.into_owned())
                    .unwrap_or(plus_decoded);
            }
        }
    }
    String::new()
}

/// Renders the standalone HTML login page for LAN network mode.
pub fn login_page_html(message: &str) -> String {
    let escaped = escape_html(message);
    let msg_html = if escaped.is_empty() {
        String::new()
    } else {
        format!(r#"<p class="err">{escaped}</p>"#)
    };
    format!(
        r#"<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Banido Studio AI - Acesso</title>
<style>
body{{font-family:system-ui,sans-serif;background:#111;color:#eee;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
form{{background:#1b1b1b;border:1px solid #333;border-radius:8px;padding:24px;width:min(340px,90vw)}}
h1{{font-size:18px;margin:0 0 6px}} p{{font-size:13px;color:#aaa;margin:0 0 14px}}
input,button{{width:100%;box-sizing:border-box;padding:10px;border-radius:6px;border:1px solid #444;font-size:14px}}
input{{background:#0e0e0e;color:#eee;margin-bottom:10px}} button{{background:#2d6cdf;border-color:#2d6cdf;color:#fff;cursor:pointer}}
.err{{color:#ff8a80}}
</style></head><body>
<form method="post" action="/login">
<h1>Banido Studio AI</h1>
<p>Este servidor está aberto na rede. Digite a senha definida em <code>BANIDO_ACCESS_PASSWORD</code>.</p>
{msg_html}
<input type="password" name="password" placeholder="Senha de acesso" autofocus required>
<button type="submit">Entrar</button>
<p style="margin-top:12px;margin-bottom:0;font-size:11px;color:#777">Conexão local HTTP (sem criptografia TLS): use apenas na sua rede privada/confiável.</p>
</form></body></html>"#
    )
}

/// Escapes HTML special characters to prevent XSS in server-rendered pages.
pub fn escape_html(input: &str) -> String {
    input
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

/// Prunes the oldest files in `directory` until total size is `<= max_bytes`,
/// always preserving the `keep_newest` most recently modified files.
///
/// Fixes the legacy TOCTOU race in `app/security.py:110-111` by capturing
/// metadata per entry with error tolerance so concurrent deletions never crash.
pub fn prune_directory(directory: &Path, max_bytes: u64, keep_newest: usize) -> usize {
    let Ok(entries) = fs::read_dir(directory) else {
        return 0;
    };
    let mut files: Vec<(std::path::PathBuf, SystemTime, u64)> = entries
        .filter_map(|e| e.ok())
        .filter_map(|e| {
            let meta = e.metadata().ok()?;
            if !meta.is_file() {
                return None;
            }
            let mtime = meta.modified().unwrap_or(UNIX_EPOCH);
            Some((e.path(), mtime, meta.len()))
        })
        .collect();

    files.sort_by_key(|a| std::cmp::Reverse(a.1));
    let mut total: u64 = files.iter().map(|f| f.2).sum();
    let mut removed = 0usize;

    if files.len() <= keep_newest {
        return 0;
    }

    for (path, _, size) in files[keep_newest..].iter().rev() {
        if total <= max_bytes {
            break;
        }
        if fs::remove_file(path).is_ok() {
            total = total.saturating_sub(*size);
            removed += 1;
        }
    }
    removed
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_session_create_verify_and_revoke() {
        let mgr = SecurityManager::new(Some("tok123".into()));
        let s = mgr.create_session();
        assert!(mgr.is_valid_session(Some(&s)));
        mgr.revoke_session(Some(&s));
        assert!(!mgr.is_valid_session(Some(&s)));
        assert!(mgr.verify_api_token(Some("tok123")));
        assert!(!mgr.verify_api_token(Some("wrong")));
    }

    #[test]
    fn test_login_rate_limit_window() {
        let mgr = SecurityManager::new(None);
        for i in 0..5 {
            assert!(!mgr.login_blocked("1.2.3.4", Some(100.0 + i as f64)));
            mgr.register_login_failure("1.2.3.4", Some(100.0 + i as f64));
        }
        assert!(mgr.login_blocked("1.2.3.4", Some(110.0)));
        assert!(!mgr.login_blocked("1.2.3.4", Some(200.0)));
    }

    #[test]
    fn test_prune_directory_keeps_newest() {
        let dir = tempdir().unwrap();
        for i in 0..6u64 {
            let p = dir.path().join(format!("p{i}.wav"));
            fs::write(&p, vec![b'x'; 1000]).unwrap();
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        let removed = prune_directory(dir.path(), 3000, 2);
        assert_eq!(removed, 3);
    }
}
