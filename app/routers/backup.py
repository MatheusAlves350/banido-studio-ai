"""F4: rotas de backup e restauração do banco de vozes e do banco de dados."""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import backup
from app.worker_control import ACTIVE_PROCESSES, reconcile_worker_processes

router = APIRouter()

MAX_IMPORT_BYTES = 2 * 1024 ** 3


class BackupName(BaseModel):
    name: str


class BackupFolder(BaseModel):
    folder: str = ""


def _run(fn, *args):
    try:
        return fn(*args)
    except backup.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/backup/status")
def backup_status():
    folder = backup.get_backup_dir()
    return {
        "folder": str(folder),
        "backups": backup.list_backups(folder),
        "auto_every_days": backup.AUTO_EVERY.days,
        "auto_keep": backup.AUTO_KEEP,
        "privacy_notice": backup.PRIVACY_NOTICE,
    }


@router.post("/api/backup/create")
def backup_create():
    reconcile_worker_processes()
    path = _run(backup.create_backup)
    return {"success": True, "name": path.name, "size_mb": round(path.stat().st_size / (1024 * 1024), 2)}


@router.post("/api/backup/download")
def backup_download(req: BackupName):
    path = _run(backup.resolve_backup_file, req.name)
    return FileResponse(str(path), media_type="application/zip", filename=path.name)


@router.post("/api/backup/preview")
def backup_preview(req: BackupName):
    path = _run(backup.resolve_backup_file, req.name)
    return _run(backup.verify_backup, path)


@router.post("/api/backup/import")
async def backup_import(request: Request):
    """Recebe o .zip como corpo da requisição (sem multipart), confere e guarda na pasta de backups."""
    folder = backup.get_backup_dir()
    final = folder / f"{backup.IMPORT_PREFIX}{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    tmp = final.with_suffix(".zip.parcial")
    total = 0
    try:
        with open(tmp, "wb") as fh:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_IMPORT_BYTES:
                    raise HTTPException(status_code=413, detail="Arquivo grande demais para importar (limite de 2 GB).")
                fh.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="Nenhum arquivo recebido.")
        try:
            info = backup.verify_backup(tmp)
        except backup.BackupError as e:
            raise HTTPException(status_code=400, detail=str(e))
        tmp.replace(final)
    finally:
        tmp.unlink(missing_ok=True)
    info["name"] = final.name
    return info


@router.post("/api/backup/restore")
def backup_restore(req: BackupName):
    reconcile_worker_processes()
    if ACTIVE_PROCESSES:
        raise HTTPException(status_code=409, detail="Há um processamento em andamento. Aguarde ou cancele antes de restaurar.")
    path = _run(backup.resolve_backup_file, req.name)
    result = _run(backup.restore_backup, path)
    result["success"] = True
    return result


@router.post("/api/backup/folder")
def backup_folder(req: BackupFolder):
    folder = _run(backup.set_backup_dir, req.folder)
    return {"success": True, "folder": str(folder), "backups": backup.list_backups(folder)}
