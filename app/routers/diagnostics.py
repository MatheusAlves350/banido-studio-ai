"""Rota do relatório de diagnóstico."""
from fastapi import APIRouter, HTTPException, Response
from typing import Optional

from app.database import (
    get_job_history, get_job_by_id, get_latest_job_for_video
)
from app.worker_control import (
    reconcile_worker_processes,
)

router = APIRouter()


# ==============================================================================
# DIAGNÓSTICO / RELATÓRIO DE BUGS (LOGS SANITIZADOS)
# ==============================================================================

@router.get("/api/diagnostics/report")
def get_diagnostic_report(
    job_id: Optional[int] = None,
    video_id: Optional[int] = None,
    strip_transcripts: bool = True,
    redact_filenames: bool = False,
):
    """Monta um relatório sanitizado de diagnóstico (pronto para revisão, download ou Issue no GitHub)."""
    from app.diagnostics import build_diagnostic_report

    reconcile_worker_processes()
    job = None
    if job_id is not None:
        job = get_job_by_id(job_id)
    elif video_id is not None:
        job = get_latest_job_for_video(video_id)
    else:
        recent = get_job_history(limit=1)
        job = recent[0] if recent else None

    return build_diagnostic_report(
        job=job,
        strip_transcript_lines=strip_transcripts,
        redact_filenames=redact_filenames,
    )


@router.get("/api/diagnostics/package")
def download_problem_package():
    """F3: gera o .zip "relatório de problema" (sem chaves, vídeos ou caminhos completos)."""
    from app.problem_report import ReportBlocked, build_problem_zip

    reconcile_worker_processes()
    try:
        data, filename = build_problem_zip()
    except ReportBlocked as e:
        raise HTTPException(status_code=500, detail=str(e))
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
