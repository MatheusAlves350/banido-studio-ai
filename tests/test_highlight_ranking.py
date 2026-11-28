"""B3: ranking híbrido de destaques."""
from app.ai import ranking
from app.ai.ranking import rank_highlights, telemetry_score, get_weights

HYPE = [{"start": 100.0, "end": 105.0, "score": 90}, {"start": 105.0, "end": 110.0, "score": 40}, {"start": 500, "end": 505, "score": 20}]


def _d(score=80, start=100.0, end=120.0):
    return {"hype_scores": HYPE, "highlights": [{"start": start, "end": end, "score": score, "title": "t"}]}


def test_telemetria_pega_o_maior_pico_do_trecho():
    assert telemetry_score(100, 120, HYPE) == 90
    assert telemetry_score(300, 320, HYPE) == 0


def test_nota_final_60_40_guarda_as_duas_notas(monkeypatch):
    monkeypatch.delenv(ranking.ENV_WEIGHT_AI, raising=False)
    monkeypatch.delenv(ranking.ENV_WEIGHT_TEL, raising=False)
    d = _d(80)
    rank_highlights(d)
    h = d["highlights"][0]
    assert (h["score_ai"], h["score_hype"]) == (80, 90)
    assert h["score"] == round(0.6 * 80 + 0.4 * 90)  # 84, com pico: sem penalidade


def test_sem_pico_leva_penalidade_leve_mas_nao_sai():
    d = _d(80, start=300.0, end=320.0)
    rank_highlights(d)
    assert d["highlights"][0]["score"] == round(0.6 * 80 + 0.4 * 0 - 5)  # 43


def test_nao_recalcula_em_cima_da_nota_final():
    d = _d(80)
    rank_highlights(d)
    primeira = d["highlights"][0]["score"]
    rank_highlights(d)
    assert d["highlights"][0]["score"] == primeira and d["highlights"][0]["score_ai"] == 80


def test_pesos_configuraveis_e_invalidos(monkeypatch):
    monkeypatch.setenv(ranking.ENV_WEIGHT_AI, "100")
    monkeypatch.setenv(ranking.ENV_WEIGHT_TEL, "0")
    assert get_weights() == (1.0, 0.0)
    monkeypatch.setenv(ranking.ENV_WEIGHT_AI, "0")
    assert get_weights() == (0.6, 0.4)
    monkeypatch.setenv(ranking.ENV_WEIGHT_AI, "abc")
    monkeypatch.setenv(ranking.ENV_WEIGHT_TEL, "50")
    assert round(get_weights()[1], 3) == round(50 / 110, 3)


def test_sem_telemetria_nao_mexe():
    d = {"hype_scores": [], "highlights": [{"start": 1, "end": 20, "score": 70}]}
    assert rank_highlights(d) == [] and d["highlights"][0]["score"] == 70
