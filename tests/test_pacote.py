"""Item E2: o pacote de publicação existe na interface e usa só o que o programa já gera."""
import re
from pathlib import Path

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"


def _html():
    return (ESTATICOS / "index.html").read_text(encoding="utf-8")


def test_aba_do_pacote_esta_ligada_ao_painel_e_a_etapa_publicar():
    html = _html()
    assert 'id="tab-btn-publicar" aria-controls="tab-publicar"' in html
    assert 'id="tab-publicar"' in html
    assert re.search(r'data-step="4" data-tab="tab-publicar"', html)


def test_cada_bloco_tem_botao_de_copiar_e_ha_copiar_tudo():
    html = _html()
    for chave in ("title", "desc", "tags", "chapters"):
        assert f'data-pack-copy="{chave}"' in html
    assert 'id="pack-copy-all"' in html


def test_script_carregado_depois_dos_scripts_do_painel_e_sem_envio_de_dados():
    html = _html()
    assert html.index("/static/js/settings.js") < html.index("/static/pacote.js")
    js = (ESTATICOS / "pacote.js").read_text(encoding="utf-8")
    assert "renderPublishPack" in js
    # publicação é manual: o pacote não chama a rede
    assert "fetch(" not in js and "XMLHttpRequest" not in js
    # ordem do YouTube Studio
    assert js.index("TÍTULO") < js.index("DESCRIÇÃO") < js.index("TAGS")


def test_videos_js_entrega_os_dados_ao_pacote():
    assert "renderPublishPack" in (ESTATICOS / "js" / "videos.js").read_text(encoding="utf-8")
