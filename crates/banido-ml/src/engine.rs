//! Safe trait abstractions for local ML inference backends (`WhisperEngine` and
//! `SpeakerEncoder`).
//!
//! Designed so production builds can plug in native Rust backends:
//! - `whisper-rs` (`whisper.cpp` with CUDA/Vulkan/CPU) implementing [`WhisperEngine`]
//! - `ort` (ONNX Runtime running `spkrec-ecapa-voxceleb.onnx`) implementing [`SpeakerEncoder`]
//!
//! All FFI boundaries remain encapsulated inside external crates without ever
//! exposing `unsafe` code inside the application workspace.

use crate::hallucination::WhisperConfidence;
use crate::MlResult;

/// Raw decoded speech segment returned by a [`WhisperEngine`] backend.
#[derive(Debug, Clone)]
pub struct RawWhisperSegment {
    /// Start offset in seconds relative to the audio slice.
    pub start: f64,
    /// End offset in seconds relative to the audio slice.
    pub end: f64,
    /// Decoded text.
    pub text: String,
    /// Model confidence metadata used by the hallucination filter.
    pub confidence: WhisperConfidence,
}

/// Transcription request parameters forwarded to a [`WhisperEngine`].
#[derive(Debug, Clone)]
pub struct TranscribeParams {
    /// Optional forced language code (`Some("pt")` or `None` for auto).
    pub language: Option<String>,
    /// Dynamic initial prompt with speaker names and game context.
    pub initial_prompt: String,
    /// Beam search width (default `5`).
    pub beam_size: u32,
}

/// Safe abstraction over speech-to-text backends (`whisper-rs` / ONNX / hybrid).
pub trait WhisperEngine: Send + Sync {
    /// Transcribes a mono 16 kHz `f32` audio slice into timestamped segments.
    fn transcribe(
        &self,
        audio_16k: &[f32],
        params: &TranscribeParams,
    ) -> MlResult<Vec<RawWhisperSegment>>;

    /// Human-readable backend description (e.g. `"whisper.cpp (CUDA)"`).
    fn backend_name(&self) -> &'static str;
}

/// Safe abstraction over speaker embedding extractors (ECAPA-TDNN ONNX via `ort`).
pub trait SpeakerEncoder: Send + Sync {
    /// Extracts an L2-normalized 192-D speaker embedding from 16 kHz mono audio.
    fn encode_embedding(&self, audio_16k: &[f32]) -> MlResult<Vec<f32>>;

    /// Embedding output dimensionality (192 for `spkrec-ecapa-voxceleb`).
    fn embedding_dim(&self) -> usize {
        192
    }
}
