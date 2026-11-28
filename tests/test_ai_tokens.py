"""B10: estimativa de tokens antes do envio."""
from app.ai.tokens import estimate_tokens, estimate_request, get_margin_pct, ENV_MARGIN
from app.model_catalog import get_context_limit


def test_estimativa_simples_de_quatro_caracteres_por_token():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("a" * 401) == 101


def test_limites_por_provedor_e_modelo():
    assert get_context_limit("gemini", "gemini-2.5-flash") == 1_000_000
    assert get_context_limit("nvidia", "mistralai/x") == 128_000
    assert get_context_limit("nvidia", "google/gemma-3-12b-it") == 128_000
    assert get_context_limit("manual_claude") == 200_000


def test_niveis_verde_amarelo_vermelho():
    pequeno = estimate_request("a" * 4000, "nvidia", "m")
    assert pequeno["level"] == "ok" and pequeno["suggestion"] == ""
    # 128k de limite: 60% = 76.800 tokens; entrada de ~80k tokens (+instruções e reserva) fica amarela
    medio = estimate_request("a" * (4 * 68_000), "nvidia", "m")
    assert medio["level"] == "atencao"
    grande = estimate_request("a" * (4 * 120_000), "nvidia", "m")
    assert grande["level"] == "estoura" and "limite" in grande["suggestion"]


def test_mosaico_so_conta_para_gemini():
    assert estimate_request("x", "gemini", "m", mosaic_count=2)["mosaic_tokens"] > 0
    assert estimate_request("x", "nvidia", "m", mosaic_count=2)["mosaic_tokens"] == 0


def test_margem_configuravel(monkeypatch):
    monkeypatch.delenv(ENV_MARGIN, raising=False)
    assert get_margin_pct() == 20
    monkeypatch.setenv(ENV_MARGIN, "35")
    assert get_margin_pct() == 35
    monkeypatch.setenv(ENV_MARGIN, "99")
    assert get_margin_pct() == 20
