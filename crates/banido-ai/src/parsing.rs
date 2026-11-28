//! Resilient JSON extraction from LLM responses (Gemini, NVIDIA NIM, Claude Web).
//!
//! Handles reasoning `<think>...</think>` blocks (including unclosed `<think>`
//! on max-token truncation), fenced Markdown blocks, and balanced brace slicing.

use crate::youtube::{normalize_ai_data, AiAnalysisData};
use crate::{AiError, AiResult};
use regex::Regex;
use serde_json::Value;
use std::sync::OnceLock;

fn strip_think_blocks(raw: &str) -> String {
    static CLOSED_THINK: OnceLock<Regex> = OnceLock::new();
    let re = CLOSED_THINK.get_or_init(|| Regex::new(r"(?is)<think>.*?</think>").unwrap());
    let cleaned = re.replace_all(raw, "");
    // Handle unclosed <think> when a reasoning model hits max_tokens
    if let Some(pos) = cleaned.to_lowercase().find("<think>") {
        cleaned[..pos].trim().to_string()
    } else {
        cleaned.trim().to_string()
    }
}

/// Extracts the JSON object payload from raw LLM text.
fn extract_json_slice(cleaned: &str) -> &str {
    static FENCED: OnceLock<Regex> = OnceLock::new();
    let re = FENCED.get_or_init(|| Regex::new(r"(?s)```(?:json)?\s*([\{\[].*?[\}\]])\s*```").unwrap());
    if let Some(caps) = re.captures(cleaned) {
        if let Some(m) = caps.get(1) {
            return m.as_str();
        }
    }
    if let (Some(s), Some(e)) = (cleaned.find('{'), cleaned.rfind('}')) {
        if e > s {
            return &cleaned[s..=e];
        }
    }
    cleaned
}

/// Parses and normalizes an AI JSON response into strongly typed [`AiAnalysisData`].
///
/// # Examples
/// ```
/// use banido_ai::parsing::parse_ai_json_response;
///
/// let raw = r#"<think>planejando</think>```json
/// {"main_game":"CS2","title_options":["[CS2] Clutch Épico"],"chapters":[{"timestamp":"00:02:00","title":"Ação"}],"highlights":[{"start":10,"end":40,"score":90,"title":"Clutch"}]}
/// ```"#;
/// let parsed = parse_ai_json_response(raw, Some(300.0), "Banido Studio").unwrap();
/// assert_eq!(parsed.main_game, "CS2");
/// assert_eq!(parsed.title_options[0], "CS2 - Clutch Épico");
/// assert_eq!(parsed.chapters[0].timestamp, "00:00:00");
/// ```
pub fn parse_ai_json_response(
    response_text: &str,
    duration_sec: Option<f64>,
    group_name: &str,
) -> AiResult<AiAnalysisData> {
    if response_text.trim().is_empty() {
        return Err(AiError::Parse("Resposta vazia recebida da IA.".into()));
    }
    let cleaned = strip_think_blocks(response_text);
    let slice = extract_json_slice(&cleaned);
    let mut val: Value = serde_json::from_str(slice)
        .map_err(|e| AiError::Parse(format!("JSON inválido retornado pela IA: {e}")))?;

    if let Some(arr) = val.as_array() {
        if let Some(first) = arr.first().filter(|v| v.is_object()) {
            val = first.clone();
        }
    }
    if !val.is_object() {
        return Err(AiError::Parse(
            "O JSON retornado pela IA precisa ser um objeto { ... }.".into(),
        ));
    }
    Ok(normalize_ai_data(&val, duration_sec, group_name))
}
