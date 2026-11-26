"""C3: exportar vários cortes em fila.

Analogia: revelar um rolo de fotos de uma vez, em vez de uma por uma com o usuário parado na
frente da máquina. Um corte por vez (para não disputar disco), em ordem cronológica, e a fila
continua se um item falhar. Cada item passa pela verificação do C4 (feita dentro de
``export_lossless_highlight``).

O estado fica só em memória (uma fila por vez); se o servidor reiniciar, a fila recomeça do zero.
"""
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import CLIPS_DIR
from app.lossless_media import export_lossless_highlight

# Margem de segurança de espaço livre (F8 vai substituir esta checagem simples pela dele).
MIN_FREE_BYTES = 1 * 1024 ** 3
SIZE_SAFETY = 1.3

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
_MAX_JOBS_KEPT = 10


def estimate_batch_bytes(items: List[Dict[str, Any]], source_bytes: int, source_duration: float) -> int:
    """Estimativa grosseira: bitrate médio do vídeo original vezes a duração de cada corte."""
    if source_bytes <= 0 or source_duration <= 0:
        return 0
    rate = source_bytes / source_duration
    return int(sum(max(1.0, i["end_sec"] - i["start_sec"]) * rate for i in items) * SIZE_SAFETY)


def check_free_space(needed_bytes: int, directory: Path = CLIPS_DIR) -> Optional[str]:
    """Devolve uma mensagem de erro se não houver espaço, ou None se estiver tudo bem."""
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        return None  # não conseguiu medir: não bloqueia
    if free < needed_bytes + MIN_FREE_BYTES:
        return (
            f"Espaço insuficiente na pasta de cortes: livres {free / 1024 ** 3:.1f} GB, "
            f"necessários cerca de {(needed_bytes + MIN_FREE_BYTES) / 1024 ** 3:.1f} GB."
        )
    return None


def _normalize_items(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for i, r in enumerate(raw):
        start, end = float(r["start_sec"]), float(r["end_sec"])
        if end <= start or start < 0:
            raise ValueError(f"Item {i + 1}: o fim precisa ser maior que o início.")
        items.append({"start_sec": start, "end_sec": end, "title": (str(r.get("title") or "").strip() or "Corte")})
    items.sort(key=lambda x: x["start_sec"])  # ordem cronológica
    return items


def public_view(job: Dict[str, Any]) -> Dict[str, Any]:
    items = job["items"]
    finished = sum(1 for i in items if i["status"] in ("done", "error", "skipped"))
    return {
        "job_id": job["job_id"],
        "video_id": job["video_id"],
        "state": job["state"],  # running | done | cancelled
        "total": len(items),
        "finished": finished,
        "ok": sum(1 for i in items if i["status"] == "done"),
        "failed": sum(1 for i in items if i["status"] == "error"),
        "items": [dict(i) for i in items],
        "error": job.get("error"),
    }


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return public_view(job) if job else None


def running_job_id() -> Optional[str]:
    with _LOCK:
        for jid, job in _JOBS.items():
            if job["state"] == "running":
                return jid
    return None


def cancel_job(job_id: str) -> bool:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job or job["state"] != "running":
            return False
        job["cancel"] = True
        return True


def start_batch(
    video_id: int,
    mkv_path: Path,
    raw_items: List[Dict[str, Any]],
    options: Dict[str, Any],
    busy_check=None,
    source_bytes: int = 0,
    source_duration: float = 0.0,
) -> str:
    """Valida, confere o espaço e começa a fila em uma thread. Levanta ValueError/RuntimeError."""
    if not raw_items:
        raise ValueError("Selecione ao menos um corte.")
    items = _normalize_items(raw_items)
    msg = check_free_space(estimate_batch_bytes(items, source_bytes, source_duration))
    if msg:
        raise RuntimeError(msg)

    with _LOCK:
        if any(j["state"] == "running" for j in _JOBS.values()):
            raise RuntimeError("Já existe uma fila de cortes em andamento. Aguarde terminar ou cancele.")
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id, "video_id": video_id, "state": "running", "cancel": False, "created": time.time(),
            "items": [dict(i, status="pending", result=None, error=None) for i in items],
        }
        _JOBS[job_id] = job
        for old in sorted(_JOBS, key=lambda k: _JOBS[k]["created"])[:-_MAX_JOBS_KEPT]:
            if _JOBS[old]["state"] != "running":
                del _JOBS[old]

    threading.Thread(
        target=_run, args=(job, Path(mkv_path), options, busy_check), daemon=True, name=f"cut-batch-{job_id}"
    ).start()
    return job_id


def _run(job: Dict[str, Any], mkv_path: Path, options: Dict[str, Any], busy_check) -> None:
    try:
        for item in job["items"]:
            if job["cancel"]:
                item["status"] = "skipped"
                item["error"] = "Cancelado pelo usuário."
                continue
            if busy_check:
                try:
                    busy_check()
                except Exception as e:  # vídeo entrou em processamento no meio da fila
                    item["status"], item["error"] = "error", str(getattr(e, "detail", e))
                    continue
            msg = check_free_space(0)
            if msg:
                item["status"], item["error"] = "error", msg
                continue
            item["status"] = "running"
            try:
                res = export_lossless_highlight(
                    mkv_path, item["start_sec"], item["end_sec"], item["title"],
                    boost_discord=bool(options.get("boost_discord", True)),
                    keep_all_tracks=bool(options.get("keep_all_tracks", False)),
                    exact_cut=bool(options.get("exact_cut", False)),
                )
            except Exception as e:
                res = {"success": False, "error": str(e)}
            if res.get("success"):
                item["status"] = "done"
                item["result"] = {
                    k: res.get(k) for k in ("filename", "clip_path", "duration", "verification")
                }
            else:
                item["status"], item["error"] = "error", str(res.get("error") or "Erro ao exportar corte")
    finally:
        with _LOCK:
            job["state"] = "cancelled" if job["cancel"] else "done"
