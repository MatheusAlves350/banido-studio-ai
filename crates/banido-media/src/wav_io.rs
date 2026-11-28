//! Mono WAV reading/writing (with automatic multi-channel downmix) and
//! silence-aligned chunk tail splitting.

use crate::MediaResult;
use hound::{SampleFormat, WavReader, WavSpec, WavWriter};
use std::path::Path;

/// Reads a WAV file into normalized `f32` mono samples (`[-1.0, 1.0]`).
///
/// Automatically downmixes multi-channel WAVs by averaging channels per frame,
/// fixing the legacy mono-only assumption in `app/audio_pipeline.py:179`.
pub fn read_wav_mono_f32(path: &Path) -> MediaResult<(Vec<f32>, u32)> {
    let mut reader = WavReader::open(path)?;
    let spec = reader.spec();
    let channels = (spec.channels as usize).max(1);
    let raw: Vec<f32> = match spec.sample_format {
        SampleFormat::Float => reader.samples::<f32>().collect::<Result<Vec<_>, _>>()?,
        SampleFormat::Int => {
            let max_val = ((1_i64 << (spec.bits_per_sample.saturating_sub(1))) as f32).max(1.0);
            reader
                .samples::<i32>()
                .map(|s| s.map(|v| (v as f32) / max_val))
                .collect::<Result<Vec<_>, _>>()?
        }
    };

    if channels == 1 {
        return Ok((raw, spec.sample_rate));
    }
    let frames = raw.len() / channels;
    let mut mono = Vec::with_capacity(frames);
    let inv = 1.0 / (channels as f32);
    for frame in raw.chunks_exact(channels) {
        let sum: f32 = frame.iter().copied().sum();
        mono.push(sum * inv);
    }
    Ok((mono, spec.sample_rate))
}

/// Writes mono `f32` samples as an IEEE float32 WAV file.
pub fn write_wav_f32(path: &Path, samples: &[f32], sample_rate: u32) -> MediaResult<()> {
    let spec = WavSpec {
        channels: 1,
        sample_rate,
        bits_per_sample: 32,
        sample_format: SampleFormat::Float,
    };
    let mut writer = WavWriter::create(path, spec)?;
    for &s in samples {
        writer.write_sample(s)?;
    }
    writer.finalize()?;
    Ok(())
}

/// Locates the quietest window midpoint inside the last `tail_window_sec`
/// seconds of an audio chunk so Whisper never cuts words in half.
///
/// Mirrors `app/transcription/chunking.py::_find_tail_silence_split`.
pub fn find_tail_silence_split(
    mix_audio: &[f32],
    sample_rate: usize,
    tail_window_sec: f64,
    step_sec: f64,
) -> usize {
    let total_len = mix_audio.len();
    let min_len = (45.0 * (sample_rate as f64)) as usize;
    if total_len <= min_len {
        return total_len;
    }
    let win_samples = (tail_window_sec * (sample_rate as f64)) as usize;
    let search_start = ((30.0 * (sample_rate as f64)) as usize)
        .max(total_len.saturating_sub(win_samples));
    let tail_slice = &mix_audio[search_start..];
    let step = ((step_sec * (sample_rate as f64)) as usize).max(1);
    let n_steps = tail_slice.len() / step;
    if n_steps == 0 {
        return total_len;
    }

    let mut best_idx = 0usize;
    let mut best_energy = f64::INFINITY;
    for (i, chunk) in tail_slice[..n_steps * step].chunks_exact(step).enumerate() {
        let energy: f64 = chunk.iter().map(|&x| (x as f64) * (x as f64)).sum();
        if energy < best_energy {
            best_energy = energy;
            best_idx = i;
        }
    }
    let split = search_start + best_idx * step + (step / 2);
    let min_carry = (0.2 * (sample_rate as f64)) as usize;
    if total_len.saturating_sub(split) < min_carry {
        total_len
    } else {
        split
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_wav_roundtrip_and_tail_split() {
        let dir = tempdir().unwrap();
        let p = dir.path().join("test.wav");
        let sr = 16_000usize;
        let mut audio = vec![0.25_f32; sr * 60];
        // Insert a 1-second silence valley at 55s..56s
        for s in &mut audio[sr * 55..sr * 56] {
            *s = 0.0;
        }
        write_wav_f32(&p, &audio, sr as u32).unwrap();
        let (loaded, loaded_sr) = read_wav_mono_f32(&p).unwrap();
        assert_eq!(loaded_sr, 16_000);
        assert_eq!(loaded.len(), audio.len());

        let split = find_tail_silence_split(&loaded, sr, 18.0, 0.25);
        assert!((sr * 55..=sr * 56).contains(&split));
    }
}
