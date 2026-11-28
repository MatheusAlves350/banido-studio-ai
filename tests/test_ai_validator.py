"""B4: validador de metadados do YouTube e tag 4K 60fps só quando confirmada."""
from app.ai.validator import validate_youtube_metadata, is_4k60
from app.ai.youtube import build_youtube_tags_csv, get_fixed_tags

BASE = {
    "main_game": "GTA V", "session_summary": "Noite de perseguições absurdas entre amigos.",
    "chapters": [{"timestamp": "00:00:00", "title": "a"}, {"timestamp": "00:05:00", "title": "b"}, {"timestamp": "00:10:00", "title": "c"}],
    "youtube_tags_csv": "GTA V, Discord", "youtube_description": "🎮 Noite de perseguições absurdas entre amigos.\n...",
}


def campos(r, nivel):
    return {i["field"] for i in r["issues"] if i["level"] == nivel}


def test_tudo_certo_nao_tem_avisos():
    r = validate_youtube_metadata(BASE, title="GTA V - Perseguição absurda")
    assert r["ok"] and r["issues"] == []


def test_titulo_longo_e_erro_e_acima_de_70_e_aviso():
    assert "titulo" in campos(validate_youtube_metadata(BASE, title="GTA V - " + "a" * 100), "erro")
    r = validate_youtube_metadata(BASE, title="GTA V - " + "a" * 70)
    assert r["ok"] and "titulo" in campos(r, "aviso")


def test_titulo_sem_jogo_e_com_colchetes():
    r = validate_youtube_metadata(BASE, title="[GTA V] Algo")
    msgs = " ".join(i["message"] for i in r["issues"])
    assert "jogo principal" in msgs and "colchetes" in msgs


def test_capitulos_primeiro_fora_do_zero_e_intervalo():
    ruim = dict(BASE, chapters=[{"timestamp": "00:00:05", "title": "a"}, {"timestamp": "00:00:08", "title": "b"}])
    r = validate_youtube_metadata(ruim)
    assert not r["ok"] and "capitulos" in campos(r, "erro") and "capitulos" in campos(r, "aviso")


def test_tags_repetidas_e_4k_nao_confirmado():
    d = dict(BASE, youtube_tags_csv="GTA V, gta v, 4K 60fps")
    r = validate_youtube_metadata(d)
    msgs = " ".join(i["message"] for i in r["issues"])
    assert "repetidas" in msgs and "4K 60fps" in msgs
    d["video_info"] = {"width": 3840, "height": 2160, "fps": 60.0}
    assert "4K 60fps" not in " ".join(i["message"] for i in validate_youtube_metadata(d)["issues"])


def test_descricao_acima_do_limite_e_erro():
    r = validate_youtube_metadata(dict(BASE, youtube_description="x" * 5001))
    assert "descricao" in campos(r, "erro")


def test_tag_4k60_so_entra_com_ffprobe_confirmando():
    sem = build_youtube_tags_csv({"main_game": "GTA V"})
    assert "4K 60fps" not in sem and "Grupo Banido" in sem
    com = build_youtube_tags_csv({"main_game": "GTA V", "video_info": {"width": 3840, "fps": 59.94}})
    assert "4K 60fps" in com
    fhd = build_youtube_tags_csv({"main_game": "GTA V", "video_info": {"width": 1920, "fps": 60}})
    assert "4K 60fps" not in fhd
    assert is_4k60({"width": 3840, "fps": 60}) and not is_4k60({})


def test_tags_fixas_editaveis(monkeypatch):
    monkeypatch.setenv("BANIDO_FIXED_TAGS", "Meu Canal, Call")
    assert get_fixed_tags() == ["Meu Canal", "Call"]
    assert "Discord" not in build_youtube_tags_csv({"main_game": "X"})
