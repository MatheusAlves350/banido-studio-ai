"""Correções manuais de texto da transcrição (A6).

As edições ficam em um arquivo separado por vídeo (database/transcript_edits/<id>.json),
com o texto original e a data. Assim dá para desfazer e as correções não se perdem se o
vídeo for processado de novo.
"""
import difflib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.config import DATABASE_DIR

EDITS_DIR = DATABASE_DIR / "transcript_edits"
MAX_TEXT_LEN = 1000
START_TOLERANCE_SEC = 0.5
_WORD_RE = re.compile(r"[\wÀ-ÿ'-]+", re.UNICODE)


def clean_text(text: str) -> str:
    return " ".join(str(text or "").split())[:MAX_TEXT_LEN]


def _edits_path(video_id: int, directory: Optional[Path] = None) -> Path:
    return (directory or EDITS_DIR) / f"{int(video_id)}.json"


def load_edits(video_id: int, directory: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_edits_path(video_id, directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    edits = data.get("edits") if isinstance(data, dict) else None
    return edits if isinstance(edits, dict) else {}


def save_edits(video_id: int, edits: Dict[str, Dict[str, Any]], directory: Optional[Path] = None) -> None:
    path = _edits_path(video_id, directory)
    if not edits:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"edits": edits}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def segment_matches(seg: Dict[str, Any], start: float) -> bool:
    try:
        return abs(float(seg.get("start", 0.0)) - float(start)) <= START_TOLERANCE_SEC
    except (TypeError, ValueError):
        return False


def apply_edits(segments: List[Dict[str, Any]], edits: Dict[str, Dict[str, Any]]) -> int:
    """Reaplica as correções salvas depois de uma nova transcrição. Retorna quantas foram aplicadas.

    Cada correção procura a fala com o mesmo instante de início (tolerância de meio segundo)."""
    applied = 0
    for edit in edits.values():
        for seg in segments:
            if segment_matches(seg, edit.get("start", -1)):
                seg["text"] = edit.get("text", seg.get("text", ""))
                applied += 1
                break
    return applied


def _words(text: str) -> List[str]:
    return _WORD_RE.findall(text or "")


def corrected_words(original: str, new: str) -> List[str]:
    """Palavras que entraram no lugar de outras (ignora trechos só apagados ou só incluídos)."""
    a, b = _words(original), _words(new)
    out: List[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=[w.lower() for w in a], b=[w.lower() for w in b]).get_opcodes():
        if tag == "replace":
            out.extend(b[j1:j2])
    return out


def suggest_glossary_terms(edits: Dict[str, Dict[str, Any]], glossary: Iterable[str], min_count: int = 2) -> List[str]:
    """Palavras corrigidas em pelo menos `min_count` falas e que ainda não estão no glossário."""
    known = {g.lower() for g in glossary}
    counts: Dict[str, int] = {}
    shown: Dict[str, str] = {}
    for edit in edits.values():
        for w in set(corrected_words(edit.get("original", ""), edit.get("text", ""))):
            if len(w) < 3:
                continue
            counts[w.lower()] = counts.get(w.lower(), 0) + 1
            shown.setdefault(w.lower(), w)
    return [shown[k] for k, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n >= min_count and k not in known]
