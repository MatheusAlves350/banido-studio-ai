"""Glossário do grupo (A5): apelidos, gírias e nomes de jogos que o Whisper costuma errar.

O arquivo é um texto simples, um termo por linha, em DATABASE_DIR/glossario.txt.
Quem preferir pode editar o arquivo à mão; a interface usa as mesmas funções.
"""
from pathlib import Path
from typing import Iterable, List, Optional

from app.config import DATABASE_DIR

GLOSSARY_PATH = DATABASE_DIR / "glossario.txt"
MAX_TERMS = 200
MAX_TERM_LEN = 40


def normalize_terms(raw: Iterable[str]) -> List[str]:
    """Limpa espaços, tira linhas vazias e repetidas (sem diferenciar maiúsculas) e aplica os limites."""
    terms: List[str] = []
    seen = set()
    for item in raw:
        term = " ".join(str(item or "").replace(",", " ").split())[:MAX_TERM_LEN].strip()
        if not term or term.lower() in seen:
            continue
        seen.add(term.lower())
        terms.append(term)
        if len(terms) >= MAX_TERMS:
            break
    return terms


def parse_glossary_text(text: str) -> List[str]:
    return normalize_terms(str(text or "").splitlines())


def load_glossary(path: Optional[Path] = None) -> List[str]:
    path = path or GLOSSARY_PATH
    try:
        return parse_glossary_text(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return []


def save_glossary(terms: Iterable[str], path: Optional[Path] = None) -> List[str]:
    path = path or GLOSSARY_PATH
    clean = normalize_terms(terms)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(clean) + ("\n" if clean else ""), encoding="utf-8")
    tmp.replace(path)
    return clean
