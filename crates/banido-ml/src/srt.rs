//! SubRip (`.srt`) timestamp formatting and export.
//!
//! Fixes the latent bug in `app/transcription/srt.py:6-13` where negative
//! timestamps emitted malformed negative timecodes.

use crate::tracks::TranscriptSegment;
use crate::MlResult;
use std::fs;
use std::path::Path;

/// Formats seconds as a valid non-negative SRT timestamp (`HH:MM:SS,mmm`).
///
/// Negative or non-finite values clamp safely to `00:00:00,000`.
///
/// # Examples
/// ```
/// use banido_ml::srt::seconds_to_srt_time;
///
/// assert_eq!(seconds_to_srt_time(90.5), "00:01:30,500");
/// assert_eq!(seconds_to_srt_time(-3.2), "00:00:00,000");
/// ```
pub fn seconds_to_srt_time(seconds: f64) -> String {
    let clamped = if seconds.is_finite() {
        seconds.max(0.0)
    } else {
        0.0
    };
    let whole = clamped.trunc() as u64;
    let hrs = whole / 3600;
    let mins = (whole % 3600) / 60;
    let secs = whole % 60;
    let msecs = (((clamped - (whole as f64)) * 1000.0).round() as u64).min(999);
    format!("{hrs:02}:{mins:02}:{secs:02},{msecs:03}")
}

/// Renders transcript segments into a UTF-8 `.srt` document string.
pub fn render_srt(segments: &[TranscriptSegment]) -> String {
    let mut out = String::with_capacity(segments.len() * 64);
    for (idx, seg) in segments.iter().enumerate() {
        let start = seconds_to_srt_time(seg.start);
        let end = seconds_to_srt_time(seg.end.max(seg.start));
        let speaker = if seg.speaker.trim().is_empty() {
            "Amigo"
        } else {
            seg.speaker.trim()
        };
        let text = seg.text.trim();
        out.push_str(&format!("{}\n{start} --> {end}\n[{speaker}]: {text}\n\n", idx + 1));
    }
    out
}

/// Exports transcript segments to `srt_path` in UTF-8.
pub fn export_srt_file(segments: &[TranscriptSegment], srt_path: &Path) -> MlResult<()> {
    if let Some(parent) = srt_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(srt_path, render_srt(segments))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        #[test]
        fn prop_srt_time_is_always_non_negative_format(sec in -1000.0_f64..359_999.0_f64) {
            let s = seconds_to_srt_time(sec);
            prop_assert!(!s.contains('-'));
            prop_assert_eq!(s.len(), 12);
            prop_assert_eq!(&s[2..3], ":");
            prop_assert_eq!(&s[5..6], ":");
            prop_assert_eq!(&s[8..9], ",");
        }
    }
}
