"""B9: estilos de título e refazer um título só."""
import pytest
from app.ai.prompt import SYSTEM_PROMPT
from app.ai.schema import TEMPERATURE_CREATIVE
from app.ai.titles import finalize_title, regenerate_title, build_title_request, TITLE_STYLES

DADOS = {"main_game": "GTA V", "session_summary": "Noite caótica.", "chapters": [{"timestamp": "00:00:00", "title": "Início"}],
         "title_options": ["GTA V - A", "GTA V - B", "GTA V - C", "GTA V - D", "GTA V - E"]}


def test_prompt_pede_cinco_titulos_em_tons_diferentes():
    for palavra in ("ENGRAÇADO", "CURIOSO", "DIRETO", "LIVRES", "EXATAMENTE 5"):
        assert palavra in SYSTEM_PROMPT
    assert len(TITLE_STYLES) == 5


def test_finalize_garante_jogo_hifen_sem_colchetes_e_limite():
    assert finalize_title('"Perseguição absurda"', "GTA V") == "GTA V - Perseguição absurda"
    assert finalize_title("[GTA V] Algo", "GTA V") == "GTA V - Algo"
    assert finalize_title("GTA V - Já certo\nexplicação", "GTA V") == "GTA V - Já certo"
    longo = finalize_title("palavra " * 40, "GTA V")
    assert len(longo) <= 100 and longo.startswith("GTA V - ")
    assert finalize_title("", "GTA V") == ""


def test_regenera_com_temperatura_criativa_e_pedido_certo():
    chamadas = []

    def gerar(system, user, temp):
        chamadas.append((system, user, temp))
        return "Curto demais"

    novo = regenerate_title(dict(DADOS), 2, "shorter", gerar)
    assert novo == "GTA V - Curto demais"
    assert chamadas[0][2] == TEMPERATURE_CREATIVE == 0.8
    assert "mais curto" in chamadas[0][1] and "GTA V - C" in chamadas[0][1]
    regenerate_title(dict(DADOS), 0, "other_tone", gerar)
    assert "Engraçado" in chamadas[1][1] and "tom diferente" in chamadas[1][1]


def test_erros_de_entrada():
    with pytest.raises(ValueError):
        regenerate_title(dict(DADOS), 9, "shorter", lambda *a: "x")
    with pytest.raises(ValueError):
        regenerate_title(dict(DADOS), 0, "xyz", lambda *a: "x")
    with pytest.raises(ValueError):
        regenerate_title(dict(DADOS), 0, "shorter", lambda *a: "   ")
    assert "GTA V" in build_title_request(DADOS, "GTA V - A", "shorter")
