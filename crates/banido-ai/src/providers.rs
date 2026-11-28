//! Async HTTP clients for Google Gemini and NVIDIA NIM with explicit timeouts
//! and accurate non-retryable error classification.

use crate::parsing::parse_ai_json_response;
use crate::prompt::SYSTEM_PROMPT;
use crate::youtube::AiAnalysisData;
use crate::{AiError, AiResult};
use regex::Regex;
use reqwest::Client;
use serde_json::{json, Value};
use std::sync::OnceLock;
use std::time::Duration;

/// Classifies whether a provider error is fatal (invalid key, unauthorized,
/// billing disabled) and should abort fallback retries immediately.
///
/// Fixes false positives in `app/ai/base.py:62-70` by requiring word boundaries
/// around HTTP status codes `401`/`403` (so `"401ms"` is NOT misclassified).
///
/// # Examples
/// ```
/// use banido_ai::providers::is_non_retryable_ai_error;
///
/// assert!(is_non_retryable_ai_error("401 API_KEY_INVALID"));
/// assert!(!is_non_retryable_ai_error("503 Service Unavailable"));
/// assert!(!is_non_retryable_ai_error("timeout after 401ms"));
/// ```
pub fn is_non_retryable_ai_error(msg: &str) -> bool {
    static STATUS_RE: OnceLock<Regex> = OnceLock::new();
    let re = STATUS_RE.get_or_init(|| Regex::new(r"\b(401|403)\b").unwrap());
    let low = msg.to_lowercase();
    re.is_match(msg)
        || low.contains("api_key_invalid")
        || low.contains("invalid api key")
        || low.contains("permission_denied")
        || low.contains("unauthorized")
        || low.contains("billing_not_active")
}

/// Async Google Gemini client using `reqwest` with explicit request timeout.
#[derive(Debug, Clone)]
pub struct GeminiClient {
    http: Client,
    api_key: String,
    model: String,
}

impl GeminiClient {
    /// Creates a Gemini client with a 180-second timeout.
    pub fn new(api_key: String, model: String) -> AiResult<Self> {
        let http = Client::builder().timeout(Duration::from_secs(180)).build()?;
        Ok(Self {
            http,
            api_key,
            model,
        })
    }

    /// Sends a transcript prompt to Gemini `generateContent` and parses the JSON output.
    pub async fn analyze(
        &self,
        transcript: &str,
        duration_sec: Option<f64>,
        group_name: &str,
    ) -> AiResult<AiAnalysisData> {
        let url = format!(
            "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent?key={}",
            self.model, self.api_key
        );
        let body = json!({
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text": transcript}]}],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json"
            }
        });
        let resp = self.http.post(url).json(&body).send().await?;
        let status = resp.status();
        let payload: Value = resp.json().await?;
        if !status.is_success() {
            return Err(AiError::Provider(format!("HTTP {status}: {payload}")));
        }
        let text = payload
            .pointer("/candidates/0/content/parts/0/text")
            .and_then(Value::as_str)
            .ok_or_else(|| AiError::Parse("Gemini não retornou texto na resposta.".into()))?;
        parse_ai_json_response(text, duration_sec, group_name)
    }
}

/// Async NVIDIA NIM OpenAI-compatible chat client.
#[derive(Debug, Clone)]
pub struct NvidiaNimClient {
    http: Client,
    api_key: String,
    model: String,
}

impl NvidiaNimClient {
    /// Creates an NVIDIA NIM client with a 300-second timeout.
    pub fn new(api_key: String, model: String) -> AiResult<Self> {
        let http = Client::builder().timeout(Duration::from_secs(300)).build()?;
        Ok(Self {
            http,
            api_key,
            model,
        })
    }

    /// Sends a transcript prompt to NVIDIA NIM `/v1/chat/completions`.
    pub async fn analyze(
        &self,
        transcript: &str,
        duration_sec: Option<f64>,
        group_name: &str,
    ) -> AiResult<AiAnalysisData> {
        let body = json!({
            "model": self.model,
            "temperature": 0.7,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript}
            ]
        });
        let resp = self
            .http
            .post("https://integrate.api.nvidia.com/v1/chat/completions")
            .bearer_auth(&self.api_key)
            .json(&body)
            .send()
            .await?;
        let status = resp.status();
        let payload: Value = resp.json().await?;
        if !status.is_success() {
            return Err(AiError::Provider(format!("HTTP {status}: {payload}")));
        }
        let text = payload
            .pointer("/choices/0/message/content")
            .and_then(Value::as_str)
            .ok_or_else(|| AiError::Parse("NVIDIA NIM retornou choices vazio.".into()))?;
        parse_ai_json_response(text, duration_sec, group_name)
    }
}
