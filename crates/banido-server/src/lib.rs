//! Axum HTTP server, security middleware stack, 49 compatible API routes,
//! thread-safe worker registry, and built-in benchmark/parity CLI for
//! **Banido Studio AI**.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod bench_runner;
pub mod middleware;
pub mod routes_admin;
pub mod routes_core;
pub mod routes_media_ai;
pub mod routes_processing;
pub mod state;

use crate::middleware::{csrf_token_guard, network_access_guard, trusted_host_guard};
use crate::state::AppState;
use axum::middleware as axum_mw;
use axum::Router;
use tower_http::services::ServeDir;

/// Builds the complete Banido Studio AI [`Router`] with all 49 API/HTML routes,
/// 4 static directory mounts (`/static`, `/mosaics`, `/vozes_amigos`, `/cortes`),
/// and properly ordered security middleware (`TrustedHost` -> `Network` -> `CSRF`).
pub fn build_router(state: AppState) -> Router {
    let static_dir = state.config.project_root.join("app").join("static");
    let mosaics_dir = state.config.database_dir.join("mosaics");
    let voices_dir = state.config.voice_bank_dir.clone();
    let clips_dir = state.config.clips_dir.clone();

    Router::new()
        .merge(routes_core::router())
        .merge(routes_processing::router())
        .merge(routes_media_ai::router())
        .merge(routes_admin::router())
        .nest_service("/static", ServeDir::new(static_dir))
        .nest_service("/mosaics", ServeDir::new(mosaics_dir))
        .nest_service("/vozes_amigos", ServeDir::new(voices_dir))
        .nest_service("/cortes", ServeDir::new(clips_dir))
        // Axum applies layers from innermost to outermost:
        // Request flows: trusted_host_guard -> network_access_guard -> csrf_token_guard -> handler.
        .layer(axum_mw::from_fn_with_state(state.clone(), csrf_token_guard))
        .layer(axum_mw::from_fn_with_state(
            state.clone(),
            network_access_guard,
        ))
        .layer(axum_mw::from_fn_with_state(
            state.clone(),
            trusted_host_guard,
        ))
        .with_state(state)
}
