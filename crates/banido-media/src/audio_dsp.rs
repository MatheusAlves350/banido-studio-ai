//! Structure-of-Arrays (SoA) audio dynamics analysis, 100ms RMS envelopes,
//! adaptive Hype Score telemetry (`0..=100`), host speech detection, and
//! soft-knee balanced mixing.
//!
//! Fixes two latent bugs from `app/audio_pipeline.py`:
//! 1. Empty or `< 100ms` audio returns `-120.0 dBFS` (digital silence) instead
//!    of `0.0 dBFS` (full-scale maximum loudness).
//! 2. 5-second burst cadence slices adapt safely to any `sample_rate` instead
//!    of crashing on `reshape(20, window_5s // 20)`.

use serde::{Deserialize, Serialize};

/// Digital silence floor in dBFS (`20 * log10(1e-6)`).
pub const SILENCE_FLOOR_DB: f32 = -120.0;

/// Contiguous Structure-of-Arrays view over synchronized mono audio tracks.
#[derive(Debug, Clone, Copy)]
pub struct AudioTracksSoA<'a> {
    /// Isolated host microphone samples (`f32` in `[-1.0, 1.0]`).
    pub mic: &'a [f32],
    /// Normalized Discord / mix track samples.
    pub discord: &'a [f32],
    /// Optional Soundpad track samples.
    pub soundpad: Option<&'a [f32]>,
    /// Sampling rate in Hz (typically `16_000`).
    pub sample_rate: usize,
}

/// Options controlling multi-track dynamics analysis.
#[derive(Debug, Clone, Copy)]
pub struct AudioAnalysisOptions {
    /// Whether the recording provides a physically isolated host mic track.
    pub has_isolated_mic: bool,
    /// Total number of audio streams reported by `ffprobe`.
    pub stream_count: usize,
}

/// 5-second hype telemetry window matching the SQLite/JSON schema.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HypeWindow {
    /// Window start time in seconds.
    pub start: f64,
    /// Window end time in seconds.
    pub end: f64,
    /// Composite excitement score in `0..=100`.
    pub score: i32,
    /// Whether Soundpad energy exceeded the activation threshold (`0.015`).
    pub soundpad: bool,
    /// Whether both host and Discord were simultaneously loud (`> -45 dBFS`).
    pub is_chaos: bool,
}

/// Output of multi-track audio dynamics analysis.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioDynamics {
    /// Total analyzed duration in seconds.
    pub duration: f64,
    /// 100ms RMS envelope (dBFS) for the host microphone.
    pub mic_rms: Vec<f32>,
    /// 100ms RMS envelope (dBFS) for the Discord track.
    pub discord_rms: Vec<f32>,
    /// Effective isolated-mic flag (disabled if 2-track duplication detected).
    pub has_isolated_mic: bool,
    /// Per-5-second hype windows.
    pub hype_scores: Vec<HypeWindow>,
}

/// Computes linear-interpolated percentile matching `numpy.percentile(arr, q)`
/// using an `O(N)` partial selection (`select_nth_unstable_by`) on a mutable slice.
///
/// # Examples
/// ```
/// use banido_media::audio_dsp::percentile_in_place;
///
/// let mut data = vec![10.0_f32, 20.0, 30.0, 40.0, 50.0];
/// assert!((percentile_in_place(&mut data, 50.0) - 30.0).abs() < 1e-6);
/// assert!((percentile_in_place(&mut data, 75.0) - 40.0).abs() < 1e-6);
/// ```
pub fn percentile_in_place(data: &mut [f32], q: f64) -> f64 {
    let n = data.len();
    if n == 0 {
        return 0.0;
    }
    if n == 1 {
        return data[0] as f64;
    }
    let pos = (q.clamp(0.0, 100.0) / 100.0) * ((n - 1) as f64);
    let lo = pos.floor() as usize;
    let hi = pos.ceil() as usize;
    let frac = pos - (lo as f64);

    let (_, lo_val, right) = data.select_nth_unstable_by(lo, |a, b| a.total_cmp(b));
    let v_lo = *lo_val as f64;
    if hi == lo || right.is_empty() {
        return v_lo;
    }
    let v_hi = right
        .iter()
        .copied()
        .min_by(|a, b| a.total_cmp(b))
        .unwrap_or(*lo_val) as f64;
    v_lo + (v_hi - v_lo) * frac
}

/// Computes 100ms RMS envelopes in dBFS for a contiguous mono sample slice.
///
/// Uses `f64` sum-of-squares accumulators to prevent precision drift and
/// auto-vectorize cleanly across SIMD lanes.
pub fn compute_rms_100ms(samples: &[f32], sample_rate: usize) -> Vec<f32> {
    let frame_size = (sample_rate / 10).max(1);
    let num_frames = samples.len() / frame_size;
    if num_frames == 0 {
        return vec![SILENCE_FLOOR_DB];
    }
    let inv_n = 1.0 / (frame_size as f64);
    let mut out = Vec::with_capacity(num_frames);
    for chunk in samples[..num_frames * frame_size].chunks_exact(frame_size) {
        let mut sum_sq = 0.0_f64;
        for &x in chunk {
            let xf = x as f64;
            sum_sq += xf * xf;
        }
        let rms_lin = (sum_sq * inv_n).sqrt().max(1e-6);
        out.push((20.0 * rms_lin.log10()) as f32);
    }
    out
}

/// Computes sample standard deviation (`ddof = 0`, matching `numpy.std`).
fn std_dev_f32(vals: &[f32]) -> f64 {
    if vals.is_empty() {
        return 0.0;
    }
    let n = vals.len() as f64;
    let mean = vals.iter().map(|&v| v as f64).sum::<f64>() / n;
    let var = vals
        .iter()
        .map(|&v| {
            let d = (v as f64) - mean;
            d * d
        })
        .sum::<f64>()
        / n;
    var.sqrt()
}

/// Computes Pearson correlation between two equal-length `f32` slices.
fn pearson_corr(a: &[f32], b: &[f32]) -> f64 {
    let n = a.len().min(b.len());
    if n < 2 {
        return 0.0;
    }
    let nf = n as f64;
    let ma = a[..n].iter().map(|&v| v as f64).sum::<f64>() / nf;
    let mb = b[..n].iter().map(|&v| v as f64).sum::<f64>() / nf;
    let mut num = 0.0_f64;
    let mut da = 0.0_f64;
    let mut db = 0.0_f64;
    for i in 0..n {
        let xa = (a[i] as f64) - ma;
        let xb = (b[i] as f64) - mb;
        num += xa * xb;
        da += xa * xa;
        db += xb * xb;
    }
    let denom = (da * db).sqrt();
    if denom <= 1e-12 {
        0.0
    } else {
        num / denom
    }
}

pub use crate::audio_dsp_naive::analyze_audio_dynamics_naive;

/// Stage 3 (`soa_simd`) optimized kernel: eliminates full-track `abs` copies,
/// uses reusable scratch buffers, and computes percentiles in `O(N)` via
/// `select_nth_unstable_by` with SIMD-friendly inner loops.
pub fn analyze_audio_dynamics_soa(
    tracks: &AudioTracksSoA<'_>,
    opts: AudioAnalysisOptions,
) -> AudioDynamics {
    let min_len = tracks.mic.len().min(tracks.discord.len());
    let mic = &tracks.mic[..min_len];
    let disc = &tracks.discord[..min_len];
    let sr = tracks.sample_rate.max(1);

    let mic_rms = compute_rms_100ms(mic, sr);
    let discord_rms = compute_rms_100ms(disc, sr);
    let has_iso = detect_effective_isolation(opts, &mic_rms, &discord_rms);

    let mut active_scratch = Vec::with_capacity(min_len / 2);
    for &v in mic {
        let a = v.abs();
        if a > 0.004 {
            active_scratch.push(a);
        }
    }
    let m_ref = if active_scratch.len() > 200 {
        percentile_in_place(&mut active_scratch, 90.0)
    } else {
        0.08
    }
    .max(0.015);

    active_scratch.clear();
    for &v in disc {
        let a = v.abs();
        if a > 0.004 {
            active_scratch.push(a);
        }
    }
    let d_ref = if active_scratch.len() > 200 {
        percentile_in_place(&mut active_scratch, 90.0)
    } else {
        0.08
    }
    .max(0.015);

    let window_5s = sr * 5;
    let num_windows = min_len / window_5s;
    let mut hype_scores = Vec::with_capacity(num_windows);

    // Reuse one 5-second scratch buffer across all windows (zero hot-loop heap allocs).
    let mut win_scratch = vec![0.0_f32; window_5s];
    let mut rms_scratch = Vec::with_capacity(50);

    for w in 0..num_windows {
        let s = w * window_5s;
        let e = s + window_5s;

        // Discord peak + burst using single pass into reusable scratch
        for (dst, &src) in win_scratch.iter_mut().zip(&disc[s..e]) {
            *dst = src.abs();
        }
        let d_burst = burst_std_abs(&win_scratch) / d_ref;
        let d_rel = percentile_in_place(&mut win_scratch, 98.0) / d_ref;

        let (m_rel, m_burst) = if has_iso {
            for (dst, &src) in win_scratch.iter_mut().zip(&mic[s..e]) {
                *dst = src.abs();
            }
            let mb = burst_std_abs(&win_scratch) / m_ref;
            let mr = percentile_in_place(&mut win_scratch, 98.0) / m_ref;
            (mr, mb)
        } else {
            (0.0, 0.0)
        };

        let peak_score = (m_rel.max(d_rel) * 22.0).min(40.0);
        let burst_score = (d_burst.max(m_burst) * 45.0).min(30.0);

        let both_active = eval_chaos_scratch(has_iso, &mic_rms, &discord_rms, w, &mut rms_scratch);
        let sp_score = eval_soundpad(tracks.soundpad, s, e);
        let total = ((peak_score + burst_score + (both_active as f64) + (sp_score as f64))
            .min(100.0)) as i32;

        hype_scores.push(HypeWindow {
            start: (w * 5) as f64,
            end: ((w + 1) * 5) as f64,
            score: total,
            soundpad: sp_score > 0,
            is_chaos: both_active > 0,
        });
    }

    AudioDynamics {
        duration: (min_len as f64) / (sr as f64),
        mic_rms,
        discord_rms,
        has_isolated_mic: has_iso,
        hype_scores,
    }
}

/// Determines whether the host is speaking during `[start_sec, end_sec]`.
///
/// Mirrors `app/audio_pipeline.py::is_host_speaking` with exact thresholds.
pub fn is_host_speaking(start_sec: f64, end_sec: f64, dynamics: &AudioDynamics) -> bool {
    if !dynamics.has_isolated_mic || dynamics.mic_rms.is_empty() {
        return false;
    }
    let len = dynamics.mic_rms.len().min(dynamics.discord_rms.len());
    let s_idx = (start_sec.max(0.0) * 10.0) as usize;
    if s_idx >= len {
        return false;
    }
    let mut e_idx = ((end_sec.max(0.0) * 10.0) as usize).min(len);
    if s_idx >= e_idx {
        e_idx = (s_idx + 1).min(len);
    }
    let m_slice = &dynamics.mic_rms[s_idx..e_idx];
    let d_slice = &dynamics.discord_rms[s_idx..e_idx];
    if m_slice.is_empty() {
        return false;
    }

    let n = m_slice.len() as f64;
    let m_active = (m_slice.iter().filter(|&&v| v > -48.0).count() as f64) / n;
    let d_active = (d_slice.iter().filter(|&&v| v > -48.0).count() as f64) / n;

    let mut m_tmp = m_slice.to_vec();
    let mut d_tmp = d_slice.to_vec();
    let m_p75 = percentile_in_place(&mut m_tmp, 75.0);
    let d_p75 = percentile_in_place(&mut d_tmp, 75.0);

    if m_p75 <= -46.0 || m_active < 0.20 {
        return false;
    }
    if m_active >= 0.25 && d_active < 0.15 {
        return true;
    }
    if m_active >= d_active * 1.15 && m_p75 >= d_p75 - 4.0 {
        return true;
    }
    m_p75 >= d_p75 - 1.5 && m_active >= d_active * 0.75
}

/// Mixes isolated host mic and Discord tracks with a soft-knee `tanh` limiter
/// asymptotically capped at `0.95`.
pub fn build_balanced_mix_array(mic: &[f32], discord: &[f32]) -> Vec<f32> {
    let n = mic.len().min(discord.len());
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        let v = mic[i] + discord[i];
        let av = v.abs();
        let limited = if av > 0.85 {
            v.signum() * (0.85 + 0.10 * ((av - 0.85) / 0.15).tanh())
        } else {
            v
        };
        out.push(limited);
    }
    out
}

pub(crate) fn detect_effective_isolation(
    opts: AudioAnalysisOptions,
    mic_rms: &[f32],
    disc_rms: &[f32],
) -> bool {
    if opts.has_isolated_mic
        && opts.stream_count == 2
        && mic_rms.len() >= 20
        && std_dev_f32(mic_rms) > 0.1
        && std_dev_f32(disc_rms) > 0.1
        && pearson_corr(mic_rms, disc_rms) > 0.92
    {
        return false;
    }
    opts.has_isolated_mic
}

pub(crate) fn burst_std_abs(abs_win: &[f32]) -> f64 {
    let sub_len = abs_win.len() / 20;
    if sub_len == 0 {
        return 0.0;
    }
    let mut means = [0.0_f32; 20];
    let inv = 1.0 / (sub_len as f64);
    for (i, slot) in means.iter_mut().enumerate() {
        let start = i * sub_len;
        let sum: f64 = abs_win[start..start + sub_len]
            .iter()
            .map(|&v| v as f64)
            .sum();
        *slot = (sum * inv) as f32;
    }
    std_dev_f32(&means)
}

pub(crate) fn eval_chaos_scratch(
    has_iso: bool,
    mic_rms: &[f32],
    disc_rms: &[f32],
    w: usize,
    scratch: &mut Vec<f32>,
) -> i32 {
    if !has_iso {
        return 0;
    }
    let f_start = w * 50;
    let f_end = mic_rms.len().min(disc_rms.len()).min(f_start + 50);
    if f_end <= f_start {
        return 0;
    }
    scratch.clear();
    scratch.extend_from_slice(&mic_rms[f_start..f_end]);
    let m_p90 = percentile_in_place(scratch, 90.0);
    scratch.clear();
    scratch.extend_from_slice(&disc_rms[f_start..f_end]);
    let d_p90 = percentile_in_place(scratch, 90.0);
    if m_p90 > -45.0 && d_p90 > -45.0 {
        20
    } else {
        0
    }
}

pub(crate) fn eval_soundpad(soundpad: Option<&[f32]>, s: usize, e: usize) -> i32 {
    let Some(sp) = soundpad else {
        return 0;
    };
    if sp.len() < e {
        return 0;
    }
    let max_abs = sp[s..e]
        .iter()
        .fold(0.0_f32, |acc, &v| acc.max(v.abs()));
    if max_abs > 0.015 {
        15
    } else {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn test_empty_audio_returns_silence_floor_not_zero_db() {
        let rms = compute_rms_100ms(&[], 16_000);
        assert_eq!(rms, vec![SILENCE_FLOOR_DB]);
    }

    #[test]
    fn test_naive_and_soa_match_exactly() {
        let n = 16_000 * 10;
        let mic: Vec<f32> = (0..n).map(|i| ((i as f32) * 0.01).sin() * 0.2).collect();
        let disc: Vec<f32> = (0..n).map(|i| ((i as f32) * 0.02).cos() * 0.15).collect();
        let tracks = AudioTracksSoA {
            mic: &mic,
            discord: &disc,
            soundpad: None,
            sample_rate: 16_000,
        };
        let opts = AudioAnalysisOptions {
            has_isolated_mic: true,
            stream_count: 6,
        };
        let a = analyze_audio_dynamics_naive(&tracks, opts);
        let b = analyze_audio_dynamics_soa(&tracks, opts);
        assert_eq!(a.hype_scores, b.hype_scores);
        assert_eq!(a.mic_rms.len(), b.mic_rms.len());
    }

    proptest! {
        #[test]
        fn prop_hype_scores_always_in_bounds_for_any_sample_rate(
            sr in prop::sample::select(vec![8000usize, 11025, 16000, 22050]),
            amp in 0.0_f32..1.0_f32,
        ) {
            let n = sr * 5;
            let mic = vec![amp; n];
            let disc = vec![amp * 0.5; n];
            let dyn_res = analyze_audio_dynamics_soa(
                &AudioTracksSoA { mic: &mic, discord: &disc, soundpad: None, sample_rate: sr },
                AudioAnalysisOptions { has_isolated_mic: true, stream_count: 6 },
            );
            prop_assert_eq!(dyn_res.hype_scores.len(), 1);
            let sc = dyn_res.hype_scores[0].score;
            prop_assert!((0..=100).contains(&sc));
        }
    }
}
