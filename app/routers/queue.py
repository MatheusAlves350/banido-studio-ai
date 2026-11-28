"""Rotas da fila de vídeos e do processamento noturno (item E3)."""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import queue_manager

router = APIRouter()


class QueueAddRequest(BaseModel):
    video_ids: List[int]
    engine: Optional[str] = "gemini"
    custom_model: Optional[str] = ""
    normalize_audio: bool = False
    separate_tracks: Optional[bool] = None
    start_time: Optional[str] = None      # "23:00"; vazio = começar agora
    after: str = "none"                   # none | sleep | shutdown
    confirm_power: bool = False


@router.get("/api/queue")
def get_queue():
    return queue_manager.snapshot()


@router.post("/api/queue/add")
def add_queue(req: QueueAddRequest):
    res = queue_manager.add_to_queue(
        video_ids=req.video_ids, engine=req.engine or "gemini", model=req.custom_model or "",
        normalize_audio=req.normalize_audio, separate_tracks=req.separate_tracks,
        start_time=(req.start_time or "").strip() or None, after=req.after, confirm_power=req.confirm_power,
    )
    if res["added"] and not res["start_at"]:
        queue_manager.tick()  # começa na hora, sem esperar o próximo ciclo do agendador
    return {**res, **queue_manager.snapshot()}


@router.post("/api/queue/remove/{item_id}")
def remove_queue_item(item_id: int):
    if not queue_manager.remove_item(item_id):
        raise HTTPException(status_code=404, detail="Esse item não está mais aguardando na fila.")
    return queue_manager.snapshot()


@router.post("/api/queue/clear")
def clear_queue_finished():
    queue_manager.clear_finished()
    return queue_manager.snapshot()


@router.post("/api/queue/cancel")
def cancel_queue():
    """Tira da fila tudo que ainda não começou. O vídeo em andamento termina normalmente."""
    queue_manager.cancel_waiting()
    queue_manager.cancel_power()
    return queue_manager.snapshot()


@router.post("/api/queue/start-now")
def start_queue_now():
    queue_manager.start_now()
    queue_manager.tick()
    return queue_manager.snapshot()


@router.post("/api/queue/cancel-power")
def cancel_queue_power():
    queue_manager.cancel_power()
    return queue_manager.snapshot()
