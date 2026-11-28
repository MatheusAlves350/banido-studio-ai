//! End-to-end Axum API parity integration tests covering all major route
//! families from `tests/test_core.py`, `tests/test_diagnostics.py`,
//! `tests/test_env_settings.py`, and `tests/test_updater.py`.

#![forbid(unsafe_code)]

use axum::body::Body;
use axum::http::{header, Method, Request, StatusCode};
use banido_core::config::AppConfig;
use banido_server::build_router;
use banido_server::state::AppState;
use serde_json::{json, Value};
use std::collections::HashMap;
use tempfile::tempdir;
use tower::ServiceExt;

const TOKEN: &str = "parity-test-token";

fn setup_app() -> (axum::Router, AppState, tempfile::TempDir) {
    let dir = tempdir().unwrap();
    let mut env = HashMap::new();
    env.insert(
        "BANIDO_BASE_DIR".into(),
        dir.path().join("data").to_string_lossy().into_owned(),
    );
    env.insert(
        "BANIDO_OBS_DIR".into(),
        dir.path().join("obs").to_string_lossy().into_owned(),
    );
    env.insert(
        "BANIDO_TEMP_DIR".into(),
        dir.path().join("tmp").to_string_lossy().into_owned(),
    );
    let cfg = AppConfig::load_with_env(dir.path().to_path_buf(), &env);
    let state = AppState::from_config(cfg, Some(TOKEN.into()));
    let router = build_router(state.clone());
    (router, state, dir)
}

async fn read_json(resp: axum::response::Response) -> Value {
    let bytes = axum::body::to_bytes(resp.into_body(), 1024 * 256)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test]
async fn test_videos_flex_bool_and_games_crud() {
    let (app, state, dir) = setup_app();
    let mkv = dir.path().join("obs").join("sessao.mkv");
    std::fs::write(&mkv, vec![b'a'; 1024]).unwrap();
    state.db.sync_obs_videos(&dir.path().join("obs"), 0.0).unwrap();

    // Frontend sends ?sync=0 and ?sync=1
    let r = app
        .clone()
        .oneshot(Request::builder().uri("/api/videos?sync=0").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
    let body = read_json(r).await;
    assert_eq!(body["videos"].as_array().unwrap().len(), 1);
    let vid = body["videos"][0]["id"].as_i64().unwrap();

    // GET /api/video/:id?include_data=1
    let r_det = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!("/api/video/{vid}?include_data=1"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_det.status(), StatusCode::OK);

    // POST & DELETE /api/games
    let r_add = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/api/games")
                .header("x-banido-token", TOKEN)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(r#"{"name":"Hades","default_context":"roguelike"}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_add.status(), StatusCode::OK);

    let r_del = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::DELETE)
                .uri("/api/games/Hades")
                .header("x-banido-token", TOKEN)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_del.status(), StatusCode::OK);
}

#[tokio::test]
async fn test_env_settings_and_diagnostics_redaction() {
    let (app, state, dir) = setup_app();
    let env_file = dir.path().join(".env");
    std::fs::write(&env_file, "# comentario\nBANIDO_PORT=8765\n").unwrap();

    // GET /api/env-settings requires token (security hardening over legacy!)
    let r403 = app
        .clone()
        .oneshot(Request::builder().uri("/api/env-settings").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(r403.status(), StatusCode::FORBIDDEN);

    let r_get = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/env-settings")
                .header("x-banido-token", TOKEN)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_get.status(), StatusCode::OK);
    let data = read_json(r_get).await;
    assert_eq!(data["values"]["BANIDO_WHISPER_MODEL"], "large-v3");

    // Save valid change preserves comment; invalid choice returns 400
    let r_save = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/api/env-settings")
                .header("x-banido-token", TOKEN)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(r#"{"values":{"BANIDO_WHISPER_MODEL":"medium"}}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_save.status(), StatusCode::OK);
    let saved = read_json(r_save).await;
    assert_eq!(saved["restart_needed"], true);
    let env_txt = std::fs::read_to_string(&env_file).unwrap();
    assert!(env_txt.contains("# comentario"));
    assert!(env_txt.contains(r#"BANIDO_WHISPER_MODEL="medium""#));

    let r_bad = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/api/env-settings")
                .header("x-banido-token", TOKEN)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(r#"{"values":{"BANIDO_WHISPER_MODEL":"gigante"}}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_bad.status(), StatusCode::BAD_REQUEST);

    // Diagnostics report redacts secrets and transcripts
    let job = state
        .db
        .create_job_record(1, "sessao_teste.mkv", "gemini", "gemini-2.5-flash")
        .unwrap();
    std::fs::write(
        &job.log_file,
        "[FALA #0001] [00:00:01 -> 00:00:03] [Host]: segredo da call\n\
         Erro com AIzaSySecretKey1234567890abcdefghij em C:\\Users\\Joao\\test.mkv\n",
    )
    .unwrap();
    let r_diag = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!(
                    "/api/diagnostics/report?job_id={}&strip_transcripts=true&redact_filenames=true",
                    job.id
                ))
                .header("x-banido-token", TOKEN)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_diag.status(), StatusCode::OK);
    let diag = read_json(r_diag).await;
    let md = diag["report_markdown"].as_str().unwrap();
    assert!(!md.contains("AIzaSy"));
    assert!(!md.contains("Joao"));
    assert!(!md.contains("segredo da call"));
}

#[tokio::test]
async fn test_claude_import_cut_chapters_and_worker_lock() {
    let (app, state, dir) = setup_app();
    let mkv = dir.path().join("obs").join("partida.mkv");
    std::fs::write(&mkv, vec![b'a'; 1024]).unwrap();
    state.db.sync_obs_videos(&dir.path().join("obs"), 0.0).unwrap();
    let vid = state.db.get_all_videos().unwrap()[0].id;

    // Import Claude JSON response
    let claude_payload = json!({
        "claude_response": "<think>ok</think>```json\n{\"main_game\":\"CS2\",\"title_options\":[\"[CS2] Virada\"],\"chapters\":[{\"timestamp\":\"00:00:00\",\"title\":\"Intro\"},{\"timestamp\":\"00:00:40\",\"title\":\"Clutch\"}],\"highlights\":[{\"start\":15,\"end\":35,\"score\":92,\"title\":\"Ace\"}]}\n```"
    });
    let r_imp = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri(format!("/api/claude-import/{vid}"))
                .header("x-banido-token", TOKEN)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(claude_payload.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_imp.status(), StatusCode::OK);

    // Embed chapters from saved AI analysis
    let r_ch = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri(format!("/api/embed-chapters/{vid}"))
                .header("x-banido-token", TOKEN)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(r#"{"mode":"both"}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_ch.status(), StatusCode::OK);
    assert_eq!(read_json(r_ch).await["chapter_count"], 3);

    // Spawn worker -> second spawn reports already_running, and /api/update/apply returns 409!
    let r_p1 = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri(format!("/api/process/{vid}"))
                .header("x-banido-token", TOKEN)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(r#"{"engine":"gemini"}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_p1.status(), StatusCode::OK);
    assert_eq!(read_json(r_p1).await["already_running"], false);

    let r_p2 = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri(format!("/api/process/{vid}"))
                .header("x-banido-token", TOKEN)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(r#"{"engine":"gemini"}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(read_json(r_p2).await["already_running"], true);

    let r_upd = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/api/update/apply")
                .header("x-banido-token", TOKEN)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r_upd.status(), StatusCode::CONFLICT);
}
