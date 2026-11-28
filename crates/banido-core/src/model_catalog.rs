//! AI model catalog for Google Gemini and NVIDIA NIM with filtering and atomic disk cache.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::path::Path;

/// Default Google Gemini model identifier.
pub const DEFAULT_GOOGLE_MODEL: &str = "gemini-2.5-flash";

/// Default NVIDIA NIM model identifier.
pub const DEFAULT_NVIDIA_MODEL: &str = "z-ai/glm-5.3-flash";

/// Built-in Gemini fallback models ordered by preference.
pub const DEFAULT_GOOGLE_MODELS: &[&str] = &[
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
];

/// Built-in NVIDIA NIM fallback models ordered by preference.
pub const DEFAULT_NVIDIA_MODELS: &[&str] = &[
    "z-ai/glm-5.3-flash",
    "meta/llama-3.3-70b-instruct",
    "deepseek-ai/deepseek-r1",
    "nvidia/llama-3.1-nemotron-70b-instruct",
];

/// Model entry displayed in the panel dropdown.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ModelOption {
    /// Model ID sent to the provider API.
    pub value: String,
    /// Human-readable label.
    pub label: String,
}

/// Catalog response returned by `/api/models/{provider}`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelCatalogResponse {
    /// Provider canonical name (`google` or `nvidia`).
    pub provider: String,
    /// Origin of the list (`default`, `cache`, or `api`).
    pub source: String,
    /// Ordered model options.
    pub models: Vec<ModelOption>,
}

/// Normalizes provider aliases (`gemini`/`google`, `nvidia`/`nim`).
///
/// # Examples
/// ```
/// use banido_core::model_catalog::normalize_provider;
///
/// assert_eq!(normalize_provider("gemini"), Some("google"));
/// assert_eq!(normalize_provider("nvidia"), Some("nvidia"));
/// assert_eq!(normalize_provider("unknown"), None);
/// ```
pub fn normalize_provider(raw: &str) -> Option<&'static str> {
    match raw.trim().to_lowercase().as_str() {
        "google" | "gemini" => Some("google"),
        "nvidia" | "nim" => Some("nvidia"),
        _ => None,
    }
}

/// Returns built-in default models for a canonical provider.
pub fn default_models(provider: &str) -> Vec<ModelOption> {
    let list = match provider {
        "google" => DEFAULT_GOOGLE_MODELS,
        "nvidia" => DEFAULT_NVIDIA_MODELS,
        _ => &[],
    };
    list.iter()
        .map(|id| ModelOption {
            value: (*id).to_string(),
            label: (*id).to_string(),
        })
        .collect()
}

/// Filters Google `/v1beta/models` JSON response to chat-capable Gemini models,
/// placing known defaults first and excluding embedding models.
pub fn filter_google_models_json(payload: &Value) -> Vec<ModelOption> {
    let mut ids: Vec<String> = payload
        .get("models")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|m| {
                    let methods = m.get("supportedGenerationMethods")?.as_array()?;
                    let supports_gen = methods
                        .iter()
                        .any(|v| v.as_str() == Some("generateContent"));
                    if !supports_gen {
                        return None;
                    }
                    let name = m.get("name")?.as_str()?;
                    let id = name.strip_prefix("models/").unwrap_or(name);
                    if id.contains("embedding") || !id.starts_with("gemini") {
                        return None;
                    }
                    Some(id.to_string())
                })
                .collect()
        })
        .unwrap_or_default();

    ids.sort_by_key(|id| {
        DEFAULT_GOOGLE_MODELS
            .iter()
            .position(|&d| d == id)
            .unwrap_or(usize::MAX)
    });
    ids.dedup();
    ids.into_iter()
        .map(|id| ModelOption {
            label: id.clone(),
            value: id,
        })
        .collect()
}

/// Filters NVIDIA `/v1/models` JSON response to exclude embed/rerank models.
pub fn filter_nvidia_models_json(payload: &Value) -> Vec<ModelOption> {
    let mut ids: Vec<String> = payload
        .get("data")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|m| {
                    let id = m.get("id")?.as_str()?;
                    let low = id.to_lowercase();
                    if low.contains("embed") || low.contains("rerank") {
                        return None;
                    }
                    Some(id.to_string())
                })
                .collect()
        })
        .unwrap_or_default();

    ids.sort_by_key(|id| {
        DEFAULT_NVIDIA_MODELS
            .iter()
            .position(|&d| d == id)
            .unwrap_or(usize::MAX)
    });
    ids.dedup();
    ids.into_iter()
        .map(|id| ModelOption {
            label: id.clone(),
            value: id,
        })
        .collect()
}

/// Merges and writes a provider cache entry atomically without clobbering other providers.
pub fn write_disk_cache(cache_path: &Path, provider: &str, models: &[ModelOption]) -> std::io::Result<()> {
    let mut all: HashMap<String, Vec<ModelOption>> = fs::read_to_string(cache_path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();
    all.insert(provider.to_string(), models.to_vec());
    if let Some(parent) = cache_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = cache_path.with_extension(format!("json.{}.tmp", std::process::id()));
    fs::write(&tmp, serde_json::to_string_pretty(&all).unwrap_or_default())?;
    fs::rename(&tmp, cache_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_filter_google_and_nvidia_models() {
        let g = serde_json::json!({
            "models": [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-9-novo", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
                {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["generateContent"]}
            ]
        });
        let g_ids: Vec<String> = filter_google_models_json(&g)
            .into_iter()
            .map(|m| m.value)
            .collect();
        assert_eq!(g_ids, vec!["gemini-2.5-flash", "gemini-9-novo"]);

        let n = serde_json::json!({
            "data": [
                {"id": "z-ai/glm-5.3-flash"},
                {"id": "nvidia/nv-embed-v1"},
                {"id": "nvidia/rerank-x"}
            ]
        });
        let n_ids: Vec<String> = filter_nvidia_models_json(&n)
            .into_iter()
            .map(|m| m.value)
            .collect();
        assert_eq!(n_ids, vec!["z-ai/glm-5.3-flash"]);
    }
}
