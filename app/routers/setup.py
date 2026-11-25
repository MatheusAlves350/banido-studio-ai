"""Rotas do assistente de primeira configuração."""
from fastapi import APIRouter
import os
from pathlib import Path
from typing import Optional
from fastapi import HTTPException
from pydantic import BaseModel
from app import security

from app.config import (
    BASE_DIR, OBS_RECORDINGS_DIR, HOST_SPEAKER_NAME, GROUP_NAME, SERVER_HOST
)
from app.database import (
    save_setting, get_setting
)
from app import first_run
from app import updater

router = APIRouter()


# ==============================================================================
# ASSISTENTE DE PRIMEIRA CONFIGURAÇÃO
# ==============================================================================

class SetupModel(BaseModel):
    obs_dir: Optional[str] = None
    host_name: Optional[str] = None
    group_name: Optional[str] = None
    google_api_key: Optional[str] = None
    skip: bool = False


def _setup_needed() -> bool:
    """Só abre sozinho em instalações novas: quem já salvou uma chave de IA não é incomodado."""
    if get_setting(first_run.SETUP_DONE_KEY, "") == "1":
        return False
    return not (get_setting("google_api_key", "") or get_setting("nvidia_api_key", ""))


@router.get("/api/setup")
def get_setup():
    return {
        "needs_setup": _setup_needed(),
        "obs_dir": str(OBS_RECORDINGS_DIR),
        "host_name": HOST_SPEAKER_NAME,
        "group_name": GROUP_NAME,
        "base_dir": str(BASE_DIR),
        "has_google_api_key": bool(get_setting("google_api_key", "")),
        "can_pick_folder": os.name == "nt" and not security.is_network_mode(SERVER_HOST),
        "restart_supported": updater.launched_by_launcher(),
    }


@router.post("/api/setup/pick-folder")
def setup_pick_folder():
    """Abre a janela de pastas do Windows no computador onde o servidor roda (uso local)."""
    if os.name != "nt" or security.is_network_mode(SERVER_HOST):
        raise HTTPException(status_code=400, detail="Escolher pasta por janela só funciona no Windows, no próprio computador.")
    try:
        folder = first_run.pick_folder_windows("Escolha a pasta onde o OBS salva as gravações")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Não consegui abrir a janela de pastas: {e}")
    return {"folder": folder}


@router.post("/api/setup")
def post_setup(req: SetupModel):
    if req.skip:
        save_setting(first_run.SETUP_DONE_KEY, "1")
        return {"success": True, "restart_needed": False, "restart_supported": updater.launched_by_launcher()}

    obs_path, err = first_run.validate_folder(req.obs_dir)
    if err:
        raise HTTPException(status_code=400, detail=err)
    host_name = first_run.clean_name(req.host_name, HOST_SPEAKER_NAME)
    group_name = first_run.clean_name(req.group_name, GROUP_NAME)

    try:
        first_run.upsert_env({
            "BANIDO_OBS_DIR": str(obs_path),
            "BANIDO_HOST_NAME": host_name,
            "BANIDO_GROUP_NAME": group_name,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Não consegui gravar o arquivo .env: {e}")

    key = (req.google_api_key or "").strip()
    if key:
        save_setting("google_api_key", key)
        save_setting("ai_provider", "gemini")
    save_setting(first_run.SETUP_DONE_KEY, "1")

    restart_needed = (
        Path(obs_path) != Path(OBS_RECORDINGS_DIR)
        or host_name != HOST_SPEAKER_NAME
        or group_name != GROUP_NAME
    )
    return {
        "success": True,
        "restart_needed": restart_needed,
        "restart_supported": updater.launched_by_launcher(),
        "obs_dir": str(obs_path),
    }
