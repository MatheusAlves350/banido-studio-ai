import json
import re
import time
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from app.config import PC_SPECS, OBS_SPECS, AI_SPECS, HOST_SPEAKER_NAME, GROUP_NAME

from app.model_catalog import (  # fonte única da lista de modelos
    DEFAULT_GOOGLE_MODEL, DEFAULT_NVIDIA_MODEL, get_fallback_models,
)

YT_MAX_DESC_CHARS = 5000
YT_MAX_TAGS_CHARS = 480

GENERIC_SPEAKER_LABELS = {
    "discord", "amigo", "amiga", "desconhecido", "desconhecida",
    "indefinido", "indefinida", "outro", "outra", "participante",
    "voz", "ninguém", "ninguem", ""
}

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


def sanitize_video_title(title: str) -> str:
    """
    Substitui colchetes [] por '-' conforme padrão aceito no YouTube.
    Ex: '[GTA V] O Golpe' -> 'GTA V - O Golpe'
    """
    txt = str(title or "").strip()
    if not txt:
        return ""
    # Caso clássico de prefixo "[Jogo] Resto" -> "Jogo - Resto"
    txt = re.sub(r"^\[\s*([^\[\]]+?)\s*\]\s*[-–—:]?\s*", r"\1 - ", txt)
    # Qualquer outro colchete restante no meio do título -> " - "
    txt = re.sub(r"\[\s*([^\[\]]+?)\s*\]", r"\1", txt)
    txt = txt.replace("[", "-").replace("]", "-")
    txt = re.sub(r"\s*-\s*-\s*", " - ", txt)
    txt = re.sub(r"\s{2,}", " ", txt).strip(" -")
    return txt


def _parse_ts_to_seconds(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    txt = value.strip()
    if not txt:
        return None
    try:
        return float(txt)
    except ValueError:
        pass
    parts = txt.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return None
    return None


def _seconds_to_hhmmss(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    hrs = s // 3600
    mins = (s % 3600) // 60
    rem = s % 60
    return f"{hrs:02d}:{mins:02d}:{rem:02d}"


def extract_video_participants(ai_data: Dict[str, Any]) -> List[str]:
    """
    Extrai a lista de integrantes do vídeo presumidos pela detecção de áudio
    (segmentos + mapeamento confirmado/deduzido), sempre colocando o anfitrião primeiro.
    """
    found: List[str] = []
    seen_lower = set()

    def _add(name: str):
        clean = str(name or "").strip()
        low = clean.lower()
        if not clean or low in GENERIC_SPEAKER_LABELS:
            return
        if re.match(r"^voz[_\s]*\d+$", low):
            return
        if low.startswith("desconhecid") or low.startswith("não ") or low.startswith("nao "):
            return
        if low not in seen_lower:
            seen_lower.add(low)
            found.append(clean)

    # 1. Nomes presentes nos segmentos de fala
    host_low = HOST_SPEAKER_NAME.strip().lower()
    has_host = False
    for seg in ai_data.get("segments") or []:
        spk = str(seg.get("speaker") or "").strip()
        if spk.lower() == host_low:
            has_host = True
        else:
            _add(spk)

    # 2. Nomes no speaker_mapping (caso algum tenha sido confirmado ou deduzido)
    mapping = ai_data.get("speaker_mapping") or {}
    if isinstance(mapping, dict):
        for _, info in mapping.items():
            raw = str(info.get("name") if isinstance(info, dict) else info or "").strip()
            head = re.split(r"[\(\-\—\:\[]", raw, maxsplit=1)[0].strip()
            if head and not any(head.lower().startswith(bad) for bad in ("desconhecid", "provavelmente", "possivelmente", "talvez", "não", "nao", "sem ", "indefinid")):
                _add(head)

    if has_host or host_low not in seen_lower:
        # Anfitrião da gravação sempre listado primeiro
        found = [n for n in found if n.lower() != host_low]
        found.insert(0, HOST_SPEAKER_NAME)

    return found


def build_youtube_tags_csv(ai_data: Dict[str, Any], max_chars: int = YT_MAX_TAGS_CHARS) -> str:
    """
    Gera lista contínua de tags separadas por vírgula (sem '#'), pronta para colar
    direto no campo de Tags do YouTube Studio (respeitando limite de ~500 chars).
    """
    raw_tags: List[str] = []
    main_game = str(ai_data.get("main_game") or "").strip()
    if main_game and main_game.lower() not in ("gameplay", "videogame"):
        raw_tags.append(main_game)

    for og in ai_data.get("other_games") or []:
        og_str = str(og or "").strip()
        if og_str and og_str.lower() not in ("gameplay", "videogame"):
            raw_tags.append(og_str)

    for t in ai_data.get("topics") or []:
        if isinstance(t, dict):
            for tag in t.get("tags") or []:
                raw_tags.append(str(tag))

    raw_tags.extend(["Grupo Banido", "Discord", "Gameplay", "Melhores Momentos", "4K 60fps"])

    cleaned_tags: List[str] = []
    seen = set()
    for tag in raw_tags:
        clean = re.sub(r"^[#]+", "", str(tag or "").strip())
        clean = re.sub(r"[,\[\]]+", "", clean).strip()
        if not clean:
            continue
        key = clean.lower()
        if key not in seen:
            seen.add(key)
            cleaned_tags.append(clean)

    # Montar texto contínuo separado por ", " respeitando max_chars
    selected: List[str] = []
    cur_len = 0
    for tag in cleaned_tags:
        add_len = len(tag) + (2 if selected else 0)
        if cur_len + add_len > max_chars:
            break
        selected.append(tag)
        cur_len += add_len

    return ", ".join(selected)


def normalize_youtube_chapters(
    raw_chapters: Any,
    duration_sec: Optional[float] = None
) -> List[Dict[str, str]]:
    """
    Normaliza capítulos segundo as regras obrigatórias do YouTube:
    - Ordenação estritamente cronológica;
    - Sem timestamps após o fim do vídeo;
    - Espaçamento mínimo de 10s entre capítulos (exigência do YouTube);
    - Primeiro capítulo obrigatoriamente em 00:00:00 (sem isso o YouTube desativa todos os capítulos!).
    """
    parsed: List[tuple] = []
    for c in raw_chapters or []:
        if not isinstance(c, dict):
            continue
        ts_raw = str(c.get("timestamp") or c.get("time") or "00:00:00").strip()
        ts_sec = _parse_ts_to_seconds(ts_raw)
        if ts_sec is None or ts_sec < 0:
            continue
        if duration_sec and duration_sec > 0 and ts_sec > duration_sec + 5:
            continue
        title = sanitize_video_title(str(c.get("title") or c.get("name") or "Capítulo").strip()) or "Capítulo"
        parsed.append((int(round(ts_sec)), title))

    parsed.sort(key=lambda x: x[0])

    # Filtrar duplicados e garantir >= 10s entre capítulos consecutivos
    clean_entries: List[tuple] = []
    for sec_val, title in parsed:
        if not clean_entries:
            clean_entries.append((sec_val, title))
        else:
            prev_sec, _ = clean_entries[-1]
            if sec_val - prev_sec >= 10:
                clean_entries.append((sec_val, title))

    # Regra obrigatória do YouTube: primeiro capítulo DEVE começar em 00:00:00
    if not clean_entries:
        clean_entries = [(0, "Início da Sessão")]
    elif clean_entries[0][0] > 0:
        if clean_entries[0][0] < 10:
            clean_entries[0] = (0, clean_entries[0][1])
        else:
            clean_entries.insert(0, (0, "Início da Sessão"))

    return [
        {"timestamp": _seconds_to_hhmmss(sec_val), "title": title}
        for sec_val, title in clean_entries
    ]


def normalize_ai_data(data: Dict[str, Any], duration_sec: Optional[float] = None) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("A resposta JSON da IA não é um objeto válido.")

    main_game_low = str(data.get("main_game") or "").strip().lower()

    # Normalizar other_games (removendo vazios, duplicados e repetições de main_game)
    og = data.get("other_games", [])
    if isinstance(og, str):
        og_list = [x.strip() for x in og.split(",") if x.strip()]
    elif isinstance(og, list):
        og_list = [str(x or "").strip() for x in og if str(x or "").strip()]
    else:
        og_list = []

    clean_other_games = []
    seen_og = set()
    for g in og_list:
        gl = g.lower()
        if gl and gl != main_game_low and gl not in ("nenhum", "none", "n/a", "gameplay", "videogame") and gl not in seen_og:
            seen_og.add(gl)
            clean_other_games.append(g)
    data["other_games"] = clean_other_games

    # Normalizar title_options (trocando [] por -)
    raw_titles = data.get("title_options")
    if isinstance(raw_titles, list):
        data["title_options"] = [sanitize_video_title(t) for t in raw_titles if str(t or "").strip()]
    else:
        data["title_options"] = []

    # Normalizar chapters seguindo regras estritas do YouTube (ordem cronológica + 00:00:00 obrigatório)
    data["chapters"] = normalize_youtube_chapters(data.get("chapters") or [], duration_sec=duration_sec)

    # Normalizar topics
    norm_topics = []
    for t in data.get("topics") or []:
        if isinstance(t, dict):
            tags = t.get("tags") or []
            if isinstance(tags, str):
                tags = [x.strip() for x in re.split(r"[\s,]+", tags) if x.strip()]
            norm_topics.append({
                "topic": str(t.get("topic") or t.get("title") or t.get("name") or "").strip(),
                "timestamp": str(t.get("timestamp") or t.get("time") or "").strip(),
                "tags": [re.sub(r"^[#]+", "", str(tag).strip()) for tag in tags if str(tag).strip()]
            })
        elif isinstance(t, str) and t.strip():
            norm_topics.append({"topic": t.strip(), "timestamp": "", "tags": []})
    data["topics"] = [t for t in norm_topics if t["topic"]]

    # Normalizar highlights (garantir start/end dentro da duração real e ordem cronológica)
    norm_highlights = []
    for h in data.get("highlights") or []:
        if not isinstance(h, dict):
            continue
        start_val = _parse_ts_to_seconds(h.get("start"))
        if start_val is None:
            start_val = _parse_ts_to_seconds(h.get("start_time"))
        end_val = _parse_ts_to_seconds(h.get("end"))
        if end_val is None:
            end_val = _parse_ts_to_seconds(h.get("end_time"))

        start_sec = max(0.0, start_val if start_val is not None else 0.0)
        if duration_sec and duration_sec > 0 and start_sec >= duration_sec:
            # Descartar highlights alucinados após o fim do vídeo
            continue

        end_sec = end_val if (end_val is not None and end_val > start_sec) else (start_sec + 30.0)
        if (end_sec - start_sec) < 10.0:
            end_sec = start_sec + 15.0
        if duration_sec and duration_sec > 0:
            end_sec = min(end_sec, duration_sec)
            if end_sec <= start_sec:
                continue

        try:
            score = int(round(float(h.get("score", 85))))
        except (ValueError, TypeError):
            score = 85
        score = max(0, min(100, score))

        norm_highlights.append({
            "start": round(start_sec, 2),
            "end": round(end_sec, 2),
            "start_time": _seconds_to_hhmmss(start_sec),
            "end_time": _seconds_to_hhmmss(end_sec),
            "score": score,
            "title": sanitize_video_title(str(h.get("title") or "Melhor Momento").strip()),
            "reason": str(h.get("reason") or "").strip()
        })

    norm_highlights.sort(key=lambda x: x["start"])
    data["highlights"] = norm_highlights

    if not isinstance(data.get("speaker_mapping"), dict):
        data["speaker_mapping"] = {}

    data["youtube_tags_csv"] = build_youtube_tags_csv(data)
    data["youtube_description"] = build_youtube_description(data)
    return data


def build_youtube_description(ai_data: Dict[str, Any], max_chars: int = YT_MAX_DESC_CHARS) -> str:
    summary = str(ai_data.get("session_summary") or "").strip()
    main_game = str(ai_data.get("main_game") or "Videogame").strip()
    raw_other_games = ai_data.get("other_games") or []
    other_games = [
        str(g).strip() for g in raw_other_games
        if str(g or "").strip() and str(g).strip().lower() != main_game.lower()
    ]

    participants = extract_video_participants(ai_data)
    if len(participants) <= 1:
        participants_text = f"{HOST_SPEAKER_NAME} e Amigos"
    else:
        participants_text = ", ".join(participants)

    norm_chaps = normalize_youtube_chapters(ai_data.get("chapters") or [])
    chapters_lines = [f"{c['timestamp']} - {c['title']}" for c in norm_chaps]

    topics_lines = []
    for t in ai_data.get("topics") or []:
        if isinstance(t, dict) and t.get("topic"):
            ts_prefix = f"{t['timestamp']} - " if t.get("timestamp") else ""
            topics_lines.append(f"• {ts_prefix}{t['topic']}")

    highlights_lines = []
    for h in ai_data.get("highlights") or []:
        if isinstance(h, dict):
            st = h.get("start_time") or _seconds_to_hhmmss(float(h.get("start") or 0))
            et = h.get("end_time") or _seconds_to_hhmmss(float(h.get("end") or 0))
            score = h.get("score", 90)
            title = h.get("title", "Melhor Momento")
            highlights_lines.append(f"• {st} a {et} - {title} (Hype: {score}/100)")

    tags_csv = build_youtube_tags_csv(ai_data)
    ai_data["youtube_tags_csv"] = tags_csv
    other_games_line = f"\n• Outros Jogos na Sessão: {', '.join(other_games)}" if other_games else ""

    def _assemble(ch_list: List[str], tp_list: List[str], hl_list: List[str]) -> str:
        chapters_text = "\n".join(ch_list) if ch_list else "00:00:00 - Início da Sessão"
        topics_block = ("\n💬 ASSUNTOS DA CALL:\n" + "\n".join(tp_list) + "\n") if tp_list else ""
        highlights_text = "\n".join(hl_list) if hl_list else "Confira os melhores momentos no vídeo!"
        tags_block = f"\n🏷️ TAGS:\n{tags_csv}" if tags_csv else ""

        return f"""🎮 {summary}

👥 INTEGRANTES:
{participants_text}

🕹️ JOGOS DESTA SESSÃO:
• Jogo Principal: {main_game}{other_games_line}

⏱️ CAPÍTULOS E ASSUNTOS:
{chapters_text}
{topics_block}
🏆 MELHORES MOMENTOS (HIGHLIGHTS DA SESSÃO):
{highlights_text}

⚙️ FICHA TÉCNICA E CONFIGURAÇÕES DE GRAVAÇÃO / IA
🖥️ Especificações do PC:
• Processador (CPU): {PC_SPECS['cpu']}
• Placa de Vídeo (GPU): {PC_SPECS['gpu']}
• Memória RAM: {PC_SPECS['ram']}
• Sistema Operacional: {PC_SPECS['os']}

🎥 Configurações de Gravação (OBS Studio - Alta Fidelidade):
• Resolução e Taxa de Quadros: {OBS_SPECS['resolution']}
• Encoder de Vídeo: {OBS_SPECS['encoder']}
• Controle de Taxa de Bits: {OBS_SPECS['rate_control']}
• Preset & Multipass: {OBS_SPECS['preset']}
• Espaço de Cor: {OBS_SPECS['color_format']}
• Áudio Multi-Track: {OBS_SPECS['audio']}

🤖 Pipeline Automatizado de Transcrição, Catalogação e Cortes (Banido Studio AI):
• Transcrição de Áudio (Speech-to-Text): {AI_SPECS['whisper']}
• Separação de Vozes e Diarização: {AI_SPECS['diarization']}
• Edição e Cortes: {AI_SPECS['cutting']}

⚠️ AVISO: Todo o conteúdo deste vídeo consiste em brincadeiras, ironias e humor interno entre amigos no Discord, sem refletir opiniões reais ou literais dos participantes.{tags_block}
"""

    ch_curr = list(chapters_lines)
    tp_curr = list(topics_lines)
    hl_curr = list(highlights_lines)

    desc = _assemble(ch_curr, tp_curr, hl_curr)

    # Garantir que respeita o limite de 5000 caracteres do YouTube sem cortar a ficha técnica/tags
    while len(desc) > max_chars and len(tp_curr) > 4:
        tp_curr.pop()
        desc = _assemble(ch_curr, tp_curr, hl_curr)

    while len(desc) > max_chars and len(hl_curr) > 5:
        hl_curr.pop()
        desc = _assemble(ch_curr, tp_curr, hl_curr)

    while len(desc) > max_chars and len(ch_curr) > 10:
        # Reduz capítulos mantendo espaçamento uniforme (sempre preservando o primeiro 00:00:00 e o último)
        step = 2
        reduced = [ch_curr[0]] + ch_curr[1:-1:step] + [ch_curr[-1]]
        if len(reduced) >= len(ch_curr):
            ch_curr.pop(-2)
        else:
            ch_curr = reduced
        desc = _assemble(ch_curr, tp_curr, hl_curr)

    if len(desc) > max_chars:
        desc = desc[:max_chars - 3].rstrip() + "..."

    return desc


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


def parse_ai_json_response(response_text: str, duration_sec: Optional[float] = None) -> Dict[str, Any]:
    if not response_text or not response_text.strip():
        raise ValueError("Resposta vazia recebida da IA.")

    # Remover blocos de raciocínio <think>...</think>
    cleaned = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL | re.IGNORECASE).strip()

    match = re.search(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", cleaned, re.DOTALL)
    if match:
        raw_json = match.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw_json = cleaned[start:end + 1]
        else:
            raw_json = cleaned

    parsed = json.loads(raw_json)
    if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        raise ValueError("O JSON retornado pela IA precisa ser um objeto { ... }.")

    return normalize_ai_data(parsed, duration_sec=duration_sec)


def parse_claude_web_response(claude_response_text: str, duration_sec: Optional[float] = None) -> Dict[str, Any]:
    return parse_ai_json_response(claude_response_text, duration_sec=duration_sec)


def _start_ai_heartbeat(
    tag: str,
    model_name: str,
    t0: float,
    state: Dict[str, Any],
    stop_event: threading.Event,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    interval_sec: float = 15.0
) -> threading.Thread:
    """
    Thread leve de acompanhamento: enquanto a API está processando o prompt (thinking / fila)
    ou se ficar mais de 15s entre pacotes, registra o tempo decorrido nos logs e na barra de progresso
    para que o usuário sempre saiba que o processo está vivo e aguardando resposta.
    """
    def _loop():
        while not stop_event.wait(interval_sec):
            elapsed = time.time() - t0
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            elapsed_str = f"{mins}m {secs:02d}s" if mins > 0 else f"{secs}s"
            json_chars = state.get("json_chars", 0)
            think_chars = state.get("think_chars", 0)
            last_chunk_t = state.get("last_chunk_t", 0.0)

            if json_chars == 0 and think_chars == 0:
                print(
                    f"[{tag}] ⏳ Aguardando primeiro pacote de '{model_name}'... "
                    f"({elapsed_str} decorridos | API processando transcrição/raciocínio)",
                    flush=True
                )
                if progress_callback:
                    pct = min(0.95, 0.92 + (elapsed / 600.0) * 0.03)
                    progress_callback(f"Aguardando resposta da IA ({model_name})... {elapsed_str}", pct)
            elif (time.time() - last_chunk_t) >= interval_sec:
                extra = f" | {think_chars:,} chars raciocínio" if think_chars > 0 else ""
                print(
                    f"[{tag}] ⏳ Stream ativo em '{model_name}'... "
                    f"({elapsed_str} | {json_chars:,} chars JSON{extra})",
                    flush=True
                )
                if progress_callback:
                    pct = min(0.98, 0.94 + min(0.04, json_chars / 20000.0))
                    progress_callback(
                        f"Recebendo resposta da IA ({model_name}): {json_chars:,} chars ({elapsed_str})",
                        pct
                    )

    th = threading.Thread(target=_loop, daemon=True)
    th.start()
    return th


def analyze_with_google_gemini_single(
    client,
    contents,
    safety_settings,
    model_name: str,
    duration_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> Dict[str, Any]:
    from google.genai import types

    t0 = time.time()
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        safety_settings=safety_settings,
        temperature=0.7
    )

    state: Dict[str, Any] = {"json_chars": 0, "think_chars": 0, "last_chunk_t": 0.0}
    stop_event = threading.Event()
    hb_thread = _start_ai_heartbeat(
        "IA GEMINI", model_name, t0, state, stop_event,
        progress_callback=progress_callback, interval_sec=15.0
    )

    text_parts: List[str] = []
    usage_meta = None
    first_packet_logged = False
    last_progress_log = 0.0

    try:
        try:
            stream = client.models.generate_content_stream(
                model=model_name,
                contents=contents,
                config=cfg
            )
            for chunk in stream:
                now_t = time.time()
                if getattr(chunk, "usage_metadata", None) is not None:
                    usage_meta = chunk.usage_metadata

                got_any = False
                candidates = getattr(chunk, "candidates", None) or []
                if candidates and getattr(candidates[0], "content", None) and getattr(candidates[0].content, "parts", None):
                    for part in candidates[0].content.parts:
                        p_txt = getattr(part, "text", None) or ""
                        if not p_txt:
                            continue
                        got_any = True
                        if getattr(part, "thought", False):
                            state["think_chars"] += len(p_txt)
                        else:
                            text_parts.append(p_txt)
                            state["json_chars"] += len(p_txt)
                else:
                    c_txt = ""
                    try:
                        c_txt = chunk.text or ""
                    except Exception:
                        c_txt = ""
                    if c_txt:
                        got_any = True
                        text_parts.append(c_txt)
                        state["json_chars"] += len(c_txt)

                if got_any:
                    state["last_chunk_t"] = now_t
                    if not first_packet_logged:
                        first_packet_logged = True
                        phase_lbl = "raciocínio interno" if (state["think_chars"] > 0 and state["json_chars"] == 0) else "resposta JSON"
                        print(
                            f"[IA GEMINI] ⚡ Primeiro pacote ({phase_lbl}) recebido de '{model_name}' em {now_t - t0:.1f}s! "
                            f"Recebendo em tempo real...",
                            flush=True
                        )
                        last_progress_log = now_t
                        if progress_callback:
                            progress_callback(f"IA ({model_name}) respondendo em tempo real...", 0.94)
                    elif (now_t - last_progress_log) >= 5.0:
                        last_progress_log = now_t
                        extra_think = f" + {state['think_chars']:,} chars raciocínio" if state["think_chars"] > 0 else ""
                        print(
                            f"[IA GEMINI] 📥 Recebendo de '{model_name}': "
                            f"{state['json_chars']:,} chars JSON{extra_think} ({now_t - t0:.0f}s)",
                            flush=True
                        )
                        if progress_callback:
                            pct = min(0.98, 0.94 + min(0.04, state["json_chars"] / 20000.0))
                            progress_callback(
                                f"Recebendo JSON da IA ({model_name}): {state['json_chars']:,} chars...",
                                pct
                            )
            raw_text = "".join(text_parts).strip()
        except AttributeError:
            # SDK sem generate_content_stream: fallback síncrono mantendo heartbeat ativo
            print(f"[IA GEMINI] Streaming indisponível nesta versão do SDK; usando chamada padrão em '{model_name}'...", flush=True)
            response = client.models.generate_content(model=model_name, contents=contents, config=cfg)
            usage_meta = getattr(response, "usage_metadata", None)
            raw_text = (response.text or "").strip()
    finally:
        stop_event.set()
        hb_thread.join(timeout=1.0)

    if not raw_text:
        raise RuntimeError(f"Resposta vazia retornada pelo modelo {model_name}.")

    elapsed = time.time() - t0
    token_info = ""
    if usage_meta is not None:
        p_tok = getattr(usage_meta, "prompt_token_count", None)
        c_tok = getattr(usage_meta, "candidates_token_count", None)
        t_tok = getattr(usage_meta, "thoughts_token_count", None)
        parts_tok = []
        if p_tok is not None:
            parts_tok.append(f"in={p_tok:,}")
        if t_tok:
            parts_tok.append(f"think={t_tok:,}")
        if c_tok is not None:
            parts_tok.append(f"out={c_tok:,}")
        if parts_tok:
            token_info = f" | Tokens: {' / '.join(parts_tok)}"

    print(
        f"[IA GEMINI] ✅ Stream finalizado de '{model_name}' em {elapsed:.1f}s ({len(raw_text):,} chars JSON{token_info}). Validando JSON...",
        flush=True
    )
    data = parse_ai_json_response(raw_text, duration_sec=duration_sec)
    print(
        f"[IA GEMINI] ✅ Análise concluída! "
        f"Jogo: {data.get('main_game')} | Capítulos: {len(data.get('chapters', []))} | "
        f"Assuntos: {len(data.get('topics', []))} | Highlights: {len(data.get('highlights', []))}",
        flush=True
    )
    return data


def _is_non_retryable_ai_error(err: Exception) -> bool:
    """Retorna True para erros definitivos (chave inválida, permissão negada, conta bloqueada)."""
    msg = str(err or "").lower()
    fatal_tokens = (
        "api_key_invalid", "invalid api key", "invalid_api_key",
        "401", "403", "unauthorized", "permission_denied", "permission denied",
        "authentication", "invalid x-api-key", "account suspended", "billing"
    )
    return any(tok in msg for tok in fatal_tokens)


def analyze_with_google_gemini(
    api_key: str,
    transcript_text: str,
    mosaic_jpg_path: Optional[Path] = None,
    model_name: str = DEFAULT_GOOGLE_MODEL,
    duration_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> Dict[str, Any]:
    from google import genai
    from google.genai import types
    from PIL import Image

    client = genai.Client(api_key=api_key)

    safety_settings = [
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    ]

    contents = []
    mosaic_info = "sem mosaico"
    if mosaic_jpg_path and mosaic_jpg_path.exists():
        try:
            mosaic_kb = mosaic_jpg_path.stat().st_size / 1024.0
            with Image.open(mosaic_jpg_path) as img:
                img.load()
                contents.append(img.copy())
            contents.append("Aqui está a folha de contato (mosaico 4x4 com 16 cenas e timestamps HH:MM:SS) para você identificar o jogo principal e eventuais trocas de jogo (other_games):")
            mosaic_info = f"mosaico {mosaic_jpg_path.name} ({mosaic_kb:.0f} KB)"
            print(f"[IA GEMINI] Mosaico visual 4x4 anexado ({mosaic_jpg_path.name}, {mosaic_kb:.0f} KB).", flush=True)
        except Exception as e:
            print(f"[IA GEMINI] Aviso: não foi possível carregar mosaico: {e}", flush=True)

    contents.append(transcript_text)
    est_tokens = max(1, len(transcript_text) // 4)
    print(
        f"[IA GEMINI] Enviando transcrição ({len(transcript_text):,} chars ≈ {est_tokens:,} tokens | {mosaic_info}) "
        f"para o modelo '{model_name}' (streaming + acompanhamento ao vivo)...",
        flush=True
    )
    if progress_callback:
        progress_callback(f"Enviando transcrição ({est_tokens:,} tokens) para {model_name}...", 0.92)

    try:
        return analyze_with_google_gemini_single(
            client, contents, safety_settings, model_name,
            duration_sec=duration_sec, progress_callback=progress_callback
        )
    except Exception as e:
        print(f"[IA GEMINI] ❌ Falha no modelo '{model_name}': {e}", flush=True)
        if _is_non_retryable_ai_error(e):
            print("[IA GEMINI] ⛔ Erro definitivo de autenticação/permissão; fallback cancelado imediatamente.", flush=True)
            raise e
        for fb in get_fallback_models("google"):
            if fb != model_name:
                print(f"[IA GEMINI] 🔄 Tentando fallback automático para '{fb}'...", flush=True)
                if progress_callback:
                    progress_callback(f"Tentando fallback automático Gemini: {fb}...", 0.92)
                try:
                    return analyze_with_google_gemini_single(
                        client, contents, safety_settings, fb,
                        duration_sec=duration_sec, progress_callback=progress_callback
                    )
                except Exception as fb_err:
                    print(f"[IA GEMINI] ❌ Fallback '{fb}' também falhou: {fb_err}", flush=True)
                    if _is_non_retryable_ai_error(fb_err):
                        raise fb_err
        raise e


def _call_nvidia_single(
    client,
    messages,
    model_name: str,
    duration_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> Dict[str, Any]:
    t0 = time.time()
    low = model_name.lower()
    is_reasoning_model = any(k in low for k in ("reasoning", "deepseek-r1", "kimi", "step-5", "nemotron"))

    state: Dict[str, Any] = {"json_chars": 0, "think_chars": 0, "last_chunk_t": 0.0}
    stop_event = threading.Event()
    hb_thread = _start_ai_heartbeat(
        "IA NVIDIA", model_name, t0, state, stop_event,
        progress_callback=progress_callback, interval_sec=15.0
    )

    def _invoke(use_json_format: bool, use_stream: bool):
        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.6 if is_reasoning_model else 0.7,
            "max_tokens": 8192 if is_reasoning_model else 4096,
            "stream": use_stream
        }
        if use_json_format:
            kwargs["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    text = ""
    try:
        try:
            if is_reasoning_model:
                stream = _invoke(use_json_format=False, use_stream=True)
            else:
                try:
                    stream = _invoke(use_json_format=True, use_stream=True)
                except Exception as fmt_err:
                    print(f"[IA NVIDIA] Aviso com response_format JSON ({fmt_err}); tentando modo padrão...", flush=True)
                    stream = _invoke(use_json_format=False, use_stream=True)

            text_parts: List[str] = []
            first_packet_logged = False
            last_progress_log = 0.0

            for chunk in stream:
                now_t = time.time()
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if not delta:
                    continue
                r_txt = getattr(delta, "reasoning_content", None) or ""
                c_txt = getattr(delta, "content", None) or ""
                if not r_txt and not c_txt:
                    continue

                if r_txt:
                    state["think_chars"] += len(r_txt)
                if c_txt:
                    text_parts.append(c_txt)
                    state["json_chars"] += len(c_txt)
                state["last_chunk_t"] = now_t

                if not first_packet_logged:
                    first_packet_logged = True
                    phase_lbl = "raciocínio" if (r_txt and not c_txt) else "resposta JSON"
                    print(
                        f"[IA NVIDIA] ⚡ Primeiro pacote ({phase_lbl}) recebido de '{model_name}' em {now_t - t0:.1f}s! "
                        f"Recebendo em tempo real...",
                        flush=True
                    )
                    last_progress_log = now_t
                    if progress_callback:
                        progress_callback(f"IA NVIDIA ({model_name}) respondendo em tempo real...", 0.94)
                elif (now_t - last_progress_log) >= 5.0:
                    last_progress_log = now_t
                    extra_think = f" + {state['think_chars']:,} chars raciocínio" if state["think_chars"] > 0 else ""
                    print(
                        f"[IA NVIDIA] 📥 Recebendo de '{model_name}': "
                        f"{state['json_chars']:,} chars JSON{extra_think} ({now_t - t0:.0f}s)",
                        flush=True
                    )
                    if progress_callback:
                        pct = min(0.98, 0.94 + min(0.04, state["json_chars"] / 20000.0))
                        progress_callback(
                            f"Recebendo JSON da IA NVIDIA ({model_name}): {state['json_chars']:,} chars...",
                            pct
                        )

            text = "".join(text_parts).strip()
        except Exception as stream_err:
            if state["json_chars"] > 0 or state["think_chars"] > 0:
                raise stream_err
            print(f"[IA NVIDIA] Aviso ao abrir streaming ({stream_err}); tentando chamada padrão...", flush=True)
            response = _invoke(use_json_format=False, use_stream=False)
            text = (response.choices[0].message.content or "").strip()
    finally:
        stop_event.set()
        hb_thread.join(timeout=1.0)

    print(
        f"[IA NVIDIA] ✅ Stream finalizado de '{model_name}' em {time.time() - t0:.1f}s ({len(text):,} chars). Validando JSON...",
        flush=True
    )
    data = parse_ai_json_response(text, duration_sec=duration_sec)
    print(
        f"[IA NVIDIA] ✅ Análise concluída! "
        f"Jogo: {data.get('main_game')} | Capítulos: {len(data.get('chapters', []))} | "
        f"Assuntos: {len(data.get('topics', []))} | Highlights: {len(data.get('highlights', []))}",
        flush=True
    )
    return data


def analyze_with_nvidia_nim(
    api_key: str,
    transcript_text: str,
    model_name: str = DEFAULT_NVIDIA_MODEL,
    duration_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> Dict[str, Any]:
    from openai import OpenAI

    est_tokens = max(1, len(transcript_text) // 4)
    print(
        f"[IA NVIDIA] Enviando transcrição ({len(transcript_text):,} chars ≈ {est_tokens:,} tokens) "
        f"para '{model_name}' (streaming + acompanhamento ao vivo)...",
        flush=True
    )
    if progress_callback:
        progress_callback(f"Enviando transcrição ({est_tokens:,} tokens) para {model_name}...", 0.92)

    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key,
        timeout=300.0
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": transcript_text}
    ]

    try:
        return _call_nvidia_single(
            client, messages, model_name,
            duration_sec=duration_sec, progress_callback=progress_callback
        )
    except Exception as e:
        print(f"[IA NVIDIA] ❌ Falha no modelo '{model_name}': {e}", flush=True)
        if _is_non_retryable_ai_error(e):
            print("[IA NVIDIA] ⛔ Erro definitivo de autenticação/permissão; fallback cancelado imediatamente.", flush=True)
            raise e
        for fb in get_fallback_models("nvidia"):
            if fb != model_name:
                print(f"[IA NVIDIA] 🔄 Tentando fallback automático para '{fb}'...", flush=True)
                if progress_callback:
                    progress_callback(f"Tentando fallback automático NVIDIA: {fb}...", 0.92)
                try:
                    return _call_nvidia_single(
                        client, messages, fb,
                        duration_sec=duration_sec, progress_callback=progress_callback
                    )
                except Exception as fb_err:
                    print(f"[IA NVIDIA] ❌ Fallback '{fb}' também falhou: {fb_err}", flush=True)
                    if _is_non_retryable_ai_error(fb_err):
                        raise fb_err
        raise e


def generate_claude_web_prompt(transcript_text: str) -> str:
    """
    Gera o pacote formatado para o usuário dar 1-Clique Copiar e colar no Claude Web!
    """
    return f"""{SYSTEM_PROMPT}

Abaixo está a transcrição da gravação com os marcadores de tempo e picos de risada/energia:

{transcript_text}

Lembre-se de retornar EXCLUSIVAMENTE o objeto JSON com o formato solicitado para que eu possa colar de volta no sistema.
"""
