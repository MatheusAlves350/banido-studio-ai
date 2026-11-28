//! Audio DSP (SoA/SIMD), WAV I/O, FFmpeg/mkvpropedit command builders,
//! chapter encoding, and 4x4 video mosaic generation for **Banido Studio AI**.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod audio_dsp;
pub mod audio_dsp_naive;
pub mod chapters;
pub mod ffmpeg_cmd;
pub mod mosaic;
pub mod wav_io;

/// Unified error type for `banido-media`.
#[derive(Debug, thiserror::Error)]
pub enum MediaError {
    /// Filesystem or subprocess I/O error.
    #[error("Media I/O error: {0}")]
    Io(#[from] std::io::Error),
    /// WAV decoding/encoding error.
    #[error("WAV error: {0}")]
    Wav(#[from] hound::Error),
    /// Image decoding/encoding error.
    #[error("Image error: {0}")]
    Image(#[from] image::ImageError),
    /// JSON parsing error (e.g. `ffprobe` output).
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    /// Invalid media parameters or missing streams.
    #[error("Invalid media input: {0}")]
    InvalidInput(String),
}

/// Convenience result alias for `banido-media`.
pub type MediaResult<T> = Result<T, MediaError>;
