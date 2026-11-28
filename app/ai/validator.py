"""Validador de metadados do YouTube (B4): confere título, descrição, tags e capítulos antes de publicar.

Analogia: é a revisão ortográfica antes de imprimir o cartaz. Retorna avisos em linguagem simples;
só "erros" bloqueiam (título com mais de 100 caracteres e descrição com mais de 5.000).
"""
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.ai.youtube import YT_MAX_DESC_CHARS, YT_MAX_TAGS_CHARS, _parse_ts_to_seconds

YT_TITLE_MAX = 100
YT_TITLE_SOFT = 70
YT_DESC_VISIBLE = 150
YT_MIN_CHAPTERS = 3

# Tag que só vale se a gravação for mesmo 4K a 60 quadros por segundo.
TAG_4K60 = "4K 60fps"


def probe_video_format(video_path: Path) -> Dict[str, Any]:
    """Lê resolução e quadros por segundo com o ffprobe. Devolve {} se não conseguir."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-print_format", "json",
             "-show_entries", "stream=width,height,avg_frame_rate", str(video_path)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        st = (json.loads(res.stdout).get("streams") or [{}])[0]
        num, _, den = str(st.get("avg_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        return {"width": int(st.get("width") or 0), "height": int(st.get("height") or 0), "fps": round(fps, 2)}
    except Exception:
        return {}


def is_4k60(video_info: Optional[Dict[str, Any]]) -> bool:
    """True só quando o ffprobe confirmou pelo menos 3840 de largura e cerca de 60 quadros por segundo."""
    info = video_info or {}
    return int(info.get("width") or 0) >= 3840 and float(info.get("fps") or 0) >= 59.0


def _issue(level: str, field: str, message: str) -> Dict[str, str]:
    return {"level": level, "field": field, "message": message}


def validate_youtube_metadata(
    ai_data: Dict[str, Any],
    title: str = "",
    description: Optional[str] = None,
    tags_csv: Optional[str] = None,
) -> Dict[str, Any]:
    """Devolve {"ok": bool, "issues": [{level: 'erro'|'aviso', field, message}]}. 'ok' é falso se houver erro."""
    issues: List[Dict[str, str]] = []
    info = ai_data.get("video_info")

    title = (title or "").strip()
    if title:
        n = len(title)
        if n > YT_TITLE_MAX:
            issues.append(_issue("erro", "titulo", f"O título tem {n} caracteres; o YouTube aceita no máximo {YT_TITLE_MAX}."))
        elif n > YT_TITLE_SOFT:
            issues.append(_issue("aviso", "titulo", f"O título tem {n} caracteres; a busca costuma cortar depois de cerca de {YT_TITLE_SOFT}."))
        game = str(ai_data.get("main_game") or "").strip()
        if game and not title.lower().startswith(game.lower() + " -"):
            issues.append(_issue("aviso", "titulo", f"O título deveria começar com o jogo principal e um hífen (\"{game} - ...\")."))
        if "[" in title or "]" in title:
            issues.append(_issue("aviso", "titulo", "O título tem colchetes; o padrão do canal é não usar."))

    desc = description if description is not None else str(ai_data.get("youtube_description") or "")
    if desc:
        if len(desc) > YT_MAX_DESC_CHARS:
            issues.append(_issue("erro", "descricao", f"A descrição tem {len(desc)} caracteres; o limite do YouTube é {YT_MAX_DESC_CHARS}."))
        summary = re.sub(r"\s+", " ", str(ai_data.get("session_summary") or "")).strip()
        visible = re.sub(r"\s+", " ", desc[:YT_DESC_VISIBLE])
        if summary and summary[:30].lower() not in visible.lower():
            issues.append(_issue("aviso", "descricao", f"O resumo não aparece nos primeiros {YT_DESC_VISIBLE} caracteres, que são os que a busca mostra."))

    csv = tags_csv if tags_csv is not None else str(ai_data.get("youtube_tags_csv") or "")
    tags = [t.strip() for t in csv.split(",") if t.strip()]
    seen = set()
    repeated = []
    for t in tags:
        if t.lower() in seen:
            repeated.append(t)
        seen.add(t.lower())
    if repeated:
        issues.append(_issue("aviso", "tags", "Tags repetidas: " + ", ".join(sorted(set(repeated))) + "."))
    if TAG_4K60.lower() in seen and not is_4k60(info):
        issues.append(_issue("aviso", "tags", f"A tag \"{TAG_4K60}\" está presente, mas a gravação não foi confirmada como 4K 60fps."))
    if len(csv) > YT_MAX_TAGS_CHARS:
        issues.append(_issue("aviso", "tags", f"As tags somam {len(csv)} caracteres; o recomendado é até {YT_MAX_TAGS_CHARS}."))

    chapters = ai_data.get("chapters") or []
    secs = [_parse_ts_to_seconds(c.get("timestamp")) for c in chapters if isinstance(c, dict)]
    if not secs or secs[0] is None or secs[0] != 0:
        issues.append(_issue("erro", "capitulos", "O primeiro capítulo precisa começar em 00:00:00, senão o YouTube ignora todos."))
    if len(secs) < YT_MIN_CHAPTERS:
        issues.append(_issue("aviso", "capitulos", f"O YouTube só mostra capítulos com pelo menos {YT_MIN_CHAPTERS} marcações."))
    valid = [s for s in secs if s is not None]
    if any(b - a < 10 for a, b in zip(valid, valid[1:])):
        issues.append(_issue("erro", "capitulos", "Há capítulos com menos de 10 segundos entre si; o YouTube exige 10 segundos no mínimo."))

    return {"ok": not any(i["level"] == "erro" for i in issues), "issues": issues}
