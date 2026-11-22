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
from app.config import CLIPS_DIR
from app.audio_pipeline import get_audio_stream_count_and_duration, parse_time_to_seconds

YOUTUBE_AUDIO_NORM_TAG = "youtube_gold_v1"

# Cadeia calibrada nos MKVs reais do OBS (aprovada em testes A/B):
# - Alvo: -15.5 a -16.6 LUFS (padrão YouTube -14 LUFS sem redução automática), True Peak <= -1.0 dBTP
# - Equilíbrio Mic (Anfitrião) vs Discord: < 1 dB de diferença | Silêncio nas pausas ~ -78 dB (zero chiado)
# - Estéreo 48 kHz AAC 320 kbps com channel coupling (c=1)
YOUTUBE_GOLD_FILTER_MULTI = (
    "[0:a:0]highpass=f=75,"
    "dynaudnorm=f=250:g=21:p=0.82:m=12:c=1:b=1:t=0.004,"
    "acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=9dB[a1];"
    "[0:a:2]highpass=f=85,"
    "dynaudnorm=f=200:g=15:p=0.78:m=75:c=1:b=1:t=0.0008,"
    "acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=7.5dB[a3];"
    "[a1][a3]amix=inputs=2:duration=longest:normalize=0:weights=1.0 0.80,"
    "volume=5.0dB,"
    "alimiter=limit=0.87:attack=5:release=50:level=false,"
    "aformat=sample_rates=48000:channel_layouts=stereo[aout]"
)

YOUTUBE_GOLD_FILTER_SINGLE = (
    "[0:a:0]highpass=f=75,"
    "dynaudnorm=f=220:g=15:p=0.80:m=45:c=1:b=1:t=0.001,"
    "acompressor=threshold=-25dB:ratio=3.2:attack=6:release=90:makeup=8.5dB,"
    "volume=4.5dB,"
    "alimiter=limit=0.87:attack=5:release=50:level=false,"
    "aformat=sample_rates=48000:channel_layouts=stereo[aout]"
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

    return {
        "audio_streams": len(audio_streams),
        "video_streams": len(video_streams),
        "duration": duration,
        "is_normalized": bool(norm_tag),
        "norm_tag": norm_tag
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
    keep_all_tracks: bool = False
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
    try:
        info = inspect_mkv_audio_norm(mkv_path)
        stream_count = info["audio_streams"]
        already_normalized = info["is_normalized"]
    except Exception:
        stream_count = 1

    if already_normalized or not boost_discord:
        # Se o MKV já teve a Faixa 1 normalizada no padrão YouTube Gold, faz stream copy instantâneo (sem normalizar 2x!)
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start_sec:.2f}",
            "-i", str(mkv_path),
            "-t", f"{dur:.2f}",
            "-map", "0:v:0",
            "-map", "0:a" if keep_all_tracks else "0:a:0",
            "-c:v", "copy",
            "-c:a", "copy",
            "-avoid_negative_ts", "make_zero",
            str(output_clip_path)
        ]
    elif stream_count >= 3:
        # Vídeo 100% LOSSLESS (-c:v copy) + Cadeia calibrada YouTube Gold (-15.5 LUFS, True Peak -1.0 dBTP)
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start_sec:.2f}",
            "-i", str(mkv_path),
            "-t", f"{dur:.2f}",
            "-filter_complex", YOUTUBE_GOLD_FILTER_MULTI,
            "-map", "0:v:0",
            "-map", "[aout]",
        ]
        if keep_all_tracks:
            for a_idx in range(1, stream_count):
                cmd.extend(["-map", f"0:a:{a_idx}"])
        cmd.extend([
            "-c:v", "copy",
            "-c:a:0", "aac", "-b:a:0", "320k", "-ar:a:0", "48000", "-ac:a:0", "2",
        ])
        if keep_all_tracks:
            for a_idx in range(1, stream_count):
                cmd.extend([f"-c:a:{a_idx}", "copy"])
        cmd.extend(["-avoid_negative_ts", "make_zero", str(output_clip_path)])
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start_sec:.2f}",
            "-i", str(mkv_path),
            "-t", f"{dur:.2f}",
            "-filter_complex", YOUTUBE_GOLD_FILTER_SINGLE,
            "-map", "0:v:0",
            "-map", "[aout]",
        ]
        if keep_all_tracks and stream_count > 1:
            for a_idx in range(1, stream_count):
                cmd.extend(["-map", f"0:a:{a_idx}"])
        cmd.extend([
            "-c:v", "copy",
            "-c:a:0", "aac", "-b:a:0", "320k", "-ar:a:0", "48000", "-ac:a:0", "2",
        ])
        if keep_all_tracks and stream_count > 1:
            for a_idx in range(1, stream_count):
                cmd.extend([f"-c:a:{a_idx}", "copy"])
        cmd.extend(["-avoid_negative_ts", "make_zero", str(output_clip_path)])

    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return {
            "success": True,
            "clip_path": str(output_clip_path),
            "filename": output_clip_path.name,
            "duration": dur
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

    cmd.extend([
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-c:v", "copy",
        "-c:a:0", "aac", "-b:a:0", "320k",
    ])
    for a_idx in range(1, orig_audio):
        cmd.extend([f"-c:a:{a_idx}", "copy"])

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

        # 3. Validação rigorosa de integridade antes de substituir o original
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
        if new_info["video_streams"] < 1 or new_info["audio_streams"] != orig_audio:
            return {
                "success": False,
                "error": (
                    f"Contagem de streams divergiu após remux (vídeo={new_info['video_streams']}, "
                    f"áudio={new_info['audio_streams']} vs esperado {orig_audio}). Original preservado!"
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
        print(
            f"[AUDIO NORM] ✅ Concluído em {elapsed}s! {mkv_path.name} ({final_size_gb} GB) "
            f"| Vídeo 4K AV1 100% lossless | Faixa 1 YouTube Gold | Faixas 2-{orig_audio} preservadas.",
            flush=True
        )
        return {
            "success": True,
            "skipped": False,
            "filename": mkv_path.name,
            "size_gb": final_size_gb,
            "mtime": orig_mtime,
            "duration": new_info["duration"],
            "audio_streams": orig_audio,
            "elapsed_sec": elapsed,
            "norm_tag": YOUTUBE_AUDIO_NORM_TAG
        }
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
