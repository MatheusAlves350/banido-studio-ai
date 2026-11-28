//! Multi-track transcript merging, microphone bleed/echo rejection, and
//! cross-speaker overlap detection.

use crate::hallucination::normalize_for_hallucination;
use serde::{Deserialize, Serialize};

/// Single transcribed speech segment across Host or Discord tracks.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TranscriptSegment {
    /// Start timestamp in seconds.
    pub start: f64,
    /// End timestamp in seconds.
    pub end: f64,
    /// Transcribed utterance text.
    pub text: String,
    /// Speaker label (`Host`, `Discord`, `Voz_1`, or canonical friend name).
    pub speaker: String,
    /// Set to `true` when this segment overlaps another speaker in time.
    #[serde(default)]
    pub overlap: bool,
    /// Optional diarization confidence score in `[0.0, 1.0]`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub speaker_confidence: Option<f64>,
    /// Optional cluster tag (`Voz_1`, etc.).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cluster_tag: Option<String>,
}

/// Computes normalized overlap ratio `inter / max(0.05, min(dur_a, dur_b))`.
pub fn time_overlap(a: &TranscriptSegment, b: &TranscriptSegment) -> f64 {
    let inter = a.end.min(b.end) - a.start.max(b.start);
    let shorter = (a.end - a.start).min(b.end - b.start).max(0.05);
    inter.max(0.0) / shorter
}

/// Computes a sequence similarity ratio in `[0.0, 1.0]` equivalent to Python's
/// `difflib.SequenceMatcher(None, a, b).ratio()` (`2 * matches / (len_a + len_b)`).
pub fn sequence_similarity_ratio(a: &str, b: &str) -> f64 {
    let ca: Vec<char> = a.chars().collect();
    let cb: Vec<char> = b.chars().collect();
    let total = ca.len() + cb.len();
    if total == 0 {
        return 1.0;
    }
    let matches = lcs_len(&ca, &cb);
    (2.0 * (matches as f64)) / (total as f64)
}

fn lcs_len(a: &[char], b: &[char]) -> usize {
    let m = b.len();
    let mut prev = vec![0usize; m + 1];
    let mut curr = vec![0usize; m + 1];
    for &ca in a {
        for (j, &cb) in b.iter().enumerate() {
            curr[j + 1] = if ca == cb {
                prev[j] + 1
            } else {
                prev[j + 1].max(curr[j])
            };
        }
        std::mem::swap(&mut prev, &mut curr);
        curr.fill(0);
    }
    prev[m]
}

/// Filters host microphone segments that are speaker-bleed echoes of Discord
/// audio, while preserving short simultaneous reactions (`"Boa!"`, `"Morri!"`).
pub fn drop_mic_bleed(
    host_segs: &[TranscriptSegment],
    discord_segs: &[TranscriptSegment],
) -> Vec<TranscriptSegment> {
    let disc_norms: Vec<String> = discord_segs
        .iter()
        .map(|d| normalize_for_hallucination(&d.text))
        .collect();

    let mut kept = Vec::with_capacity(host_segs.len());
    for h in host_segs {
        let h_norm = normalize_for_hallucination(&h.text);
        if h_norm.len() < 12 && h_norm.split_whitespace().count() < 3 {
            kept.push(h.clone());
            continue;
        }
        let mut is_echo = false;
        for (d, d_norm) in discord_segs.iter().zip(&disc_norms) {
            if d.start > h.end {
                break;
            }
            if time_overlap(h, d) < 0.5 {
                continue;
            }
            if sequence_similarity_ratio(&h_norm, d_norm) >= 0.8 {
                is_echo = true;
                break;
            }
        }
        if !is_echo {
            kept.push(h.clone());
        }
    }
    kept
}

/// Merges Host and Discord track segments chronologically without mutating inputs,
/// dropping mic bleed echoes and flagging cross-speaker overlaps.
///
/// # Examples
/// ```
/// use banido_ml::tracks::{merge_track_segments, TranscriptSegment};
///
/// let host = vec![TranscriptSegment {
///     start: 10.0, end: 11.2, text: "Boa!".into(), speaker: "Host".into(),
///     overlap: false, speaker_confidence: None, cluster_tag: None,
/// }];
/// let disc = vec![TranscriptSegment {
///     start: 10.1, end: 11.3, text: "Boa!".into(), speaker: "Discord".into(),
///     overlap: false, speaker_confidence: None, cluster_tag: None,
/// }];
/// let merged = merge_track_segments(&host, &disc);
/// assert_eq!(merged.len(), 2);
/// assert!(merged.iter().all(|s| s.overlap));
/// ```
pub fn merge_track_segments(
    host_segs: &[TranscriptSegment],
    discord_segs: &[TranscriptSegment],
) -> Vec<TranscriptSegment> {
    let mut disc_sorted = discord_segs.to_vec();
    disc_sorted.sort_by(|a, b| a.start.total_cmp(&b.start).then(a.end.total_cmp(&b.end)));

    let mut host_sorted = host_segs.to_vec();
    host_sorted.sort_by(|a, b| a.start.total_cmp(&b.start).then(a.end.total_cmp(&b.end)));

    let mut merged = drop_mic_bleed(&host_sorted, &disc_sorted);
    merged.extend(disc_sorted);
    merged.sort_by(|a, b| a.start.total_cmp(&b.start).then(a.end.total_cmp(&b.end)));

    let n = merged.len();
    let mut overlaps = vec![false; n];
    for i in 0..n {
        for j in (i + 1)..n {
            if merged[j].start >= merged[i].end {
                break;
            }
            if merged[i].speaker != merged[j].speaker {
                overlaps[i] = true;
                overlaps[j] = true;
            }
        }
    }
    for (seg, is_ov) in merged.iter_mut().zip(overlaps) {
        if is_ov {
            seg.overlap = true;
        }
    }
    merged
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seg(start: f64, end: f64, text: &str, speaker: &str) -> TranscriptSegment {
        TranscriptSegment {
            start,
            end,
            text: text.into(),
            speaker: speaker.into(),
            overlap: false,
            speaker_confidence: None,
            cluster_tag: None,
        }
    }

    #[test]
    fn test_drop_echo_and_keep_short_reaction() {
        let host = vec![
            seg(1.0, 4.0, "Vamos pelo outro lado, pessoal!", "Host"),
            seg(10.0, 11.2, "Boa!", "Host"),
        ];
        let disc = vec![
            seg(1.1, 4.0, "vamos pelo outro lado pessoal", "Discord"),
            seg(10.1, 11.3, "Boa!", "Discord"),
        ];
        let merged = merge_track_segments(&host, &disc);
        assert_eq!(merged.len(), 3);
        assert_eq!(merged[0].speaker, "Discord");
        assert!(merged[1].overlap && merged[2].overlap);
    }
}
