//! AI Creative Director crate for **Banido Studio AI**.
//!
//! Implements resilient LLM JSON response parsing, YouTube chapter/title/tags
//! normalization, Claude Web prompt generation, and async Google Gemini /
//! NVIDIA NIM HTTP clients with explicit timeouts and precise error triage.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod parsing;
pub mod prompt;
pub mod providers;
pub mod youtube;

/// Unified error type for `banido-ai`.
#[derive(Debug, thiserror::Error)]
pub enum AiError {
    /// HTTP transport or timeout failure.
    #[error("AI HTTP error: {0}")]
    Http(#[from] reqwest::Error),
    /// Malformed or non-object JSON returned by the model.
    #[error("AI JSON parse error: {0}")]
    Parse(String),
    /// Provider rejected request (auth, quota, or model error).
    #[error("AI provider error: {0}")]
    Provider(String),
}

/// Convenience result alias for `banido-ai`.
pub type AiResult<T> = Result<T, AiError>;
