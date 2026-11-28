"""Garante que a interface continua carregando os scripts na ordem certa (issue #14, etapa 4).

O antigo app.js foi dividido em app/static/js/*.js. Sao scripts classicos (sem
`type="module"`), que compartilham o mesmo escopo global, como antes.
"""
import re
from pathlib import Path

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"
ORDEM = ["api", "state", "utils", "events", "status", "videos",
         "transcript", "transcript_edit", "speakers", "highlights", "processing", "history", "settings", "glossary",
         "cut_batch", "clip_preview", "tokens", "validation", "titles", "mosaics"]


def _scripts_do_html():
    html = (ESTATICOS / "index.html").read_text(encoding="utf-8")
    return re.findall(r'<script src="/static/([^"?]+)', html)


def test_scripts_da_interface_na_ordem():
    scripts = _scripts_do_html()
    esperado = [f"js/{n}.js" for n in ORDEM]
    assert scripts[:len(esperado)] == esperado
    for outro in ("guide.js", "updater.js", "setup.js", "diagnostics.js"):
        assert scripts.index(outro) > scripts.index("js/mosaics.js")


def test_todos_os_scripts_existem_e_nao_sao_modulos():
    html = (ESTATICOS / "index.html").read_text(encoding="utf-8")
    assert 'type="module"' not in html
    for s in _scripts_do_html():
        assert (ESTATICOS / s).is_file(), s
    assert not (ESTATICOS / "app.js").exists()


def test_nenhuma_funcao_global_duplicada_entre_os_arquivos():
    vistos = {}
    for n in ORDEM:
        texto = (ESTATICOS / "js" / f"{n}.js").read_text(encoding="utf-8")
        for nome in re.findall(r"^(?:async )?function (\w+)", texto, flags=re.M):
            assert nome not in vistos, f"{nome} em {n}.js e {vistos[nome]}.js"
            vistos[nome] = n
