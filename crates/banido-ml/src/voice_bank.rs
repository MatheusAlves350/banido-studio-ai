//! Voice Bank canonical friend name resolution, highest-energy 8s window
//! selection, and outlier-safe incremental embedding centroid updates.
//!
//! Fixes the latent poisoning bug in `app/voice_bank.py:691-700`: when all new
//! candidate embeddings fail the `>= 0.35` cosine similarity gate against an
//! established speaker profile (`old_count >= 3`), the update is rejected
//! cleanly instead of corrupting `embedding.npy` with outliers.

use crate::clustering::{dot_f32, l2_norm};
use std::path::Path;
use unicode_normalization::UnicodeNormalization;

/// Windows DOS reserved device names forbidden as speaker folder names.
const WINDOWS_RESERVED: &[&str] = &[
    "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8",
    "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
];

fn normalize_name_key(name: &str) -> String {
    let stripped: String = name
        .trim()
        .nfkd()
        .filter(|c| !matches!(*c as u32, 0x0300..=0x036F))
        .collect();
    stripped
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

/// Resolves a speaker display name to a safe canonical folder name while
/// blocking path traversal (`..`, `.`), invalid characters, and Windows
/// reserved device names (`CON`, `NUL`, `COM1`, ...).
///
/// Matches existing folder spelling accent/case-insensitively when present in
/// `existing_names`.
///
/// # Examples
/// ```
/// use banido_ml::voice_bank::resolve_canonical_friend_name;
///
/// assert_eq!(resolve_canonical_friend_name("..", &[]), "");
/// assert_eq!(resolve_canonical_friend_name("CON", &[]), "");
/// assert_eq!(resolve_canonical_friend_name("joao pedro", &[]), "Joao Pedro");
/// assert_eq!(
///     resolve_canonical_friend_name("joao pedro", &["João Pedro".into()]),
///     "João Pedro"
/// );
/// ```
pub fn resolve_canonical_friend_name(raw_name: &str, existing_names: &[String]) -> String {
    let cleaned_chars: String = raw_name
        .chars()
        .filter(|&c| {
            !matches!(
                c,
                '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' | '\x00'..='\x1f'
            )
        })
        .collect();
    let collapsed = cleaned_chars
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    let trimmed = collapsed.trim_matches(|c: char| c == '.' || c == ' ');
    if trimmed.is_empty() {
        return String::new();
    }

    let base_stem = trimmed.split('.').next().unwrap_or("").trim().to_lowercase();
    if WINDOWS_RESERVED.contains(&base_stem.as_str()) {
        return String::new();
    }

    let target_key = normalize_name_key(trimmed);
    for existing in existing_names {
        if normalize_name_key(existing) == target_key {
            return existing.clone();
        }
    }

    if trimmed == trimmed.to_lowercase() {
        title_case_words(trimmed)
    } else {
        trimmed.to_string()
    }
}

fn title_case_words(s: &str) -> String {
    s.split_whitespace()
        .map(|w| {
            let mut chars = w.chars();
            match chars.next() {
                None => String::new(),
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// Lists existing speaker directory names inside `voice_bank_dir`.
pub fn list_known_voice_names(
    voice_bank_dir: &Path,
    host_name: &str,
    exclude_host: bool,
) -> Vec<String> {
    let Ok(entries) = std::fs::read_dir(voice_bank_dir) else {
        return Vec::new();
    };
    let host_low = host_name.trim().to_lowercase();
    let mut names: Vec<String> = entries
        .flatten()
        .filter(|e| e.path().is_dir())
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|n| !(exclude_host && n.trim().to_lowercase() == host_low))
        .collect();
    names.sort_by_key(|a| a.to_lowercase());
    names
}

/// Selects the contiguous `max_sec` window with highest vocal RMS energy for
/// segments longer than `max_sec`, reducing encoder latency without accuracy loss.
pub fn select_best_voice_window(audio: &[f32], sample_rate: usize, max_sec: f64) -> &[f32] {
    let max_samples = (max_sec * (sample_rate as f64)) as usize;
    if audio.len() <= max_samples || max_samples == 0 {
        return audio;
    }
    let step = sample_rate.max(1);
    let mut best_start = 0usize;
    let mut best_energy = -1.0_f64;
    let mut s = 0usize;
    while s + max_samples <= audio.len() {
        let energy: f64 = audio[s..s + max_samples]
            .iter()
            .map(|&v| (v as f64) * (v as f64))
            .sum();
        if energy > best_energy {
            best_energy = energy;
            best_start = s;
        }
        s += step;
    }
    &audio[best_start..best_start + max_samples]
}

/// Updates an existing normalized voice centroid safely with outlier rejection.
///
/// When `old_count >= 3`, candidate embeddings with cosine similarity `< 0.35`
/// against `old_vec` are discarded. If **all** candidates are outliers, returns
/// `None` instead of poisoning the profile (fixes `app/voice_bank.py:691`).
pub fn update_voice_centroid(
    old_vec: Option<&[f32]>,
    old_count: usize,
    new_embs: &[Vec<f32>],
) -> Option<(Vec<f32>, usize)> {
    if new_embs.is_empty() {
        return None;
    }
    let dim = new_embs[0].len();
    if dim == 0 {
        return None;
    }

    let filtered: Vec<&Vec<f32>> = match old_vec {
        Some(prev) if old_count >= 3 && prev.len() == dim => new_embs
            .iter()
            .filter(|e| e.len() == dim && dot_f32(e, prev) >= 0.35)
            .collect(),
        _ => new_embs.iter().filter(|e| e.len() == dim).collect(),
    };

    if filtered.is_empty() {
        return None;
    }

    let mut mean = vec![0.0_f64; dim];
    for emb in &filtered {
        for (slot, &v) in mean.iter_mut().zip(emb.iter()) {
            *slot += v as f64;
        }
    }
    let new_cnt = filtered.len() as f64;
    for slot in &mut mean {
        *slot /= new_cnt;
    }

    let mut combined = vec![0.0_f32; dim];
    if let Some(prev) = old_vec.filter(|p| p.len() == dim && old_count > 0) {
        let w_old = (old_count.min(40)) as f64;
        for i in 0..dim {
            combined[i] = ((prev[i] as f64) * w_old + mean[i] * new_cnt) as f32;
        }
    } else {
        for i in 0..dim {
            combined[i] = mean[i] as f32;
        }
    }

    let norm = l2_norm(&combined);
    if norm <= 0.0 {
        return None;
    }
    for x in &mut combined {
        *x = ((*x as f64) / norm) as f32;
    }
    Some((combined, old_count + filtered.len()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn test_outlier_rejection_prevents_profile_poisoning() {
        let existing = vec![1.0_f32, 0.0, 0.0];
        let outlier = vec![0.0_f32, 1.0, 0.0]; // cosine sim = 0.0 < 0.35
        assert!(update_voice_centroid(Some(&existing), 5, &[outlier]).is_none());

        let inlier = vec![0.9_f32, 0.43589, 0.0];
        let (updated, count) = update_voice_centroid(Some(&existing), 5, &[inlier]).unwrap();
        assert_eq!(count, 6);
        assert!((l2_norm(&updated) - 1.0).abs() < 1e-5);
    }

    proptest! {
        #[test]
        fn prop_canonical_name_never_contains_traversal_or_separators(raw in ".*") {
            let res = resolve_canonical_friend_name(&raw, &[]);
            prop_assert!(!res.contains('/'));
            prop_assert!(!res.contains('\\'));
            prop_assert_ne!(res.as_str(), ".");
            prop_assert_ne!(res.as_str(), "..");
        }
    }
}
