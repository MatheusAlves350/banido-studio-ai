//! Media (rename, preview, cut, chapters), voice confirmation, and Claude Web
//! prompt/import routes.

use crate::state::AppState;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use banido_ai::parsing::parse_ai_json_response;
use banido_ai::prompt::{
    format_transcript_for_ai, generate_claude_web_prompt, PromptHype, PromptSegment,
};
use banido_media::chapters::{build_chapter_entries, sanitize_filename, ChapterInput, HighlightInput};
use banido_media::ffmpeg_cmd::build_highlight_cut_args;
use banido_ml::voice_bank::{list_known_voice_names, resolve_canonical_friend_name};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::PathBuf;

/// Registers media, speaker confirmation, and Claude Web routes.
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/rename/:video_id", post(rename_video))
        .route("/api/audio/preview/:video_id", get(preview_audio))
        .route("/api/cut", post(cut_highlight))
        .route("/api/embed-chapters/:video_id", post(embed_chapters))
        .route(
            "/api/speakers/preview/:video_id/*speaker_tag",
            get(preview_speaker),
        )
        .route("/api/speakers/confirm", post(confirm_speaker))
        .route(
            "/api/speakers/harvest-host/:video_id",
            post(harvest_host_samples),
        )
        .route("/api/claude-prompt/:video_id", get(get_claude_prompt))
        .route("/api/claude-import/:video_id", post(import_claude_response))
}

#[derive(Deserialize)]
struct RenameBody {
    title: String,
    #[serde(default)]
    game_name: String,
}

async fn rename_video(
    State(state): State<AppState>,
    Path(video_id): Path<i64>,
    Json(body): Json<RenameBody>,
) -> Response {
    let Ok(Some((video, _))) = state.db.get_video_by_id(video_id) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo não encontrado."})),
        )
            .into_response();
    };
    let clean = sanitize_filename(&body.title, 120);
    let clean_game = sanitize_filename(&body.game_name, 60);
    let new_name = if !clean_game.is_empty() && !clean.to_lowercase().contains(&clean_game.to_lowercase()) {
        format!("{clean_game} - {clean}.mkv")
    } else {
        format!("{clean}.mkv")
    };
    Json(json!({
        "success": true,
        "old_name": video.filename,
        "new_name": new_name
    }))
    .into_response()
}

#[derive(Deserialize)]
struct PreviewQuery {
    #[serde(default)]
    start: f64,
    #[serde(default = "default_preview_end")]
    end: f64,
}

fn default_preview_end() -> f64 {
    15.0
}

async fn preview_audio(
    State(state): State<AppState>,
    Path(video_id): Path<i64>,
    Query(q): Query<PreviewQuery>,
) -> Response {
    if !q.start.is_finite() || !q.end.is_finite() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Timestamps inválidos (NaN/Inf)."})),
        )
            .into_response();
    }
    if state.db.get_video_by_id(video_id).ok().flatten().is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo não encontrado."})),
        )
            .into_response();
    }
    (
        StatusCode::NOT_FOUND,
        Json(json!({"detail": "Prévia WAV ainda não gerada para este vídeo."})),
    )
        .into_response()
}

#[derive(Deserialize)]
struct CutBody {
    video_id: i64,
    start_sec: f64,
    end_sec: f64,
    title: String,
    #[serde(default)]
    keep_all_tracks: bool,
    #[serde(default)]
    exact_cut: bool,
}

async fn cut_highlight(State(state): State<AppState>, Json(body): Json<CutBody>) -> Response {
    let Ok(Some((video, _))) = state.db.get_video_by_id(body.video_id) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo não encontrado."})),
        )
            .into_response();
    };
    let safe_title = sanitize_filename(&body.title, 100);
    let out_path = state.config.clips_dir.join(format!("{safe_title}.mp4"));
    let cmd = build_highlight_cut_args(
        &PathBuf::from(&video.file_path),
        &out_path,
        body.start_sec,
        body.end_sec,
        body.exact_cut,
        body.keep_all_tracks,
    );
    Json(json!({
        "success": true,
        "exact_cut": body.exact_cut,
        "output_path": out_path,
        "command": cmd
    }))
    .into_response()
}

#[derive(Deserialize)]
struct EmbedChaptersBody {
    #[serde(default = "default_mode")]
    mode: String,
}

fn default_mode() -> String {
    "both".to_string()
}

async fn embed_chapters(
    State(state): State<AppState>,
    Path(video_id): Path<i64>,
    body: Option<Json<EmbedChaptersBody>>,
) -> Response {
    let Ok(Some((video, data_opt))) = state.db.get_video_by_id(video_id) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo não encontrado."})),
        )
            .into_response();
    };
    let Some(data) = data_opt else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Vídeo ainda não possui análise salva."})),
        )
            .into_response();
    };
    let mode = body.map(|b| b.0.mode).unwrap_or_else(default_mode);
    let chapters: Vec<ChapterInput> = data
        .get("chapters")
        .and_then(|v| serde_json::from_value(v.clone()).ok())
        .unwrap_or_default();
    let highlights: Vec<HighlightInput> = data
        .get("highlights")
        .and_then(|v| serde_json::from_value(v.clone()).ok())
        .unwrap_or_default();
    let entries = build_chapter_entries(&chapters, &highlights, video.duration_sec.max(60.0), &mode);
    Json(json!({
        "success": true,
        "chapter_count": entries.len(),
        "entries": entries
    }))
    .into_response()
}

async fn preview_speaker(
    State(state): State<AppState>,
    Path((video_id, _speaker_tag)): Path<(i64, String)>,
) -> Response {
    if state.db.get_video_by_id(video_id).ok().flatten().is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo não encontrado."})),
        )
            .into_response();
    }
    (
        StatusCode::NOT_FOUND,
        Json(json!({"detail": "Amostra de voz não encontrada."})),
    )
        .into_response()
}

#[derive(Deserialize)]
struct ConfirmSpeakerBody {
    video_id: i64,
    speaker_tag: String,
    friend_name: String,
}

async fn confirm_speaker(
    State(state): State<AppState>,
    Json(body): Json<ConfirmSpeakerBody>,
) -> Response {
    let existing = list_known_voice_names(
        &state.config.voice_bank_dir,
        &state.config.host_speaker_name,
        false,
    );
    let canonical = resolve_canonical_friend_name(&body.friend_name, &existing);
    if canonical.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Nome de amigo inválido."})),
        )
            .into_response();
    }
    let Ok(Some((_, Some(_data)))) = state.db.get_video_by_id(body.video_id) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo sem dados de transcrição."})),
        )
            .into_response();
    };
    Json(json!({
        "success": true,
        "speaker_tag": body.speaker_tag,
        "friend_name": canonical,
        "updated_segments": 0
    }))
    .into_response()
}

async fn harvest_host_samples(
    State(state): State<AppState>,
    Path(video_id): Path<i64>,
) -> Response {
    if state.db.get_video_by_id(video_id).ok().flatten().is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo não encontrado."})),
        )
            .into_response();
    }
    Json(json!({"success": true, "harvest": {"saved": 0}})).into_response()
}

async fn get_claude_prompt(State(state): State<AppState>, Path(video_id): Path<i64>) -> Response {
    let Ok(Some((video, Some(data)))) = state.db.get_video_by_id(video_id) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo sem transcrição disponível."})),
        )
            .into_response();
    };
    let segs: Vec<PromptSegment> = data
        .get("segments")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|s| {
                    Some(PromptSegment {
                        start: s.get("start")?.as_f64()?,
                        speaker: s.get("speaker")?.as_str()?.to_string(),
                        text: s.get("text")?.as_str()?.to_string(),
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    let hypes: Vec<PromptHype> = data
        .get("hype_scores")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|h| {
                    Some(PromptHype {
                        start: h.get("start")?.as_f64()?,
                        score: h.get("score")?.as_i64()? as i32,
                        soundpad: h.get("soundpad").and_then(Value::as_bool).unwrap_or(false),
                        is_chaos: h.get("is_chaos").and_then(Value::as_bool).unwrap_or(false),
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    let known = list_known_voice_names(
        &state.config.voice_bank_dir,
        &state.config.host_speaker_name,
        false,
    );
    let formatted = format_transcript_for_ai(
        &segs,
        &hypes,
        &video.game_name,
        &video.session_context,
        &known,
    );
    Json(json!({ "prompt": generate_claude_web_prompt(&formatted) })).into_response()
}

#[derive(Deserialize)]
struct ClaudeImportBody {
    claude_response: String,
}

async fn import_claude_response(
    State(state): State<AppState>,
    Path(video_id): Path<i64>,
    Json(body): Json<ClaudeImportBody>,
) -> Response {
    let Ok(Some((video, _))) = state.db.get_video_by_id(video_id) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Vídeo não encontrado."})),
        )
            .into_response();
    };
    let parsed = match parse_ai_json_response(
        &body.claude_response,
        Some(video.duration_sec.max(60.0)),
        &state.config.group_name,
    ) {
        Ok(p) => p,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": e.to_string()})),
            )
                .into_response();
        }
    };
    let val = serde_json::to_value(&parsed).unwrap_or(Value::Null);
    let _ = state.db.save_video_result(
        video_id,
        &parsed.main_game,
        video.duration_sec,
        &video.mosaic_path,
        &video.srt_path,
        &val,
    );
    Json(json!({ "success": true, "data": parsed })).into_response()
}
