"""
Fonte única dos modelos de IA (Gemini e NVIDIA NIM).

- `DEFAULT_*_MODELS`: lista padrão, usada sem internet ou sem chave de API.
- `list_models()`: consulta a API do provedor (cache de 24 h) e devolve só o que existe de fato.
- `get_fallback_models()`: ordem de tentativa do fallback automático, limitada aos modelos
  que a API confirmou (quando a consulta já foi feita).

O front-end (app.js e as Configurações) preenche os seletores a partir de `GET /api/models/{provider}`.
"""
import hashlib
import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import DATABASE_DIR

DEFAULT_GOOGLE_MODEL = "gemini-2.5-flash-lite"
DEFAULT_NVIDIA_MODEL = "mistralai/mistral-large-4-1t-a52b"

CACHE_TTL_SEC = 24 * 3600
HTTP_TIMEOUT_SEC = 10

# `fallback=True`: entra na cadeia de tentativas automáticas (modelos de cota acessível).
DEFAULT_GOOGLE_MODELS: List[Dict[str, Any]] = [
    {"value": "gemini-2.5-flash-lite", "label": "gemini-2.5-flash-lite (Recomendado - Rápido e Cota Alta)", "fallback": True},
    {"value": "gemini-2.5-flash", "label": "gemini-2.5-flash (Gemini 2.5 Flash)", "fallback": True},
    {"value": "gemini-3-flash-preview", "label": "gemini-3-flash-preview (Gemini 3 Flash)", "fallback": True},
    {"value": "gemini-3.1-flash-lite", "label": "gemini-3.1-flash-lite (Gemini 3.1 Flash Lite)", "fallback": True},
    {"value": "gemini-3.5-flash-lite", "label": "gemini-3.5-flash-lite (Gemini 3.5 Flash Lite)", "fallback": True},
    {"value": "gemini-3.5-flash", "label": "gemini-3.5-flash (Gemini 3.5 Flash)", "fallback": False},
    {"value": "gemini-3.6-flash", "label": "gemini-3.6-flash (Gemini 3.6 Flash)", "fallback": True},
    {"value": "gemini-3.7-flash", "label": "gemini-3.7-flash (Gemini 3.7 Flash)", "fallback": False},
    {"value": "gemini-3.8-flash", "label": "gemini-3.8-flash (Gemini 3.8 Flash)", "fallback": True},
    {"value": "gemini-4-hydrogen", "label": "gemini-4-hydrogen (Gemini 4 Hydrogen)", "fallback": True},
    {"value": "gemini-flash-latest", "label": "gemini-flash-latest (Gemini Flash Latest)", "fallback": True},
    {"value": "gemini-3.1-pro-preview", "label": "gemini-3.1-pro-preview (Plano Pago API)", "fallback": False},
    {"value": "gemini-pro-latest", "label": "gemini-pro-latest (Plano Pago API)", "fallback": False},
]

DEFAULT_NVIDIA_MODELS: List[Dict[str, Any]] = [
    {"value": "mistralai/mistral-large-4-1t-a52b", "label": "mistralai/mistral-large-4-1t-a52b (Mistral Large 4 - Recomendado)", "fallback": True},
    {"value": "moonshotai/kimi-k3.1-pro", "label": "moonshotai/kimi-k3.1-pro (Kimi K3.1 Pro - Textos Longos)", "fallback": True},
    {"value": "nvidia/nemotron-3-ultra-550b-a55b", "label": "nvidia/nemotron-3-ultra-550b-a55b (Nemotron 3 Ultra 550B)", "fallback": True},
    {"value": "nvidia/nemotron-3.5-super-vl-120b-a12b", "label": "nvidia/nemotron-3.5-super-vl-120b-a12b (Nemotron 3.5 Super VL 120B)", "fallback": False},
    {"value": "nvidia/nemotron-3.5-lightning-30b-a3b", "label": "nvidia/nemotron-3.5-lightning-30b-a3b (Nemotron 3.5 Lightning 30B)", "fallback": True},
    {"value": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "label": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning (Nemotron Raciocínio)", "fallback": False},
    {"value": "z-ai/glm-5.3-flash", "label": "z-ai/glm-5.3-flash (GLM 5.3 Flash - Ultra Rápido)", "fallback": True},
    {"value": "minimaxai/minimax-m3.1-flash", "label": "minimaxai/minimax-m3.1-flash (MiniMax M3.1 Flash)", "fallback": True},
    {"value": "google/gemma-4-31b-it", "label": "google/gemma-4-31b-it (Gemma 4 31B)", "fallback": True},
    {"value": "meta/muse-glimmer-30b", "label": "meta/muse-glimmer-30b (Meta Muse Glimmer 30B)", "fallback": False},
    {"value": "stepfun-ai/step-5-preview", "label": "stepfun-ai/step-5-preview (StepFun Step 5 Preview)", "fallback": False},
    {"value": "google/gemma-3-12b-it", "label": "google/gemma-3-12b-it (Gemma 3 12B)", "fallback": False},
]

_DEFAULTS = {"google": DEFAULT_GOOGLE_MODELS, "nvidia": DEFAULT_NVIDIA_MODELS}
_PROVIDER_ALIASES = {"gemini": "google", "google": "google", "nvidia": "nvidia"}

_GOOGLE_EXCLUDE = ("embedding", "tts", "image", "imagen", "live", "audio", "aqa", "veo", "robotics", "computer-use")
_NVIDIA_EXCLUDE = ("embed", "rerank", "guard", "reward", "retriev", "parse", "clip", "safety", "nvclip")

# provider -> {"fetched_at": float, "key_hash": str, "ids": [str]}
_CACHE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
# O worker roda em outro processo: o cache vai também para disco para o fallback dele enxergar.
CACHE_FILE: Path = DATABASE_DIR / "models_cache.json"


def _read_disk_cache() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_disk_cache() -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        merged = _read_disk_cache()
        merged.update(_CACHE)
        tmp_path = CACHE_FILE.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(merged), encoding="utf-8")
        tmp_path.replace(CACHE_FILE)
    except Exception:
        pass  # só otimização: sem disco, o cache em memória continua valendo


def normalize_provider(provider: str) -> Optional[str]:
    return _PROVIDER_ALIASES.get(str(provider or "").strip().lower())


def _key_hash(api_key: str) -> str:
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:16]


def _http_get_json(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_google_ids(api_key: str) -> List[str]:
    ids: List[str] = []
    page_token = ""
    for _ in range(10):
        url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200"
        if page_token:
            url += f"&pageToken={page_token}"
        data = _http_get_json(url, {"x-goog-api-key": api_key})
        for m in data.get("models", []):
            name = str(m.get("name", "")).removeprefix("models/")
            methods = m.get("supportedGenerationMethods") or []
            if not name.startswith(("gemini", "gemma")) or "generateContent" not in methods:
                continue
            if any(x in name for x in _GOOGLE_EXCLUDE):
                continue
            ids.append(name)
        page_token = data.get("nextPageToken") or ""
        if not page_token:
            break
    return ids


def _fetch_nvidia_ids(api_key: str) -> List[str]:
    data = _http_get_json(
        "https://integrate.api.nvidia.com/v1/models",
        {"Authorization": f"Bearer {api_key}"},
    )
    ids = []
    for m in data.get("data", []):
        mid = str(m.get("id", ""))
        if mid and not any(x in mid.lower() for x in _NVIDIA_EXCLUDE):
            ids.append(mid)
    return ids


_FETCHERS = {"google": _fetch_google_ids, "nvidia": _fetch_nvidia_ids}


def _build_entries(provider: str, ids: List[str]) -> List[Dict[str, Any]]:
    """Modelos confirmados pela API: os conhecidos primeiro (na ordem padrão), depois os novos."""
    known = {m["value"]: m for m in _DEFAULTS[provider]}
    confirmed = set(ids)
    entries = [dict(m) for m in _DEFAULTS[provider] if m["value"] in confirmed]
    for mid in sorted(confirmed - set(known)):
        entries.append({"value": mid, "label": f"{mid} (novo)", "fallback": False})
    return entries


def list_models(provider: str, api_key: str = "", force_refresh: bool = False) -> Dict[str, Any]:
    prov = normalize_provider(provider)
    if prov is None:
        raise ValueError(f"Provedor desconhecido: {provider}")

    default_result = {
        "provider": prov,
        "source": "default",
        "models": [dict(m) for m in _DEFAULTS[prov]],
    }
    if not api_key:
        return default_result

    kh = _key_hash(api_key)
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(prov) or _read_disk_cache().get(prov)
        fresh = cached and cached["key_hash"] == kh and now - cached["fetched_at"] < CACHE_TTL_SEC
    if fresh and not force_refresh:
        ids = cached["ids"]
    else:
        try:
            ids = _FETCHERS[prov](api_key)
        except Exception as e:
            print(f"[MODELOS] Não foi possível consultar a lista de {prov}: {e}. Usando lista padrão.", flush=True)
            return default_result
        if not ids:
            return default_result
        with _LOCK:
            _CACHE[prov] = {"fetched_at": now, "key_hash": kh, "ids": ids}
            _write_disk_cache()

    entries = _build_entries(prov, ids)
    if not entries:
        return default_result
    return {"provider": prov, "source": "api", "models": entries}


def get_fallback_models(provider: str) -> List[str]:
    """
    Ordem de tentativa do fallback automático. Se a API já confirmou a lista, só entram modelos
    que existem; sem consulta prévia, usa a lista padrão.
    """
    prov = normalize_provider(provider)
    if prov is None:
        return []
    with _LOCK:
        cached = _CACHE.get(prov) or _read_disk_cache().get(prov)
    if cached and time.time() - cached.get("fetched_at", 0) > CACHE_TTL_SEC * 7:
        cached = None  # lista com mais de 7 dias: não confiar
    confirmed = set(cached["ids"]) if cached else None
    out = []
    for m in _DEFAULTS[prov]:
        if not m.get("fallback"):
            continue
        if confirmed is not None and m["value"] not in confirmed:
            continue
        out.append(m["value"])
    return out


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
        try:
            CACHE_FILE.unlink()
        except Exception:
            pass
