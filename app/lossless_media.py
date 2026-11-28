import os
import re
import json
import time
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from xml.sax.saxutils import escape as xml_escape
from app.clip_verify import verify_clip
from app.config import CLIPS_DIR
from app.audio_pipeline import (
    YOUTUBE_AUDIO_NORM_TAG,
    YOUTUBE_GOLD_FILTER_MULTI,
    YOUTUBE_GOLD_FILTER_SINGLE,
    get_audio_stream_count_and_duration,
    parse_time_to_seconds,
)


def inspect_mkv_audio_norm(mkv_path: Path) -> Dict[str, Any]:
    """
    Inspeciona streams de vídeo/áudio, duração e verifica se o MKV já recebeu
    a normalização YouTube Gold (tag BANIDO_AUDIO_NORM no container ou Faixa 1).
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(mkv_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    duration = float(fmt.get("duration", 0.0) or 0.0)

    fmt_tags = {str(k).upper(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    norm_tag = fmt_tags.get("BANIDO_AUDIO_NORM", "")
    if not norm_tag and audio_streams:
        a0_tags = {str(k).upper(): str(v) for k, v in (audio_streams[0].get("tags") or {}).items()}
        norm_tag = a0_tags.get("BANIDO_AUDIO_NORM", "")
        if not norm_tag and "youtube gold" in a0_tags.get("TITLE", "").lower():
            norm_tag = YOUTUBE_AUDIO_NORM_TAG

    original_backup_idx = None
    for idx_a, a_st in enumerate(audio_streams):
        a_tags = {str(k).upper(): str(v) for k, v in (a_st.get("tags") or {}).items()}
        if a_tags.get("BANIDO_ORIGINAL_MIX") or "original mix backup" in a_tags.get("TITLE", "").lower():
            original_backup_idx = idx_a
            break

    return {
        "audio_streams": len(audio_streams),
        "video_streams": len(video_streams),
        "duration": duration,
        "is_normalized": bool(norm_tag),
        "norm_tag": norm_tag,
        "has_original_backup": original_backup_idx is not None,
        "original_backup_index": original_backup_idx,
    }


def sanitize_filename(name: str, max_len: int = 120) -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", str(name or ""))
    # Substituir colchetes [] por hífen conforme padrão YouTube / Windows
    clean = re.sub(r"^\[\s*([^\[\]]+?)\s*\]\s*[-–—:]?\s*", r"\1 - ", clean)
    clean = re.sub(r"\[\s*([^\[\]]+?)\s*\]", r"\1", clean)
    clean = clean.replace("[", "-").replace("]", "-")
    clean = re.sub(r"\s*-\s*-\s*", " - ", clean)
    clean = re.sub(r'\s+', " ", clean).strip(" -")
    return clean[:max_len]


def _unique_path(path: Path) -> Path:
    """Evita sobrescrita silenciosa acrescentando sufixo ' (2)', ' (3)', ... caso o arquivo já exista."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        cand = parent / f"{stem} ({counter}){suffix}"
        if not cand.exists():
            return cand
        counter += 1


def rename_mkv_and_srt(original_mkv_path: Path, title: str, game_name: str = "") -> Dict[str, Any]:
    if not original_mkv_path.exists():
        return {"success": False, "error": f"Arquivo não encontrado: {original_mkv_path}"}

    parent_dir = original_mkv_path.parent
    base_name = original_mkv_path.stem

    match_date = re.match(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}-\d{2}-\d{2})", base_name)
    prefix_date = match_date.group(1) if match_date else ""

    clean_title = sanitize_filename(title)
    clean_game = sanitize_filename(game_name) if game_name else ""
    if clean_game and clean_game.lower() not in clean_title.lower():
        clean_title = f"{clean_game} - {clean_title}"

    if prefix_date:
        new_stem = f"{prefix_date} - {clean_title}" if clean_title else prefix_date
    else:
        new_stem = clean_title or base_name

    new_mkv_path = parent_dir / f"{new_stem}.mkv"
    if new_mkv_path != original_mkv_path and new_mkv_path.exists():
        new_mkv_path = _unique_path(new_mkv_path)

    try:
        if original_mkv_path != new_mkv_path:
            original_mkv_path.rename(new_mkv_path)
            print(f"Vídeo renomeado para: {new_mkv_path.name}")
    except Exception as e:
        return {"success": False, "error": f"Erro ao renomear MKV: {e}"}

    old_srt = original_mkv_path.with_suffix(".srt")
    new_srt = new_mkv_path.with_suffix(".srt")
    if old_srt.exists() and old_srt != new_srt:
        try:
            if new_srt.exists():
                new_srt = _unique_path(new_srt)
            old_srt.rename(new_srt)
            print(f"Legenda renomeada para: {new_srt.name}")
        except Exception as e:
            print(f"Aviso: não foi possível renomear SRT: {e}")

    return {
        "success": True,
        "old_name": original_mkv_path.name,
        "new_name": new_mkv_path.name,
        "new_path": str(new_mkv_path),
        "srt_path": str(new_srt) if new_srt.exists() else None
    }


def export_lossless_highlight(
    mkv_path: Path,
    start_sec: float,
    end_sec: float,
    clip_title: str,
    boost_discord: bool = True,
    keep_all_tracks: bool = False,
    exact_cut: bool = False,
) -> Dict[str, Any]:
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    clean_clip_name = sanitize_filename(clip_title, max_len=80) or "Highlight"

    # Evitar estouro do limite MAX_PATH (260 chars) no Windows usando prefixo compacto do vídeo
    match_date = re.match(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}-\d{2}-\d{2})", mkv_path.stem)
    source_prefix = match_date.group(1) if match_date else sanitize_filename(mkv_path.stem, max_len=45)
    output_clip_path = _unique_path(CLIPS_DIR / f"{source_prefix} - Corte - {clean_clip_name}.mkv")

    dur = max(1.0, float(end_sec) - float(start_sec))
    start_sec = max(0.0, float(start_sec))

    already_normalized = False
    source_duration = 0.0
    try:
        info = inspect_mkv_audio_norm(mkv_path)
        stream_count = info["audio_streams"]
        already_normalized = info["is_normalized"]
        source_duration = float(info.get("duration") or 0.0)
    except Exception:
        stream_count = 1

    video_codec_args = (
        ["-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p"]
        if exact_cut
        else ["-c:v", "copy"]
    )
    seek_args = (
        ["-ss", f"{max(0.0, start_sec - 4.0):.2f}", "-i", str(mkv_path), "-ss", f"{min(4.0, start_sec):.2f}"]
        if exact_cut
        else ["-ss", f"{start_sec:.2f}", "-i", str(mkv_path)]
    )

    if already_normalized or not boost_discord:
        # Se o MKV já teve a Faixa 1 normalizada no padrão YouTube Gold, evita normalizar 2x
        audio_codec_args = (
            ["-c:a:0", "aac", "-b:a:0", "320k", "-ar:a:0", "48000", "-ac:a:0", "2"]
            if exact_cut
            else ["-c:a", "copy"]
        )
        cmd = [
            "ffmpeg", "-y",
            *seek_args,
            "-t", f"{dur:.2f}",
            "-map", "0:v:0",
            "-map", "0:a" if keep_all_tracks else "0:a:0",
            *video_codec_args,
            *audio_codec_args,
        ]
        if exact_cut and keep_all_tracks and stream_count > 1:
            for a_idx in range(1, stream_count):
                cmd.extend([f"-c:a:{a_idx}", "aac", f"-b:a:{a_idx}", "192k"])
        cmd.extend(["-avoid_negative_ts", "make_zero", str(output_clip_path)])
    elif stream_count >= 3:
        cmd = [
            "ffmpeg", "-y",
            *seek_args,
            "-t", f"{dur:.2f}",
            "-filter_complex", YOUTUBE_GOLD_FILTER_MULTI,
            "-map", "0:v:0",
            "-map", "[aout]",
        ]
        if keep_all_tracks:
            for a_idx in range(1, stream_count):
                cmd.extend(["-map", f"0:a:{a_idx}"])
        cmd.extend([
            *video_codec_args,
            "-c:a:0", "aac", "-b:a:0", "320k", "-ar:a:0", "48000", "-ac:a:0", "2",
        ])
        if keep_all_tracks:
            for a_idx in range(1, stream_count):
                cmd.extend([f"-c:a:{a_idx}", "aac" if exact_cut else "copy"])
        cmd.extend(["-avoid_negative_ts", "make_zero", str(output_clip_path)])
    else:
        cmd = [
            "ffmpeg", "-y",
            *seek_args,
            "-t", f"{dur:.2f}",
            "-filter_complex", YOUTUBE_GOLD_FILTER_SINGLE,
            "-map", "0:v:0",
            "-map", "[aout]",
        ]
        if keep_all_tracks and stream_count > 1:
            for a_idx in range(1, stream_count):
                cmd.extend(["-map", f"0:a:{a_idx}"])
        cmd.extend([
            *video_codec_args,
            "-c:a:0", "aac", "-b:a:0", "320k", "-ar:a:0", "48000", "-ac:a:0", "2",
        ])
        if keep_all_tracks and stream_count > 1:
            for a_idx in range(1, stream_count):
                cmd.extend([f"-c:a:{a_idx}", "aac" if exact_cut else "copy"])
        cmd.extend(["-avoid_negative_ts", "make_zero", str(output_clip_path)])

    try:
        subprocess.run(cmd, capture_output=True, check=True)
        # C4: conferência automática (nunca apaga o corte; só informa verde/amarelo/vermelho)
        expected_dur = dur
        if source_duration > 0:
            expected_dur = max(1.0, min(start_sec + dur, source_duration) - start_sec)
        expected_tracks = max(1, stream_count) if keep_all_tracks else 1
        try:
            verification = verify_clip(output_clip_path, expected_dur, expected_tracks)
        except Exception as e:  # a conferência jamais pode derrubar uma exportação que deu certo
            verification = {"status": "warn", "message": f"Não foi possível conferir o corte automaticamente: {e}"}
        return {
            "success": True,
            "clip_path": str(output_clip_path),
            "filename": output_clip_path.name,
            "duration": dur,
            "exact_cut": exact_cut,
            "verification": verification,
        }
    except subprocess.CalledProcessError as e:
        return {
            "success": False,
            "error": e.stderr.decode("utf-8", errors="ignore")[-500:]
        }


def _parse_ts_sec(val: Any) -> Optional[float]:
    if isinstance(val, (int, float)):
        return float(val)
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return None
    return None


def _ms_to_mkv_time(ms: int) -> str:
    ms = max(0, int(ms))
    hrs = ms // 3600000
    mins = (ms % 3600000) // 60000
    secs = (ms % 60000) // 1000
    rem_ms = ms % 1000
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{rem_ms:03d}000000"


def _escape_ffmetadata(val: str) -> str:
    s = str(val or "").replace("\\", "\\\\")
    for ch in ("=", ";", "#", "\n"):
        s = s.replace(ch, f"\\{ch}")
    return s


def _build_chapter_entries(
    chapters: List[Dict[str, Any]],
    highlights: List[Dict[str, Any]],
    total_duration_sec: float,
    mode: str = "both"
) -> List[Dict[str, Any]]:
    total_ms = max(1000, int(round(total_duration_sec * 1000)))
    entries: List[Dict[str, Any]] = []

    if mode in ("chapters", "both"):
        for c in chapters or []:
            if not isinstance(c, dict):
                continue
            st = _parse_ts_sec(c.get("timestamp") or c.get("time"))
            if st is None or st < 0 or st * 1000 >= total_ms:
                continue
            title = str(c.get("title") or c.get("name") or "Capítulo").strip()
            entries.append({
                "start_ms": int(round(st * 1000)),
                "end_ms": None,
                "title": title
            })

    if mode in ("highlights", "both"):
        for h in highlights or []:
            if not isinstance(h, dict):
                continue
            st = _parse_ts_sec(h.get("start"))
            if st is None:
                st = _parse_ts_sec(h.get("start_time"))
            et = _parse_ts_sec(h.get("end"))
            if et is None:
                et = _parse_ts_sec(h.get("end_time"))
            if st is None or st < 0 or st * 1000 >= total_ms:
                continue
            start_ms = int(round(st * 1000))
            end_ms = int(round(min(et, total_duration_sec) * 1000)) if (et is not None and et > st) else min(total_ms, start_ms + 30000)
            score = h.get("score", 90)
            title = str(h.get("title") or "Melhor Momento").strip()
            entries.append({
                "start_ms": start_ms,
                "end_ms": end_ms,
                "title": f"★ Hype {score} - {title}"
            })

    entries.sort(key=lambda e: e["start_ms"])

    # Preencher end_ms ausentes e garantir intervalos válidos
    cleaned: List[Dict[str, Any]] = []
    for i, e in enumerate(entries):
        s_ms = e["start_ms"]
        if e["end_ms"] is not None and e["end_ms"] > s_ms:
            e_ms = min(total_ms, e["end_ms"])
        elif i + 1 < len(entries) and entries[i + 1]["start_ms"] > s_ms:
            e_ms = entries[i + 1]["start_ms"]
        else:
            e_ms = total_ms
        if e_ms <= s_ms:
            e_ms = min(total_ms, s_ms + 1000)
        cleaned.append({
            "start_ms": s_ms,
            "end_ms": e_ms,
            "title": e["title"]
        })

    return cleaned


def find_mkvpropedit() -> Optional[str]:
    """
    Localiza o executável mkvpropedit (MKVToolNix) tanto no PATH do sistema
    quanto nos diretórios padrão de instalação do Windows.
    """
    found = shutil.which("mkvpropedit")
    if found:
        return found

    if os.name == "nt":
        candidates = []
        for env_var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_var)
            if base:
                candidates.append(Path(base) / "MKVToolNix" / "mkvpropedit.exe")
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "Programs" / "MKVToolNix" / "mkvpropedit.exe")
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            candidates.append(Path(user_profile) / "scoop" / "shims" / "mkvpropedit.exe")
        candidates.extend([
            Path(r"C:\Program Files\MKVToolNix\mkvpropedit.exe"),
            Path(r"C:\Program Files (x86)\MKVToolNix\mkvpropedit.exe"),
            Path(r"C:\ProgramData\chocolatey\bin\mkvpropedit.exe"),
        ])

        seen = set()
        for cand in candidates:
            key = str(cand).lower()
            if key in seen:
                continue
            seen.add(key)
            if cand.exists():
                return str(cand)

    return None


def embed_chapters_into_mkv(
    mkv_path: Path,
    chapters: List[Dict[str, Any]],
    highlights: List[Dict[str, Any]],
    mode: str = "both"
) -> Dict[str, Any]:
    """
    Embute capítulos e/ou melhores momentos diretamente no arquivo MKV original
    SEM re-renderização (100% lossless) e preservando o mtime original.
    Usa mkvpropedit (in-place em <0.5s) se instalado; caso contrário usa FFmpeg -c copy
    com substituição atômica segura após verificação com ffprobe.
    """
    if not mkv_path.exists():
        return {"success": False, "error": f"Arquivo MKV não encontrado: {mkv_path}"}

    try:
        orig_stat = mkv_path.stat()
        orig_atime = orig_stat.st_atime
        orig_mtime = orig_stat.st_mtime
        orig_streams, total_dur = get_audio_stream_count_and_duration(mkv_path)
    except Exception as e:
        return {"success": False, "error": f"Falha ao inspecionar MKV com ffprobe: {e}"}

    entries = _build_chapter_entries(chapters, highlights, total_dur, mode=mode)
    if not entries:
        return {"success": False, "error": "Nenhum capítulo ou melhor momento válido para embutir."}

    tmp_dir = Path(tempfile.mkdtemp(prefix="banido_chapters_"))
    mkvpropedit = find_mkvpropedit()
    t0 = time.time()

    try:
        if mkvpropedit:
            # Modo instantâneo in-place (<0.5s até em arquivos de 137 GB!)
            xml_lines = [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<!DOCTYPE Chapters SYSTEM "matroskachapters.dtd">',
                '<Chapters>',
                '  <EditionEntry>'
            ]
            for e in entries:
                xml_lines.extend([
                    '    <ChapterAtom>',
                    f'      <ChapterTimeStart>{_ms_to_mkv_time(e["start_ms"])}</ChapterTimeStart>',
                    f'      <ChapterTimeEnd>{_ms_to_mkv_time(e["end_ms"])}</ChapterTimeEnd>',
                    '      <ChapterDisplay>',
                    f'        <ChapterString>{xml_escape(e["title"])}</ChapterString>',
                    '        <ChapterLanguage>por</ChapterLanguage>',
                    '        <ChapLanguageIETF>pt-BR</ChapLanguageIETF>',
                    '      </ChapterDisplay>',
                    '    </ChapterAtom>'
                ])
            xml_lines.extend(['  </EditionEntry>', '</Chapters>'])
            xml_path = tmp_dir / "chapters.xml"
            xml_path.write_text("\n".join(xml_lines), encoding="utf-8")

            res = subprocess.run(
                [mkvpropedit, str(mkv_path), "--chapters", str(xml_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            if res.returncode != 0:
                err_msg = (res.stderr or res.stdout or "").strip()[-400:]
                return {"success": False, "error": f"mkvpropedit falhou: {err_msg}"}

            try:
                os.utime(str(mkv_path), (orig_atime, orig_mtime))
            except Exception:
                pass

            elapsed = round(time.time() - t0, 2)
            return {
                "success": True,
                "method": f"mkvpropedit (in-place instantâneo em {elapsed:.2f}s)",
                "elapsed_sec": elapsed,
                "chapters_count": len(entries),
                "filename": mkv_path.name
            }

        # Fallback 100% lossless via FFmpeg stream copy (-c copy)
        meta_lines = [";FFMETADATA1"]
        for e in entries:
            meta_lines.extend([
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={e['start_ms']}",
                f"END={e['end_ms']}",
                f"title={_escape_ffmetadata(e['title'])}"
            ])
        meta_path = tmp_dir / "chapters.ffmeta"
        meta_path.write_text("\n".join(meta_lines) + "\n", encoding="utf-8")

        temp_mkv = mkv_path.with_name(f".{mkv_path.stem}.chapters_tmp.mkv")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(mkv_path),
            "-i", str(meta_path),
            "-map", "0",
            "-map_metadata", "0",
            "-map_chapters", "1",
            "-c", "copy",
            str(temp_mkv)
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0 or not temp_mkv.exists():
            if temp_mkv.exists():
                temp_mkv.unlink(missing_ok=True)
            return {
                "success": False,
                "error": f"FFmpeg falhou ao embutir capítulos: {res.stderr.decode('utf-8', errors='ignore')[-400:]}"
            }

        # Verificação de integridade antes de substituir o arquivo original
        orig_size = mkv_path.stat().st_size
        new_size = temp_mkv.stat().st_size
        if new_size < orig_size * 0.90:
            temp_mkv.unlink(missing_ok=True)
            return {"success": False, "error": "Verificação de integridade falhou (arquivo resultante menor que o esperado). Original preservado."}

        new_streams, _ = get_audio_stream_count_and_duration(temp_mkv)
        if new_streams != orig_streams:
            temp_mkv.unlink(missing_ok=True)
            return {"success": False, "error": "Contagem de faixas de áudio divergiu após remux. Original preservado."}

        os.replace(str(temp_mkv), str(mkv_path))
        try:
            os.utime(str(mkv_path), (orig_atime, orig_mtime))
        except Exception:
            pass

        elapsed = round(time.time() - t0, 2)
        return {
            "success": True,
            "method": f"ffmpeg -c copy (lossless em {elapsed:.1f}s)",
            "elapsed_sec": elapsed,
            "chapters_count": len(entries),
            "filename": mkv_path.name
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _fmt_hhmmss(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def prepare_single_pass_norm(mkv_path: Path) -> Dict[str, Any]:
    """
    Valida pré-condições para a normalização YouTube Gold em Passada Única
    (durante a extração paralela do Whisper).
    Retorna {"should_run": True, "temp_mkv": Path, "orig_info": dict, ...} ou o motivo de skip/erro.
    """
    if not mkv_path.exists():
        return {"should_run": False, "already_normalized": False, "error": f"Arquivo MKV não encontrado: {mkv_path}"}

    try:
        with open(mkv_path, "rb") as tf:
            tf.read(16)
    except OSError as e:
        return {"should_run": False, "already_normalized": False, "error": f"Sem permissão ou arquivo em uso/bloqueado: {e}"}

    try:
        orig_stat = mkv_path.stat()
        orig_size = orig_stat.st_size
        info = inspect_mkv_audio_norm(mkv_path)
    except Exception as e:
        return {"should_run": False, "already_normalized": False, "error": f"Falha ao inspecionar MKV com ffprobe: {e}"}

    if info["audio_streams"] == 0:
        return {"should_run": False, "already_normalized": False, "error": "O vídeo não possui nenhuma faixa de áudio."}

    if info["is_normalized"]:
        return {
            "should_run": False,
            "already_normalized": True,
            "norm_tag": info["norm_tag"],
            "orig_info": info
        }

    free_bytes = shutil.disk_usage(str(mkv_path.parent)).free
    needed_bytes = orig_size + (2 * 1024 ** 3)
    if free_bytes < needed_bytes:
        return {
            "should_run": False,
            "already_normalized": False,
            "error": (
                f"Espaço livre insuficiente em {mkv_path.drive or mkv_path.parent}: "
                f"{free_bytes / (1024**3):.1f} GB livres, necessário pelo menos {needed_bytes / (1024**3):.1f} GB "
                f"para remux seguro deste arquivo ({orig_size / (1024**3):.1f} GB)."
            )
        }

    temp_mkv = mkv_path.with_name(f".{mkv_path.stem}.audio_norm_tmp.mkv")
    return {
        "should_run": True,
        "already_normalized": False,
        "temp_mkv": temp_mkv,
        "orig_info": info,
        "orig_size": orig_size,
        "orig_atime": orig_stat.st_atime,
        "orig_mtime": orig_stat.st_mtime
    }


def finalize_normalized_mkv(
    mkv_path: Path,
    temp_mkv: Path,
    orig_info: Optional[Dict[str, Any]] = None,
    orig_size: Optional[int] = None,
    orig_atime: Optional[float] = None,
    orig_mtime: Optional[float] = None,
    t_start: Optional[float] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> Dict[str, Any]:
    """
    Valida integralmente um MKV temporário normalizado com ffprobe (tamanho >= 90%,
    streams de vídeo/áudio e duração) e substitui o original atomicamente (os.replace)
    preservando o mtime original. Remove temp_mkv em caso de falha.
    """
    t0 = t_start if t_start is not None else time.time()
    replaced = False
    try:
        if not temp_mkv.exists():
            return {"success": False, "error": f"Arquivo temporário normalizado não encontrado: {temp_mkv.name}"}

        if orig_size is None or orig_atime is None or orig_mtime is None:
            st = mkv_path.stat()
            orig_size = st.st_size
            orig_atime = st.st_atime
            orig_mtime = st.st_mtime

        if orig_info is None:
            orig_info = inspect_mkv_audio_norm(mkv_path)

        orig_audio = orig_info["audio_streams"]
        total_dur = orig_info["duration"]

        if progress_callback:
            progress_callback("Validando integridade do MKV normalizado com ffprobe...", 0.94)

        new_size = temp_mkv.stat().st_size
        if new_size < orig_size * 0.90:
            return {
                "success": False,
                "error": (
                    f"Verificação de integridade falhou (arquivo resultante {new_size / (1024**3):.2f} GB "
                    f"menor que 90% do original {orig_size / (1024**3):.2f} GB). Original preservado!"
                )
            }

        new_info = inspect_mkv_audio_norm(temp_mkv)
        expected_audio = orig_audio + (0 if orig_info.get("has_original_backup") else 1)
        if new_info["video_streams"] < 1 or new_info["audio_streams"] not in (orig_audio, expected_audio):
            return {
                "success": False,
                "error": (
                    f"Contagem de streams divergiu após remux (vídeo={new_info['video_streams']}, "
                    f"áudio={new_info['audio_streams']} vs esperado {expected_audio}). Original preservado!"
                )
            }

        dur_tol = max(3.0, total_dur * 0.01)
        if total_dur > 0 and abs(new_info["duration"] - total_dur) > dur_tol:
            return {
                "success": False,
                "error": (
                    f"Duração divergiu após remux ({new_info['duration']:.1f}s vs {total_dur:.1f}s). "
                    f"Original preservado!"
                )
            }

        if progress_callback:
            progress_callback("Substituindo arquivo original com segurança...", 0.98)

        os.replace(str(temp_mkv), str(mkv_path))
        replaced = True
        try:
            os.utime(str(mkv_path), (orig_atime, orig_mtime))
        except Exception:
            pass

        elapsed = round(time.time() - t0, 1)
        final_size_gb = round(mkv_path.stat().st_size / (1024 ** 3), 2)
        final_audio = new_info["audio_streams"]
        backup_note = " | Mix original preservado como faixa extra (backup)" if new_info.get("has_original_backup") else ""
        print(
            f"[AUDIO NORM] ✅ Concluído em {elapsed}s! {mkv_path.name} ({final_size_gb} GB) "
            f"| Vídeo 4K AV1 100% lossless | Faixa 1 YouTube Gold | Faixas 2-{orig_audio} preservadas{backup_note}.",
            flush=True
        )
        return {
            "success": True,
            "skipped": False,
            "filename": mkv_path.name,
            "size_gb": final_size_gb,
            "mtime": orig_mtime,
            "duration": new_info["duration"],
            "audio_streams": final_audio,
            "has_original_backup": bool(new_info.get("has_original_backup")),
            "elapsed_sec": elapsed,
            "norm_tag": YOUTUBE_AUDIO_NORM_TAG
        }
    except Exception as e:
        return {"success": False, "error": f"Erro ao validar/substituir MKV normalizado: {e}"}
    finally:
        if not replaced and temp_mkv.exists():
            try:
                temp_mkv.unlink()
            except Exception:
                pass


def normalize_mkv_audio_lossless(
    mkv_path: Path,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    force: bool = False
) -> Dict[str, Any]:
    """
    Normaliza a Faixa 1 (Mix Geral — utilizada pelo YouTube e players) no padrão
    YouTube Gold (-15.5 LUFS, True Peak -1.0 dBTP, Mic + Discord equilibrados),
    mantendo o vídeo 4K 60fps AV1 100% LOSSLESS (-c:v copy), preservando todas as
    faixas auxiliares (2..6) intactas (-c:a copy), capítulos e metadados.

    Executa validação integral com ffprobe antes de substituir o arquivo original
    atomicamente (os.replace) e preserva o mtime original.
    """
    if not mkv_path.exists():
        return {"success": False, "error": f"Arquivo MKV não encontrado: {mkv_path}"}

    # 1. Testar acesso de leitura (captura arquivos bloqueados/sem permissão antes do FFmpeg)
    try:
        with open(mkv_path, "rb") as tf:
            tf.read(16)
    except OSError as e:
        return {"success": False, "error": f"Sem permissão ou arquivo em uso/bloqueado: {e}"}

    try:
        orig_stat = mkv_path.stat()
        orig_size = orig_stat.st_size
        orig_atime = orig_stat.st_atime
        orig_mtime = orig_stat.st_mtime
        info = inspect_mkv_audio_norm(mkv_path)
    except Exception as e:
        return {"success": False, "error": f"Falha ao inspecionar MKV com ffprobe: {e}"}

    if info["audio_streams"] == 0:
        return {"success": False, "error": "O vídeo não possui nenhuma faixa de áudio."}

    if info["is_normalized"] and not force:
        return {
            "success": True,
            "skipped": True,
            "already_normalized": True,
            "norm_tag": info["norm_tag"],
            "filename": mkv_path.name,
            "message": f"Áudio já normalizado ({info['norm_tag']})."
        }

    # 2. Verificar espaço livre em disco (exige tamanho do arquivo + 2 GB de margem)
    free_bytes = shutil.disk_usage(str(mkv_path.parent)).free
    needed_bytes = orig_size + (2 * 1024 ** 3)
    if free_bytes < needed_bytes:
        return {
            "success": False,
            "error": (
                f"Espaço livre insuficiente em {mkv_path.drive or mkv_path.parent}: "
                f"{free_bytes / (1024**3):.1f} GB livres, necessário pelo menos {needed_bytes / (1024**3):.1f} GB "
                f"para remux seguro deste arquivo ({orig_size / (1024**3):.1f} GB)."
            )
        }

    orig_audio = info["audio_streams"]
    total_dur = info["duration"]
    total_str = _fmt_hhmmss(total_dur)

    filter_complex = YOUTUBE_GOLD_FILTER_MULTI if orig_audio >= 3 else YOUTUBE_GOLD_FILTER_SINGLE
    mode_label = f"Multi-faixa ({orig_audio} faixas: Mix Geral + Discord isolado)" if orig_audio >= 3 else f"Faixa única ({orig_audio} faixa)"

    temp_mkv = mkv_path.with_name(f".{mkv_path.stem}.audio_norm_tmp.mkv")
    stderr_log = mkv_path.with_name(f".{mkv_path.stem}.audio_norm_err.log")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(mkv_path),
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", "[aout]",
    ]
    # Preservar intactas todas as faixas auxiliares (2..N: Mic, Discord cru, Jogo, Desktop, Soundpad)
    for a_idx in range(1, orig_audio):
        cmd.extend(["-map", f"0:a:{a_idx}"])
    # Preservar o mix original (0:a:0) como faixa extra de backup lossless (sem alterar índices 2..N)
    from app.config import KEEP_ORIG_AUDIO_BACKUP
    append_backup = KEEP_ORIG_AUDIO_BACKUP and not bool(info.get("has_original_backup"))
    if append_backup:
        cmd.extend(["-map", "0:a:0"])

    cmd.extend([
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-c:v", "copy",
        "-c:a:0", "aac", "-b:a:0", "320k",
    ])
    for a_idx in range(1, orig_audio):
        cmd.extend([f"-c:a:{a_idx}", "copy"])
    if append_backup:
        cmd.extend([
            f"-c:a:{orig_audio}", "copy",
            f"-disposition:a:{orig_audio}", "0",
            f"-metadata:s:a:{orig_audio}", "title=Track1 (Original Mix Backup)",
            f"-metadata:s:a:{orig_audio}", "BANIDO_ORIGINAL_MIX=1",
        ])

    cmd.extend([
        "-disposition:a:0", "default",
        "-metadata", f"BANIDO_AUDIO_NORM={YOUTUBE_AUDIO_NORM_TAG}",
        "-metadata:s:a:0", "title=Track1 (YouTube Gold)",
        "-metadata:s:a:0", f"BANIDO_AUDIO_NORM={YOUTUBE_AUDIO_NORM_TAG}",
        "-progress", "pipe:1", "-nostats",
        str(temp_mkv)
    ])

    print(
        f"[AUDIO NORM] Iniciando normalização YouTube Gold | {mkv_path.name} "
        f"({orig_size / (1024**3):.2f} GB | {total_str} | {mode_label})",
        flush=True
    )
    if progress_callback:
        progress_callback(f"Normalizando áudio YouTube Gold [00:00:00 / {total_str}]...", 0.05)

    t0 = time.time()
    replaced = False
    try:
        with open(stderr_log, "w", encoding="utf-8", errors="replace") as err_fh:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_fh, text=True, bufsize=1)
            last_cb = 0.0
            last_log = 0.0
            current_speed = ""

            if proc.stdout:
                for line in proc.stdout:
                    line = line.strip()
                    if line.startswith("speed="):
                        current_speed = line.split("=", 1)[1].strip()
                    elif line.startswith("out_time="):
                        curr_str = line.split("=", 1)[1][:8]
                        curr_sec = parse_time_to_seconds(curr_str)
                        now_t = time.time()
                        if total_dur > 0:
                            pct = 0.05 + min(0.87, (curr_sec / total_dur) * 0.87)
                            if progress_callback and (now_t - last_cb) >= 2.0:
                                last_cb = now_t
                                spd_txt = f" ({current_speed})" if current_speed else ""
                                progress_callback(
                                    f"Normalizando Faixa 1 (Vídeo Lossless) [{curr_str} / {total_str}]{spd_txt}...",
                                    pct
                                )
                            if (now_t - last_log) >= 12.0:
                                last_log = now_t
                                spd_txt = f" | Velocidade: {current_speed}" if current_speed else ""
                                print(
                                    f"[AUDIO NORM] Progresso: {curr_str} / {total_str} "
                                    f"({int(min(100, (curr_sec / total_dur) * 100))}%){spd_txt}",
                                    flush=True
                                )
            proc.wait()

        if proc.returncode != 0 or not temp_mkv.exists():
            err_tail = ""
            if stderr_log.exists():
                try:
                    err_tail = "\n".join(stderr_log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:])
                except Exception:
                    pass
            return {
                "success": False,
                "error": f"FFmpeg falhou (código {proc.returncode}): {err_tail[-400:]}"
            }

        # 3. Validação rigorosa de integridade e substituição atômica
        fin_res = finalize_normalized_mkv(
            mkv_path=mkv_path,
            temp_mkv=temp_mkv,
            orig_info=info,
            orig_size=orig_size,
            orig_atime=orig_atime,
            orig_mtime=orig_mtime,
            t_start=t0,
            progress_callback=progress_callback
        )
        replaced = bool(fin_res.get("success"))
        return fin_res
    except Exception as e:
        return {"success": False, "error": f"Erro inesperado ao normalizar áudio: {e}"}
    finally:
        if not replaced and temp_mkv.exists():
            try:
                temp_mkv.unlink()
            except Exception:
                pass
        if stderr_log.exists():
            try:
                stderr_log.unlink()
            except Exception:
                pass


def remove_original_mix_backup(mkv_path: Path) -> Dict[str, Any]:
    """
    Remove a faixa extra de backup do mix original (BANIDO_ORIGINAL_MIX=1) via stream copy
    100% lossless (-c copy), liberando espaço sem re-renderizar vídeo nem áudio.
    """
    if not mkv_path.exists():
        return {"success": False, "error": f"Arquivo MKV não encontrado: {mkv_path}"}

    try:
        orig_stat = mkv_path.stat()
        orig_size = orig_stat.st_size
        orig_atime = orig_stat.st_atime
        orig_mtime = orig_stat.st_mtime
        info = inspect_mkv_audio_norm(mkv_path)
    except Exception as e:
        return {"success": False, "error": f"Falha ao inspecionar MKV com ffprobe: {e}"}

    backup_idx = info.get("original_backup_index")
    if backup_idx is None:
        return {
            "success": True,
            "skipped": True,
            "freed_mb": 0.0,
            "message": "Este vídeo não possui faixa extra de backup para remover.",
        }

    orig_audio = info["audio_streams"]
    if orig_audio <= 1:
        return {"success": False, "error": "Não é possível remover a única faixa de áudio do vídeo."}

    temp_mkv = mkv_path.with_name(f".{mkv_path.stem}.strip_backup_tmp.mkv")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(mkv_path),
        "-map", "0:v",
        "-map", "0:a",
        "-map", f"-0:a:{backup_idx}",
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-c", "copy",
        str(temp_mkv),
    ]

    replaced = False
    try:
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0 or not temp_mkv.exists():
            err = (res.stderr or b"").decode("utf-8", errors="ignore")[-400:]
            return {"success": False, "error": f"FFmpeg falhou ao remover backup: {err}"}

        new_info = inspect_mkv_audio_norm(temp_mkv)
        if new_info["video_streams"] < 1 or new_info["audio_streams"] != orig_audio - 1:
            return {
                "success": False,
                "error": (
                    f"Contagem de streams divergiu ao remover backup "
                    f"(áudio={new_info['audio_streams']} vs esperado {orig_audio - 1}). Original preservado!"
                ),
            }

        new_size = temp_mkv.stat().st_size
        freed_mb = round(max(0, orig_size - new_size) / (1024 ** 2), 1)
        os.replace(str(temp_mkv), str(mkv_path))
        replaced = True
        try:
            os.utime(str(mkv_path), (orig_atime, orig_mtime))
        except Exception:
            pass
        return {
            "success": True,
            "skipped": False,
            "freed_mb": freed_mb,
            "audio_streams": new_info["audio_streams"],
            "filename": mkv_path.name,
            "message": f"Faixa de backup removida com sucesso (~{freed_mb:.1f} MB liberados).",
        }
    except Exception as e:
        return {"success": False, "error": f"Erro ao remover faixa de backup: {e}"}
    finally:
        if not replaced and temp_mkv.exists():
            try:
                temp_mkv.unlink()
            except Exception:
                pass

