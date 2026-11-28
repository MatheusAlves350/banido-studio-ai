//! FFmpeg / `ffprobe` filter graphs, command builders, and probe inspection.
//!
//! Preserves exact calibrated filter chains from `app/audio_pipeline.py` and
//! `app/lossless_media.py` while handling missing/`"N/A"` durations safely.

use crate::MediaResult;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;

/// Dynamic normalization filter applied to the Discord track.
pub const DISCORD_NORM_FILTER: &str = "dynaudnorm=f=150:g=15:p=0.90:m=20";

/// 16 kHz mono downmix + dynamic normalization filter for Whisper.
pub const DISCORD_NORM_FILTER_16K: &str =
    "aresample=16000,aformat=channel_layouts=mono,dynaudnorm=f=150:g=15:p=0.90:m=20";

/// Metadata tag written to Track 1 / container after YouTube Gold normalization.
pub const YOUTUBE_AUDIO_NORM_TAG: &str = "youtube_gold_v1";

/// Calibrated YouTube Gold filter chain (`-15.5 LUFS`, `<= -1.0 dBTP`) for multi-track MKVs.
pub const YOUTUBE_GOLD_FILTER_MULTI: &str = "[0:a:0]highpass=f=75,\
dynaudnorm=f=250:g=21:p=0.82:m=12:c=1:b=1:t=0.004,\
acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=9dB[a1];\
[0:a:2]highpass=f=85,\
dynaudnorm=f=200:g=15:p=0.78:m=75:c=1:b=1:t=0.0008,\
acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=7.5dB[a3];\
[a1][a3]amix=inputs=2:duration=longest:normalize=0:weights=1.0 0.80,\
volume=5.0dB,\
alimiter=limit=0.87:attack=5:release=50:level=false,\
aformat=sample_rates=48000:channel_layouts=stereo[aout]";

/// Calibrated YouTube Gold filter chain for single-track recordings.
pub const YOUTUBE_GOLD_FILTER_SINGLE: &str = "[0:a:0]highpass=f=75,\
dynaudnorm=f=220:g=15:p=0.80:m=45:c=1:b=1:t=0.001,\
acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=8.5dB,\
volume=4.5dB,\
alimiter=limit=0.87:attack=5:release=50:level=false,\
aformat=sample_rates=48000:channel_layouts=stereo[aout]";

/// Parsed MKV stream and YouTube Gold normalization inspection summary.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MkvAudioNormInfo {
    /// Count of audio streams.
    pub audio_streams: usize,
    /// Count of video streams.
    pub video_streams: usize,
    /// Container duration in seconds (`0.0` if missing/`N/A`).
    pub duration: f64,
    /// Whether the MKV is already normalized with YouTube Gold.
    pub is_normalized: bool,
    /// Matched normalization tag value.
    pub norm_tag: String,
    /// Whether an original Track 1 mix backup stream is present.
    pub has_original_backup: bool,
    /// Zero-based audio stream index of the backup track, if present.
    pub original_backup_index: Option<usize>,
}

/// Parses `ffprobe -print_format json -show_format -show_streams` output safely.
pub fn inspect_mkv_probe_json(json_str: &str) -> MediaResult<MkvAudioNormInfo> {
    let data: Value = serde_json::from_str(json_str)?;
    let streams = data
        .get("streams")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let audio_streams: Vec<&Value> = streams
        .iter()
        .filter(|s| s.get("codec_type").and_then(Value::as_str) == Some("audio"))
        .collect();
    let video_streams = streams
        .iter()
        .filter(|s| s.get("codec_type").and_then(Value::as_str) == Some("video"))
        .count();

    let duration = data
        .get("format")
        .and_then(|f| f.get("duration"))
        .and_then(|d| {
            d.as_f64()
                .or_else(|| d.as_str().and_then(|s| s.parse::<f64>().ok()))
        })
        .unwrap_or(0.0);

    let tag_upper = |obj: Option<&Value>, key: &str| -> String {
        obj.and_then(|o| o.get("tags"))
            .and_then(Value::as_object)
            .and_then(|map| {
                map.iter()
                    .find(|(k, _)| k.eq_ignore_ascii_case(key))
                    .and_then(|(_, v)| v.as_str())
            })
            .unwrap_or("")
            .to_string()
    };

    let mut norm_tag = tag_upper(data.get("format"), "BANIDO_AUDIO_NORM");
    if norm_tag.is_empty() {
        if let Some(a0) = audio_streams.first() {
            norm_tag = tag_upper(Some(a0), "BANIDO_AUDIO_NORM");
            if norm_tag.is_empty()
                && tag_upper(Some(a0), "TITLE")
                    .to_lowercase()
                    .contains("youtube gold")
            {
                norm_tag = YOUTUBE_AUDIO_NORM_TAG.to_string();
            }
        }
    }

    let mut original_backup_index = None;
    for (idx, a_st) in audio_streams.iter().enumerate() {
        let has_flag = !tag_upper(Some(a_st), "BANIDO_ORIGINAL_MIX").is_empty();
        let has_title = tag_upper(Some(a_st), "TITLE")
            .to_lowercase()
            .contains("original mix backup");
        if has_flag || has_title {
            original_backup_index = Some(idx);
            break;
        }
    }

    Ok(MkvAudioNormInfo {
        audio_streams: audio_streams.len(),
        video_streams,
        duration,
        is_normalized: !norm_tag.is_empty(),
        norm_tag,
        has_original_backup: original_backup_index.is_some(),
        original_backup_index,
    })
}

/// Builds the unified Single-Pass FFmpeg `filter_complex` graph that emits both
/// `[disc_wh]` (16 kHz mono Whisper input) and `[aout]` (YouTube Gold mix).
///
/// # Examples
/// ```
/// use banido_media::ffmpeg_cmd::build_single_pass_filter_complex;
///
/// let multi = build_single_pass_filter_complex(6);
/// assert!(multi.contains("[0:a:2]asplit=2[disc_wh_in][disc_yt_in]"));
/// assert!(multi.contains("[disc_wh]"));
/// assert!(multi.contains("[aout]"));
/// ```
pub fn build_single_pass_filter_complex(stream_count: usize) -> String {
    if stream_count >= 3 {
        format!(
            "[0:a:2]asplit=2[disc_wh_in][disc_yt_in];\
             [disc_wh_in]{DISCORD_NORM_FILTER_16K}[disc_wh];\
             [0:a:0]highpass=f=75,dynaudnorm=f=250:g=21:p=0.82:m=12:c=1:b=1:t=0.004,\
             acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=9dB[a1];\
             [disc_yt_in]highpass=f=85,dynaudnorm=f=200:g=15:p=0.78:m=75:c=1:b=1:t=0.0008,\
             acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=7.5dB[a3];\
             [a1][a3]amix=inputs=2:duration=longest:normalize=0:weights=1.0 0.80,\
             volume=5.0dB,alimiter=limit=0.87:attack=5:release=50:level=false,\
             aformat=sample_rates=48000:channel_layouts=stereo[aout]"
        )
    } else {
        format!(
            "[0:a:0]asplit=2[disc_wh_in][yt_in];\
             [disc_wh_in]{DISCORD_NORM_FILTER_16K}[disc_wh];\
             [yt_in]highpass=f=75,dynaudnorm=f=220:g=15:p=0.80:m=45:c=1:b=1:t=0.001,\
             acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=8.5dB,\
             volume=4.5dB,alimiter=limit=0.87:attack=5:release=50:level=false,\
             aformat=sample_rates=48000:channel_layouts=stereo[aout]"
        )
    }
}

/// Builds FFmpeg CLI arguments for exporting a highlight clip.
///
/// Uses `-c:v copy` for lossless fast cuts, or hybrid two-stage `-ss` +
/// `libx264` CRF 16 when `exact_cut` is enabled.
pub fn build_highlight_cut_args(
    mkv_path: &Path,
    out_path: &Path,
    start_sec: f64,
    end_sec: f64,
    exact_cut: bool,
    keep_all_tracks: bool,
) -> Vec<String> {
    let start = start_sec.max(0.0);
    let dur = (end_sec - start).max(0.5);
    let mut args = vec!["ffmpeg".to_string(), "-y".to_string()];

    if exact_cut {
        let fast_seek = (start - 4.0).max(0.0);
        let fine_seek = start - fast_seek;
        args.extend([
            "-ss".into(),
            format!("{fast_seek:.3}"),
            "-i".into(),
            mkv_path.to_string_lossy().into_owned(),
            "-ss".into(),
            format!("{fine_seek:.3}"),
            "-t".into(),
            format!("{dur:.3}"),
            "-map".into(),
            "0:v:0".into(),
            "-map".into(),
            if keep_all_tracks { "0:a" } else { "0:a:0" }.into(),
            "-c:v".into(),
            "libx264".into(),
            "-preset".into(),
            "fast".into(),
            "-crf".into(),
            "16".into(),
            "-pix_fmt".into(),
            "yuv420p".into(),
            "-c:a".into(),
            "aac".into(),
            "-b:a".into(),
            "320k".into(),
        ]);
    } else {
        args.extend([
            "-ss".into(),
            format!("{start:.3}"),
            "-i".into(),
            mkv_path.to_string_lossy().into_owned(),
            "-t".into(),
            format!("{dur:.3}"),
            "-map".into(),
            "0:v:0".into(),
            "-map".into(),
            if keep_all_tracks { "0:a" } else { "0:a:0" }.into(),
            "-c:v".into(),
            "copy".into(),
            "-c:a".into(),
            "copy".into(),
            "-avoid_negative_ts".into(),
            "make_zero".into(),
        ]);
    }
    args.push(out_path.to_string_lossy().into_owned());
    args
}

/// Builds lossless FFmpeg stream-copy arguments to drop the backup audio track
/// while preserving video, remaining audio, subtitles, metadata, and chapters.
pub fn build_remove_backup_args(
    mkv_path: &Path,
    temp_out: &Path,
    backup_audio_index: usize,
) -> Vec<String> {
    vec![
        "ffmpeg".into(),
        "-y".into(),
        "-i".into(),
        mkv_path.to_string_lossy().into_owned(),
        "-map".into(),
        "0".into(),
        "-map".into(),
        format!("-0:a:{backup_audio_index}"),
        "-map_metadata".into(),
        "0".into(),
        "-map_chapters".into(),
        "0".into(),
        "-c".into(),
        "copy".into(),
        temp_out.to_string_lossy().into_owned(),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn test_single_pass_graph_multi_and_single() {
        let m = build_single_pass_filter_complex(6);
        assert!(m.contains("[0:a:2]asplit=2[disc_wh_in][disc_yt_in]"));
        assert!(m.contains("[disc_wh]"));
        assert!(m.contains("[aout]"));

        let s = build_single_pass_filter_complex(1);
        assert!(s.contains("[0:a:0]asplit=2[disc_wh_in][yt_in]"));
        assert!(s.contains("[disc_wh]"));
        assert!(s.contains("[aout]"));
    }

    #[test]
    fn test_inspect_mkv_probe_and_exact_cut_cmd() {
        let probe = serde_json::json!({
            "format": {"duration": "120.0", "tags": {"BANIDO_AUDIO_NORM": YOUTUBE_AUDIO_NORM_TAG}},
            "streams": [
                {"codec_type": "video"},
                {"codec_type": "audio", "tags": {"title": "Track1 (YouTube Gold)"}},
                {"codec_type": "audio", "tags": {"title": "Track2 (Mic)"}},
                {"codec_type": "audio", "tags": {"title": "Track3 (Discord)"}},
                {"codec_type": "audio", "tags": {"title": "Track1 (Original Mix Backup)", "BANIDO_ORIGINAL_MIX": "1"}}
            ]
        });
        let info = inspect_mkv_probe_json(&probe.to_string()).unwrap();
        assert!(info.is_normalized);
        assert!(info.has_original_backup);
        assert_eq!(info.original_backup_index, Some(3));

        let cmd = build_highlight_cut_args(
            &PathBuf::from("a.mkv"),
            &PathBuf::from("out.mp4"),
            100.0,
            115.0,
            true,
            false,
        );
        assert!(cmd.contains(&"libx264".to_string()));
        assert_eq!(cmd.iter().filter(|a| a.as_str() == "-ss").count(), 2);
    }
}
