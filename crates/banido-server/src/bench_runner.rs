//! Built-in deterministic benchmark (`--bench`) and A/B differential parity
//! verification (`--parity --golden <path>`) runner matching `legacy_bench.py`
//! bit-for-bit via [`SplitMix64`].

use banido_ai::parsing::parse_ai_json_response;
use banido_core::diagnostics::sanitize_log_text;
use banido_core::prng::SplitMix64;
use banido_media::audio_dsp::{
    analyze_audio_dynamics_naive, analyze_audio_dynamics_soa, is_host_speaking,
    AudioAnalysisOptions, AudioTracksSoA, HypeWindow,
};
use banido_media::chapters::{
    build_chapter_entries, sanitize_filename, ChapterInput, HighlightInput,
};
use banido_media::wav_io::{read_wav_mono_f32, write_wav_f32};
use banido_ml::clustering::{agglomerative_cosine_average, l2_norm, EmbeddingMatrixSoA};
use banido_ml::hallucination::{is_whisper_hallucination, WhisperConfidence};
use banido_ml::srt::seconds_to_srt_time;
use banido_ml::tracks::{merge_track_segments, TranscriptSegment};
use banido_ml::voice_bank::resolve_canonical_friend_name;
use serde::Deserialize;
use serde_json::json;
use std::collections::HashSet;
use std::f64::consts::PI;
use std::path::Path;
use std::time::Instant;

/// Generates deterministic synthetic 16 kHz Mic, Discord, and Soundpad buffers
/// identical to `legacy_bench.py::generate_audio_buffers`.
pub fn generate_audio_buffers(seed: u64, duration_sec: usize) -> (Vec<f32>, Vec<f32>, Vec<f32>) {
    let sr = 16_000usize;
    let n_samples = duration_sec * sr;
    let block_len = sr / 4; // 4000 samples (250ms)
    let n_blocks = n_samples / block_len;
    let mut rng = SplitMix64::new(seed);

    let mut mic = vec![0.0_f32; n_samples];
    let mut disc = vec![0.0_f32; n_samples];
    let mut sp = vec![0.0_f32; n_samples];
    let two_pi = 2.0 * PI;
    let inv_sr = 1.0 / (sr as f64);

    for b in 0..n_blocks {
        let m_act = rng.next_f64();
        let d_act = rng.next_f64();
        let s_act = rng.next_f64();
        let m_amp = if m_act < 0.45 {
            0.0002
        } else {
            0.04 + 0.30 * rng.next_f64()
        };
        let d_amp = if d_act < 0.35 {
            0.0002
        } else {
            0.05 + 0.35 * rng.next_f64()
        };
        let s_amp = if s_act < 0.88 {
            0.0
        } else {
            0.04 + 0.15 * rng.next_f64()
        };

        let m_freq = 120.0 + 180.0 * rng.next_f64();
        let d_freq = 140.0 + 220.0 * rng.next_f64();
        let s_freq = 320.0 + 400.0 * rng.next_f64();

        let base = b * block_len;
        let m_w = two_pi * m_freq * inv_sr;
        let d_w = two_pi * d_freq * inv_sr;
        let s_w = two_pi * s_freq * inv_sr;

        for k in 0..block_len {
            let kf = k as f64;
            mic[base + k] = (m_amp * (m_w * kf).sin()) as f32;
            disc[base + k] = (d_amp * (d_w * kf).sin()) as f32;
            if s_amp > 0.0 {
                sp[base + k] = (s_amp * (s_w * kf).sin()) as f32;
            }
        }
    }
    (mic, disc, sp)
}

/// Generates deterministic 192-D speaker embeddings identical to `legacy_bench.py`.
pub fn generate_embeddings(seed: u64, n_items: usize, dim: usize, n_speakers: usize) -> Vec<Vec<f32>> {
    let mut rng = SplitMix64::new(seed ^ 0xA5A5_A5A5_A5A5_A5A5);
    let mut centers: Vec<Vec<f64>> = Vec::with_capacity(n_speakers);
    for _ in 0..n_speakers {
        let mut v: Vec<f64> = (0..dim).map(|_| rng.next_signed_f32() as f64).collect();
        let norm: f64 = v.iter().map(|x| x * x).sum::<f64>().sqrt();
        for x in &mut v {
            *x /= norm;
        }
        centers.push(v);
    }
    let mut vecs = Vec::with_capacity(n_items);
    for i in 0..n_items {
        let c = &centers[i % n_speakers];
        let mut v: Vec<f64> = (0..dim)
            .map(|j| c[j] + (rng.next_signed_f32() as f64) * 0.18)
            .collect();
        let norm: f64 = v.iter().map(|x| x * x).sum::<f64>().sqrt();
        let out: Vec<f32> = v.iter_mut().map(|x| (*x / norm) as f32).collect();
        let _ = l2_norm(&out);
        vecs.push(out);
    }
    vecs
}

fn read_peak_rss_mb() -> f64 {
    if let Ok(status) = std::fs::read_to_string("/proc/self/status") {
        for line in status.lines() {
            if let Some(rest) = line.strip_prefix("VmHWM:") {
                if let Some(kb) = rest.split_whitespace().next().and_then(|s| s.parse::<f64>().ok()) {
                    return (kb / 1024.0 * 100.0).round() / 100.0;
                }
            }
        }
    }
    0.0
}

/// Runs the exact 300s workload matching `legacy_bench.py --mode bench` and prints JSON.
pub fn run_bench(naive_mode: bool, duration_sec: usize, n_embeddings: usize) {
    let tmp = tempfile::tempdir().expect("tempdir");
    let (mic, disc, sp) = generate_audio_buffers(42, duration_sec);
    let mic_p = tmp.path().join("mic_42.wav");
    let disc_p = tmp.path().join("disc_42.wav");
    let sp_p = tmp.path().join("sp_42.wav");
    write_wav_f32(&mic_p, &mic, 16_000).unwrap();
    write_wav_f32(&disc_p, &disc, 16_000).unwrap();
    write_wav_f32(&sp_p, &sp, 16_000).unwrap();
    let vecs = generate_embeddings(42, n_embeddings, 192, 5);

    let t0 = Instant::now();
    let (mic_r, sr) = read_wav_mono_f32(&mic_p).unwrap();
    let (disc_r, _) = read_wav_mono_f32(&disc_p).unwrap();
    let (sp_r, _) = read_wav_mono_f32(&sp_p).unwrap();
    let tracks = AudioTracksSoA {
        mic: &mic_r,
        discord: &disc_r,
        soundpad: Some(&sp_r),
        sample_rate: sr as usize,
    };
    let opts = AudioAnalysisOptions {
        has_isolated_mic: true,
        stream_count: 6,
    };
    let dyn_res = if naive_mode {
        analyze_audio_dynamics_naive(&tracks, opts)
    } else {
        analyze_audio_dynamics_soa(&tracks, opts)
    };

    let host_true = (0..duration_sec.saturating_sub(2))
        .step_by(2)
        .filter(|&t| is_host_speaking(t as f64, (t as f64) + 2.5, &dyn_res))
        .count();

    let mat = EmbeddingMatrixSoA::from_vecs(&vecs);
    let labels = agglomerative_cosine_average(&mat, 0.68);
    let n_clusters = labels.into_iter().collect::<HashSet<_>>().len();

    let host_segs = sample_host_segs();
    let disc_segs = sample_disc_segs();
    let mut h_cnt = 0usize;
    let repeat_phrase = "e aí ".repeat(10);
    for _ in 0..200 {
        for txt in [
            "Inscreva-se no canal, ative o sininho...",
            "Sim, bora jogar!",
            repeat_phrase.as_str(),
        ] {
            if is_whisper_hallucination(txt, 2.0, WhisperConfidence::default()) {
                h_cnt += 1;
            }
        }
        let merged = merge_track_segments(&host_segs, &disc_segs);
        let _srts: Vec<String> = merged.iter().map(|s| seconds_to_srt_time(s.start)).collect();
        let _fn = sanitize_filename("[GTA V] O Golpe: Parte 1?", 120);
        let _log = sanitize_log_text(
            "GOOGLE_API_KEY=AIzaSyDummyKey1234567890abcdefghijk\n[FALA #0001] [00:00:05 -> 00:00:08] [Host]: segredo",
            true,
            true,
        );
    }

    let elapsed_ms = (t0.elapsed().as_secs_f64() * 1000.0 * 100.0).round() / 100.0;
    let peak_rss_mb = read_peak_rss_mb();
    println!(
        "{}",
        json!({
            "mode": if naive_mode { "safe_rust" } else { "soa_simd" },
            "wall_time_ms": elapsed_ms,
            "peak_rss_mb": peak_rss_mb,
            "hype_windows": dyn_res.hype_scores.len(),
            "host_true": host_true,
            "n_clusters": n_clusters,
            "h_cnt": h_cnt,
        })
    );
}

#[derive(Deserialize)]
struct GoldenSeed {
    seed: u64,
    duration: f64,
    mic_rms_mean: f64,
    discord_rms_mean: f64,
    hype_scores: Vec<HypeWindow>,
    host_checks: Vec<bool>,
    n_clusters: usize,
    halluc_flags: Vec<bool>,
    merged_speakers: Vec<String>,
    merged_overlaps: Vec<bool>,
    srt_times: Vec<String>,
    ai_title: String,
    chapter_starts_ms: Vec<u64>,
    sanitized_fn: String,
    sanitized_log_has_secret: bool,
    canonical_friend: String,
}

/// Verifies A/B differential parity across all golden seeds generated by Python.
///
/// Returns `true` if all seeds pass within `epsilon` tolerance.
pub fn run_parity(golden_path: &Path, epsilon: f64) -> bool {
    let raw = std::fs::read_to_string(golden_path).expect("read golden json");
    let seeds: Vec<GoldenSeed> = serde_json::from_str(&raw).expect("parse golden json");
    let mut passed = 0usize;
    let mut max_abs_err = 0.0_f64;
    let mut mismatches: Vec<String> = Vec::new();

    for g in &seeds {
        let dur_sec = g.duration.round() as usize;
        let (mic, disc, sp) = generate_audio_buffers(g.seed, dur_sec);
        let dyn_res = analyze_audio_dynamics_soa(
            &AudioTracksSoA {
                mic: &mic,
                discord: &disc,
                soundpad: Some(&sp),
                sample_rate: 16_000,
            },
            AudioAnalysisOptions {
                has_isolated_mic: true,
                stream_count: 6,
            },
        );

        let m_mean = dyn_res.mic_rms.iter().map(|&v| v as f64).sum::<f64>()
            / (dyn_res.mic_rms.len() as f64);
        let d_mean = dyn_res.discord_rms.iter().map(|&v| v as f64).sum::<f64>()
            / (dyn_res.discord_rms.len() as f64);
        let err = (m_mean - g.mic_rms_mean)
            .abs()
            .max((d_mean - g.discord_rms_mean).abs());
        max_abs_err = max_abs_err.max(err);

        let host_checks: Vec<bool> = (0..dur_sec.saturating_sub(2))
            .step_by(3)
            .map(|t| is_host_speaking(t as f64, (t as f64) + 2.5, &dyn_res))
            .collect();

        let vecs = generate_embeddings(g.seed, 60, 192, 5);
        let mat = EmbeddingMatrixSoA::from_vecs(&vecs);
        let n_clusters = agglomerative_cosine_average(&mat, 0.68)
            .into_iter()
            .collect::<HashSet<_>>()
            .len();

        let rep = "e aí ".repeat(10);
        let halluc_flags = vec![
            is_whisper_hallucination("Inscreva-se no canal, ative o sininho...", 2.0, WhisperConfidence { no_speech_prob: Some(0.2), avg_logprob: Some(-0.3), compression_ratio: Some(1.2) }),
            is_whisper_hallucination("Sim, bora jogar mais uma partida!", 2.2, WhisperConfidence { no_speech_prob: Some(0.05), avg_logprob: Some(-0.2), compression_ratio: Some(1.1) }),
            is_whisper_hallucination("hmm tá bom então", 1.5, WhisperConfidence { no_speech_prob: Some(0.9), avg_logprob: Some(-1.4), compression_ratio: Some(1.2) }),
            is_whisper_hallucination("kkkkk corre corre", 1.8, WhisperConfidence { no_speech_prob: Some(0.05), avg_logprob: Some(-0.2), compression_ratio: Some(2.6) }),
            is_whisper_hallucination(&rep, 5.0, WhisperConfidence { no_speech_prob: Some(0.1), avg_logprob: Some(-0.2), compression_ratio: Some(1.5) }),
        ];

        let merged = merge_track_segments(&sample_host_segs(), &sample_disc_segs());
        let merged_speakers: Vec<String> = merged.iter().map(|s| s.speaker.clone()).collect();
        let merged_overlaps: Vec<bool> = merged.iter().map(|s| s.overlap).collect();
        let srt_times: Vec<String> = merged.iter().map(|s| seconds_to_srt_time(s.start)).collect();

        let ai_raw = "<think>planejando</think>```json\n{\"main_game\": \"CS2\", \"title_options\": [\"[CS2] Clutch Épico\"], \"chapters\": [{\"timestamp\": \"00:02:00\", \"title\": \"Ação\"}], \"highlights\": [{\"start\": 10, \"end\": 40, \"score\": 90, \"title\": \"Clutch\"}]}\n```";
        let ai_parsed = parse_ai_json_response(ai_raw, Some(300.0), "Banido Studio").unwrap();
        let ch_in: Vec<ChapterInput> = ai_parsed
            .chapters
            .iter()
            .map(|c| ChapterInput { timestamp: c.timestamp.clone(), title: c.title.clone() })
            .collect();
        let hl_in: Vec<HighlightInput> = ai_parsed
            .highlights
            .iter()
            .map(|h| HighlightInput { start: h.start, end: h.end, score: h.score, title: h.title.clone() })
            .collect();
        let ch_starts: Vec<u64> = build_chapter_entries(&ch_in, &hl_in, 300.0, "both")
            .into_iter()
            .map(|c| c.start_ms)
            .collect();

        let san_fn = sanitize_filename("[GTA V] O Golpe: Parte 1?", 120);
        let san_log = sanitize_log_text(
            "GOOGLE_API_KEY=AIzaSyDummyKey1234567890abcdefghijk\n[FALA #0001] [00:00:05 -> 00:00:08] [Host]: fala privada\nIP 192.168.1.42 (local 127.0.0.1)",
            true,
            true,
        );
        let friend = resolve_canonical_friend_name("joao pedro", &[]);

        let ok = err <= epsilon
            && dyn_res.hype_scores == g.hype_scores
            && host_checks == g.host_checks
            && n_clusters == g.n_clusters
            && halluc_flags == g.halluc_flags
            && merged_speakers == g.merged_speakers
            && merged_overlaps == g.merged_overlaps
            && srt_times == g.srt_times
            && ai_parsed.title_options[0] == g.ai_title
            && ch_starts == g.chapter_starts_ms
            && san_fn == g.sanitized_fn
            && san_log.contains("AIzaSy") == g.sanitized_log_has_secret
            && friend == g.canonical_friend;

        if ok {
            passed += 1;
        } else {
            mismatches.push(format!(
                "seed={} err={:.3e} hype_eq={} host_eq={} clusters={}/{}",
                g.seed,
                err,
                dyn_res.hype_scores == g.hype_scores,
                host_checks == g.host_checks,
                n_clusters,
                g.n_clusters
            ));
        }
    }

    println!(
        "{}",
        json!({
            "seeds_tested": seeds.len(),
            "seeds_passed": passed,
            "max_abs_error": format!("{max_abs_err:.3e}"),
            "epsilon": epsilon,
            "mismatches": mismatches,
        })
    );
    passed == seeds.len()
}

fn sample_host_segs() -> Vec<TranscriptSegment> {
    vec![
        TranscriptSegment { start: 1.0, end: 4.0, text: "Vamos pelo outro lado, pessoal!".into(), speaker: "Host".into(), overlap: false, speaker_confidence: None, cluster_tag: None },
        TranscriptSegment { start: 10.0, end: 11.2, text: "Boa!".into(), speaker: "Host".into(), overlap: false, speaker_confidence: None, cluster_tag: None },
        TranscriptSegment { start: 20.0, end: 23.0, text: "eu acho que vai dar certo".into(), speaker: "Host".into(), overlap: false, speaker_confidence: None, cluster_tag: None },
    ]
}

fn sample_disc_segs() -> Vec<TranscriptSegment> {
    vec![
        TranscriptSegment { start: 1.1, end: 4.0, text: "vamos pelo outro lado pessoal".into(), speaker: "Discord".into(), overlap: false, speaker_confidence: None, cluster_tag: None },
        TranscriptSegment { start: 10.1, end: 11.3, text: "Boa!".into(), speaker: "Discord".into(), overlap: false, speaker_confidence: None, cluster_tag: None },
        TranscriptSegment { start: 22.0, end: 25.0, text: "não cara, vamos pelo outro lado".into(), speaker: "Discord".into(), overlap: false, speaker_confidence: None, cluster_tag: None },
    ]
}
