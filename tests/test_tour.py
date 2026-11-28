"""Item E8: tour inicial de 4 passos e dicas contextuais (verificação estática; o comportamento foi conferido no navegador)."""
import re
from pathlib import Path

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"


def _js():
    return (ESTATICOS / "tour.js").read_text(encoding="utf-8")


def test_script_carregado_por_ultimo_entre_os_do_painel():
    html = (ESTATICOS / "index.html").read_text(encoding="utf-8")
    assert html.index("/static/js/settings.js") < html.index("/static/tour.js")
    assert (ESTATICOS / "tour.js").is_file()


def test_tour_tem_quatro_passos_com_texto_curto():
    js = _js()
    bloco = js[js.index("passos: ["):js.index("botoes:")]
    assert len(re.findall(r"titulo:", bloco)) == 4
    for texto in re.findall(r'texto: "([^"]+)"', bloco):
        assert len(texto) <= 190, texto


def test_dicas_tem_botao_entendi_e_so_aparecem_uma_vez_por_tela():
    js = _js()
    assert '"Entendi"' in js
    assert "dicasVistas().includes(tela)" in js
    for tela in ("inicio", "processar", "vozes", "publicar"):
        assert re.search(rf"\b{tela}: \"", js)


def test_estado_fica_no_navegador_e_o_armazenamento_pode_falhar():
    js = _js()
    assert "localStorage" in js
    # leitura e gravação protegidas com try/catch (navegador pode bloquear o armazenamento)
    assert js.count("try {") >= 3
    assert "fetch(" not in js  # nada sai do navegador


def test_tem_botao_para_mostrar_o_tour_de_novo_e_nao_abre_sobre_janelas():
    js = _js()
    assert "Mostrar o tour de novo" in js
    assert "haModalAberto()" in js
