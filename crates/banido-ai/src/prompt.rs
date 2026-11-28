//! AI Director system prompt, hype-annotated transcript formatting, and
//! Claude Web prompt package generator.

use crate::youtube::format_hhmmss;
use std::collections::HashMap;

/// System prompt instructing Gemini / NVIDIA NIM / Claude to return structured JSON.
pub const SYSTEM_PROMPT: &str = "Você é o Diretor Criativo do Banido Studio AI. Analise a transcrição e os picos de hype/risada de uma sessão de gameplay entre amigos no Discord e devolva ESTRITAMENTE um objeto JSON válido contendo: speaker_mapping, main_game, other_games, title_options (5 títulos no padrão 'Jogo - Título', sem colchetes []), session_summary, chapters (8 a 22 capítulos HH:MM:SS começando em 00:00:00), topics (topic, timestamp, tags) e highlights (start, end, score 0-100, title, reason).";

/// Minimal segment view for AI prompt formatting.
#[derive(Debug, Clone)]
pub struct PromptSegment {
    /// Start time in seconds.
    pub start: f64,
    /// Speaker label.
    pub speaker: String,
    /// Spoken text.
    pub text: String,
}

/// Minimal hype bucket view for AI prompt formatting.
#[derive(Debug, Clone)]
pub struct PromptHype {
    /// Bucket start time in seconds.
    pub start: f64,
    /// Hype score `0..=100`.
    pub score: i32,
    /// Soundpad active flag.
    pub soundpad: bool,
    /// Simultaneous chaos flag.
    pub is_chaos: bool,
}

/// Formats transcript segments with 5-second hype annotations (`score >= 75`).
///
/// Fixes `app/ai/prompt.py:105-108` by retaining the maximum hype score per
/// 5-second bucket instead of blindly overwriting earlier entries.
pub fn format_transcript_for_ai(
    segments: &[PromptSegment],
    hype_scores: &[PromptHype],
    manual_game: &str,
    extra_context: &str,
    known_friends: &[String],
) -> String {
    let mut hype_map: HashMap<i64, &PromptHype> = HashMap::new();
    for h in hype_scores {
        let bucket = (h.start / 5.0).floor() as i64;
        hype_map
            .entry(bucket)
            .and_modify(|cur| {
                if h.score > cur.score {
                    *cur = h;
                }
            })
            .or_insert(h);
    }

    let mut lines = Vec::with_capacity(segments.len() + 4);
    if !manual_game.trim().is_empty() {
        lines.push(format!("JOGO INFORMADO: {}", manual_game.trim()));
    }
    if !extra_context.trim().is_empty() {
        lines.push(format!("CONTEXTO EXTRA: {}", extra_context.trim()));
    }
    if !known_friends.is_empty() {
        lines.push(format!("AMIGOS CONHECIDOS: {}", known_friends.join(", ")));
    }

    for seg in segments {
        let ts = format_hhmmss(seg.start);
        let bucket = (seg.start / 5.0).floor() as i64;
        let hype_badge = match hype_map.get(&bucket) {
            Some(h) if h.score >= 75 => {
                let mut extras = Vec::new();
                if h.is_chaos {
                    extras.push("CAOS");
                }
                if h.soundpad {
                    extras.push("SOUNDPAD");
                }
                if extras.is_empty() {
                    format!(" [🔥 HYPE: {}/100]", h.score)
                } else {
                    format!(" [🔥 HYPE: {}/100 {}]", h.score, extras.join("+"))
                }
            }
            _ => String::new(),
        };
        lines.push(format!("[{ts}] [{}]{hype_badge}: {}", seg.speaker, seg.text.trim()));
    }
    lines.join("\n")
}

/// Wraps [`SYSTEM_PROMPT`] and formatted transcript into a copy-pasteable Claude Web package.
pub fn generate_claude_web_prompt(formatted_transcript: &str) -> String {
    format!("{SYSTEM_PROMPT}\n\n=== TRANSCRIÇÃO DA SESSÃO ===\n{formatted_transcript}\n")
}
