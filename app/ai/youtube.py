"""Montagem de título, descrição, tags e capítulos para o YouTube a partir da resposta da IA."""
import re
from typing import Dict, Any, List, Optional
from app.config import PC_SPECS, OBS_SPECS, AI_SPECS, HOST_SPEAKER_NAME


YT_MAX_DESC_CHARS = 5000
YT_MAX_TAGS_CHARS = 480

GENERIC_SPEAKER_LABELS = {
    "discord", "amigo", "amiga", "desconhecido", "desconhecida",
    "indefinido", "indefinida", "outro", "outra", "participante",
    "voz", "ninguém", "ninguem", ""
}

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
