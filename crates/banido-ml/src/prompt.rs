//! Dynamic Whisper `initial_prompt` builder injecting known speaker names,
//! game title, and user session context within Whisper's ~224-token window.

use std::collections::HashSet;

/// Safe maximum character count (~224 PT-BR tokens for Whisper `initial_prompt`).
pub const MAX_WHISPER_PROMPT_CHARS: usize = 680;

/// Constructs a dynamic `initial_prompt` for Whisper containing the host name,
/// deduplicated known voice bank friends, game title, and extra context.
///
/// # Examples
/// ```
/// use banido_ml::prompt::build_whisper_initial_prompt;
///
/// let p = build_whisper_initial_prompt("Matheus", &["Carlos".into()], "CS2", "Mirage clutch");
/// assert!(p.contains("Matheus, Carlos"));
/// assert!(p.contains("Jogo: CS2."));
/// assert!(p.chars().count() <= 680);
/// ```
pub fn build_whisper_initial_prompt(
    host_name: &str,
    known_friends: &[String],
    manual_game: &str,
    extra_context: &str,
) -> String {
    let mut parts = vec![
        "Conversa informal, engraçada e descontraída entre amigos jogando no Discord.".to_string(),
    ];

    let mut seen = HashSet::new();
    let mut names = Vec::new();
    for raw in std::iter::once(host_name).chain(known_friends.iter().map(String::as_str)) {
        let clean = raw.trim();
        if !clean.is_empty() && seen.insert(clean.to_lowercase()) {
            names.push(clean.to_string());
        }
    }
    if !names.is_empty() {
        parts.push(format!("Participantes: {}.", names.join(", ")));
    }

    let game = manual_game.trim();
    if !game.is_empty() && !matches!(game.to_lowercase().as_str(), "gameplay" | "videogame") {
        parts.push(format!("Jogo: {game}."));
    }

    let ctx: String = extra_context
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    if !ctx.is_empty() {
        let truncated: String = ctx.chars().take(260).collect();
        parts.push(format!("Contexto e termos: {truncated}."));
    }

    parts
        .join(" ")
        .chars()
        .take(MAX_WHISPER_PROMPT_CHARS)
        .collect()
}
