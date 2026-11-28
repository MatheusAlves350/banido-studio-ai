//! Core infrastructure crate for **Banido Studio AI**.
//!
//! Provides 100% memory-safe (`#![forbid(unsafe_code)]`) configuration loading,
//! network/CSRF security primitives, SQLite WAL storage with automatic schema
//! migrations, `.env` settings validation, privacy-preserving diagnostic log
//! redaction, OBS/game auto-pause detection, model catalog caching, and SemVer
//! update verification.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod config;
pub mod database;
pub mod database_models;
pub mod diagnostics;
pub mod env_settings;
pub mod game_guard;
pub mod model_catalog;
pub mod prng;
pub mod security;
pub mod updater;

/// Unified error type returned by `banido-core` operations.
#[derive(Debug, thiserror::Error)]
pub enum CoreError {
    /// Underlying filesystem or process I/O error.
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    /// SQLite database error.
    #[error("SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    /// JSON serialization or deserialization error.
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    /// Validation error with user-facing message.
    #[error("Validation error: {0}")]
    Validation(String),
    /// Requested entity was not found.
    #[error("Not found: {0}")]
    NotFound(String),
}

/// Convenience result alias for `banido-core`.
pub type CoreResult<T> = Result<T, CoreError>;
