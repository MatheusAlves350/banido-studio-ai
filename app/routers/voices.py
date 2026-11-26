"""Rotas de vozes: prévia de falantes, confirmação e coleta de amostras."""
from fastapi import APIRouter
import re
from pathlib import Path
from typing import Dict, Any
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app import security

from app.config import (
    TEMP_DIR, HOST_SPEAKER_NAME
)
from app.database import (
    get_video_by_id, save_video_result
)
from app.transcriber_v3 import export_srt_file
from app.ai_director import (
    format_transcript_for_ai, build_youtube_description
)
from app.voice_bank import (
    list_known_voice_names, resolve_canonical_friend_name,
    extract_speaker_preview_wav
)
from app.worker_control import (
    SPEAKER_CONFIRM_LOCK,
    _ensure_not_busy,
    _run_voice_harvest_subprocess,
)

router = APIRouter()


@router.get("/api/speakers/preview/{video_id}/{speaker_tag:path}")
def preview_speaker_audio(video_id: int, speaker_tag: str):
    """
    Extrai (ou reutiliza em cache) um trecho limpo de 3s-12s da voz diretamente do MKV
    para o usuário ouvir no navegador antes de confirmar e salvar no Banco de Vozes.
    """
    video = get_video_by_id(video_id)
    if not video or not video.get("data"):
        raise HTTPException(status_code=404, detail="Vídeo ou transcrição não encontrados")

    mkv_path = Path(video["file_path"])
    if not mkv_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo MKV não encontrado no disco")

    data = video["data"]
    samples = data.get("speaker_samples") or {}
    segments = data.get("segments") or []
    is_host = speaker_tag.strip().lower() == HOST_SPEAKER_NAME.strip().lower()

    start_sec = None
    end_sec = None

    if not is_host and speaker_tag in samples:
        info = samples[speaker_tag] or {}
        if info.get("start") is not None and info.get("end") is not None:
            start_sec = float(info["start"])
            end_sec = float(info["end"])

    if start_sec is None or end_sec is None:
        tag_low = speaker_tag.strip().lower()
        matching = [
            s for s in segments
            if str(s.get("cluster_tag", "")).strip().lower() == tag_low
            or str(s.get("speaker", "")).strip().lower() == tag_low
        ]
        if not matching:
            raise HTTPException(status_code=404, detail=f"Nenhum trecho de áudio encontrado para '{speaker_tag}'")
        best_seg = max(matching, key=lambda s: float(s.get("end", 0)) - float(s.get("start", 0)))
        start_sec = float(best_seg["start"])
        end_sec = float(best_seg["end"])

    safe_tag = re.sub(r"[^A-Za-z0-9_-]+", "_", speaker_tag.strip())[:40] or "voz"
    preview_dir = TEMP_DIR / "previews"
    preview_wav = preview_dir / f"v{video_id}_{int(video.get('mtime') or 0)}_{safe_tag}_{int(start_sec)}.wav"

    if not preview_wav.exists() or preview_wav.stat().st_size <= 1000:
        ok = extract_speaker_preview_wav(mkv_path, start_sec, end_sec, preview_wav, is_host=is_host)
        if not ok:
            raise HTTPException(status_code=500, detail="Não foi possível extrair o trecho de áudio do MKV.")
        security.prune_directory(preview_dir)

    return FileResponse(str(preview_wav), media_type="audio/wav", filename=preview_wav.name)


class SpeakerConfirmRequest(BaseModel):
    video_id: int
    speaker_tag: str
    friend_name: str


@router.post("/api/speakers/confirm")
def confirm_speaker(req: SpeakerConfirmRequest):
    _ensure_not_busy(req.video_id, "confirmar vozes neste vídeo")

    clean_name = resolve_canonical_friend_name(req.friend_name)
    if not clean_name:
        raise HTTPException(status_code=400, detail="Informe um nome válido para o participante.")

    # Lock protege cliques simultâneos em vários cards sem sobrescrever confirmações vizinhas
    with SPEAKER_CONFIRM_LOCK:
        video = get_video_by_id(req.video_id)
        if not video or "data" not in video:
            raise HTTPException(status_code=404, detail="Vídeo não encontrado")

        ai_data = video["data"]
        segments = ai_data.get("segments", [])
        tag_lower = req.speaker_tag.strip().lower()

        updated_count = 0
        for seg in segments:
            c_tag = str(seg.get("cluster_tag", "")).strip().lower()
            spk = str(seg.get("speaker", "")).strip().lower()
            if c_tag == tag_lower or spk == tag_lower:
                if not seg.get("cluster_tag"):
                    seg["cluster_tag"] = req.speaker_tag
                seg["speaker"] = clean_name
                updated_count += 1

        srt_path = Path(video["srt_path"]) if video.get("srt_path") else Path(video["file_path"]).with_suffix(".srt")
        export_srt_file(segments, srt_path)

        mkv_path = Path(video["file_path"])
        harvest_res: Dict[str, Any] = {}
        try:
            harvest_res = _run_voice_harvest_subprocess(
                mode="friend",
                mkv_path=mkv_path,
                segments=segments,
                speaker_tag=req.speaker_tag,
                friend_name=clean_name,
                max_samples=3
            )
        except Exception as e:
            print(f"[VOZES] Erro no harvest manual isolado: {e}")

        if not isinstance(ai_data.get("speaker_mapping"), dict):
            ai_data["speaker_mapping"] = {}
        ai_data["speaker_mapping"][req.speaker_tag] = f"{clean_name} (Confirmado manualmente)"

        # Atualizar imediatamente speaker_samples para o card da interface refletir ✅ Confirmado na hora
        samples_dict = ai_data.get("speaker_samples")
        if isinstance(samples_dict, dict) and req.speaker_tag in samples_dict and isinstance(samples_dict[req.speaker_tag], dict):
            samples_dict[req.speaker_tag]["known"] = True
            samples_dict[req.speaker_tag]["confirmed_manually"] = True
            samples_dict[req.speaker_tag]["confirmed_name"] = clean_name
            samples_dict[req.speaker_tag]["avg_confidence"] = 1.0

        ai_data["raw_transcript_prompt"] = format_transcript_for_ai(
            segments,
            ai_data.get("hype_scores", []),
            manual_game=ai_data.get("main_game", video.get("game_name", "")),
            known_friends=list_known_voice_names(exclude_host=False),
            extra_context=video.get("session_context", "")
        )
        ai_data["youtube_description"] = build_youtube_description(ai_data)

        save_video_result(
            video_id=req.video_id,
            game_name=video.get("game_name", ""),
            duration=video.get("duration_sec", 0.0),
            mosaic_path=video.get("mosaic_path", ""),
            srt_path=str(srt_path),
            ai_data=ai_data
        )

    return {
        "success": True,
        "friend_name": clean_name,
        "updated_segments": updated_count,
        "harvest": harvest_res
    }


@router.post("/api/speakers/harvest-host/{video_id}")
def harvest_host_samples(video_id: int):
    _ensure_not_busy(video_id, "salvar amostras vocais deste vídeo")

    with SPEAKER_CONFIRM_LOCK:
        video = get_video_by_id(video_id)
        if not video or not video.get("data"):
            raise HTTPException(status_code=404, detail="Vídeo não encontrado ou ainda não transcrito")

        segments = video["data"].get("segments", [])
        mkv_path = Path(video["file_path"])
        try:
            harvest_res = _run_voice_harvest_subprocess(
                mode="host",
                mkv_path=mkv_path,
                segments=segments,
                max_samples=3
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro ao salvar amostras de {HOST_SPEAKER_NAME}: {e}")

    return {"success": True, "harvest": harvest_res}
