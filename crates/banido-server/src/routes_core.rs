//! Core HTML, authentication (`/login`, `/logout`), status, video listing, and
//! game history routes.

use crate::middleware::extract_cookie;
use crate::state::AppState;
use axum::body::Bytes;
use axum::extract::{Path, Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{Html, IntoResponse, Redirect, Response};
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use banido_core::security::{
    constant_time_eq_secret, login_page_html, parse_login_body, SESSION_COOKIE, SESSION_MAX_AGE_SEC,
};
use banido_ml::voice_bank::list_known_voice_names;
use chrono::Local;
use serde::Deserialize;
use serde_json::{json, Value};

/// Registers core routes (`/`, `/login`, `/logout`, `/api/status`, `/api/videos`, `/api/games`, `/api/video/:id`).
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(get_index))
        .route("/login", get(login_page).post(login_submit))
        .route("/logout", get(logout).post(logout))
        .route("/api/status", get(server_status))
        .route("/api/videos", get(list_videos))
        .route("/api/games", get(list_games).post(add_game_history))
        .route("/api/games/*game_name", delete(remove_game_history))
        .route("/api/video/:video_id", get(get_video_details))
        .route("/api/video/:video_id/game", post(save_video_game))
}

async fn get_index(State(state): State<AppState>) -> Response {
    let index_path = state
        .config
        .project_root
        .join("app")
        .join("static")
        .join("index.html");
    let raw = std::fs::read_to_string(&index_path)
        .unwrap_or_else(|_| "<!DOCTYPE html><html><head><title>Banido Studio AI</title></head><body><h1>Banido Studio AI (Rust)</h1></body></html>".to_string());
    let meta_tag = format!(
        r#"<meta name="banido-token" content="{}">"#,
        state.security.api_auth_token()
    );
    let injected = if let Some(pos) = raw.find("<head>") {
        let insert_at = pos + "<head>".len();
        format!("{}\n  {meta_tag}{}", &raw[..insert_at], &raw[insert_at..])
    } else {
        format!("{meta_tag}\n{raw}")
    };
    let mut headers = HeaderMap::new();
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    (headers, Html(injected)).into_response()
}

async fn login_page() -> Response {
    let mut headers = HeaderMap::new();
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    (headers, Html(login_page_html(""))).into_response()
}

async fn login_submit(State(state): State<AppState>, headers: HeaderMap, body: Bytes) -> Response {
    let client_id = headers
        .get("x-forwarded-for")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("client");
    if state.security.login_blocked(client_id, None) {
        return (
            StatusCode::TOO_MANY_REQUESTS,
            Html(login_page_html("Muitas tentativas incorretas. Aguarde 1 minuto.")),
        )
            .into_response();
    }
    let password = parse_login_body(&body);
    if !constant_time_eq_secret(&password, &state.config.access_password) {
        state.security.register_login_failure(client_id, None);
        return (
            StatusCode::UNAUTHORIZED,
            Html(login_page_html("Senha incorreta.")),
        )
            .into_response();
    }
    state.security.clear_login_failures(client_id);
    let session = state.security.create_session();
    let cookie = format!(
        "{SESSION_COOKIE}={session}; Path=/; Max-Age={SESSION_MAX_AGE_SEC}; HttpOnly; SameSite=Strict"
    );
    let mut resp = Redirect::to("/").into_response();
    if let Ok(val) = HeaderValue::from_str(&cookie) {
        resp.headers_mut().insert(header::SET_COOKIE, val);
    }
    resp
}

async fn logout(State(state): State<AppState>, headers: HeaderMap) -> Response {
    let cookie = extract_cookie(
        headers.get(header::COOKIE).and_then(|v| v.to_str().ok()),
        SESSION_COOKIE,
    );
    state.security.revoke_session(cookie.as_deref());
    let mut resp = Redirect::to("/login").into_response();
    resp.headers_mut().insert(
        header::SET_COOKIE,
        HeaderValue::from_static("banido_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"),
    );
    resp
}

async fn server_status(State(state): State<AppState>) -> Json<Value> {
    let active = state.active_workers.read().await;
    let active_jobs: Vec<Value> = active.values().map(|w| json!(w)).collect();
    let batch_job = active.get(&0).cloned();
    let total_jobs = state.db.get_job_count().unwrap_or(0);
    Json(json!({
        "online": true,
        "runtime": "rust-axum",
        "server_time": Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        "active_count": active.len(),
        "active_jobs": active_jobs,
        "batch_audio_active": batch_job.is_some(),
        "batch_job": batch_job,
        "total_jobs": total_jobs,
        "config": {
            "host_name": state.config.host_speaker_name,
            "group_name": state.config.group_name,
            "base_dir": state.config.base_dir,
            "obs_dir": state.config.obs_recordings_dir,
            "clips_dir": state.config.clips_dir,
            "voice_bank_dir": state.config.voice_bank_dir,
            "temp_dir": state.config.temp_dir,
        }
    }))
}

#[derive(Deserialize)]
struct VideosQuery {
    #[serde(default)]
    sync: Option<String>,
}

fn parse_query_bool(raw: Option<&str>, default: bool) -> bool {
    match raw.map(str::trim).filter(|s| !s.is_empty()) {
        None => default,
        Some(v) => !matches!(v.to_lowercase().as_str(), "0" | "false" | "no" | "off"),
    }
}

async fn list_videos(State(state): State<AppState>, Query(q): Query<VideosQuery>) -> Json<Value> {
    if parse_query_bool(q.sync.as_deref(), true) {
        let _ = state
            .db
            .sync_obs_videos(&state.config.obs_recordings_dir, 60.0);
    }
    let videos = state.db.get_all_videos().unwrap_or_default();
    Json(json!({ "videos": videos }))
}

async fn list_games(State(state): State<AppState>) -> Json<Value> {
    let games = state.db.get_game_history().unwrap_or_default();
    Json(json!({ "games": games }))
}

#[derive(Deserialize)]
struct AddGameBody {
    name: String,
    #[serde(default)]
    default_context: String,
}

async fn add_game_history(
    State(state): State<AppState>,
    Json(body): Json<AddGameBody>,
) -> Response {
    if let Err(e) = state
        .db
        .record_game_history(&body.name, &body.default_context)
    {
        return (StatusCode::BAD_REQUEST, Json(json!({"detail": e.to_string()}))).into_response();
    }
    let games = state.db.get_game_history().unwrap_or_default();
    Json(json!({ "success": true, "games": games })).into_response()
}

async fn remove_game_history(
    State(state): State<AppState>,
    Path(game_name): Path<String>,
) -> Json<Value> {
    let _ = state.db.delete_game_from_history(&game_name);
    let games = state.db.get_game_history().unwrap_or_default();
    Json(json!({ "success": true, "games": games }))
}

#[derive(Deserialize)]
struct VideoDetailQuery {
    #[serde(default)]
    include_data: Option<String>,
}

async fn get_video_details(
    State(state): State<AppState>,
    Path(video_id): Path<i64>,
    Query(q): Query<VideoDetailQuery>,
) -> Response {
    let include_data = parse_query_bool(q.include_data.as_deref(), true);
    let Ok(Some((video, data))) = state.db.get_video_by_id(video_id) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo não encontrado."})),
        )
            .into_response();
    };
    let is_worker_alive = state.active_workers.read().await.contains_key(&video_id);
    let known_friends = if include_data {
        list_known_voice_names(
            &state.config.voice_bank_dir,
            &state.config.host_speaker_name,
            false,
        )
    } else {
        Vec::new()
    };
    Json(json!({
        "video": video,
        "data": if include_data { data } else { None },
        "is_worker_alive": is_worker_alive,
        "known_friends": known_friends,
    }))
    .into_response()
}

#[derive(Deserialize)]
struct SaveVideoGameBody {
    #[serde(default)]
    game_name: String,
    #[serde(default)]
    session_context: String,
    #[serde(default)]
    save_default_context: bool,
}

async fn save_video_game(
    State(state): State<AppState>,
    Path(video_id): Path<i64>,
    Json(body): Json<SaveVideoGameBody>,
) -> Response {
    if state.db.get_video_by_id(video_id).ok().flatten().is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo não encontrado."})),
        )
            .into_response();
    }
    if body.save_default_context && !body.game_name.trim().is_empty() {
        let _ = state
            .db
            .record_game_history(&body.game_name, &body.session_context);
    }
    let games = state.db.get_game_history().unwrap_or_default();
    Json(json!({
        "success": true,
        "game_name": body.game_name,
        "session_context": body.session_context,
        "games": games
    }))
    .into_response()
}
