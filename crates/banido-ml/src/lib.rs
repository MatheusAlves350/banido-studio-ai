//! Whisper post-processing, SRT export, multi-track echo removal, native SoA
//! UPGMA speaker clustering, Voice Bank profile management, and safe ML engine
//! traits (`WhisperEngine`, `SpeakerEncoder`) for **Banido Studio AI**.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod clustering;
pub mod engine;
pub mod hallucination;
pub mod prompt;
pub mod srt;
pub mod tracks;
pub mod voice_bank;

/// Unified error type for `banido-ml`.
#[derive(Debug, thiserror::Error)]
pub enum MlError {
    /// Filesystem I/O error.
    #[error("ML I/O error: {0}")]
    Io(#[from] std::io::Error),
    /// Invalid input or model backend failure.
    #[error("ML engine error: {0}")]
    Engine(String),
}

/// Convenience result alias for `banido-ml`.
pub type MlResult<T> = Result<T, MlError>;
