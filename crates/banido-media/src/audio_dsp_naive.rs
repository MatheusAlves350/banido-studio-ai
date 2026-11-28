//! Stage 2 (`safe_rust`) idiomatic baseline audio dynamics analyzer.
//!
//! Retained alongside [`crate::audio_dsp::analyze_audio_dynamics_soa`] so the
//! 4-stage benchmark staircase can directly measure the speedup of SoA scratch
//! buffers and `O(N)` quickselect over full-array allocations and sorting.

use crate::audio_dsp::{
    burst_std_abs, compute_rms_100ms, detect_effective_isolation, eval_chaos_scratch,
    eval_soundpad, AudioAnalysisOptions, AudioDynamics, AudioTracksSoA, HypeWindow,
};

/// Idiomatic Safe Rust port that mirrors NumPy's temporary `abs` copies and full sorts.
pub fn analyze_audio_dynamics_naive(
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

    let m_abs: Vec<f32> = mic.iter().map(|x| x.abs()).collect();
    let d_abs: Vec<f32> = disc.iter().map(|x| x.abs()).collect();

    let mut m_active: Vec<f32> = m_abs.iter().copied().filter(|&v| v > 0.004).collect();
    let mut d_active: Vec<f32> = d_abs.iter().copied().filter(|&v| v > 0.004).collect();
    let m_ref = sorted_percentile(&mut m_active, 90.0, 0.08).max(0.015);
    let d_ref = sorted_percentile(&mut d_active, 90.0, 0.08).max(0.015);

    let window_5s = sr * 5;
    let num_windows = min_len / window_5s;
    let mut hype_scores = Vec::with_capacity(num_windows);
    let mut rms_scratch = Vec::with_capacity(50);

    for w in 0..num_windows {
        let s = w * window_5s;
        let e = s + window_5s;
        let mut m_copy = m_abs[s..e].to_vec();
        let mut d_copy = d_abs[s..e].to_vec();
        m_copy.sort_by(|a, b| a.total_cmp(b));
        d_copy.sort_by(|a, b| a.total_cmp(b));
        let m_rel = if has_iso {
            sorted_slice_percentile(&m_copy, 98.0) / m_ref
        } else {
            0.0
        };
        let d_rel = sorted_slice_percentile(&d_copy, 98.0) / d_ref;
        let peak_score = (m_rel.max(d_rel) * 22.0).min(40.0);

        let d_burst = burst_std_abs(&d_abs[s..e]) / d_ref;
        let m_burst = if has_iso {
            burst_std_abs(&m_abs[s..e]) / m_ref
        } else {
            0.0
        };
        let burst_score = (d_burst.max(m_burst) * 45.0).min(30.0);

        let both_active = eval_chaos_scratch(has_iso, &mic_rms, &discord_rms, w, &mut rms_scratch);
        let sp_score = eval_soundpad(tracks.soundpad, s, e);
        let total = ((peak_score + burst_score + (both_active as f64) + (sp_score as f64))
            .min(100.0)) as i32;

        hype_scores.push(HypeWindow {
            start: ((w * 5) as f64 * 100.0).round() / 100.0,
            end: (((w + 1) * 5) as f64 * 100.0).round() / 100.0,
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

fn sorted_percentile(vals: &mut [f32], q: f64, fallback: f64) -> f64 {
    if vals.len() <= 200 {
        return fallback;
    }
    vals.sort_by(|a, b| a.total_cmp(b));
    sorted_slice_percentile(vals, q)
}

fn sorted_slice_percentile(sorted: &[f32], q: f64) -> f64 {
    let n = sorted.len();
    if n == 0 {
        return 0.0;
    }
    let pos = (q / 100.0) * ((n - 1) as f64);
    let lo = pos.floor() as usize;
    let hi = pos.ceil() as usize;
    let frac = pos - (lo as f64);
    let a = sorted[lo] as f64;
    let b = sorted[hi] as f64;
    a + (b - a) * frac
}
