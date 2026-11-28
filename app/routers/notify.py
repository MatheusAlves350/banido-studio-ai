"""Rotas dos avisos de fim de processamento (item E4): link do Discord e aviso de teste."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import notifications
from app.database import get_setting, save_setting
from app.worker_control import _mask_secret

router = APIRouter()


class WebhookModel(BaseModel):
    url: Optional[str] = ""
    clear: bool = False


def _state():
    raw = get_setting(notifications.WEBHOOK_SETTING, "")
    return {"has_discord_webhook": bool(raw), "discord_webhook_masked": _mask_secret(raw)}


@router.get("/api/notify")
def get_notify_settings():
    return _state()


@router.post("/api/notify/webhook")
def save_notify_webhook(req: WebhookModel):
    """Guarda (ou remove) o link do webhook do Discord. O link nunca volta inteiro: só mascarado."""
    if req.clear:
        save_setting(notifications.WEBHOOK_SETTING, "")
    else:
        url = (req.url or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="Cole o link do webhook do Discord.")
        if not notifications.is_valid_webhook(url):
            raise HTTPException(
                status_code=400,
                detail="Esse link não parece um webhook do Discord. Ele começa com https://discord.com/api/webhooks/"
            )
        save_setting(notifications.WEBHOOK_SETTING, url)
    return {"success": True, **_state()}


@router.post("/api/notify/test")
def notify_test():
    """Manda um aviso de teste pelos canais ligados, para o usuário conferir se aparece."""
    result = notifications.notify(
        "Banido Studio: aviso de teste",
        "Se você leu isto, os avisos de fim de processamento estão funcionando."
    )
    return {
        "success": True,
        "windows": result["windows"],
        "discord": result["discord"],
        "discord_configured": bool(get_setting(notifications.WEBHOOK_SETTING, "")),
    }
