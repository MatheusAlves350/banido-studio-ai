//! Safe `.env` configuration catalog, validation, and comment-preserving upsert.
//!
//! Mirrors `app/env_settings.py` and `app/first_run.py` while eliminating
//! side-effect directory creation during pure validation and preventing
//! duplicate-key split-brain in `.env`.

use crate::config::parse_dotenv_str;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

/// Maximum length allowed for sanitized identity names.
pub const MAX_NAME_LEN: usize = 80;

/// Kind of UI-editable `.env` setting.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum FieldKind {
    /// Free-form sanitized text value.
    Text,
    /// Restricted list of allowed values.
    Choice,
    /// Boolean stored as `"1"` or `"0"`.
    Bool,
    /// Absolute filesystem directory path.
    Folder,
}

/// Metadata describing one safe `.env` field exposed to the web UI.
#[derive(Debug, Clone, Serialize)]
pub struct EnvFieldSpec {
    /// Environment variable key (never includes secrets, ports, or bind hosts).
    pub key: &'static str,
    /// Grouping label for UI sections.
    pub group: &'static str,
    /// Human-readable Portuguese label.
    pub label: &'static str,
    /// Input validation kind.
    pub kind: FieldKind,
    /// Default value when absent from `.env`.
    pub default_value: &'static str,
    /// Allowed options for [`FieldKind::Choice`].
    pub choices: &'static [&'static str],
    /// Whether an empty string is allowed.
    pub optional: bool,
}

/// Safe catalog of `.env` fields exposed to the panel (excludes keys/passwords/host/port).
pub const ENV_FIELDS: &[EnvFieldSpec] = &[
    EnvFieldSpec {
        key: "BANIDO_HOST_NAME",
        group: "Identidade",
        label: "Seu nome (Anfitrião)",
        kind: FieldKind::Text,
        default_value: "Host",
        choices: &[],
        optional: false,
    },
    EnvFieldSpec {
        key: "BANIDO_GROUP_NAME",
        group: "Identidade",
        label: "Nome do canal / grupo",
        kind: FieldKind::Text,
        default_value: "Banido Studio",
        choices: &[],
        optional: false,
    },
    EnvFieldSpec {
        key: "BANIDO_OBS_DIR",
        group: "Pastas",
        label: "Pasta das gravações do OBS",
        kind: FieldKind::Folder,
        default_value: "",
        choices: &[],
        optional: false,
    },
    EnvFieldSpec {
        key: "BANIDO_CLIPS_DIR",
        group: "Pastas",
        label: "Pasta de cortes exportados",
        kind: FieldKind::Folder,
        default_value: "",
        choices: &[],
        optional: true,
    },
    EnvFieldSpec {
        key: "BANIDO_TEMP_DIR",
        group: "Pastas",
        label: "Pasta temporária (SSD NVMe)",
        kind: FieldKind::Folder,
        default_value: "",
        choices: &[],
        optional: true,
    },
    EnvFieldSpec {
        key: "BANIDO_VOICE_BANK_DIR",
        group: "Pastas",
        label: "Pasta do Banco de Vozes",
        kind: FieldKind::Folder,
        default_value: "",
        choices: &[],
        optional: true,
    },
    EnvFieldSpec {
        key: "BANIDO_MODELS_DIR",
        group: "Pastas",
        label: "Pasta dos modelos de IA",
        kind: FieldKind::Folder,
        default_value: "",
        choices: &[],
        optional: true,
    },
    EnvFieldSpec {
        key: "BANIDO_WHISPER_MODEL",
        group: "Transcrição",
        label: "Modelo Whisper",
        kind: FieldKind::Choice,
        default_value: "large-v3",
        choices: &["tiny", "base", "small", "medium", "large-v2", "large-v3", "turbo"],
        optional: false,
    },
    EnvFieldSpec {
        key: "BANIDO_WHISPER_DEVICE",
        group: "Transcrição",
        label: "Dispositivo Whisper",
        kind: FieldKind::Choice,
        default_value: "auto",
        choices: &["auto", "cuda", "cpu"],
        optional: false,
    },
    EnvFieldSpec {
        key: "BANIDO_WHISPER_COMPUTE_TYPE",
        group: "Transcrição",
        label: "Precisão Whisper",
        kind: FieldKind::Choice,
        default_value: "auto",
        choices: &["auto", "float16", "int8_float16", "int8", "float32"],
        optional: false,
    },
    EnvFieldSpec {
        key: "BANIDO_WHISPER_LANGUAGE",
        group: "Transcrição",
        label: "Idioma principal",
        kind: FieldKind::Choice,
        default_value: "pt",
        choices: &["pt", "en", "es", "auto"],
        optional: false,
    },
    EnvFieldSpec {
        key: "BANIDO_SEPARATE_TRACKS",
        group: "Transcrição",
        label: "Transcrição separada por faixa",
        kind: FieldKind::Bool,
        default_value: "1",
        choices: &[],
        optional: false,
    },
    EnvFieldSpec {
        key: "BANIDO_KEEP_ORIG_AUDIO_BACKUP",
        group: "Transcrição",
        label: "Guardar backup lossless da Faixa 1",
        kind: FieldKind::Bool,
        default_value: "1",
        choices: &[],
        optional: false,
    },
    EnvFieldSpec {
        key: "BANIDO_AUTO_PAUSE_ON_RECORDING",
        group: "Pausa automática",
        label: "Pausar se OBS estiver gravando",
        kind: FieldKind::Bool,
        default_value: "1",
        choices: &[],
        optional: false,
    },
    EnvFieldSpec {
        key: "BANIDO_PAUSE_PROCESSES",
        group: "Pausa automática",
        label: "Processos extras que pausam",
        kind: FieldKind::Text,
        default_value: "",
        choices: &[],
        optional: true,
    },
];

/// Cleans an identity or group name by stripping dangerous `.env` characters
/// (`"`, `'`, `#`, newlines, tabs), collapsing whitespace, and capping length.
///
/// # Examples
/// ```
/// use banido_core::env_settings::clean_name;
///
/// assert_eq!(clean_name(Some("  Meu \"Canal\"\n#x "), "Padrao"), "Meu Canal x");
/// assert_eq!(clean_name(Some("   "), "Padrao"), "Padrao");
/// ```
pub fn clean_name(raw: Option<&str>, fallback: &str) -> String {
    let Some(s) = raw else {
        return fallback.to_string();
    };
    let replaced: String = s
        .chars()
        .map(|c| match c {
            '"' | '\'' | '#' | '\r' | '\n' | '\t' => ' ',
            _ => c,
        })
        .collect();
    let collapsed = replaced.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.is_empty() {
        fallback.to_string()
    } else {
        collapsed.chars().take(MAX_NAME_LEN).collect()
    }
}

/// Validates that `raw` is a complete absolute folder path, optionally creating it.
pub fn validate_folder(raw: &str, create_if_missing: bool) -> Result<PathBuf, String> {
    let trimmed = raw.trim().trim_matches('"').trim_matches('\'').trim();
    if trimmed.is_empty() {
        return Err("Informe o caminho completo da pasta.".to_string());
    }
    let is_win_drive = trimmed.len() >= 3
        && trimmed.as_bytes()[0].is_ascii_alphabetic()
        && trimmed.as_bytes()[1] == b':'
        && (trimmed.as_bytes()[2] == b'\\' || trimmed.as_bytes()[2] == b'/');
    let p = PathBuf::from(trimmed);
    if !p.is_absolute() && !is_win_drive {
        return Err("Informe um caminho completo (absoluto) para a pasta.".to_string());
    }
    if p.exists() && !p.is_dir() {
        return Err("O caminho informado aponta para um arquivo, não uma pasta.".to_string());
    }
    if create_if_missing && !is_win_drive {
        fs::create_dir_all(&p).map_err(|e| format!("Não foi possível criar a pasta: {e}"))?;
    }
    Ok(p)
}

/// Reads current values for all cataloged fields from `env_path`, applying defaults.
pub fn current_values(env_path: &Path) -> HashMap<String, String> {
    let file_map = fs::read_to_string(env_path)
        .map(|c| parse_dotenv_str(&c))
        .unwrap_or_default();
    let mut out = HashMap::new();
    for field in ENV_FIELDS {
        let val = file_map
            .get(field.key)
            .cloned()
            .unwrap_or_else(|| field.default_value.to_string());
        out.insert(field.key.to_string(), val);
    }
    out
}

/// Validates a map of submitted `.env` values against [`ENV_FIELDS`].
///
/// Returns `(cleaned_values, field_errors)`.
pub fn validate_env_updates(
    updates: &HashMap<String, String>,
    create_folders: bool,
) -> (HashMap<String, String>, HashMap<String, String>) {
    let mut cleaned = HashMap::new();
    let mut errors = HashMap::new();

    for (k, raw) in updates {
        let Some(spec) = ENV_FIELDS.iter().find(|f| f.key == k) else {
            errors.insert(k.clone(), "Opção desconhecida ou protegida.".to_string());
            continue;
        };
        match spec.kind {
            FieldKind::Text => {
                let c = clean_name(Some(raw), "");
                if c.is_empty() && !spec.optional {
                    errors.insert(k.clone(), "Campo obrigatório.".to_string());
                } else {
                    cleaned.insert(k.clone(), c);
                }
            }
            FieldKind::Choice => {
                let v = raw.trim();
                if spec.choices.contains(&v) {
                    cleaned.insert(k.clone(), v.to_string());
                } else {
                    errors.insert(
                        k.clone(),
                        format!("Valor inválido. Use um de: {}", spec.choices.join(", ")),
                    );
                }
            }
            FieldKind::Bool => {
                let low = raw.trim().to_lowercase();
                if matches!(low.as_str(), "1" | "true" | "yes" | "sim" | "on") {
                    cleaned.insert(k.clone(), "1".to_string());
                } else if matches!(low.as_str(), "0" | "false" | "no" | "nao" | "não" | "off") {
                    cleaned.insert(k.clone(), "0".to_string());
                } else {
                    errors.insert(k.clone(), "Valor booleano inválido (use 1 ou 0).".to_string());
                }
            }
            FieldKind::Folder => {
                let v = raw.trim();
                if v.is_empty() && spec.optional {
                    cleaned.insert(k.clone(), String::new());
                } else {
                    match validate_folder(v, create_folders) {
                        Ok(p) => {
                            cleaned.insert(k.clone(), p.to_string_lossy().into_owned());
                        }
                        Err(msg) => {
                            errors.insert(k.clone(), msg);
                        }
                    }
                }
            }
        }
    }
    (cleaned, errors)
}

/// Returns only entries whose normalized values differ from `baseline`.
pub fn changed_only(
    incoming: &HashMap<String, String>,
    baseline: &HashMap<String, String>,
) -> HashMap<String, String> {
    let norm = |s: &str| s.trim().trim_end_matches(['/', '\\']).to_string();
    incoming
        .iter()
        .filter(|(k, v)| baseline.get(*k).map(|b| norm(b) != norm(v)).unwrap_or(true))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect()
}

/// Upserts key-value pairs into a `.env` file while preserving comments,
/// removing duplicate occurrences of updated keys, and formatting values safely.
pub fn upsert_env(env_path: &Path, updates: &HashMap<String, String>) -> std::io::Result<()> {
    if let Some(parent) = env_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let existing = fs::read_to_string(env_path).unwrap_or_default();
    let mut pending = updates.clone();
    let mut out_lines: Vec<String> = Vec::new();

    for raw in existing.lines() {
        let stripped = raw.trim();
        let effective = stripped.strip_prefix("export ").map(str::trim).unwrap_or(stripped);
        let key_opt = if !effective.is_empty() && !effective.starts_with('#') {
            effective.split_once('=').map(|(k, _)| k.trim())
        } else {
            None
        };
        if let Some(k) = key_opt {
            if let Some(new_val) = pending.remove(k) {
                out_lines.push(format_env_line(k, &new_val));
                continue;
            }
            if updates.contains_key(k) {
                // Drop duplicate keys to avoid split-brain between readers
                continue;
            }
        }
        out_lines.push(raw.to_string());
    }

    let mut remaining: Vec<(&String, &String)> = pending.iter().collect();
    remaining.sort_by(|a, b| a.0.cmp(b.0));
    for (k, v) in remaining {
        out_lines.push(format_env_line(k, v));
    }

    let content = if out_lines.is_empty() {
        String::new()
    } else {
        format!("{}\n", out_lines.join("\n"))
    };
    fs::write(env_path, content)
}

fn format_env_line(key: &str, value: &str) -> String {
    let clean = value.replace(['\r', '\n'], " ");
    let normalized = if clean.starts_with("\\\\") {
        clean
    } else {
        clean.replace('\\', "/")
    };
    format!(r#"{key}="{normalized}""#)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_catalog_never_exposes_secrets() {
        for f in ENV_FIELDS {
            assert!(!f.key.contains("API_KEY"));
            assert!(!f.key.contains("PASSWORD"));
            assert_ne!(f.key, "BANIDO_HOST");
            assert_ne!(f.key, "BANIDO_PORT");
        }
    }

    #[test]
    fn test_upsert_env_preserves_comments_and_deduplicates() {
        let dir = tempdir().unwrap();
        let env_file = dir.path().join(".env");
        fs::write(&env_file, "# comentario\nexport BANIDO_PORT=1\nBANIDO_PORT=3\n").unwrap();
        let mut upd = HashMap::new();
        upd.insert("BANIDO_PORT".into(), "2".into());
        upd.insert("BANIDO_HOST_NAME".into(), "Matheus".into());
        upsert_env(&env_file, &upd).unwrap();
        let txt = fs::read_to_string(&env_file).unwrap();
        assert!(txt.contains("# comentario"));
        assert_eq!(txt.matches("BANIDO_PORT").count(), 1);
        assert!(txt.contains(r#"BANIDO_HOST_NAME="Matheus""#));
    }
}
