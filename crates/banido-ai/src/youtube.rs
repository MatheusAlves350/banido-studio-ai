//! YouTube metadata normalization: bracket-free titles, valid `00:00:00`
//! chapter timelines (`>= 10s` spacing), highlight bounds clamping, tags CSV,
//! and description length budgeting (`<= 5000` chars).

use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

/// Normalized chapter item compliant with YouTube's chapter rules.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct YoutubeChapter {
    /// Timestamp formatted as `HH:MM:SS`.
    pub timestamp: String,
    /// Chapter title.
    pub title: String,
}

/// Normalized topic callout item.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct TopicItem {
    /// Topic summary.
    pub topic: String,
    /// Timestamp formatted as `HH:MM:SS`.
    pub timestamp: String,
    /// Associated keywords.
    pub tags: Vec<String>,
}

/// Normalized highlight clip suggestion.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HighlightClip {
    /// Start timestamp in seconds.
    pub start: f64,
    /// End timestamp in seconds.
    pub end: f64,
    /// Formatted start (`HH:MM:SS`).
    pub start_time: String,
    /// Formatted end (`HH:MM:SS`).
    pub end_time: String,
    /// Excitement score clamped to `0..=100`.
    pub score: i32,
    /// Clip title.
    pub title: String,
    /// Explanation of why this moment stands out.
    pub reason: String,
}

/// Complete normalized creative direction payload returned by AI providers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AiAnalysisData {
    /// Suggested mapping of `Voz_N` labels to friend names.
    pub speaker_mapping: HashMap<String, String>,
    /// Primary game detected or selected.
    pub main_game: String,
    /// Secondary games mentioned or played.
    pub other_games: Vec<String>,
    /// Up to 5 sanitized YouTube title candidates.
    pub title_options: Vec<String>,
    /// Concise session summary.
    pub session_summary: String,
    /// Validated YouTube chapters starting at `00:00:00`.
    pub chapters: Vec<YoutubeChapter>,
    /// Key conversation topics.
    pub topics: Vec<TopicItem>,
    /// Highlight clip suggestions sorted chronologically.
    pub highlights: Vec<HighlightClip>,
    /// Comma-separated YouTube tags (`<= 480` chars).
    pub youtube_tags_csv: String,
    /// Full YouTube description (`<= 5000` chars).
    pub youtube_description: String,
}

/// Formats non-negative seconds as `HH:MM:SS`.
pub fn format_hhmmss(sec: f64) -> String {
    let s = sec.max(0.0).round() as u64;
    let h = s / 3600;
    let m = (s % 3600) / 60;
    let rem = s % 60;
    format!("{h:02}:{m:02}:{rem:02}")
}

/// Parses `"HH:MM:SS"`, `"MM:SS"`, or numeric seconds.
pub fn parse_timestamp_sec(raw: &str) -> Option<f64> {
    let trimmed = raw.trim();
    if let Ok(v) = trimmed.parse::<f64>() {
        return if v.is_finite() && v >= 0.0 {
            Some(v)
        } else {
            None
        };
    }
    let parts: Vec<&str> = trimmed.split(':').collect();
    match parts.as_slice() {
        [m, s] => Some(m.trim().parse::<f64>().ok()? * 60.0 + s.trim().parse::<f64>().ok()?),
        [h, m, s] => Some(
            h.trim().parse::<f64>().ok()? * 3600.0
                + m.trim().parse::<f64>().ok()? * 60.0
                + s.trim().parse::<f64>().ok()?,
        ),
        _ => None,
    }
}

/// Converts bracketed prefixes like `"[CS2] Clutch Épico"` into `"CS2 - Clutch Épico"`.
///
/// # Examples
/// ```
/// use banido_ai::youtube::sanitize_video_title;
///
/// assert_eq!(sanitize_video_title("[CS2] Clutch Épico"), "CS2 - Clutch Épico");
/// ```
pub fn sanitize_video_title(title: &str) -> String {
    static LEAD: OnceLock<Regex> = OnceLock::new();
    static INLINE: OnceLock<Regex> = OnceLock::new();
    static DOUBLE: OnceLock<Regex> = OnceLock::new();
    let lead = LEAD.get_or_init(|| Regex::new(r"^\[\s*([^\[\]]+?)\s*\]\s*[-–—:]?\s*").unwrap());
    let inline = INLINE.get_or_init(|| Regex::new(r"\[\s*([^\[\]]+?)\s*\]").unwrap());
    let double = DOUBLE.get_or_init(|| Regex::new(r"\s*-\s*-\s*").unwrap());

    let s = lead.replace(title.trim(), "$1 - ");
    let s = inline.replace_all(&s, "$1");
    let s = double.replace_all(&s, " - ");
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Normalizes chapters to obey YouTube requirements:
/// sorted ascending, first chapter forced to `00:00:00`, within `duration_sec + 5`,
/// and minimum `10s` spacing between consecutive chapters.
///
/// Fixes the ordering bug in `app/ai/youtube.py:188-203` by clamping the first
/// chapter to `00:00:00` **before** enforcing the 10-second gap.
pub fn normalize_youtube_chapters(
    raw_chapters: &[YoutubeChapter],
    duration_sec: Option<f64>,
) -> Vec<YoutubeChapter> {
    let mut parsed: Vec<(f64, String)> = raw_chapters
        .iter()
        .filter_map(|c| {
            let sec = parse_timestamp_sec(&c.timestamp)?;
            if let Some(dur) = duration_sec {
                if sec > dur + 5.0 {
                    return None;
                }
            }
            let title = c.title.trim();
            if title.is_empty() {
                return None;
            }
            Some((sec, title.to_string()))
        })
        .collect();

    if parsed.is_empty() {
        return vec![YoutubeChapter {
            timestamp: "00:00:00".to_string(),
            title: "Início da Sessão".to_string(),
        }];
    }
    parsed.sort_by(|a, b| a.0.total_cmp(&b.0));

    // Anchor to 00:00:00 before enforcing >= 10s spacing:
    // shift if first chapter < 10s; prepend "Início da Sessão" if >= 10s.
    if parsed[0].0 > 0.0 {
        if parsed[0].0 < 10.0 {
            parsed[0].0 = 0.0;
        } else {
            parsed.insert(0, (0.0, "Início da Sessão".to_string()));
        }
    }

    let mut out: Vec<YoutubeChapter> = Vec::with_capacity(parsed.len());
    let mut last_sec = -100.0_f64;
    for (sec, title) in parsed {
        if out.is_empty() || (sec - last_sec) >= 10.0 {
            last_sec = sec;
            out.push(YoutubeChapter {
                timestamp: format_hhmmss(sec),
                title,
            });
        }
    }
    out
}

/// Builds a YouTube tags CSV string capped at 480 characters using `group_name`.
pub fn build_youtube_tags_csv(main_game: &str, topics: &[TopicItem], group_name: &str) -> String {
    let mut seen = HashSet::new();
    let mut tags = Vec::new();
    let mut push_tag = |raw: &str| {
        let clean = raw.trim();
        if !clean.is_empty() && seen.insert(clean.to_lowercase()) {
            tags.push(clean.to_string());
        }
    };

    push_tag(main_game);
    push_tag(group_name);
    push_tag("gameplay");
    push_tag("discord");
    push_tag("melhores momentos");
    for t in topics {
        for kw in &t.tags {
            push_tag(kw);
        }
    }

    let mut csv = String::new();
    for tag in tags {
        let sep = if csv.is_empty() { "" } else { ", " };
        if csv.len() + sep.len() + tag.len() > 480 {
            break;
        }
        csv.push_str(sep);
        csv.push_str(&tag);
    }
    csv
}

/// Builds a YouTube description capped at 5000 characters.
pub fn build_youtube_description(
    summary: &str,
    chapters: &[YoutubeChapter],
    highlights: &[HighlightClip],
    tags_csv: &str,
) -> String {
    let mut parts = Vec::new();
    if !summary.trim().is_empty() {
        parts.push(summary.trim().to_string());
    }
    if !chapters.is_empty() {
        let ch_lines: Vec<String> = chapters
            .iter()
            .map(|c| format!("{} {}", c.timestamp, c.title))
            .collect();
        parts.push(format!("Capítulos:\n{}", ch_lines.join("\n")));
    }
    if !highlights.is_empty() {
        let hl_lines: Vec<String> = highlights
            .iter()
            .take(8)
            .map(|h| format!("{} - {} ({}/100)", h.start_time, h.title, h.score))
            .collect();
        parts.push(format!("Melhores Momentos:\n{}", hl_lines.join("\n")));
    }
    if !tags_csv.is_empty() {
        parts.push(format!("Tags: {tags_csv}"));
    }
    let full = parts.join("\n\n");
    full.chars().take(5000).collect()
}

/// Normalizes raw parsed JSON object into [`AiAnalysisData`].
pub fn normalize_ai_data(
    raw: &Value,
    duration_sec: Option<f64>,
    group_name: &str,
) -> AiAnalysisData {
    let main_game = raw
        .get("main_game")
        .and_then(Value::as_str)
        .unwrap_or("Gameplay")
        .trim()
        .to_string();

    let other_games: Vec<String> = raw
        .get("other_games")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty() && !s.eq_ignore_ascii_case(&main_game))
                .map(ToOwned::to_owned)
                .collect()
        })
        .unwrap_or_default();

    let title_options: Vec<String> = raw
        .get("title_options")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(Value::as_str)
                .map(sanitize_video_title)
                .filter(|s| !s.is_empty())
                .take(5)
                .collect()
        })
        .unwrap_or_default();

    let session_summary = raw
        .get("session_summary")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();

    let speaker_mapping: HashMap<String, String> = raw
        .get("speaker_mapping")
        .and_then(Value::as_object)
        .map(|obj| {
            obj.iter()
                .filter_map(|(k, v)| Some((k.clone(), v.as_str()?.trim().to_string())))
                .collect()
        })
        .unwrap_or_default();

    let raw_chapters: Vec<YoutubeChapter> = raw
        .get("chapters")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|item| {
                    let ts = item.get("timestamp")?.as_str()?.to_string();
                    let title = item.get("title")?.as_str()?.to_string();
                    Some(YoutubeChapter {
                        timestamp: ts,
                        title,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    let chapters = normalize_youtube_chapters(&raw_chapters, duration_sec);

    let topics: Vec<TopicItem> = raw
        .get("topics")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|item| {
                    let topic = item.get("topic")?.as_str()?.trim().to_string();
                    let ts = item
                        .get("timestamp")
                        .and_then(Value::as_str)
                        .unwrap_or("00:00:00")
                        .to_string();
                    let tags = item
                        .get("tags")
                        .and_then(Value::as_array)
                        .map(|t| {
                            t.iter()
                                .filter_map(Value::as_str)
                                .map(|s| s.trim().to_string())
                                .collect()
                        })
                        .unwrap_or_default();
                    Some(TopicItem {
                        topic,
                        timestamp: ts,
                        tags,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let mut highlights: Vec<HighlightClip> = raw
        .get("highlights")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|item| {
                    let mut start = item.get("start").and_then(Value::as_f64)?.max(0.0);
                    let mut end = item.get("end").and_then(Value::as_f64)?.max(0.0);
                    if let Some(dur) = duration_sec {
                        start = start.min(dur);
                        end = end.min(dur);
                    }
                    if end - start < 10.0 {
                        end = (start + 15.0).min(duration_sec.unwrap_or(start + 15.0));
                    }
                    let score = item
                        .get("score")
                        .and_then(Value::as_i64)
                        .unwrap_or(75)
                        .clamp(0, 100) as i32;
                    let title = item
                        .get("title")
                        .and_then(Value::as_str)
                        .unwrap_or("Destaque")
                        .trim()
                        .to_string();
                    let reason = item
                        .get("reason")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .trim()
                        .to_string();
                    Some(HighlightClip {
                        start,
                        end,
                        start_time: format_hhmmss(start),
                        end_time: format_hhmmss(end),
                        score,
                        title,
                        reason,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    highlights.sort_by(|a, b| a.start.total_cmp(&b.start));

    let youtube_tags_csv = build_youtube_tags_csv(&main_game, &topics, group_name);
    let youtube_description =
        build_youtube_description(&session_summary, &chapters, &highlights, &youtube_tags_csv);

    AiAnalysisData {
        speaker_mapping,
        main_game,
        other_games,
        title_options,
        session_summary,
        chapters,
        topics,
        highlights,
        youtube_tags_csv,
        youtube_description,
    }
}
