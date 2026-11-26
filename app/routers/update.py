"""Rotas de atualização do programa."""
from fastapi import APIRouter
import sys
from fastapi import HTTPException

from app.config import (
    VENV_PYTHON
)
from app.worker_control import (
    ACTIVE_PROCESSES,
    reconcile_worker_processes,
)
from app import updater

router = APIRouter()


# ==============================================================================
# ATUALIZAÇÕES (GitHub)
# ==============================================================================

@router.get("/api/update/status")
def update_status():
    """Versão instalada + novidades disponíveis no GitHub (cache de 10 min)."""
    info = updater.check_for_updates(force=False)
    return {**info, "apply": updater.get_apply_state(), "restart_supported": updater.launched_by_launcher()}


@router.post("/api/update/check")
def update_check_now():
    """Força uma nova consulta ao GitHub (botão "Verificar agora")."""
    info = updater.check_for_updates(force=True)
    return {**info, "apply": updater.get_apply_state(), "restart_supported": updater.launched_by_launcher()}


def _ensure_idle_for_update(action_desc: str):
    reconcile_worker_processes()
    if ACTIVE_PROCESSES:
        raise HTTPException(
            status_code=409,
            detail=f"Não é possível {action_desc} enquanto há {len(ACTIVE_PROCESSES)} processamento(s) em andamento. Aguarde terminar ou cancele."
        )


@router.post("/api/update/apply")
def update_apply():
    _ensure_idle_for_update("atualizar")
    python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    res = updater.start_apply(python_exe=python_exe)
    return {**res, "apply": updater.get_apply_state()}


@router.get("/api/update/progress")
def update_progress():
    return updater.get_apply_state()


@router.post("/api/update/restart")
def update_restart():
    _ensure_idle_for_update("reiniciar o servidor")
    if not updater.launched_by_launcher():
        return {
            "success": False,
            "restart_supported": False,
            "message": "Feche esta janela/terminal do servidor e abra o Banido Studio de novo pelo Iniciar_Banido_Studio.bat (ou ./start.sh)."
        }
    updater.schedule_restart()
    return {"success": True, "restart_supported": True, "message": "Reiniciando o servidor..."}
