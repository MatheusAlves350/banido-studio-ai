"""Garante que a separação de ai_director.py em app/ai/ manteve os mesmos nomes e a interface dos provedores."""
import app.ai_director as d
from app.ai.gemini import GeminiProvider
from app.ai.nvidia import NvidiaProvider

NOMES_PUBLICOS = [
    "DEFAULT_GOOGLE_MODEL", "DEFAULT_NVIDIA_MODEL", "format_transcript_for_ai",
    "analyze_with_google_gemini", "analyze_with_nvidia_nim", "generate_claude_web_prompt",
    "parse_claude_web_response", "build_youtube_description", "build_youtube_tags_csv",
    "sanitize_video_title", "normalize_youtube_chapters", "normalize_ai_data",
    "parse_ai_json_response", "SYSTEM_PROMPT",
]


def test_ai_director_continua_exportando_os_mesmos_nomes():
    for nome in NOMES_PUBLICOS:
        assert hasattr(d, nome), nome


def test_provedores_chamam_as_funcoes_existentes(monkeypatch):
    chamadas = []
    import app.ai.gemini as g
    import app.ai.nvidia as n
    monkeypatch.setattr(g, "analyze_with_google_gemini", lambda *a, **k: chamadas.append(("g", a, k)) or {"ok": 1})
    monkeypatch.setattr(n, "analyze_with_nvidia_nim", lambda *a, **k: chamadas.append(("n", a, k)) or {"ok": 2})
    assert GeminiProvider("chave", "modelo-g").analyze("texto", duration_sec=10.0) == {"ok": 1}
    assert NvidiaProvider("chave", "modelo-n").analyze("texto", duration_sec=10.0) == {"ok": 2}
    assert chamadas[0][1][:2] == ("chave", "texto") and chamadas[0][2]["model_name"] == "modelo-g"
    assert chamadas[1][1][:2] == ("chave", "texto") and chamadas[1][2]["model_name"] == "modelo-n"
