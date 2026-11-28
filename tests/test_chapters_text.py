"""C5: capítulos em texto para colar na descrição."""
from app.ai.youtube import build_chapters_text, build_youtube_description


def test_primeiro_capitulo_em_zero_e_formato():
    txt = build_chapters_text([
        {"timestamp": "00:05:00", "title": "Fase 2"},
        {"timestamp": "00:20:10", "title": "Chefe"},
    ])
    assert txt.splitlines() == ["00:00:00 - Início da Sessão", "00:05:00 - Fase 2", "00:20:10 - Chefe"]


def test_intervalo_minimo_de_10_segundos():
    txt = build_chapters_text([
        {"timestamp": "00:00:00", "title": "A"},
        {"timestamp": "00:00:05", "title": "B"},
        {"timestamp": "00:01:00", "title": "C"},
    ])
    assert txt.splitlines() == ["00:00:00 - A", "00:01:00 - C"]


def test_mesmas_linhas_da_descricao_completa():
    caps = [{"timestamp": "00:00:00", "title": "A"}, {"timestamp": "00:10:00", "title": "B"},
            {"timestamp": "00:30:00", "title": "C"}]
    desc = build_youtube_description({"main_game": "Jogo", "session_summary": "x", "chapters": caps})
    for linha in build_chapters_text(caps).splitlines():
        assert linha in desc


def test_sem_capitulos_devolve_inicio():
    assert build_chapters_text([]) == "00:00:00 - Início da Sessão"
