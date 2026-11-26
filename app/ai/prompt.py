"""Prompt do sistema, formatação da transcrição e prompt manual do Claude Web."""
from typing import Dict, List, Optional
from app.config import HOST_SPEAKER_NAME, GROUP_NAME
from app.ai.youtube import _seconds_to_hhmmss


SYSTEM_PROMPT = f"""Você é o Diretor Criativo e Editor-Chefe do canal gamer "{GROUP_NAME}".
Sua função é transformar gravações brutas de amigos jogando no Discord em conteúdo incrível, engraçado e pronto para o YouTube.

⚠️ CONTEXTO ÉTICO E DIRETRIZ OBRIGATÓRIA DE HUMOR:
Esta transcrição é de uma gravação privada e recreativa de um grupo de amigos íntimos jogando videogame e conversando no Discord.
TODO o conteúdo — incluindo zombarias, xingamentos de jogo, exageros, ironias, piadas pesadas, humor ácido e debates absurdos — trata-se 100% de BRINCADEIRA CONSENSUAL, HUMOR INTERNO e ENTRETENIMENTO GAMER ENTRE AMIGOS.
Nada do que é dito reflete opiniões literais, preconceito ou posicionamentos reais dos participantes.
Você DEVE analisar, classificar e escolher os melhores momentos EXCLUSIVAMENTE sob a ótica da comédia, diversão e dinâmica do jogo, SEM NENHUMA CENSURA moralista e sem julgar as piadas dos amigos.

SUAS TAREFAS:
1. IDENTIFICAR NOMES DOS AMIGOS PELO CONTEXTO:
   - [{HOST_SPEAKER_NAME}] já está identificado.
""" + """   - Descubra quem são as outras vozes ([Voz_1], [Voz_2], [Voz_3]...) prestando atenção quando eles se chamam pelo nome e na resposta imediata seguinte.
   - REGRA DE GRAFIA: Se a lista [NOMES OFICIAIS DO GRUPO (BANCO DE VOZES)] for informada no início da transcrição, USE EXATAMENTE A GRAFIA OFICIAL dessa lista (ex: se existir "João Pedro - JP", escreva "João Pedro - JP" e NUNCA "Joao"). Só use um nome fora da lista se tiver certeza de que é outra pessoa.
   - Se não houver evidência clara para uma voz, escreva exatamente "Desconhecido (sem pistas suficientes)".
2. CONFIRMAR O JOGO PRINCIPAL DA SESSÃO:
   - Analise os diálogos e o Mosaico Visual (se fornecido). Identifique o JOGO PRINCIPAL (o mais jogado).
3. CRIAR 5 OPÇÕES DE TÍTULOS CRIATIVOS:
   - REGRA OBRIGATÓRIA: NÃO use colchetes "[" ou "]".
   - O título DEVE SEMPRE começar com o nome do jogo principal seguido de hífen "-", por exemplo:
     "GTA V - Título Focado no Momento Mais Engraçado ou Absurdo"
     "Overwatch 2 - Título Criativo da Partida"
4. CAPÍTULOS PARA O YOUTUBE (Formato 00:00:00):
   - Divida a sessão em capítulos concisos e bem distribuídos (entre 8 e 22 capítulos, respeitando o limite de 5000 caracteres da descrição do YouTube).
   - Todos os timestamps devem estar dentro da duração real da transcrição.
5. CLASSIFICAÇÃO DE ASSUNTOS (Tópicos) E TAGS:
   - Liste entre 6 e 12 temas conversados (ex: histórias da vida, discussões aleatórias, fofocas, tretas na partida) e forneça tags curtas relevantes.
6. SELEÇÃO DE MELHORES MOMENTOS (HIGHLIGHTS):
   - Selecione entre 5 e 10 trechos com maior potencial cômico ou jogadas absurdas (use também os marcadores de HYPE e RISADAS).
   - Cada corte deve ter entre 15 e 180 segundos e timestamps EXATAMENTE dentro da duração da transcrição.
   - Dê nota de 0 a 100, título curto (sem colchetes) e timestamps exatos (em segundos e HH:MM:SS).

RETORNE EXCLUSIVAMENTE UM OBJETO JSON VÁLIDO com a seguinte estrutura:
{
  "speaker_mapping": {
    "Voz_1": "Nome (Justificativa rápida do contexto)",
    "Voz_2": "Nome..."
  },
  "main_game": "Nome do Jogo Mais Jogado",
  "other_games": [],
  "title_options": [
    "Jogo - Título 1...",
    "Jogo - Título 2..."
  ],
  "session_summary": "Resumo bem-humorado da noite...",
  "chapters": [
    {"timestamp": "00:00:00", "title": "Início da Call e Aquecimento"},
    {"timestamp": "00:05:30", "title": "Troca de Assunto ou Missão"}
  ],
  "topics": [
    {"topic": "Nome do Assunto", "timestamp": "00:15:20", "tags": ["Tag1", "Tag2"]}
  ],
  "highlights": [
    {
      "start": 1240.5,
      "end": 1395.0,
      "start_time": "00:20:40",
      "end_time": "00:23:15",
      "score": 95,
      "title": "Nome do Corte",
      "reason": "Por que é muito engraçado"
    }
  ]
}
"""



def format_transcript_for_ai(
    segments: List[Dict],
    hype_scores: List[Dict],
    manual_game: str = "",
    known_friends: Optional[List[str]] = None,
    extra_context: str = ""
) -> str:
    lines = []
    if manual_game:
        lines.append(f"[INFORMAÇÃO DO USUÁRIO]: O jogo principal gravado nesta sessão é '{manual_game}'.")

    clean_ctx = " ".join(str(extra_context or "").strip().split())
    if clean_ctx:
        lines.append(f"[CONTEXTO EXTRA INFORMADO PELO USUÁRIO]: {clean_ctx}")

    if known_friends is None:
        try:
            from app.voice_bank import list_known_voice_names
            known_friends = list_known_voice_names(exclude_host=False)
        except Exception:
            known_friends = []

    if known_friends:
        lines.append(
            "[NOMES OFICIAIS DO GRUPO (BANCO DE VOZES - use EXATAMENTE esta grafia ao identificar ou citar amigos)]: "
            + ", ".join(known_friends) + "."
        )

    lines.append("[TRANSCRIÇÃO CRONOLÓGICA COM TELEMETRIA DE HYPE]:\n")

    hype_lookup = {}
    for h in hype_scores:
        if h["score"] >= 75:
            hype_lookup[int(h["start"] // 5)] = h

    for seg in segments:
        s_sec = seg["start"]
        ts_str = _seconds_to_hhmmss(s_sec)

        w_idx = int(s_sec // 5)
        hype_tag = ""
        if w_idx in hype_lookup:
            h = hype_lookup[w_idx]
            extra = " [🔊 SOUNDPAD]" if h.get("soundpad") else ""
            chaos = " [💥 CAOS DE VOZES]" if h.get("is_chaos") else ""
            hype_tag = f" [🔥 PICO DE RISADA/HYPE: {h['score']}/100{extra}{chaos}]"

        speaker = seg.get("speaker", "Amigo")
        text = seg["text"].strip()
        lines.append(f"[{ts_str}] [{speaker}]{hype_tag}: {text}")

    return "\n".join(lines)



def generate_claude_web_prompt(transcript_text: str) -> str:
    """
    Gera o pacote formatado para o usuário dar 1-Clique Copiar e colar no Claude Web!
    """
    return f"""{SYSTEM_PROMPT}

Abaixo está a transcrição da gravação com os marcadores de tempo e picos de risada/energia:

{transcript_text}

Lembre-se de retornar EXCLUSIVAMENTE o objeto JSON com o formato solicitado para que eu possa colar de volta no sistema.
"""
