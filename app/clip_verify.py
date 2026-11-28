"""C4: verificação automática de um corte logo depois de exportado.

Analogia: é conferir o troco antes de sair da loja. O corte já está salvo (nunca
é apagado por aqui); só medimos com o ffprobe se ele parece o que foi pedido e
devolvemos um aviso em linguagem simples:

- ``ok``      verde: "Corte conferido."
- ``warn``    amarelo: diferença pequena, pode ser normal em corte sem recodificar.
- ``bad``     vermelho: arquivo suspeito (ilegível, sem imagem, sem faixa de áudio
              esperada ou com duração muito diferente do pedido).

As tolerâncias abaixo são deliberadamente amplas (ver "Riscos" da especificação C4)
e ficam num só lugar para serem apertadas depois das medições manuais do item A1.
"""
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

# Corte sem recodificar começa no quadro-chave anterior, então algum excesso é normal.
DURATION_WARN_SEC = 1.0      # acima disso: amarelo
DURATION_BAD_SEC = 10.0      # acima disso (ou abaixo de metade do pedido): vermelho
SYNC_WARN_SEC = 0.5          # diferença entre o início do vídeo e o do áudio: amarelo
SYNC_BAD_SEC = 3.0           # vermelho


def probe_media(path: Path) -> Dict[str, Any]:
    """Roda o ffprobe e devolve um resumo simples (levanta exceção se o arquivo for ilegível)."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams") or []

    def _num(v: Any) -> Optional[float]:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    return {
        "duration": _num((data.get("format") or {}).get("duration")) or 0.0,
        "video_streams": len(video),
        "audio_streams": len(audio),
        "video_start": _num(video[0].get("start_time")) if video else None,
        "audio_start": _num(audio[0].get("start_time")) if audio else None,
    }


def read_whole_file(path: Path) -> Dict[str, Any]:
    """Lê todos os pacotes do arquivo (sem decodificar, é rápido) para achar corte truncado.

    Devolve ``readable`` e ``measured_duration`` (até onde os pacotes realmente vão, que pode ser
    menor que a duração escrita no cabeçalho quando o arquivo foi cortado ao meio).
    """
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-nostats", "-progress", "pipe:1",
             "-i", str(path), "-map", "0", "-c", "copy", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        return {"readable": False, "measured_duration": None}
    measured = None
    for line in (res.stdout or "").splitlines():
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                measured = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                pass
    return {"readable": res.returncode == 0, "measured_duration": measured}


def evaluate_clip(
    probe: Dict[str, Any],
    expected_duration: float,
    expected_audio_tracks: int,
    readable: bool = True,
    measured_duration: Optional[float] = None,
) -> Dict[str, Any]:
    """Decide verde/amarelo/vermelho. Função pura (não chama ffprobe), por isso fácil de testar."""
    problems: List[str] = []
    notices: List[str] = []

    if not readable:
        problems.append("O arquivo do corte não pôde ser lido até o fim.")
    if probe.get("video_streams", 0) < 1:
        problems.append("O corte ficou sem imagem.")
    audio_found = int(probe.get("audio_streams", 0))
    if audio_found < max(1, expected_audio_tracks):
        problems.append(f"O corte tem {audio_found} faixa(s) de áudio, mas eram esperadas {max(1, expected_audio_tracks)}.")

    duration = float(probe.get("duration") or 0.0)
    if measured_duration is not None and 0 < measured_duration < duration - 1.5:
        duration = measured_duration  # o cabeçalho promete mais do que o arquivo realmente tem
    diff = duration - float(expected_duration)
    too_short = expected_duration > 0 and duration < expected_duration * 0.5
    if too_short or abs(diff) > DURATION_BAD_SEC:
        problems.append(
            f"A duração do corte ({duration:.1f} s) é muito diferente do pedido ({expected_duration:.1f} s)."
        )
    elif abs(diff) > DURATION_WARN_SEC:
        notices.append(
            f"O corte tem {abs(diff):.1f} segundos {'a mais' if diff > 0 else 'a menos'} que o pedido. "
            "Pode ser normal; confira antes de publicar."
        )

    v_start, a_start = probe.get("video_start"), probe.get("audio_start")
    if v_start is not None and a_start is not None:
        drift = abs(v_start - a_start)
        if drift > SYNC_BAD_SEC:
            problems.append(f"Imagem e som começam com {drift:.1f} s de diferença (possível dessincronia).")
        elif drift > SYNC_WARN_SEC:
            notices.append(f"Imagem e som começam com {drift:.1f} s de diferença. Confira a sincronia.")

    if problems:
        status, message = "bad", " ".join(problems) + " O arquivo foi mantido, mas é suspeito: considere exportar de novo."
    elif notices:
        status, message = "warn", " ".join(notices)
    else:
        status, message = "ok", "Corte conferido."

    return {
        "status": status,
        "message": message,
        "duration": round(duration, 2),
        "expected_duration": round(float(expected_duration), 2),
        "audio_streams": audio_found,
        "expected_audio_tracks": max(1, expected_audio_tracks),
    }


def verify_clip(clip_path: Path, expected_duration: float, expected_audio_tracks: int) -> Dict[str, Any]:
    """Confere um corte já salvo. Nunca levanta exceção: falha de leitura vira resultado vermelho."""
    clip_path = Path(clip_path)
    try:
        probe = probe_media(clip_path)
    except Exception as e:
        return {
            "status": "bad",
            "message": "Não foi possível abrir o corte para conferir. O arquivo foi mantido, mas é suspeito.",
            "detail": str(e)[-300:],
        }
    scan = read_whole_file(clip_path)
    return evaluate_clip(
        probe, expected_duration, expected_audio_tracks,
        readable=scan["readable"], measured_duration=scan["measured_duration"],
    )
