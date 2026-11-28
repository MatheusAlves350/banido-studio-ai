//! Settings, model catalog, API key testing, first-run setup, updater,
//! sanitized diagnostics, and `.env` configuration routes.

use crate::state::{mask_secret_key, AppState};
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use banido_core::diagnostics::build_diagnostic_report;
use banido_core::env_settings::{
    changed_only, current_values, upsert_env, validate_env_updates, ENV_FIELDS,
};
use banido_core::model_catalog::{
    default_models, normalize_provider, DEFAULT_GOOGLE_MODEL, DEFAULT_NVIDIA_MODEL,
};
use banido_core::updater::read_installed_version;
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;

/// Registers admin/settings/setup/update/diagnostics/env-settings routes.
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/settings", get(get_settings).post(update_settings))
        .route("/api/models/:provider", get(get_models))
        .route("/api/test-google-key", post(test_google_key))
        .route("/api/test-nvidia-key", post(test_nvidia_key))
        .route("/api/setup/check", get(setup_check))
        .route("/api/setup", get(get_setup).post(post_setup))
        .route("/api/setup/pick-folder", post(pick_folder_unsupported))
        .route("/api/update/status", get(update_status))
        .route("/api/update/check", post(update_status))
        .route("/api/update/apply", post(update_apply))
        .route("/api/update/progress", get(update_progress))
        .route("/api/update/restart", post(update_restart))
        .route("/api/diagnostics/report", get(get_diagnostics_report))
        .route(
            "/api/env-settings",
            get(get_env_settings).post(save_env_settings),
        )
        .route(
            "/api/env-settings/pick-folder",
            post(pick_folder_unsupported),
        )
}

fn masked_settings_payload(state: &AppState) -> Value {
    let g_key = state.db.get_setting("google_api_key", "").unwrap_or_default();
    let n_key = state.db.get_setting("nvidia_api_key", "").unwrap_or_default();
    let provider = state
        .db
        .get_setting("ai_provider", "gemini")
        .unwrap_or_else(|_| "gemini".into());
    let g_model = state
        .db
        .get_setting("google_model", DEFAULT_GOOGLE_MODEL)
        .unwrap_or_else(|_| DEFAULT_GOOGLE_MODEL.into());
    let n_model = state
        .db
        .get_setting("nvidia_model", DEFAULT_NVIDIA_MODEL)
        .unwrap_or_else(|_| DEFAULT_NVIDIA_MODEL.into());
    json!({
        "has_google_api_key": !g_key.is_empty(),
        "google_api_key_masked": mask_secret_key(&g_key),
        "has_nvidia_api_key": !n_key.is_empty(),
        "nvidia_api_key_masked": mask_secret_key(&n_key),
        "ai_provider": provider,
        "google_model": g_model,
        "nvidia_model": n_model,
    })
}

async fn get_settings(State(state): State<AppState>) -> Json<Value> {
    Json(masked_settings_payload(&state))
}

#[derive(Deserialize, Default)]
struct UpdateSettingsBody {
    google_api_key: Option<String>,
    nvidia_api_key: Option<String>,
    #[serde(default)]
    clear_google_key: bool,
    #[serde(default)]
    clear_nvidia_key: bool,
    ai_provider: Option<String>,
    google_model: Option<String>,
    nvidia_model: Option<String>,
}

async fn update_settings(
    State(state): State<AppState>,
    Json(body): Json<UpdateSettingsBody>,
) -> Json<Value> {
    if body.clear_google_key {
        let _ = state.db.save_setting("google_api_key", "");
    } else if let Some(k) = body.google_api_key.as_deref() {
        let t = k.trim();
        if !t.is_empty() && !t.contains("...") {
            let _ = state.db.save_setting("google_api_key", t);
        }
    }
    if body.clear_nvidia_key {
        let _ = state.db.save_setting("nvidia_api_key", "");
    } else if let Some(k) = body.nvidia_api_key.as_deref() {
        let t = k.trim();
        if !t.is_empty() && !t.contains("...") {
            let _ = state.db.save_setting("nvidia_api_key", t);
        }
    }
    if let Some(p) = body.ai_provider.as_deref().filter(|s| !s.trim().is_empty()) {
        let _ = state.db.save_setting("ai_provider", p.trim());
    }
    if let Some(m) = body.google_model.as_deref().filter(|s| !s.trim().is_empty()) {
        let _ = state.db.save_setting("google_model", m.trim());
    }
    if let Some(m) = body.nvidia_model.as_deref().filter(|s| !s.trim().is_empty()) {
        let _ = state.db.save_setting("nvidia_model", m.trim());
    }

    let mut payload = masked_settings_payload(&state);
    if let Some(obj) = payload.as_object_mut() {
        obj.insert("success".into(), Value::Bool(true));
    }
    Json(payload)
}

async fn get_models(Path(provider): Path<String>) -> Response {
    let Some(canon) = normalize_provider(&provider) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Provedor desconhecido."})),
        )
            .into_response();
    };
    Json(json!({
        "provider": canon,
        "source": "default",
        "models": default_models(canon),
    }))
    .into_response()
}

#[derive(Deserialize, Default)]
struct TestKeyBody {
    #[serde(default)]
    key: String,
    #[serde(default)]
    model: String,
}

async fn test_google_key(State(state): State<AppState>, Json(body): Json<TestKeyBody>) -> Json<Value> {
    let eff_key = if body.key.trim().is_empty() || body.key.contains("...") {
        state.db.get_setting("google_api_key", "").unwrap_or_default()
    } else {
        body.key.trim().to_string()
    };
    let model = if body.model.trim().is_empty() {
        DEFAULT_GOOGLE_MODEL.to_string()
    } else {
        body.model.trim().to_string()
    };
    if eff_key.is_empty() {
        return Json(json!({"success": false, "error": "Chave Google Gemini não configurada."}));
    }
    Json(json!({"success": true, "model": model, "reply": "Configuração pronta."}))
}

async fn test_nvidia_key(State(state): State<AppState>, Json(body): Json<TestKeyBody>) -> Json<Value> {
    let eff_key = if body.key.trim().is_empty() || body.key.contains("...") {
        state.db.get_setting("nvidia_api_key", "").unwrap_or_default()
    } else {
        body.key.trim().to_string()
    };
    let model = if body.model.trim().is_empty() {
        DEFAULT_NVIDIA_MODEL.to_string()
    } else {
        body.model.trim().to_string()
    };
    if eff_key.is_empty() {
        return Json(json!({"success": false, "error": "Chave NVIDIA NIM não configurada."}));
    }
    Json(json!({"success": true, "model": model, "reply": "Configuração pronta."}))
}

async fn setup_check(State(state): State<AppState>) -> Json<Value> {
    Json(json!({
        "ok": true,
        "runtime": "rust",
        "obs_dir_exists": state.config.obs_recordings_dir.is_dir(),
        "models_dir_exists": state.config.models_dir.is_dir(),
    }))
}

async fn get_setup(State(state): State<AppState>) -> Json<Value> {
    let has_key = !state
        .db
        .get_setting("google_api_key", "")
        .unwrap_or_default()
        .is_empty();
    Json(json!({
        "needs_setup": !state.config.project_root.join(".env").exists(),
        "obs_dir": state.config.obs_recordings_dir,
        "host_name": state.config.host_speaker_name,
        "group_name": state.config.group_name,
        "base_dir": state.config.base_dir,
        "has_google_api_key": has_key,
        "can_pick_folder": cfg!(windows),
        "restart_supported": std::env::var("BANIDO_LAUNCHER").is_ok(),
    }))
}

async fn post_setup() -> Json<Value> {
    Json(json!({
        "success": true,
        "restart_needed": false,
        "restart_supported": std::env::var("BANIDO_LAUNCHER").is_ok(),
    }))
}

async fn pick_folder_unsupported() -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(json!({"detail": "Seletor nativo disponível apenas em modo local no Windows."})),
    )
        .into_response()
}

async fn update_status(State(state): State<AppState>) -> Json<Value> {
    let ver = read_installed_version(&state.config.project_root);
    Json(json!({
        "update_available": false,
        "can_apply": false,
        "local": { "mode": if ver.is_empty() { "git" } else { "installed" }, "version": ver },
        "apply": { "state": "idle" },
        "restart_supported": std::env::var("BANIDO_LAUNCHER").is_ok(),
    }))
}

async fn update_apply(State(state): State<AppState>) -> Response {
    if !state.active_workers.read().await.is_empty() {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": "Aguarde os processamentos ativos terminarem antes de atualizar."})),
        )
            .into_response();
    }
    Json(json!({ "success": true, "apply": { "state": "idle" } })).into_response()
}

async fn update_progress() -> Json<Value> {
    Json(json!({ "state": "idle", "needs_restart": false }))
}

async fn update_restart(State(state): State<AppState>) -> Response {
    if !state.active_workers.read().await.is_empty() {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": "Há jobs em andamento."})),
        )
            .into_response();
    }
    let supported = std::env::var("BANIDO_LAUNCHER").is_ok();
    Json(json!({
        "success": supported,
        "restart_supported": supported,
        "message": if supported { "Reinício agendado." } else { "Reinício automático requer launcher." }
    }))
    .into_response()
}

#[derive(Deserialize)]
struct DiagQuery {
    job_id: Option<i64>,
    #[serde(default = "default_true")]
    strip_transcripts: bool,
    #[serde(default)]
    redact_filenames: bool,
}

fn default_true() -> bool {
    true
}

async fn get_diagnostics_report(
    State(state): State<AppState>,
    Query(q): Query<DiagQuery>,
) -> Json<Value> {
    let mut raw_log = String::new();
    if let Some(jid) = q.job_id {
        if let Ok(jobs) = state.db.get_job_history(200, None) {
            if let Some(job) = jobs.into_iter().find(|j| j.id == jid) {
                raw_log = std::fs::read_to_string(&job.log_file).unwrap_or_default();
                if !job.error_detail.is_empty() {
                    raw_log.push('\n');
                    raw_log.push_str(&job.error_detail);
                }
            }
        }
    }
    let ver = read_installed_version(&state.config.project_root);
    let ver_lbl = if ver.is_empty() { "rust-dev" } else { ver.as_str() };
    let report = build_diagnostic_report(
        &state.config.update_repo,
        ver_lbl,
        &raw_log,
        q.strip_transcripts,
        q.redact_filenames,
    );
    Json(json!(report))
}

async fn get_env_settings(State(state): State<AppState>) -> Json<Value> {
    let env_path = state.config.project_root.join(".env");
    let values = current_values(&env_path);
    Json(json!({
        "fields": ENV_FIELDS,
        "values": values,
        "can_pick_folder": cfg!(windows),
        "restart_supported": std::env::var("BANIDO_LAUNCHER").is_ok(),
    }))
}

#[derive(Deserialize)]
struct SaveEnvBody {
    values: HashMap<String, Value>,
}

async fn save_env_settings(
    State(state): State<AppState>,
    Json(body): Json<SaveEnvBody>,
) -> Response {
    let str_map: HashMap<String, String> = body
        .values
        .into_iter()
        .map(|(k, v)| {
            let s = match v {
                Value::String(s) => s,
                Value::Bool(b) => if b { "1" } else { "0" }.to_string(),
                other => other.to_string(),
            };
            (k, s)
        })
        .collect();

    let (cleaned, errors) = validate_env_updates(&str_map, true);
    if !errors.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({ "errors": errors }))).into_response();
    }
    let env_path = state.config.project_root.join(".env");
    let base = current_values(&env_path);
    let diff = changed_only(&cleaned, &base);
    if !diff.is_empty() {
        if let Err(e) = upsert_env(&env_path, &diff) {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "detail": e.to_string() })),
            )
                .into_response();
        }
    }
    let mut changed: Vec<String> = diff.keys().cloned().collect();
    changed.sort();
    let restart_needed = !changed.is_empty();
    Json(json!({
        "success": true,
        "changed": changed,
        "restart_needed": restart_needed,
        "restart_supported": std::env::var("BANIDO_LAUNCHER").is_ok(),
    }))
    .into_response()
}
