import sys
import os
import re
import time
import shutil
import argparse
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# Garantir UTF-8 no Windows
os.environ["PYTHONIOENCODING"] = "utf-8"

from app.config import DATABASE_DIR, TEMP_DIR, LEGACY_TEMP_DIR, OBS_RECORDINGS_DIR, HOST_SPEAKER_NAME, GROUP_NAME
from app.database import (
    get_video_by_id, get_all_videos, get_job_by_id, update_video_progress,
    update_job_step, finish_job_record, save_video_result,
    save_video_transcription_checkpoint, has_video_data, get_setting,
    set_video_audio_normalized, reset_video_status, update_video_game_and_context
)
from app.lossless_media import (
    normalize_mkv_audio_lossless,
    inspect_mkv_audio_norm,
    prepare_single_pass_norm,
    finalize_normalized_mkv,
)
from app.mosaic_generator import generate_video_mosaic
from app.transcriber_v3 import run_transcription_pipeline
from app.voice_bank import list_known_voice_names
from app.ai_director import (
    DEFAULT_GOOGLE_MODEL, DEFAULT_NVIDIA_MODEL,
    format_transcript_for_ai, analyze_with_google_gemini,
    analyze_with_nvidia_nim, build_youtube_description
)

INVALID_AUTO_SPEAKER_NAMES = {
    "desconhecido", "desconhecida", "desconhecidos", "amigo", "amiga",
    "voz", "não", "nao", "indefinido", "indefinida", "provavelmente",
    "possivelmente", "talvez", "sem", "ninguém", "ninguem", "outro",
    "outra", "participante", "usuário", "usuario", "host", "discord",
    "anônimo", "anonimo", "incerto", "incerta", "n/a", "none", "null",
    HOST_SPEAKER_NAME.strip().lower(),
}


def extract_clean_deduced_name(deduced_info) -> Optional[str]:
    if isinstance(deduced_info, dict):
        raw_str = str(deduced_info.get("name") or "")
    else:
        raw_str = str(deduced_info or "")

    # Pegar o trecho antes de parênteses/traço e extrair o primeiro nome limpo
    head = re.split(r"[\(\-\—\:\[]", raw_str, maxsplit=1)[0].strip()
    parts = head.split()
    if not parts:
        return None
    candidate = parts[0].strip("():,[]\"'!.?")
    if len(candidate) < 2 or len(candidate) > 30:
        return None
    if candidate.lower() in INVALID_AUTO_SPEAKER_NAMES:
        return None
    if not re.match(r"^[A-Za-zÀ-ÖØ-öø-ÿ]+$", candidate):
        return None
    return candidate.title()


class TimestampedLogWriter:
    def __init__(self, log_path: Path, orig_stream):
        self.log_file = open(log_path, "a", encoding="utf-8", buffering=1, errors="replace")
        self.orig_stream = orig_stream
        self._at_line_start = True

    def write(self, data: str):
        if not data:
            return 0
        ts = datetime.now().strftime("%H:%M:%S")
        out_chunks = []
        for ch in data:
            if self._at_line_start and ch not in ("\r", "\n"):
                out_chunks.append(f"[{ts}] ")
                self._at_line_start = False
            out_chunks.append(ch)
            if ch == "\n":
                self._at_line_start = True
        formatted = "".join(out_chunks)
        try:
            self.log_file.write(formatted)
            self.log_file.flush()
        except Exception:
            pass
        if self.orig_stream:
            try:
                self.orig_stream.write(formatted)
                self.orig_stream.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        try:
            self.log_file.flush()
        except Exception:
            pass
        if self.orig_stream:
            try:
                self.orig_stream.flush()
            except Exception:
                pass

    def close(self):
        try:
            self.log_file.close()
        except Exception:
            pass


def run_worker(
    job_id: int,
    video_id: int,
    mode: str,
    manual_game: str,
    engine: str,
    custom_model: str,
    extra_context: str = "",
    normalize_audio: bool = False,
    separate_tracks: Optional[bool] = None,
):
    job = get_job_by_id(job_id)
    log_path = Path(job["log_file"]) if job and job.get("log_file") else (DATABASE_DIR / "logs" / f"job_{job_id}.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    orig_out, orig_err = sys.stdout, sys.stderr
    logger_stream = TimestampedLogWriter(log_path, orig_out)
    sys.stdout = logger_stream
    sys.stderr = logger_stream

    temp_job_dir = TEMP_DIR / f"video_{video_id}_work"
    legacy_job_dir = LEGACY_TEMP_DIR / f"video_{video_id}_work"
    if (
        not (temp_job_dir / "whisper_checkpoint.json").exists()
        and not (temp_job_dir / "transcription_checkpoint.json").exists()
        and ((legacy_job_dir / "whisper_checkpoint.json").exists() or (legacy_job_dir / "transcription_checkpoint.json").exists())
    ):
        temp_job_dir = legacy_job_dir
    job_succeeded = False

    def report(msg: str, pct: float):
        if video_id > 0:
            update_video_progress(video_id, msg, pct, "processing")
        update_job_step(job_id, msg, pct)

    try:
        t_start = time.time()
        from app.game_guard import lower_current_process_priority
        prio_ok = lower_current_process_priority()
        prio_note = " | Prioridade CPU: abaixo do normal" if prio_ok else ""
        print(f"[SISTEMA] Worker iniciado (PID {os.getpid()}) | Modo: {mode} | Temp: {temp_job_dir}{prio_note}", flush=True)

        if mode == "normalize_audio":
            video = get_video_by_id(video_id)
            if not video:
                raise RuntimeError(f"Vídeo #{video_id} não encontrado no banco de dados.")
            mkv_path = Path(video["file_path"])
            if not mkv_path.exists():
                raise RuntimeError(f"Arquivo MKV não encontrado no disco: {mkv_path}")

            had_data = has_video_data(video_id)
            res = normalize_mkv_audio_lossless(mkv_path, progress_callback=report, force=False)
            if not res.get("success"):
                raise RuntimeError(res.get("error", "Falha ao normalizar áudio do MKV."))

            set_video_audio_normalized(
                video_id,
                normalized=True,
                size_gb=res.get("size_gb"),
                mtime=res.get("mtime")
            )
            total_elapsed = time.time() - t_start
            if res.get("skipped"):
                done_msg = f"Áudio já estava normalizado ({res.get('norm_tag', 'YouTube Gold')})!"
            else:
                done_msg = f"Áudio YouTube Gold normalizado em {int(total_elapsed // 60)}m {int(total_elapsed % 60)}s! (Vídeo 100% lossless)"

            reset_video_status(video_id, done_msg, restore_completed=had_data)
            finish_job_record(job_id, status="completed", last_step=done_msg)
            job_succeeded = True
            print("========================================================================", flush=True)
            print(f"✅ [SUCESSO] {done_msg}", flush=True)
            print("========================================================================", flush=True)
            return

        if mode == "normalize_batch":
            videos = get_all_videos()
            total = len(videos)
            if total == 0:
                done_msg = f"Nenhum vídeo encontrado em {OBS_RECORDINGS_DIR}."
                finish_job_record(job_id, status="completed", last_step=done_msg)
                job_succeeded = True
                return

            print(f"[AUDIO LOTE] Iniciando varredura e normalização em lote de {total} vídeo(s)...", flush=True)
            ok_count = 0
            skipped_count = 0
            err_list = []

            for idx, v in enumerate(videos, 1):
                vid = v["id"]
                vpath = Path(v["file_path"])
                vname = v["filename"]
                base_pct = (idx - 1) / float(total)
                span_pct = 1.0 / float(total)

                if not vpath.exists():
                    print(f"[AUDIO LOTE] ({idx}/{total}) Arquivo ausente, pulando: {vname}", flush=True)
                    skipped_count += 1
                    continue

                # Checagem rápida de tag já normalizada
                try:
                    info = inspect_mkv_audio_norm(vpath)
                    if info.get("is_normalized"):
                        set_video_audio_normalized(vid, normalized=True)
                        skipped_count += 1
                        print(f"[AUDIO LOTE] ({idx}/{total}) ✅ Já normalizado, pulando: {vname}", flush=True)
                        update_job_step(job_id, f"[{idx}/{total}] Já normalizado: {vname}", min(0.99, idx / float(total)))
                        continue
                except Exception as insp_err:
                    msg_e = f"{vname}: {insp_err}"
                    err_list.append(msg_e)
                    print(f"[AUDIO LOTE] ({idx}/{total}) ⚠️ Erro ao ler arquivo (pulado): {msg_e}", flush=True)
                    continue

                had_data = has_video_data(vid)
                print(f"\n[AUDIO LOTE] ({idx}/{total}) 🔊 Normalizando: {vname} ({v.get('size_gb', 0)} GB)", flush=True)
                update_video_progress(vid, f"Lote [{idx}/{total}]: Normalizando áudio YouTube Gold...", 0.05, "processing")

                def batch_cb(sub_msg: str, sub_pct: float, _vid=vid, _idx=idx, _vname=vname, _base=base_pct, _span=span_pct):
                    update_video_progress(_vid, f"Lote [{_idx}/{total}]: {sub_msg}", sub_pct, "processing")
                    update_job_step(job_id, f"[{_idx}/{total}] {_vname} — {sub_msg}", min(0.99, _base + sub_pct * _span))

                res = normalize_mkv_audio_lossless(vpath, progress_callback=batch_cb, force=False)
                if res.get("success"):
                    set_video_audio_normalized(vid, normalized=True, size_gb=res.get("size_gb"), mtime=res.get("mtime"))
                    if res.get("skipped"):
                        skipped_count += 1
                    else:
                        ok_count += 1
                    reset_video_status(vid, "Áudio YouTube Gold normalizado (100% lossless)!", restore_completed=had_data)
                else:
                    err_txt = res.get("error", "Erro desconhecido")
                    err_list.append(f"{vname}: {err_txt}")
                    print(f"[AUDIO LOTE] ({idx}/{total}) ❌ Falha em {vname}: {err_txt}", flush=True)
                    reset_video_status(vid, f"Aviso na normalização de áudio: {err_txt[:140]}", restore_completed=had_data)

            total_elapsed = time.time() - t_start
            done_msg = (
                f"Lote concluído em {int(total_elapsed // 60)}m {int(total_elapsed % 60)}s! "
                f"Normalizados: {ok_count} | Já prontos/pulados: {skipped_count} | Avisos/Erros: {len(err_list)}"
            )
            print("========================================================================", flush=True)
            print(f"✅ [SUCESSO] {done_msg}", flush=True)
            if err_list:
                print("[AVISO] Arquivos com erro/bloqueio (demais foram concluídos):", flush=True)
                for e_item in err_list:
                    print(f"  - {e_item}", flush=True)
            print("========================================================================", flush=True)
            finish_job_record(job_id, status="completed", last_step=done_msg, error_detail="; ".join(err_list)[:500])
            job_succeeded = True
            return

        video = get_video_by_id(video_id)
        if not video:
            raise RuntimeError(f"Vídeo #{video_id} não encontrado no banco de dados.")

        mkv_path = Path(video["file_path"])
        if not mkv_path.exists():
            raise RuntimeError(f"Arquivo MKV não encontrado no disco: {mkv_path}")

        mosaics_dir = DATABASE_DIR / "mosaics"
        mosaics_dir.mkdir(parents=True, exist_ok=True)
        mosaic_jpg = mosaics_dir / f"{video_id}_mosaic.jpg"
        srt_path = mkv_path.with_suffix(".srt")

        effective_game = (manual_game or "").strip() or (video.get("game_name") or "").strip()
        effective_context = (extra_context or "").strip() or (video.get("session_context") or "").strip()
        if manual_game or extra_context:
            update_video_game_and_context(
                video_id=video_id,
                game_name=manual_game.strip() if manual_game.strip() else None,
                session_context=extra_context.strip() if extra_context is not None else None,
                save_default_context=bool(manual_game.strip() and extra_context.strip())
            )

        single_pass_prep = None
        single_pass_norm_mkv = None
        single_pass_t0 = None

        if mode == "ai_only":
            existing_data = video.get("data") or {}
            segments = existing_data.get("segments") or []
            hype_scores = existing_data.get("hype_scores") or []
            speaker_samples = existing_data.get("speaker_samples") or {}
            duration_sec = video.get("duration_sec") or 0.0
            if not segments:
                raise RuntimeError("Este vídeo ainda não possui transcrição salva. Execute o Processamento Completo primeiro.")
            if not mosaic_jpg.exists():
                report("Gerando Mosaico Visual 4x4 (16 cenas HD)...", 0.20)
                generate_video_mosaic(mkv_path, mosaic_jpg)
        else:
            # 1. Gerar Mosaico Visual 4x4 (reaproveita se já gerado em checkpoint anterior)
            has_prior_ckpt = (temp_job_dir / "extract_meta.json").exists() or (temp_job_dir / "transcription_checkpoint.json").exists()
            if mosaic_jpg.exists() and has_prior_ckpt:
                print(f"[CHECKPOINT] Reaproveitando Mosaico Visual existente ({mosaic_jpg.name})!", flush=True)
            else:
                report("Etapa 1/4: Gerando Mosaico Visual 4x4 (16 cenas HD)...", 0.04)
                print(f"[MOSAICO] Extraindo 16 frames HD distribuídos de '{mkv_path.name}'...", flush=True)
                generate_video_mosaic(mkv_path, mosaic_jpg)

            # 1.5 Preparar Passada Única de normalização de áudio (evita reler/reprocessar o MKV no final!)
            norm_output_target = None
            if normalize_audio:
                single_pass_prep = prepare_single_pass_norm(mkv_path)
                if single_pass_prep.get("should_run"):
                    norm_output_target = single_pass_prep["temp_mkv"]
                    single_pass_t0 = time.time()
                    print(
                        "[AUDIO NORM] ⚡ Passada Única ativada: o MKV YouTube Gold será gerado "
                        "EM PARALELO durante a extração do Whisper (zero reprocessamento no final)!",
                        flush=True
                    )
                elif single_pass_prep.get("already_normalized"):
                    print("[AUDIO NORM] ✅ Vídeo já está normalizado (YouTube Gold); etapa de áudio será pulada.", flush=True)
                else:
                    print(
                        f"[AUDIO NORM] ⚠️ Passada Única indisponível ({single_pass_prep.get('error', 'motivo desconhecido')}); "
                        f"seguindo com a transcrição normalmente.",
                        flush=True
                    )

            # 2. Transcrição Completa + Telemetria + Vozes (+ Normalização em Passada Única se ativa)
            temp_job_dir.mkdir(parents=True, exist_ok=True)
            trans_res = run_transcription_pipeline(
                mkv_path, temp_job_dir, srt_path,
                progress_callback=report,
                manual_game=effective_game,
                extra_context=effective_context,
                norm_output_mkv=norm_output_target,
                separate_tracks=separate_tracks,
            )
            segments = trans_res["segments"]
            hype_scores = trans_res["hype_scores"]
            speaker_samples = trans_res["speaker_samples"]
            duration_sec = trans_res["duration"]
            if trans_res.get("single_pass_norm_mkv"):
                single_pass_norm_mkv = Path(trans_res["single_pass_norm_mkv"])

        # 3. Preparar prompt e SALVAR CHECKPOINT NO SQLITE ANTES DA API DA IA
        known_friends = list_known_voice_names(exclude_host=False)
        transcript_for_ai = format_transcript_for_ai(
            segments, hype_scores,
            manual_game=effective_game,
            known_friends=known_friends,
            extra_context=effective_context
        )

        ai_data = {
            "main_game": manual_game or video.get("game_name") or "Gameplay",
            "other_games": [],
            "title_options": [f"{manual_game or GROUP_NAME} - Sessão com Amigos no Discord ({mkv_path.stem})"],
            "session_summary": "Sessão gravada com amigos no Discord.",
            "chapters": [{"timestamp": "00:00:00", "title": "Início da Gravação"}],
            "topics": [],
            "highlights": [],
            "speaker_mapping": {},
            "ai_warning": "",
            "raw_transcript_prompt": transcript_for_ai,
            "speaker_samples": speaker_samples,
            "segments": segments,
            "hype_scores": hype_scores
        }
        ai_data["youtube_description"] = build_youtube_description(ai_data)

        if mode != "ai_only":
            save_video_transcription_checkpoint(
                video_id=video_id,
                game_name=ai_data["main_game"],
                duration=duration_sec,
                mosaic_path=f"/mosaics/{mosaic_jpg.name}",
                srt_path=str(srt_path),
                ai_data=ai_data,
                progress_msg=f"Transcrição e vozes salvas no banco! Consultando Diretor Criativo ({engine})...",
                progress_pct=0.90
            )
            print(
                f"[CHECKPOINT] ✅ Transcrição ({len(segments)} falas), vozes e .SRT salvos no banco SQLite "
                f"ANTES da chamada à API! Se a API falhar, nada será perdido.",
                flush=True
            )

        report(f"Etapa Final: Consultando Diretor Criativo de IA ({engine})...", 0.92)

        google_key = get_setting("google_api_key", "")
        nvidia_key = get_setting("nvidia_api_key", "")
        google_model = custom_model or get_setting("google_model", DEFAULT_GOOGLE_MODEL)
        nvidia_model = custom_model or get_setting("nvidia_model", DEFAULT_NVIDIA_MODEL)

        if engine == "gemini":
            if not google_key:
                msg_warn = "Chave da API Google Gemini não configurada. Legenda .SRT gerada; configure a chave ou use Claude Web."
                print(f"[AVISO IA] {msg_warn}", flush=True)
                ai_data["ai_warning"] = msg_warn
            else:
                try:
                    ai_res = analyze_with_google_gemini(
                        google_key, transcript_for_ai,
                        mosaic_jpg_path=mosaic_jpg,
                        model_name=google_model,
                        duration_sec=duration_sec,
                        progress_callback=report
                    )
                    ai_data.update(ai_res)
                    ai_data["ai_warning"] = ""
                except Exception as ai_err:
                    err_txt = f"Falha na etapa da IA Gemini ({google_model}): {ai_err}"
                    print(f"[AVISO IA] {err_txt}", flush=True)
                    if mode == "ai_only":
                        raise RuntimeError(err_txt)
                    ai_data["ai_warning"] = err_txt
        elif engine == "nvidia":
            if not nvidia_key:
                msg_warn = "Chave da API NVIDIA NIM não configurada. Legenda .SRT gerada; configure a chave ou use Claude Web."
                print(f"[AVISO IA] {msg_warn}", flush=True)
                ai_data["ai_warning"] = msg_warn
            else:
                try:
                    ai_res = analyze_with_nvidia_nim(
                        nvidia_key, transcript_for_ai,
                        model_name=nvidia_model,
                        duration_sec=duration_sec,
                        progress_callback=report
                    )
                    ai_data.update(ai_res)
                    ai_data["ai_warning"] = ""
                except Exception as ai_err:
                    err_txt = f"Falha na etapa da IA NVIDIA ({nvidia_model}): {ai_err}"
                    print(f"[AVISO IA] {err_txt}", flush=True)
                    if mode == "ai_only":
                        raise RuntimeError(err_txt)
                    ai_data["ai_warning"] = err_txt
        else:
            print("[IA CLAUDE WEB] Pacote de prompt preparado para cópia manual no Claude Web!", flush=True)

        # 4. Deduções da IA mantidas APENAS como sugestão no painel (SEM auto-salvar amostras .WAV!)
        if isinstance(ai_data.get("speaker_mapping"), dict) and ai_data["speaker_mapping"]:
            print(
                f"[VOZES] Sugestões de nomes da IA registradas ({len(ai_data['speaker_mapping'])} voz(es)) — "
                f"nenhuma amostra .WAV foi salva automaticamente. Ouça os trechos no painel e confirme manualmente!",
                flush=True
            )

        # Recalcular descrição final do YouTube já com os integrantes finais (segments + speaker_mapping)
        ai_data["youtube_description"] = build_youtube_description(ai_data)

        # Persistir resultado completo no SQLite ANTES da etapa opcional de normalização de áudio
        # (garante que a transcrição, vozes e análise da IA fiquem 100% salvas mesmo se faltar espaço em disco)
        save_video_result(
            video_id=video_id,
            game_name=ai_data.get("main_game", manual_game or "Gameplay"),
            duration=duration_sec,
            mosaic_path=f"/mosaics/{mosaic_jpg.name}",
            srt_path=str(srt_path),
            ai_data=ai_data,
            status_msg="Análise de IA concluída!"
        )

        audio_norm_suffix = ""
        if mode == "full" and normalize_audio:
            def _norm_cb(sub_msg: str, sub_pct: float):
                mapped_pct = 0.95 + min(0.04, float(sub_pct) * 0.04)
                report(f"Áudio YouTube Gold: {sub_msg}", mapped_pct)

            if single_pass_prep and single_pass_prep.get("already_normalized"):
                set_video_audio_normalized(video_id, normalized=True)
                print("[AUDIO NORM] ✅ Vídeo já estava normalizado (YouTube Gold); etapa pulada automaticamente.", flush=True)
                audio_norm_suffix = " | Áudio já normalizado"
            elif single_pass_norm_mkv and single_pass_norm_mkv.exists():
                print("\n[AUDIO NORM] ⚡ Passada Única concluída em paralelo! Validando e substituindo MKV instantaneamente...", flush=True)
                report("Validando e aplicando MKV YouTube Gold (Passada Única)...", 0.96)
                norm_res = finalize_normalized_mkv(
                    mkv_path=mkv_path,
                    temp_mkv=single_pass_norm_mkv,
                    orig_info=single_pass_prep.get("orig_info") if single_pass_prep else None,
                    orig_size=single_pass_prep.get("orig_size") if single_pass_prep else None,
                    orig_atime=single_pass_prep.get("orig_atime") if single_pass_prep else None,
                    orig_mtime=single_pass_prep.get("orig_mtime") if single_pass_prep else None,
                    t_start=single_pass_t0,
                    progress_callback=_norm_cb
                )
                if norm_res.get("success"):
                    set_video_audio_normalized(
                        video_id,
                        normalized=True,
                        size_gb=norm_res.get("size_gb"),
                        mtime=norm_res.get("mtime")
                    )
                    audio_norm_suffix = " | Áudio YouTube Gold (Passada Única)"
                else:
                    norm_err = norm_res.get("error", "Falha desconhecida")
                    print(f"[AUDIO NORM] ⚠️ Validação da Passada Única falhou (transcrição e IA preservadas): {norm_err}", flush=True)
                    audio_norm_suffix = f" | Aviso áudio: {norm_err[:80]}"
            elif single_pass_prep and not single_pass_prep.get("should_run") and single_pass_prep.get("error"):
                norm_err = single_pass_prep["error"]
                print(f"[AUDIO NORM] ⚠️ Normalização pulada (transcrição e IA preservadas): {norm_err}", flush=True)
                audio_norm_suffix = f" | Aviso áudio: {norm_err[:80]}"
            else:
                # Fallback (ex.: transcrição reaproveitada de checkpoint anterior onde o FFmpeg não rodou novamente)
                print("\n[AUDIO NORM] Transcrição reaproveitada de checkpoint; executando normalização YouTube Gold (vídeo 100% lossless)...", flush=True)
                report("Etapa Opcional: Normalizando áudio YouTube Gold (vídeo 100% lossless)...", 0.95)
                norm_res = normalize_mkv_audio_lossless(mkv_path, progress_callback=_norm_cb, force=False)
                if norm_res.get("success"):
                    set_video_audio_normalized(
                        video_id,
                        normalized=True,
                        size_gb=norm_res.get("size_gb"),
                        mtime=norm_res.get("mtime")
                    )
                    if norm_res.get("skipped"):
                        print("[AUDIO NORM] ✅ Vídeo já estava normalizado (YouTube Gold); etapa pulada automaticamente.", flush=True)
                        audio_norm_suffix = " | Áudio já normalizado"
                    else:
                        audio_norm_suffix = " | Áudio YouTube Gold normalizado"
                else:
                    norm_err = norm_res.get("error", "Falha desconhecida")
                    print(f"[AUDIO NORM] ⚠️ Normalização opcional não concluída (transcrição e IA preservadas): {norm_err}", flush=True)
                    audio_norm_suffix = f" | Aviso áudio: {norm_err[:80]}"

        total_elapsed = time.time() - t_start
        done_msg = f"Concluído em {int(total_elapsed // 60)}m {int(total_elapsed % 60)}s!{audio_norm_suffix}"
        if ai_data.get("ai_warning"):
            done_msg += " (Legenda pronta; verifique aviso da IA)"

        update_video_progress(video_id, done_msg, 1.0, "completed")
        finish_job_record(job_id, status="completed", last_step=done_msg)
        job_succeeded = True
        print("========================================================================", flush=True)
        print(f"✅ [SUCESSO] Processamento finalizado com êxito em {total_elapsed:.1f}s!", flush=True)
        print("========================================================================", flush=True)

    except Exception as e:
        tb = traceback.format_exc()
        print(f"\n❌ [ERRO FATAL] {e}\n{tb}", flush=True)
        err_short = f"Erro: {str(e)}"
        if video_id > 0:
            if has_video_data(video_id):
                update_video_progress(
                    video_id,
                    f"Aviso (transcrição e .SRT preservados!): {str(e)[:140]}",
                    1.0,
                    "completed"
                )
            elif mode == "normalize_audio":
                update_video_progress(video_id, err_short, 0.0, "error")
            else:
                update_video_progress(video_id, err_short, 0.0, "error")
        finish_job_record(job_id, status="error", last_step="Falhou com erro", error_detail=str(e))
        sys.exit(1)
    finally:
        if job_succeeded and temp_job_dir.exists():
            try:
                shutil.rmtree(temp_job_dir, ignore_errors=True)
                print(f"[LIMPEZA] Pasta temporária {temp_job_dir.name} removida.", flush=True)
            except Exception:
                pass
        elif not job_succeeded and temp_job_dir.exists():
            print(f"[CHECKPOINT] Arquivos intermediários mantidos em {temp_job_dir.name} para retomada automática na próxima tentativa.", flush=True)
        sys.stdout = orig_out
        sys.stderr = orig_err
        logger_stream.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Banido Studio Worker")
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--video-id", type=int, required=True)
    parser.add_argument("--mode", type=str, default="full", choices=["full", "ai_only", "normalize_audio", "normalize_batch"])
    parser.add_argument("--game", type=str, default="")
    parser.add_argument("--context", type=str, default="")
    parser.add_argument("--engine", type=str, default="gemini")
    parser.add_argument("--model", type=str, default="")
    parser.add_argument("--normalize-audio", action="store_true", default=False)
    parser.add_argument("--separate-tracks", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--no-notify", action="store_true", default=False)
    args = parser.parse_args()

    from app.keep_awake import stay_awake
    from app.notifications import notify_job_end

    # E4: o PC não suspende enquanto o job roda e o resultado é avisado ao terminar.
    with stay_awake():
        try:
            run_worker(
                job_id=args.job_id,
                video_id=args.video_id,
                mode=args.mode,
                manual_game=args.game,
                engine=args.engine,
                custom_model=args.model,
                extra_context=args.context,
                normalize_audio=args.normalize_audio,
                separate_tracks=args.separate_tracks,
            )
        finally:
            # Só jobs longos avisam; reanálise de IA e ajuste de volume de um vídeo o usuário acompanha na tela.
            if args.mode in ("full", "normalize_batch") and not args.no_notify:
                notify_job_end(args.job_id, wait=True)
