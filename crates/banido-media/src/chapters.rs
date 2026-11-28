//! Filename sanitization, collision-free path resolution, timestamp parsing,
//! and Matroska XML / FFMETADATA1 chapter generation.

use regex::Regex;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// Windows reserved DOS device names blocked in sanitized paths.
const WINDOWS_RESERVED: &[&str] = &[
    "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8",
    "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
];

/// Input chapter descriptor from AI analysis.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChapterInput {
    /// Timestamp string (`HH:MM:SS` or `MM:SS`).
    pub timestamp: String,
    /// Chapter title.
    pub title: String,
}

/// Input highlight clip descriptor from AI analysis.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HighlightInput {
    /// Start timestamp in seconds.
    pub start: f64,
    /// End timestamp in seconds.
    pub end: f64,
    /// Excitement score `0..=100`.
    pub score: i32,
    /// Highlight title.
    pub title: String,
}

/// Resolved millisecond chapter entry ready for Matroska XML or FFMETADATA1.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChapterEntry {
    /// Start timestamp in milliseconds.
    pub start_ms: u64,
    /// End timestamp in milliseconds (always `> start_ms`).
    pub end_ms: u64,
    /// Display title.
    pub title: String,
}

struct FilenameRegexes {
    invalid: Regex,
    leading_bracket: Regex,
    inline_bracket: Regex,
    double_dash: Regex,
    spaces: Regex,
}

fn fn_regexes() -> &'static FilenameRegexes {
    static RE: OnceLock<FilenameRegexes> = OnceLock::new();
    RE.get_or_init(|| FilenameRegexes {
        invalid: Regex::new(r#"[\\/*?:"<>|\x00-\x1f]"#).unwrap(),
        leading_bracket: Regex::new(r"^\[\s*([^\[\]]+?)\s*\]\s*[-–—:]?\s*").unwrap(),
        inline_bracket: Regex::new(r"\[\s*([^\[\]]+?)\s*\]").unwrap(),
        double_dash: Regex::new(r"\s*-\s*-\s*").unwrap(),
        spaces: Regex::new(r"\s+").unwrap(),
    })
}

/// Sanitizes a title into a Windows/macOS/Linux-safe filename, converting
/// leading `[Tag]` prefixes to `Tag - ` as used on YouTube.
///
/// # Examples
/// ```
/// use banido_media::chapters::sanitize_filename;
///
/// assert_eq!(sanitize_filename("[GTA V] O Golpe: Parte 1?", 120), "GTA V - O Golpe Parte 1");
/// assert_eq!(sanitize_filename("Overwatch 2 - [Highlight] Final", 120), "Overwatch 2 - Highlight Final");
/// ```
pub fn sanitize_filename(name: &str, max_len: usize) -> String {
    let re = fn_regexes();
    let s = re.invalid.replace_all(name, "");
    let s = re.leading_bracket.replace(&s, "$1 - ");
    let s = re.inline_bracket.replace_all(&s, "$1");
    let s = s.replace(['[', ']'], "-");
    let s = re.double_dash.replace_all(&s, " - ");
    let s = re.spaces.replace_all(&s, " ");
    let trimmed = s.trim_matches(|c: char| c == ' ' || c == '-' || c == '.');
    let base_stem = trimmed.split('.').next().unwrap_or("").trim().to_lowercase();
    let safe = if WINDOWS_RESERVED.contains(&base_stem.as_str()) {
        format!("{trimmed}_")
    } else {
        trimmed.to_string()
    };
    safe.chars().take(max_len).collect()
}

/// Returns `path` if free, otherwise appends ` (2)`, ` (3)`, ... to avoid clobbering.
pub fn unique_path(path: &Path) -> PathBuf {
    if !path.exists() {
        return path.to_path_buf();
    }
    let stem = path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("file");
    let ext = path
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| format!(".{e}"))
        .unwrap_or_default();
    let parent = path.parent().unwrap_or_else(|| Path::new(""));
    let mut counter = 2usize;
    loop {
        let cand = parent.join(format!("{stem} ({counter}){ext}"));
        if !cand.exists() {
            return cand;
        }
        counter += 1;
    }
}

/// Parses `"HH:MM:SS"`, `"MM:SS"`, or numeric seconds into `Option<f64>`.
///
/// # Examples
/// ```
/// use banido_media::chapters::parse_ts_sec;
///
/// assert_eq!(parse_ts_sec("00:01:30"), Some(90.0));
/// assert_eq!(parse_ts_sec("02:15"), Some(135.0));
/// assert_eq!(parse_ts_sec("45.5"), Some(45.5));
/// assert_eq!(parse_ts_sec("invalid"), None);
/// ```
pub fn parse_ts_sec(raw: &str) -> Option<f64> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    if let Ok(v) = trimmed.parse::<f64>() {
        return if v.is_finite() && v >= 0.0 {
            Some(v)
        } else {
            None
        };
    }
    let parts: Vec<&str> = trimmed.split(':').collect();
    match parts.as_slice() {
        [m, s] => {
            let mv: f64 = m.trim().parse().ok()?;
            let sv: f64 = s.trim().parse().ok()?;
            Some((mv * 60.0 + sv).max(0.0))
        }
        [h, m, s] => {
            let hv: f64 = h.trim().parse().ok()?;
            let mv: f64 = m.trim().parse().ok()?;
            let sv: f64 = s.trim().parse().ok()?;
            Some((hv * 3600.0 + mv * 60.0 + sv).max(0.0))
        }
        _ => None,
    }
}

/// Builds sorted, valid chapter entries from chapters and/or highlights.
///
/// Mode can be `"both"`, `"chapters"`, or `"highlights"`. Guarantees every
/// entry satisfies `start_ms < end_ms <= total_ms`.
pub fn build_chapter_entries(
    chapters: &[ChapterInput],
    highlights: &[HighlightInput],
    total_duration_sec: f64,
    mode: &str,
) -> Vec<ChapterEntry> {
    let total_ms = (total_duration_sec.max(1.0) * 1000.0).round() as u64;
    let mut raw: Vec<(u64, Option<u64>, String)> = Vec::new();

    if mode == "both" || mode == "chapters" {
        for ch in chapters {
            if let Some(st) = parse_ts_sec(&ch.timestamp) {
                let st_ms = (st * 1000.0).round() as u64;
                if st_ms < total_ms {
                    raw.push((st_ms, None, ch.title.trim().to_string()));
                }
            }
        }
    }
    if mode == "both" || mode == "highlights" {
        for hl in highlights {
            let st_ms = (hl.start.max(0.0) * 1000.0).round() as u64;
            let en_ms = (hl.end.max(0.0) * 1000.0).round() as u64;
            if st_ms < total_ms {
                let clamped_end = en_ms.clamp(st_ms + 1000, total_ms);
                let title = format!("[HIGHLIGHT {}] {}", hl.score, hl.title.trim());
                raw.push((st_ms, Some(clamped_end), title));
            }
        }
    }

    raw.sort_by_key(|(s, _, _)| *s);
    let mut out = Vec::with_capacity(raw.len());
    for i in 0..raw.len() {
        let (st, explicit_end, ref title) = raw[i];
        let next_start = raw
            .get(i + 1)
            .map(|(ns, _, _)| *ns)
            .filter(|&ns| ns > st)
            .unwrap_or(total_ms);
        let en = explicit_end.unwrap_or(next_start).max(st + 1).min(total_ms);
        if st < en {
            out.push(ChapterEntry {
                start_ms: st,
                end_ms: en,
                title: title.clone(),
            });
        }
    }
    out
}

/// Renders Matroska XML chapters compatible with `mkvpropedit --chapters`.
pub fn render_matroska_chapters_xml(entries: &[ChapterEntry]) -> String {
    let mut xml = String::from(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE Chapters SYSTEM \"matroskachapters.dtd\">\n<Chapters><EditionEntry>\n",
    );
    for e in entries {
        let st = format_mkv_ns(e.start_ms);
        let en = format_mkv_ns(e.end_ms);
        let title = xml_escape(&e.title);
        xml.push_str(&format!(
            "  <ChapterAtom>\n    <ChapterTimeStart>{st}</ChapterTimeStart>\n    <ChapterTimeEnd>{en}</ChapterTimeEnd>\n    <ChapterDisplay><ChapterString>{title}</ChapterString><ChapterLanguage>por</ChapterLanguage></ChapterDisplay>\n  </ChapterAtom>\n"
        ));
    }
    xml.push_str("</EditionEntry></Chapters>\n");
    xml
}

/// Renders `;FFMETADATA1` chapters for the lossless FFmpeg fallback path.
pub fn render_ffmetadata_chapters(entries: &[ChapterEntry]) -> String {
    let mut out = String::from(";FFMETADATA1\n");
    for e in entries {
        let clean = escape_ffmetadata(&e.title);
        out.push_str(&format!(
            "[CHAPTER]\nTIMEBASE=1/1000\nSTART={}\nEND={}\ntitle={clean}\n",
            e.start_ms, e.end_ms
        ));
    }
    out
}

fn format_mkv_ns(ms: u64) -> String {
    let total_sec = ms / 1000;
    let rem_ms = ms % 1000;
    let h = total_sec / 3600;
    let m = (total_sec % 3600) / 60;
    let s = total_sec % 60;
    format!("{h:02}:{m:02}:{s:02}.{rem_ms:03}000000")
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

fn escape_ffmetadata(s: &str) -> String {
    s.replace(['\r', '\n'], " ")
        .replace('\\', r"\\")
        .replace('=', r"\=")
        .replace(';', r"\;")
        .replace('#', r"\#")
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_unique_path_and_chapters() {
        let dir = tempdir().unwrap();
        let f1 = dir.path().join("video.mkv");
        assert_eq!(unique_path(&f1), f1);
        std::fs::write(&f1, b"a").unwrap();
        let f2 = unique_path(&f1);
        assert_eq!(f2.file_name().unwrap(), "video (2).mkv");

        let entries = build_chapter_entries(
            &[
                ChapterInput { timestamp: "00:00:00".into(), title: "Intro".into() },
                ChapterInput { timestamp: "00:05:00".into(), title: "Partida".into() },
            ],
            &[HighlightInput { start: 120.0, end: 180.0, score: 95, title: "Jogada".into() }],
            600.0,
            "both",
        );
        assert_eq!(entries.len(), 3);
        assert_eq!(entries[0].start_ms, 0);
        assert_eq!(entries[1].start_ms, 120_000);
        assert_eq!(entries[1].end_ms, 180_000);
        assert_eq!(entries[2].start_ms, 300_000);
    }
}
