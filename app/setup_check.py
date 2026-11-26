"""
Diagnóstico de "Primeiros passos": verifica, em linguagem simples, se tudo o que o
Banido Studio precisa está instalado e configurado, e explica como resolver cada item.

Não importa PyTorch nem Whisper (resposta rápida, < 1 s na maioria das máquinas).
"""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from app.config import (
    OBS_RECORDINGS_DIR, TEMP_DIR, MODELS_DIR, VOICE_BANK_DIR, PC_SPECS, WHISPER_MODEL_SIZE
)

OK, WARN, ERROR = "ok", "warn", "error"


def _item(key: str, status: str, title: str, detail: str, fix: str = "", action: str = "") -> Dict[str, str]:
    return {"key": key, "status": status, "title": title, "detail": detail, "fix": fix, "action": action}


def _install_hint(winget_id: str, apt_pkg: str, brew_pkg: str) -> str:
    if os.name == "nt":
        return f"Abra o PowerShell e rode: winget install {winget_id} — depois feche e abra o Banido Studio de novo."
    if shutil.which("brew"):
        return f"No terminal, rode: brew install {brew_pkg}"
    return f"No terminal, rode: sudo apt install {apt_pkg}"


def _check_ffmpeg() -> Dict[str, str]:
    missing = [b for b in ("ffmpeg", "ffprobe") if not shutil.which(b)]
    if missing:
        return _item(
            "ffmpeg", ERROR, "FFmpeg não encontrado",
            "Sem o FFmpeg não dá para ler o áudio das gravações nem exportar cortes.",
            _install_hint("Gyan.FFmpeg", "ffmpeg", "ffmpeg"),
        )
    return _item("ffmpeg", OK, "FFmpeg instalado", "Leitura de áudio e cortes de vídeo funcionando.")


def _check_mkvpropedit() -> Dict[str, str]:
    try:
        from app.lossless_media import find_mkvpropedit
        found = find_mkvpropedit()
    except Exception:
        found = shutil.which("mkvpropedit")
    if not found:
        return _item(
            "mkvtoolnix", WARN, "MKVToolNix não instalado (opcional)",
            "Gravar capítulos no arquivo continua funcionando, só que mais devagar (o arquivo é copiado inteiro).",
            _install_hint("MoritzBunkus.MKVToolNix", "mkvtoolnix", "mkvtoolnix"),
        )
    return _item("mkvtoolnix", OK, "MKVToolNix instalado", "Capítulos são gravados no arquivo em menos de 1 segundo.")


def _check_gpu() -> Dict[str, str]:
    has_nvidia = False
    if shutil.which("nvidia-smi"):
        try:
            res = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=3)
            has_nvidia = res.returncode == 0 and "GPU" in (res.stdout or "")
        except Exception:
            has_nvidia = False
    gpu_label = str(PC_SPECS.get("gpu") or "")
    if has_nvidia:
        return _item("gpu", OK, "Placa de vídeo NVIDIA detectada", f"{gpu_label}. A transcrição vai usar a GPU (bem mais rápido).")
    return _item(
        "gpu", WARN, "Sem placa de vídeo NVIDIA",
        "A transcrição vai rodar no processador. Funciona, mas uma sessão de 2 horas pode levar horas.",
        "Se o PC tem placa NVIDIA, atualize o driver. Sem GPU, use BANIDO_WHISPER_MODEL=medium no arquivo .env para ganhar velocidade.",
    )


def _check_whisper_model() -> Dict[str, str]:
    model_dir = MODELS_DIR / "whisper"
    has_files = model_dir.exists() and any(p.is_file() and p.stat().st_size > 1_000_000 for p in model_dir.rglob("*"))
    if has_files:
        return _item("whisper", OK, "Modelo de transcrição baixado", f"Whisper {WHISPER_MODEL_SIZE} já está no computador.")
    return _item(
        "whisper", WARN, "Modelo de transcrição ainda não baixado",
        f"No primeiro processamento o Whisper {WHISPER_MODEL_SIZE} (cerca de 3 GB) será baixado automaticamente.",
        "Nada a fazer: só garanta internet e espaço livre na primeira vez.",
    )


def _check_ai_key() -> Dict[str, str]:
    from app.database import get_setting
    has_google = bool(get_setting("google_api_key", ""))
    has_nvidia = bool(get_setting("nvidia_api_key", ""))
    if has_google or has_nvidia:
        which = " e ".join(n for n, ok in (("Google Gemini", has_google), ("NVIDIA", has_nvidia)) if ok)
        return _item("ai_key", OK, "Chave de IA configurada", f"{which} pronto para criar títulos, capítulos e melhores momentos.")
    return _item(
        "ai_key", WARN, "Nenhuma chave de IA configurada",
        "A legenda é gerada mesmo assim, mas títulos, capítulos e melhores momentos precisam de uma IA.",
        "Crie uma chave gratuita em aistudio.google.com e cole em Configurações. Ou use a opção Claude Web (copiar e colar).",
        action="open-settings",
    )


def _check_obs_folder() -> Dict[str, str]:
    if not OBS_RECORDINGS_DIR.exists():
        return _item(
            "obs_folder", ERROR, "Pasta de gravações não existe",
            f"O sistema procura vídeos em: {OBS_RECORDINGS_DIR}",
            "Crie a pasta ou ajuste BANIDO_OBS_DIR no arquivo .env para a pasta onde o OBS salva as gravações.",
        )
    count = sum(1 for _ in OBS_RECORDINGS_DIR.glob("*.mkv"))
    if count == 0:
        return _item(
            "obs_folder", WARN, "Nenhuma gravação .mkv na pasta",
            f"Pasta usada: {OBS_RECORDINGS_DIR}",
            "No OBS, em Configurações › Saída › Gravação, escolha o formato MKV e esta pasta. Ou copie seus .mkv para ela.",
        )
    plural = "gravação encontrada" if count == 1 else "gravações encontradas"
    return _item("obs_folder", OK, f"{count} {plural}", f"Pasta: {OBS_RECORDINGS_DIR}")


def _free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(str(path)).free / (1024 ** 3)
    except Exception:
        return -1.0


def _check_disk() -> Dict[str, str]:
    rec_free = _free_gb(OBS_RECORDINGS_DIR)
    tmp_free = _free_gb(TEMP_DIR)
    biggest = 0.0
    try:
        biggest = max((p.stat().st_size for p in OBS_RECORDINGS_DIR.glob("*.mkv")), default=0) / (1024 ** 3)
    except Exception:
        pass
    detail = f"Livre no disco das gravações: {rec_free:.0f} GB. No disco temporário: {tmp_free:.0f} GB."
    if rec_free >= 0 and biggest > 0 and rec_free < biggest + 2:
        return _item(
            "disk", WARN, "Pouco espaço para ajustar o áudio",
            detail + f" Ajustar o áudio da maior gravação ({biggest:.0f} GB) precisa de espaço igual ao tamanho dela.",
            "Libere espaço no disco das gravações. Transcrever e criar títulos continua funcionando.",
        )
    if 0 <= tmp_free < 5:
        return _item("disk", WARN, "Pouco espaço temporário", detail, "Libere espaço ou aponte BANIDO_TEMP_DIR para outro disco no .env.")
    return _item("disk", OK, "Espaço em disco suficiente", detail)


def _check_voice_bank() -> Dict[str, str]:
    names: List[str] = []
    if VOICE_BANK_DIR.exists():
        names = [d.name for d in VOICE_BANK_DIR.iterdir() if d.is_dir()]
    if not names:
        return _item(
            "voices", WARN, "Banco de vozes vazio",
            "É normal no começo: o sistema ainda não conhece a voz dos seus amigos.",
            "Depois do primeiro vídeo, ouça cada voz na aba Vozes e confirme o nome. Ele aprende a partir daí.",
        )
    return _item("voices", OK, f"{len(names)} voz(es) conhecida(s)", ", ".join(sorted(names)[:8]))


CHECKS = [_check_ffmpeg, _check_obs_folder, _check_ai_key, _check_gpu, _check_whisper_model, _check_disk, _check_mkvpropedit, _check_voice_bank]


def run_setup_check() -> Dict[str, Any]:
    items = []
    for fn in CHECKS:
        try:
            items.append(fn())
        except Exception as e:  # um item com problema não derruba o diagnóstico inteiro
            items.append(_item(fn.__name__.replace("_check_", ""), WARN, "Não foi possível verificar", str(e)))
    errors = sum(1 for i in items if i["status"] == ERROR)
    warns = sum(1 for i in items if i["status"] == WARN)
    return {
        "ready": errors == 0,
        "errors": errors,
        "warnings": warns,
        "items": items,
    }
