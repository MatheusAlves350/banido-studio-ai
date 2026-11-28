"""Rotas da tela "Configurações avançadas" (edição do .env pelo painel, item D2)."""
import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import env_settings, first_run, security, updater
from app.config import (
    OBS_RECORDINGS_DIR, CLIPS_DIR, VOICE_BANK_DIR, MODELS_DIR, TEMP_DIR, SERVER_HOST,
)

router = APIRouter()


class EnvSettingsModel(BaseModel):
    values: Dict[str, Any] = {}


def _effective_folders() -> Dict[str, str]:
    return {
        "BANIDO_OBS_DIR": str(OBS_RECORDINGS_DIR),
        "BANIDO_CLIPS_DIR": str(CLIPS_DIR),
        "BANIDO_VOICE_BANK_DIR": str(VOICE_BANK_DIR),
        "BANIDO_MODELS_DIR": str(MODELS_DIR),
        "BANIDO_TEMP_DIR": str(TEMP_DIR),
    }


def _can_pick_folder() -> bool:
    return os.name == "nt" and not security.is_network_mode(SERVER_HOST)


@router.get("/api/env-settings")
def get_env_settings():
    data = env_settings.describe(_effective_folders())
    data["can_pick_folder"] = _can_pick_folder()
    data["restart_supported"] = updater.launched_by_launcher()
    return data


@router.post("/api/env-settings/pick-folder")
def env_settings_pick_folder(title: str = "Escolha a pasta"):
    """Abre a janela de pastas do Windows no computador onde o servidor roda (uso local)."""
    if not _can_pick_folder():
        raise HTTPException(status_code=400, detail="Escolher pasta por janela só funciona no Windows, no próprio computador.")
    try:
        folder = first_run.pick_folder_windows(title[:80])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Não consegui abrir a janela de pastas: {e}")
    return {"folder": folder}


@router.post("/api/env-settings")
def save_env_settings(req: EnvSettingsModel):
    clean, errors = env_settings.validate(req.values)
    if errors:
        raise HTTPException(status_code=400, detail=" ".join(errors.values()))
    baseline = env_settings.describe(_effective_folders())["values"]
    changes = env_settings.changed_only(clean, baseline)
    if changes:
        try:
            first_run.upsert_env(changes)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Não consegui gravar o arquivo .env: {e}")
    return {
        "success": True,
        "changed": sorted(changes),
        "restart_needed": bool(changes),
        "restart_supported": updater.launched_by_launcher(),
    }
