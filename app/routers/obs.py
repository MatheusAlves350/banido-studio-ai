"""Rotas da tela "Conferir o OBS" (item E5): ler, pré-visualizar, aplicar e desfazer."""
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import obs_panel

router = APIRouter()


class ObsChoice(BaseModel):
    layout: str = "6"
    roles: Dict[str, str] = {}
    add_filters: bool = True


class ObsApply(ObsChoice):
    base_hash: Optional[str] = ""
    confirm: bool = False


class ObsUndo(BaseModel):
    confirm: bool = False


@router.get("/api/obs/state")
def obs_state():
    try:
        return obs_panel.read_state()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Não consegui ler a configuração do OBS: {e}")


@router.post("/api/obs/preview")
def obs_preview(req: ObsChoice):
    try:
        return obs_panel.preview(req.layout, req.roles, req.add_filters)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/obs/apply")
def obs_apply(req: ObsApply):
    try:
        return obs_panel.apply(req.layout, req.roles, req.add_filters, req.base_hash or "", req.confirm)
    except obs_panel.ObsPanelError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/obs/undo")
def obs_undo(req: ObsUndo):
    try:
        return obs_panel.undo(req.confirm)
    except obs_panel.ObsPanelError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
