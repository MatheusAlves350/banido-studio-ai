"""Rotas de configurações de IA, modelos e teste de chaves."""
from fastapi import APIRouter
from typing import Dict, Any, Optional
from fastapi import HTTPException
from pydantic import BaseModel

from app.database import (
    save_setting, get_setting
)
from app.ai_director import (
    DEFAULT_GOOGLE_MODEL, DEFAULT_NVIDIA_MODEL
)
from app.worker_control import (
    _mask_secret,
)

router = APIRouter()


class SettingsModel(BaseModel):
    google_api_key: Optional[str] = None
    nvidia_api_key: Optional[str] = None
    clear_google_key: bool = False
    clear_nvidia_key: bool = False
    ai_provider: Optional[str] = "gemini"
    google_model: Optional[str] = DEFAULT_GOOGLE_MODEL
    nvidia_model: Optional[str] = DEFAULT_NVIDIA_MODEL


def _build_safe_settings_response() -> Dict[str, Any]:
    g_raw = get_setting("google_api_key", "")
    n_raw = get_setting("nvidia_api_key", "")
    return {
        "has_google_api_key": bool(g_raw),
        "google_api_key_masked": _mask_secret(g_raw),
        "has_nvidia_api_key": bool(n_raw),
        "nvidia_api_key_masked": _mask_secret(n_raw),
        "ai_provider": get_setting("ai_provider", "gemini"),
        "google_model": get_setting("google_model", DEFAULT_GOOGLE_MODEL),
        "nvidia_model": get_setting("nvidia_model", DEFAULT_NVIDIA_MODEL)
    }


@router.get("/api/settings")
def get_settings():
    return _build_safe_settings_response()


@router.post("/api/settings")
def update_settings(req: SettingsModel):
    if req.clear_google_key:
        save_setting("google_api_key", "")
    elif req.google_api_key is not None:
        g_in = req.google_api_key.strip()
        if g_in and g_in != _mask_secret(get_setting("google_api_key", "")):
            save_setting("google_api_key", g_in)

    if req.clear_nvidia_key:
        save_setting("nvidia_api_key", "")
    elif req.nvidia_api_key is not None:
        n_in = req.nvidia_api_key.strip()
        if n_in and n_in != _mask_secret(get_setting("nvidia_api_key", "")):
            save_setting("nvidia_api_key", n_in)

    save_setting("ai_provider", req.ai_provider or "gemini")
    save_setting("google_model", req.google_model or DEFAULT_GOOGLE_MODEL)
    save_setting("nvidia_model", req.nvidia_model or DEFAULT_NVIDIA_MODEL)
    res = _build_safe_settings_response()
    res["success"] = True
    return res


@router.get("/api/models/{provider}")
def get_models(provider: str, refresh: bool = False):
    """Lista de modelos do provedor (Gemini ou NVIDIA), com cache de 24 h e lista padrão como reserva."""
    from app.model_catalog import list_models, normalize_provider
    prov = normalize_provider(provider)
    if prov is None:
        raise HTTPException(status_code=404, detail="Provedor desconhecido. Use 'gemini' ou 'nvidia'.")
    key = get_setting("google_api_key" if prov == "google" else "nvidia_api_key", "")
    return list_models(prov, key, force_refresh=refresh)


class TestKeyModel(BaseModel):
    key: Optional[str] = ""
    model: Optional[str] = ""


@router.post("/api/test-google-key")
def test_google_key(req: TestKeyModel):
    key = req.key.strip() if req.key else get_setting("google_api_key", "")
    model = req.model.strip() if req.model else get_setting("google_model", DEFAULT_GOOGLE_MODEL)
    if not key:
        return {"success": False, "error": "Nenhuma chave informada ou salva."}
    try:
        from google import genai
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=model,
            contents="Responda apenas: OK"
        )
        return {"success": True, "reply": (resp.text or "").strip(), "model": model}
    except Exception as e:
        return {"success": False, "error": str(e), "model": model}


@router.post("/api/test-nvidia-key")
def test_nvidia_key(req: TestKeyModel):
    key = req.key.strip() if req.key else get_setting("nvidia_api_key", "")
    model = req.model.strip() if req.model else get_setting("nvidia_model", DEFAULT_NVIDIA_MODEL)
    if not key:
        return {"success": False, "error": "Nenhuma chave NVIDIA informada ou salva."}
    try:
        from openai import OpenAI
        client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key, timeout=30.0)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Responda apenas: OK"}],
            max_tokens=128
        )
        reply_txt = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return {"success": True, "reply": reply_txt or "OK", "model": model}
    except Exception as e:
        return {"success": False, "error": str(e), "model": model}
