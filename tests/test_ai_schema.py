"""B7: esquema da resposta da IA, temperatura e correção única."""
import pytest
from app.ai import schema
from app.ai.parsing import parse_ai_json_response, call_with_schema_retry
from app.ai.schema import AISchemaError, validate_ai_schema, get_structure_temperature

VALIDO = {
    "main_game": "GTA V", "session_summary": "Noite boa.", "title_options": ["GTA V - Algo"],
    "chapters": [{"timestamp": "00:00:00", "title": "Início"}], "topics": [],
    "highlights": [{"start": 10, "end": 40, "title": "x"}], "speaker_mapping": {},
}


def test_resposta_valida_nao_tem_problemas():
    assert validate_ai_schema(VALIDO) == []


def test_detecta_campos_faltando_e_tipos_errados():
    ruim = dict(VALIDO, main_game="", chapters=[{"timestamp": "00:00:00"}], highlights=[{"start": 1}])
    problemas = " ".join(validate_ai_schema(ruim))
    assert "main_game" in problemas and "chapters" in problemas and "highlights" in problemas
    assert validate_ai_schema([1]) != []


def test_parse_estrito_levanta_e_parse_tolerante_aceita():
    import json
    texto = json.dumps({"main_game": "X"})
    with pytest.raises(AISchemaError) as e:
        parse_ai_json_response(texto, strict=True)
    assert e.value.problems
    assert parse_ai_json_response(texto)["main_game"] == "X"


def test_correcao_acontece_uma_vez_e_usa_a_nota():
    notas = []

    def chamada(nota):
        notas.append(nota)
        if nota is None:
            raise AISchemaError(["'chapters' ausente"], raw_text="{}")
        return {"ok": True}

    assert call_with_schema_retry(chamada) == {"ok": True}
    assert notas[0] is None and "chapters" in notas[1]


def test_se_a_correcao_falhar_usa_leitura_tolerante():
    import json

    def chamada(nota):
        raise AISchemaError(["x"], raw_text=json.dumps({"main_game": "Z"}), duration_sec=100.0)

    assert call_with_schema_retry(chamada)["main_game"] == "Z"


def test_temperatura_padrao_e_override(monkeypatch):
    monkeypatch.delenv(schema.ENV_TEMPERATURE, raising=False)
    assert get_structure_temperature() == 0.2
    monkeypatch.setenv(schema.ENV_TEMPERATURE, "0,4")
    assert get_structure_temperature() == 0.4
    monkeypatch.setenv(schema.ENV_TEMPERATURE, "abc")
    assert get_structure_temperature() == 0.2
    monkeypatch.setenv(schema.ENV_TEMPERATURE, "9")
    assert get_structure_temperature() == 0.2
