//! Privacy-preserving diagnostic log sanitizer and GitHub issue report builder.
//!
//! Fixes two bugs found in `app/diagnostics.py`:
//! 1. Redacts both backslash (`C:\Users\...`) and forward-slash (`C:/Users/...`)
//!    Windows user paths (as written by `.env` normalization).
//! 2. Uses non-greedy filename matching so `.mkv`/`.srt` redaction never wipes
//!    out unrelated text earlier on the same log line.

use regex::Regex;
use serde::Serialize;
use std::sync::OnceLock;

/// Compiled sanitization patterns shared across calls.
struct SanitizePatterns {
    google_key: Regex,
    nvidia_key: Regex,
    bearer: Regex,
    secret_kv: Regex,
    win_user_path: Regex,
    unix_home_path: Regex,
    ipv4: Regex,
    transcript_line: Regex,
    media_filename: Regex,
}

fn patterns() -> &'static SanitizePatterns {
    static PATS: OnceLock<SanitizePatterns> = OnceLock::new();
    PATS.get_or_init(|| SanitizePatterns {
        google_key: Regex::new(r"AIza[0-9A-Za-z\-_]{16,}").unwrap(),
        nvidia_key: Regex::new(r"nvapi-[0-9A-Za-z\-_]{10,}").unwrap(),
        bearer: Regex::new(r"(?i)(Authorization\s*:\s*(?:Bearer|Basic)\s+)[^\s]+").unwrap(),
        secret_kv: Regex::new(
            r"(?i)((?:GOOGLE_API_KEY|NVIDIA_API_KEY|BANIDO_ACCESS_PASSWORD|API_KEY|PASSWORD|COOKIE)\s*=\s*)[^\r\n]+",
        )
        .unwrap(),
        win_user_path: Regex::new(r"(?i)[A-Z]:[/\\]Users[/\\][^/\\\s]+").unwrap(),
        unix_home_path: Regex::new(r"/(?:home|Users)/[^/\s]+").unwrap(),
        ipv4: Regex::new(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b").unwrap(),
        transcript_line: Regex::new(r"^\s*\[FALA #\d+\]").unwrap(),
        media_filename: Regex::new(
            r"(?i)(?:[\w\-]+(?:\s+\d{2}-\d{2}-\d{2})?|[^\s/\\:]+)\.(mkv|mp4|srt|wav)",
        )
        .unwrap(),
    })
}

/// Sanitizes raw log text by redacting API keys, tokens, user home paths,
/// non-loopback IPv4 addresses, optional transcript lines, and media filenames.
///
/// # Examples
/// ```
/// use banido_core::diagnostics::sanitize_log_text;
///
/// let raw = "GOOGLE_API_KEY=AIzaSyDummyKey1234567890abcdefghijk\nIP 192.168.1.42 (local 127.0.0.1)";
/// let clean = sanitize_log_text(raw, true, true);
/// assert!(!clean.contains("AIzaSy"));
/// assert!(!clean.contains("192.168.1.42"));
/// assert!(clean.contains("127.0.0.1"));
/// ```
pub fn sanitize_log_text(
    raw: &str,
    strip_transcript_lines: bool,
    redact_filenames: bool,
) -> String {
    let p = patterns();
    let mut out_lines: Vec<String> = Vec::new();
    let mut omitted_transcripts = 0usize;

    for line in raw.lines() {
        if strip_transcript_lines && p.transcript_line.is_match(line) {
            omitted_transcripts += 1;
            continue;
        }
        let s = p.google_key.replace_all(line, "<GOOGLE_API_KEY>");
        let s = p.nvidia_key.replace_all(&s, "<NVIDIA_API_KEY>");
        let s = p.bearer.replace_all(&s, "${1}<REDACTED>");
        let s = p.secret_kv.replace_all(&s, "${1}<REDACTED>");
        let s = p.win_user_path.replace_all(&s, r"C:\Users\<USER>");
        let s = p.unix_home_path.replace_all(&s, "/home/<USER>");
        let s = p.ipv4.replace_all(&s, |caps: &regex::Captures<'_>| {
            let ip = &caps[1];
            if ip == "127.0.0.1" || ip == "0.0.0.0" {
                ip.to_string()
            } else {
                "<IP>".to_string()
            }
        });
        let final_line = if redact_filenames {
            p.media_filename
                .replace_all(&s, "<ARQUIVO.$1>")
                .into_owned()
        } else {
            s.into_owned()
        };
        out_lines.push(final_line);
    }

    if omitted_transcripts > 0 {
        out_lines.push(format!(
            "[PRIVACIDADE] {omitted_transcripts} fala(s) transcrita(s) omitida(s)."
        ));
    }
    out_lines.join("\n")
}

/// Structured sanitized diagnostic report returned by `/api/diagnostics/report`.
#[derive(Debug, Clone, Serialize)]
pub struct DiagnosticReport {
    /// Sanitized Markdown body ready to paste into a GitHub issue.
    pub report_markdown: String,
    /// Pre-filled GitHub new-issue URL.
    pub github_issue_url: String,
    /// Human-readable privacy notice.
    pub privacy_notice: String,
}

/// Builds a sanitized diagnostic report and pre-filled GitHub issue URL.
pub fn build_diagnostic_report(
    repo_slug: &str,
    version_label: &str,
    log_text: &str,
    strip_transcripts: bool,
    redact_filenames: bool,
) -> DiagnosticReport {
    let clean_log = sanitize_log_text(log_text, strip_transcripts, redact_filenames);
    let md = format!(
        "## Diagnóstico — Banido Studio AI\n\n- **Versão**: `{version_label}`\n- **SO**: `{}` (`{}`)\n\n### Log sanitizado\n```text\n{clean_log}\n```\n",
        std::env::consts::OS,
        std::env::consts::ARCH
    );
    let body_preview: String = md.chars().take(1800).collect();
    let encoded_body = urlencoding::encode(&body_preview);
    let github_issue_url = format!(
        "https://github.com/{repo_slug}/issues/new?title=Relatorio+de+Diagnostico&body={encoded_body}"
    );
    DiagnosticReport {
        report_markdown: md,
        github_issue_url,
        privacy_notice: "Chaves de API, tokens, nomes de usuário do sistema, IPs privados e falas transcritas foram removidos automaticamente.".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sanitize_log_text_redacts_secrets_paths_ips_and_transcripts() {
        let raw = [
            "GOOGLE_API_KEY=AIzaSyDummyKey1234567890abcdefghijk",
            "NVIDIA_API_KEY=nvapi-1234567890abcdef",
            "Authorization: Bearer super_secret_token_99999",
            r"Abrindo C:\Users\Matheus\Videos\2026-07-26 18-32-49.mkv",
            "Caminho forward C:/Users/Maria/obs/teste.mkv",
            "Log em /home/fulano/banido/data/log.txt no IP 192.168.1.42 (local 127.0.0.1)",
            "[FALA #0001] [00:00:05 -> 00:00:08] [Host]: fala privada da call",
            "[FALA #0002] [00:00:09 -> 00:00:12] [Discord]: outra fala privada",
            "Traceback: RuntimeError: falha simulada",
        ]
        .join("\n");

        let clean = sanitize_log_text(&raw, true, true);
        assert!(!clean.contains("AIzaSy"));
        assert!(!clean.contains("nvapi-"));
        assert!(!clean.contains("super_secret_token"));
        assert!(!clean.contains("Matheus"));
        assert!(!clean.contains("Maria"));
        assert!(!clean.contains("fulano"));
        assert!(!clean.contains("192.168.1.42"));
        assert!(clean.contains("127.0.0.1"));
        assert!(!clean.contains("fala privada"));
        assert!(clean.contains("2 fala(s) transcrita(s) omitida(s)"));
        assert!(!clean.contains("2026-07-26 18-32-49.mkv"));
        assert!(clean.contains("<ARQUIVO.mkv>"));
        assert!(clean.contains("RuntimeError: falha simulada"));
    }
}
