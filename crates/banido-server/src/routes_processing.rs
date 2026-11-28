//! Processing, worker lifecycle, resume-override, batch audio normalization,
//! log tailing, and job history routes.

use crate::state::{ActiveWorker, AppState};
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

/// Registers processing, normalization, reset, logs, and history routes.
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/process/:video_id", post(process_video))
        .route(
            "/api/process/:video_id/resume-override",
            post(resume_override),
        )
        .route("/api/reanalyze-ai/:video_id", post(reanalyze_ai))
        .route("/api/normalize-audio/:video_id", post(normalize_audio))
        .route(
            "/api/audio/remove-backup/:video_id",
            post(remove_audio_backup),
        )
        .route("/api/normalize-audio-batch", post(normalize_batch))
        .route(
            "/api/normalize-audio-batch/cancel",
            post(cancel_normalize_batch),
        )
        .route("/api/reset/:video_id", post(reset_video))
        .route("/api/logs/video/:video_id", get(get_video_logs))
        .route("/api/logs/job/:job_id", get(get_job_logs))
        .route("/api/history", get(list_job_history))
}

#[derive(Deserialize, Default)]
struct ProcessBody {
    #[serde(default)]
    engine: String,
    #[serde(default)]
    custom_model: String,
}

async fn spawn_job(
    state: &AppState,
    video_id: i64,
    mode: &str,
    engine: &str,
    model: &str,
) -> Response {
    let mut active = state.active_workers.write().await;
    if let Some(existing) = active.get(&video_id) {
        return Json(json!({
            "already_running": true,
            "job_id": existing.job_id,
            "pid": existing.pid,
            "message": "Processamento já está em andamento."
        }))
        .into_response();
    }
    let filename = if video_id == 0 {
        "Lote de Áudio".to_string()
    } else {
        match state.db.get_video_by_id(video_id) {
            Ok(Some((v, _))) => v.filename,
            _ => {
                return (
                    StatusCode::NOT_FOUND,
                    Json(json!({"detail": "Vídeo não encontrado."})),
                )
                    .into_response();
            }
        }
    };

    let Ok(job) = state.db.create_job_record(video_id, &filename, engine, model) else {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": "Erro ao criar registro de job."})),
        )
            .into_response();
    };
    active.insert(
        video_id,
        ActiveWorker {
            video_id,
            job_id: job.id,
            mode: mode.to_string(),
            pid: std::process::id(),
        },
    );
    Json(json!({
        "success": true,
        "already_running": false,
        "job_id": job.id,
        "pid": std::process::id(),
        "message": format!("Job #{id} iniciado em modo '{mode}'.", id = job.id)
    }))
    .into_response()
}

async fn process_video(
    State(state): State<AppState>,
    Path(video_id): Path<i64>,
    body: Option<Json<ProcessBody>>,
) -> Response {
    let req = body.map(|b| b.0).unwrap_or_default();
    let engine = if req.engine.is_empty() {
        "gemini"
    } else {
        req.engine.as_str()
    };
    spawn_job(&state, video_id, "full", engine, &req.custom_model).await
}

async fn resume_override(State(state): State<AppState>, Path(video_id): Path<i64>) -> Response {
    let work_dir = state
        .config
        .temp_dir
        .join(format!("video_{video_id}_work"));
    let _ = std::fs::create_dir_all(&work_dir);
    let flag = work_dir.join("force_resume.flag");
    if let Err(e) = std::fs::write(&flag, "1") {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": format!("Erro ao gravar flag: {e}")})),
        )
            .into_response();
    }
    Json(json!({
        "success": true,
        "message": "Sinal de retomada enviado ao worker."
    }))
    .into_response()
}

async fn reanalyze_ai(
    State(state): State<AppState>,
    Path(video_id): Path<i64>,
    body: Option<Json<ProcessBody>>,
) -> Response {
    let req = body.map(|b| b.0).unwrap_or_default();
    let engine = if req.engine.is_empty() {
        "gemini"
    } else {
        req.engine.as_str()
    };
    spawn_job(&state, video_id, "ai_only", engine, &req.custom_model).await
}

async fn normalize_audio(State(state): State<AppState>, Path(video_id): Path<i64>) -> Response {
    spawn_job(&state, video_id, "normalize_audio", "ffmpeg", "").await
}

async fn remove_audio_backup(State(state): State<AppState>, Path(video_id): Path<i64>) -> Response {
    if state.db.get_video_by_id(video_id).ok().flatten().is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo não encontrado."})),
        )
            .into_response();
    }
    Json(json!({"success": true, "skipped": true, "message": "Verificação de backup concluída."}))
        .into_response()
}

async fn normalize_batch(State(state): State<AppState>) -> Response {
    spawn_job(&state, 0, "normalize_batch", "ffmpeg", "").await
}

async fn cancel_normalize_batch(State(state): State<AppState>) -> Json<Value> {
    let removed = state.active_workers.write().await.remove(&0);
    Json(json!({
        "success": true,
        "cancelled": removed.is_some(),
        "job_id": removed.map(|w| w.job_id),
        "message": "Cancelamento processado."
    }))
}

async fn reset_video(State(state): State<AppState>, Path(video_id): Path<i64>) -> Json<Value> {
    let removed = state.active_workers.write().await.remove(&video_id);
    Json(json!({
        "success": true,
        "cancelled_job_id": removed.map(|w| w.job_id),
        "message": "Status do vídeo reiniciado."
    }))
}

#[derive(Deserialize)]
struct TailQuery {
    #[serde(default = "default_tail")]
    tail: usize,
}

fn default_tail() -> usize {
    250
}

fn read_tail_lines(path: &str, max_lines: usize) -> String {
    let Ok(text) = std::fs::read_to_string(path) else {
        return String::new();
    };
    let lines: Vec<&str> = text.lines().collect();
    let start = lines.len().saturating_sub(max_lines);
    lines[start..].join("\n")
}

async fn get_video_logs(
    State(state): State<AppState>,
    Path(video_id): Path<i64>,
    Query(q): Query<TailQuery>,
) -> Json<Value> {
    let jobs = state
        .db
        .get_job_history(1, Some(video_id))
        .unwrap_or_default();
    if let Some(job) = jobs.into_iter().next() {
        let log_text = read_tail_lines(&job.log_file, q.tail);
        let is_running = state.active_workers.read().await.contains_key(&video_id);
        Json(json!({
            "has_job": true,
            "job": job,
            "is_running": is_running,
            "log_text": log_text
        }))
    } else {
        Json(json!({ "has_job": false, "job": null, "log_text": "" }))
    }
}

async fn get_job_logs(
    State(state): State<AppState>,
    Path(job_id): Path<i64>,
    Query(q): Query<TailQuery>,
) -> Response {
    let jobs = state.db.get_job_history(200, None).unwrap_or_default();
    if let Some(job) = jobs.into_iter().find(|j| j.id == job_id) {
        let log_text = read_tail_lines(&job.log_file, q.tail);
        Json(json!({ "job": job, "log_text": log_text })).into_response()
    } else {
        (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Job não encontrado."})),
        )
            .into_response()
    }
}

#[derive(Deserialize)]
struct HistoryQuery {
    #[serde(default = "default_limit")]
    limit: i64,
    video_id: Option<i64>,
}

fn default_limit() -> i64 {
    50
}

async fn list_job_history(
    State(state): State<AppState>,
    Query(q): Query<HistoryQuery>,
) -> Json<Value> {
    let jobs = state
        .db
        .get_job_history(q.limit.clamp(1, 500), q.video_id)
        .unwrap_or_default();
    Json(json!({ "jobs": jobs }))
}
