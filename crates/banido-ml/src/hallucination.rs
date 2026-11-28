//! Whisper hallucination filter (phantom silence subtitles, confidence gates,
//! and repetition-loop detection).
//!
//! Fixes the suspicious length guard in `app/transcription/hallucination.py:119`
//! so absurd `<= 50ms` segments are always rejected regardless of character length.

use std::collections::HashSet;
use unicode_normalization::UnicodeNormalization;

/// Substrings that always indicate phantom Whisper subtitles.
pub const HALLUCINATION_SUBSTRINGS: &[&str] = &[
    "legenda por ",
    "legendas por ",
    "legendas pela comunidade",
    "amara.org",
    "traducao e legendas",
    "inscreva-se no canal",
    "obrigado por assistir",
    "subtitles by",
];

/// Short-segment phrases (`<= 10` words) indicating phantom YouTube outro text.
pub const HALLUCINATION_PHRASES: &[&str] = &[
    "inscreva se no canal",
    "inscreva se",
    "se inscreva",
    "ative o sininho",
    "ativa o sininho",
    "ative as notificacoes",
    "ative a notificacao",
    "deixe o like",
    "deixe seu like",
    "deixa o like",
    "deixe um like",
    "curta e compartilhe",
    "curta o video",
    "compartilhe este video",
    "ate o proximo video",
    "ate a proxima",
    "obrigado por assistir",
    "obrigada por assistir",
    "obrigado por ter assistido",
    "legendas pela comunidade",
    "traducao e legendas",
    "subtitles by",
    "thanks for watching",
    "please subscribe",
];

/// Confidence metrics emitted by Whisper for a decoded segment.
#[derive(Debug, Clone, Copy, Default)]
pub struct WhisperConfidence {
    /// Probability that the segment contains no speech (`0.0..=1.0`).
    pub no_speech_prob: Option<f64>,
    /// Mean token log-probability (negative values closer to 0 are higher confidence).
    pub avg_logprob: Option<f64>,
    /// Gzip compression ratio (high values indicate repetitive loops).
    pub compression_ratio: Option<f64>,
}

/// Normalizes text to lowercase ASCII alphanumeric tokens without accents or punctuation.
///
/// # Examples
/// ```
/// use banido_ml::hallucination::normalize_for_hallucination;
///
/// assert_eq!(normalize_for_hallucination("Inscreva-se no canal, já!"), "inscreva se no canal ja");
/// ```
pub fn normalize_for_hallucination(text: &str) -> String {
    let lowered = text.to_lowercase();
    let stripped: String = lowered
        .nfkd()
        .filter(|c| !is_combining_mark(*c))
        .map(|c| {
            if c.is_ascii_alphanumeric() || c.is_whitespace() {
                c
            } else {
                ' '
            }
        })
        .collect();
    stripped.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn is_combining_mark(c: char) -> bool {
    matches!(c as u32, 0x0300..=0x036F | 0x1AB0..=0x1AFF | 0x1DC0..=0x1DFF | 0x20D0..=0x20FF | 0xFE20..=0xFE2F)
}

/// Returns `true` when `text` and its Whisper confidence metrics indicate a
/// phantom hallucination rather than real speech.
///
/// # Examples
/// ```
/// use banido_ml::hallucination::{is_whisper_hallucination, WhisperConfidence};
///
/// assert!(is_whisper_hallucination("Inscreva-se no canal, ative o sininho...", 2.0, WhisperConfidence::default()));
/// assert!(!is_whisper_hallucination("Sim, bora jogar mais uma partida", 2.0, WhisperConfidence::default()));
/// ```
pub fn is_whisper_hallucination(text: &str, seg_dur: f64, metrics: WhisperConfidence) -> bool {
    let low = text.trim().to_lowercase();
    if low.is_empty() {
        return true;
    }
    let norm = normalize_for_hallucination(&low);
    if norm.is_empty() {
        return true;
    }

    if HALLUCINATION_SUBSTRINGS
        .iter()
        .any(|sub| low.contains(sub) || norm.contains(sub))
    {
        return true;
    }

    let toks: Vec<&str> = norm.split_whitespace().collect();
    let padded = format!(" {norm} ");
    if toks.len() <= 10
        && HALLUCINATION_PHRASES
            .iter()
            .any(|ph| padded.contains(&format!(" {ph} ")))
    {
        return true;
    }

    if let (Some(nsp), Some(lp)) = (metrics.no_speech_prob, metrics.avg_logprob) {
        if nsp > 0.6 && lp < -1.0 {
            return true;
        }
    }

    if let Some(cr) = metrics.compression_ratio {
        if cr > 3.0 {
            return true;
        }
        if cr > 2.4 {
            let low_conf = metrics.avg_logprob.is_some_and(|lp| lp < -0.5)
                || metrics.no_speech_prob.is_some_and(|nsp| nsp > 0.35)
                || (metrics.avg_logprob.is_none() && metrics.no_speech_prob.is_none());
            if low_conf {
                return true;
            }
        }
    }

    if toks.len() >= 8 {
        let unique: HashSet<&str> = toks.iter().copied().collect();
        if unique.len() <= 2.max(toks.len() / 5) {
            return true;
        }
    }

    seg_dur <= 0.05
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hallucination_phrases_real_speech_and_laughter() {
        let def = WhisperConfidence::default();
        for phantom in [
            "Inscreva-se no canal, ative o sininho....",
            "Ative o sininho",
            "Deixe seu like",
            "Até o próximo vídeo!",
            "Legendas pela comunidade Amara.org",
            "",
        ] {
            assert!(is_whisper_hallucination(phantom, 2.0, def), "{phantom}");
        }
        assert!(!is_whisper_hallucination("Sim, bora jogar mais uma partida", 2.0, def));
        assert!(!is_whisper_hallucination("Não", 0.6, def));
        assert!(is_whisper_hallucination(&"e aí ".repeat(10), 5.0, def));

        // High-confidence laughter preserved; low-confidence rejected
        let laugh_hi = WhisperConfidence {
            no_speech_prob: Some(0.05),
            avg_logprob: Some(-0.2),
            compression_ratio: Some(2.6),
        };
        let laugh_lo = WhisperConfidence {
            no_speech_prob: Some(0.5),
            avg_logprob: Some(-0.8),
            compression_ratio: Some(2.6),
        };
        assert!(!is_whisper_hallucination("kkkkk corre corre", 1.8, laugh_hi));
        assert!(is_whisper_hallucination("kkkkk corre corre", 1.8, laugh_lo));
    }
}
