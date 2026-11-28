//! Ordered Axum security middleware:
//! 1. [`trusted_host_guard`] (outermost): validates `Host` header against `allowed_hosts`.
//! 2. [`network_access_guard`]: enforces LAN password + revocable session cookie.
//! 3. [`csrf_token_guard`]: enforces `X-Banido-Token` on all mutating and sensitive GET APIs.

use crate::state::AppState;
use axum::body::Body;
use axum::extract::State;
use axum::http::{header, Method, Request, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Redirect, Response};
use axum::Json;
use banido_core::security::{is_network_mode, NO_PASSWORD_MESSAGE, SESSION_COOKIE};
use serde_json::json;

/// Validates the HTTP `Host` header against `config.allowed_hosts`.
pub async fn trusted_host_guard(
    State(state): State<AppState>,
    req: Request<Body>,
    next: Next,
) -> Response {
    let allowed = &state.config.allowed_hosts;
    if allowed.iter().any(|h| h == "*") {
        return next.run(req).await;
    }
    let host_hdr = req
        .headers()
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("localhost");
    let host_only = strip_port(host_hdr);
    let ok = allowed.iter().any(|a| a.eq_ignore_ascii_case(host_only));
    if !ok {
        return (StatusCode::BAD_REQUEST, "Invalid host header").into_response();
    }
    next.run(req).await
}

fn strip_port(host: &str) -> &str {
    if host.starts_with('[') {
        host.find(']').map(|i| &host[..=i]).unwrap_or(host)
    } else {
        host.split(':').next().unwrap_or(host)
    }
}

/// Enforces network-mode password authentication and valid `banido_session` cookie.
pub async fn network_access_guard(
    State(state): State<AppState>,
    req: Request<Body>,
    next: Next,
) -> Response {
    if !is_network_mode(&state.config.server_host) {
        return next.run(req).await;
    }
    if state.config.access_password.is_empty() {
        return (StatusCode::FORBIDDEN, NO_PASSWORD_MESSAGE).into_response();
    }
    let path = req.uri().path();
    if path == "/login" || path == "/logout" {
        return next.run(req).await;
    }

    let cookie = extract_cookie(req.headers().get(header::COOKIE).and_then(|v| v.to_str().ok()), SESSION_COOKIE);
    if state.security.is_valid_session(cookie.as_deref()) {
        return next.run(req).await;
    }

    if path.starts_with("/api/") {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Sessão expirada ou não autenticada."})),
        )
            .into_response()
    } else {
        Redirect::to("/login").into_response()
    }
}

/// Enforces `X-Banido-Token` on all mutating `/api/*` calls and sensitive GET routes.
///
/// Closes legacy coverage gaps in `app/main.py:59-65` by also protecting
/// `/api/env-settings`, `/api/logs/*`, `/api/history`, and `/api/claude-prompt/*`.
pub async fn csrf_token_guard(
    State(state): State<AppState>,
    req: Request<Body>,
    next: Next,
) -> Response {
    let path = req.uri().path();
    let method = req.method();
    let is_mutating = matches!(
        *method,
        Method::POST | Method::PUT | Method::PATCH | Method::DELETE
    );
    let sensitive_get = *method == Method::GET
        && (path == "/api/settings"
            || path == "/api/setup/check"
            || path == "/api/env-settings"
            || path == "/api/history"
            || path.starts_with("/api/models/")
            || path.starts_with("/api/audio/preview/")
            || path.starts_with("/api/speakers/preview/")
            || path.starts_with("/api/update/")
            || path.starts_with("/api/diagnostics/")
            || path.starts_with("/api/logs/")
            || path.starts_with("/api/claude-prompt/"));

    if (path.starts_with("/api/") && is_mutating) || sensitive_get {
        let provided = req
            .headers()
            .get("x-banido-token")
            .and_then(|v| v.to_str().ok());
        if !state.security.verify_api_token(provided) {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"detail": "Token de segurança inválido ou ausente (X-Banido-Token)."})),
            )
                .into_response();
        }
    }
    next.run(req).await
}

/// Extracts a named cookie value from an HTTP `Cookie` header string.
pub fn extract_cookie(cookie_header: Option<&str>, name: &str) -> Option<String> {
    let header_str = cookie_header?;
    for part in header_str.split(';') {
        if let Some((k, v)) = part.trim().split_once('=') {
            if k.trim() == name {
                return Some(v.trim().to_string());
            }
        }
    }
    None
}
