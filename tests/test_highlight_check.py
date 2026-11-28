"""B2: conferir destaques contra a fala."""
from app.ai.highlight_check import check_highlights, refine_highlight

SEGS = [
    {"start": 100.0, "end": 108.0, "text": "olha isso"},
    {"start": 108.5, "end": 120.0, "text": "não acredito"},
    {"start": 130.0, "end": 140.0, "text": "outra frase"},
    {"start": 300.0, "end": 310.0, "text": "longe"},
]


def _h(start, end):
    return {"start": start, "end": end, "start_time": "", "end_time": "", "score": 90, "title": "t", "reason": "r"}


def test_encaixa_inicio_na_frase_e_fim_no_fim_da_fala():
    r, why = refine_highlight(_h(104.0, 118.0), SEGS, [], 1000.0)
    assert why == ""
    assert r["start"] == 99.5          # início da frase (100) menos 0,5 s
    assert r["end"] == 121.0           # fim da fala (120) mais 1 s
    assert r["start_time"] == "00:01:40" and r["end_time"] == "00:02:01"
    assert "ajustado" in r["adjust_note"]


def test_descarta_sem_fala_e_sem_pico():
    r, why = refine_highlight(_h(200.0, 230.0), SEGS, [], 1000.0)
    assert r is None and "sem fala" in why


def test_mantem_trecho_sem_fala_com_pico_de_risada():
    hype = [{"start": 200.0, "end": 205.0, "score": 90}]
    r, _ = refine_highlight(_h(200.0, 230.0), SEGS, hype, 1000.0)
    assert r is not None


def test_ajuste_maior_que_10s_marca_para_revisao_sem_mexer_na_ponta():
    segs = [{"start": 150.0, "end": 190.0, "text": "x"}]
    r, _ = refine_highlight(_h(120.0, 160.0), segs, [{"start": 120, "end": 125, "score": 80}], 1000.0)
    assert r["needs_review"] and r["start"] == 120.0


def test_duracao_fora_de_15_a_180_marca_revisao():
    r, _ = refine_highlight(_h(100.0, 105.0), SEGS, [], 1000.0)
    assert r["needs_review"] and "menor" in r["adjust_note"]


def test_fim_respeita_duracao_do_video():
    segs = [{"start": 90.0, "end": 100.0, "text": "x"}]
    r, _ = refine_highlight(_h(90.0, 100.0), segs, [], 100.5)
    assert r["end"] == 100.5


def test_check_highlights_em_lote_e_sem_segmentos():
    d = {"segments": SEGS, "hype_scores": [], "highlights": [_h(104, 118), _h(200, 230)]}
    log = check_highlights(d, 1000.0)
    assert len(d["highlights"]) == 1 and any("Descartado" in x for x in log)
    vazio = {"highlights": [_h(1, 20)]}
    assert check_highlights(vazio, 100.0) == [] and len(vazio["highlights"]) == 1
