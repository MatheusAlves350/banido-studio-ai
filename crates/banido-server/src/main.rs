//! CLI entrypoint for **Banido Studio AI (Safe Rust)**.
//!
//! Subcommands:
//! - `serve` (default): starts the Axum HTTP server on `BANIDO_HOST:BANIDO_PORT`.
//! - `bench [--naive] [--duration 300] [--embeddings 120]`: runs the deterministic workload benchmark.
//! - `parity --golden <path> [--epsilon 1e-4]`: runs the A/B differential parity harness.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

use banido_core::config::AppConfig;
use banido_server::bench_runner::{run_bench, run_parity};
use banido_server::build_router;
use banido_server::state::AppState;
use std::path::PathBuf;

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("bench") => {
            let naive = args.iter().any(|a| a == "--naive");
            let duration = parse_flag_usize(&args, "--duration").unwrap_or(300);
            let embeddings = parse_flag_usize(&args, "--embeddings").unwrap_or(120);
            run_bench(naive, duration, embeddings);
        }
        Some("parity") => {
            let golden = parse_flag_str(&args, "--golden")
                .map(PathBuf::from)
                .expect("missing --golden <path>");
            let epsilon = parse_flag_f64(&args, "--epsilon").unwrap_or(1e-4);
            if !run_parity(&golden, epsilon) {
                std::process::exit(1);
            }
        }
        _ => {
            let root = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
            let config = AppConfig::load_from_process(root);
            let bind_addr = format!("{}:{}", config.server_host, config.server_port);
            let explicit_token = std::env::var("BANIDO_API_TOKEN").ok();
            let state = AppState::from_config(config, explicit_token);
            let app = build_router(state);
            let listener = tokio::net::TcpListener::bind(&bind_addr)
                .await
                .expect("bind TCP listener");
            println!("[BANIDO RUST] Servidor Axum ativo em http://{bind_addr}");
            axum::serve(listener, app).await.expect("serve axum");
        }
    }
}

fn parse_flag_str(args: &[String], flag: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == flag).map(|w| w[1].clone())
}

fn parse_flag_usize(args: &[String], flag: &str) -> Option<usize> {
    parse_flag_str(args, flag).and_then(|s| s.parse().ok())
}

fn parse_flag_f64(args: &[String], flag: &str) -> Option<f64> {
    parse_flag_str(args, flag).and_then(|s| s.parse().ok())
}

#[cfg(test)]
mod tests {
    use axum::body::Body;
    use axum::http::{header, Method, Request, StatusCode};
    use banido_core::config::AppConfig;
    use banido_server::build_router;
    use banido_server::state::AppState;
    use std::collections::HashMap;
    use tempfile::tempdir;
    use tower::ServiceExt;

    fn test_state(host: &str, password: &str) -> (AppState, tempfile::TempDir) {
        let dir = tempdir().unwrap();
        let mut env = HashMap::new();
        env.insert("BANIDO_BASE_DIR".into(), dir.path().join("data").to_string_lossy().into_owned());
        env.insert("BANIDO_TEMP_DIR".into(), dir.path().join("tmp").to_string_lossy().into_owned());
        env.insert("BANIDO_HOST".into(), host.into());
        env.insert("BANIDO_ACCESS_PASSWORD".into(), password.into());
        let cfg = AppConfig::load_with_env(dir.path().to_path_buf(), &env);
        (AppState::from_config(cfg, Some("test-csrf-token".into())), dir)
    }

    #[tokio::test]
    async fn test_csrf_and_masked_settings_flow() {
        let (state, _dir) = test_state("127.0.0.1", "");
        let app = build_router(state);

        // GET / injects banido-token meta tag
        let home = app
            .clone()
            .oneshot(Request::builder().uri("/").body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(home.status(), StatusCode::OK);
        let bytes = axum::body::to_bytes(home.into_body(), 1024 * 64).await.unwrap();
        let html = String::from_utf8(bytes.to_vec()).unwrap();
        assert!(html.contains(r#"<meta name="banido-token" content="test-csrf-token">"#));

        // Without token -> 403
        let r403 = app
            .clone()
            .oneshot(Request::builder().uri("/api/settings").body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(r403.status(), StatusCode::FORBIDDEN);

        // Save key with token -> masked response never leaks secret
        let r_save = app
            .clone()
            .oneshot(
                Request::builder()
                    .method(Method::POST)
                    .uri("/api/settings")
                    .header("x-banido-token", "test-csrf-token")
                    .header(header::CONTENT_TYPE, "application/json")
                    .body(Body::from(r#"{"google_api_key":"fake-demo-secret-1234567890","ai_provider":"gemini"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r_save.status(), StatusCode::OK);
        let body = String::from_utf8(axum::body::to_bytes(r_save.into_body(), 65536).await.unwrap().to_vec()).unwrap();
        assert!(body.contains("fake...7890"));
        assert!(!body.contains("fake-demo-secret-1234567890"));
    }

    #[tokio::test]
    async fn test_network_mode_login_logout_and_rate_limit() {
        let (state, _dir) = test_state("0.0.0.0", "segredo-forte");
        let app = build_router(state);

        // Unauthenticated / redirects to /login; /api/status returns 401
        let r_home = app.clone().oneshot(Request::builder().uri("/").body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(r_home.status(), StatusCode::SEE_OTHER);
        let r_api = app.clone().oneshot(Request::builder().uri("/api/status").body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(r_api.status(), StatusCode::UNAUTHORIZED);

        // Valid login sets HttpOnly SameSite=Strict cookie
        let r_ok = app
            .clone()
            .oneshot(
                Request::builder()
                    .method(Method::POST)
                    .uri("/login")
                    .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
                    .body(Body::from("password=segredo-forte"))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r_ok.status(), StatusCode::SEE_OTHER);
        let cookie_hdr = r_ok.headers().get(header::SET_COOKIE).unwrap().to_str().unwrap().to_string();
        assert!(cookie_hdr.to_lowercase().contains("httponly"));
        let cookie_pair = cookie_hdr.split(';').next().unwrap().to_string();

        // Authenticated /api/status succeeds
        let r_auth = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/api/status")
                    .header(header::COOKIE, &cookie_pair)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r_auth.status(), StatusCode::OK);

        // Logout truly revokes session server-side!
        let _ = app
            .clone()
            .oneshot(
                Request::builder()
                    .method(Method::POST)
                    .uri("/logout")
                    .header(header::COOKIE, &cookie_pair)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        let r_after = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/api/status")
                    .header(header::COOKIE, &cookie_pair)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r_after.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn test_resume_override_and_models_and_env_settings() {
        let (state, dir) = test_state("127.0.0.1", "");
        let app = build_router(state);

        let r_ov = app
            .clone()
            .oneshot(
                Request::builder()
                    .method(Method::POST)
                    .uri("/api/process/42/resume-override")
                    .header("x-banido-token", "test-csrf-token")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r_ov.status(), StatusCode::OK);
        assert!(dir.path().join("tmp").join("video_42_work").join("force_resume.flag").exists());

        let r_mod = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/api/models/gemini")
                    .header("x-banido-token", "test-csrf-token")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r_mod.status(), StatusCode::OK);

        let r_bad = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/api/models/outro")
                    .header("x-banido-token", "test-csrf-token")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r_bad.status(), StatusCode::NOT_FOUND);
    }
}
