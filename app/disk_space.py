"""F8: estima o espaço em disco que um processamento vai usar e avisa antes de começar.

Analogia: olhar o tanque de combustível antes de sair de viagem. A conta é conservadora
(sempre arredonda para mais) e só bloqueia quando falta mesmo, para não impedir ninguém
por causa de uma estimativa pessimista.

Regras (spec F8, PR #40):
- Aviso (amarelo): cabe, mas sobra menos que a margem de segurança.
- Bloqueio (vermelho): o espaço livre é menor que o necessário.
- Margem de segurança: 10% do necessário ou 5 GB, o que for maior.
"""
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

GB = 1024 ** 3

# Blocos WAV temporários: PCM 16-bit mono a 16 kHz. Conta com até 4 faixas (mic, Discord,
# Soundpad e a faixa de mix usada na detecção), que é o pior caso do pipeline atual.
WAV_BYTES_PER_SEC_PER_TRACK = 16000 * 2
WAV_TRACKS_WORST_CASE = 4
# Se a duração do vídeo ainda não foi lida, estima por ~50 Mbps (gravação 4K típica do OBS).
FALLBACK_BYTES_PER_SEC = 6_250_000

MARGIN_MIN_BYTES = 5 * GB
MARGIN_FRACTION = 0.10

OK = "ok"
WARNING = "warning"
BLOCKED = "blocked"


def _fmt_gb(n_bytes: float) -> str:
    return f"{n_bytes / GB:.1f} GB"


def _existing_ancestor(path: Path) -> Path:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def free_bytes(path: Path) -> Optional[int]:
    """Espaço livre (bytes) no disco onde a pasta fica; None se não foi possível medir."""
    try:
        return shutil.disk_usage(str(_existing_ancestor(path))).free
    except Exception:
        return None


def _device_id(path: Path) -> Any:
    try:
        return os.stat(str(_existing_ancestor(path))).st_dev
    except Exception:
        return str(_existing_ancestor(path).anchor or path)


def safety_margin(needed_bytes: float) -> float:
    return max(MARGIN_MIN_BYTES, needed_bytes * MARGIN_FRACTION)


def estimate_process_needs(
    duration_sec: Optional[float],
    size_bytes: Optional[float],
    normalize_audio: bool,
) -> Dict[str, float]:
    """Bytes necessários por tipo: 'temp' (blocos WAV) e 'recordings' (MKV normalizado)."""
    size = float(size_bytes or 0)
    duration = float(duration_sec or 0)
    if duration <= 0 and size > 0:
        duration = size / FALLBACK_BYTES_PER_SEC
    temp = duration * WAV_BYTES_PER_SEC_PER_TRACK * WAV_TRACKS_WORST_CASE
    # Normalizar áudio grava um MKV novo ao lado do original, de tamanho parecido, antes de trocar.
    recordings = size * 1.02 if normalize_audio else 0.0
    return {"temp": temp, "recordings": recordings}


def check_process_space(
    video: Dict[str, Any],
    normalize_audio: bool,
    temp_dir: Path,
    recordings_dir: Path,
) -> Dict[str, Any]:
    """
    Compara o espaço necessário com o livre nas pastas de trabalho.
    Retorna {"level": ok|warning|blocked, "message": str, "items": [...]}.
    """
    size_bytes = video.get("file_size_bytes") or (float(video.get("size_gb") or 0) * GB)
    needs = estimate_process_needs(video.get("duration_sec"), size_bytes, normalize_audio)

    # Pastas no mesmo disco dividem o mesmo espaço livre: somar as necessidades.
    groups: Dict[Any, Dict[str, Any]] = {}
    for key, folder, label in (
        ("temp", temp_dir, "a pasta de temporários"),
        ("recordings", recordings_dir, "a pasta das gravações"),
    ):
        if needs[key] <= 0:
            continue
        g = groups.setdefault(_device_id(folder), {"needed": 0.0, "folder": folder, "label": label, "keys": []})
        g["needed"] += needs[key]
        g["keys"].append(key)

    items: List[Dict[str, Any]] = []
    level = OK
    messages: List[str] = []
    for g in groups.values():
        free = free_bytes(g["folder"])
        needed = g["needed"]
        margin = safety_margin(needed)
        item = {
            "folder": str(g["folder"]),
            "needed_bytes": int(needed),
            "free_bytes": free,
            "level": OK,
        }
        if free is not None:
            if free < needed:
                item["level"] = BLOCKED
                lack = needed - free
                messages.append(
                    f"Espaço insuficiente em {g['folder']}: faltam cerca de {_fmt_gb(lack)} "
                    f"(livre {_fmt_gb(free)}, necessário {_fmt_gb(needed)}). "
                    "Libere espaço ou escolha outra pasta de temporários nas configurações."
                )
            elif free < needed + margin:
                item["level"] = WARNING
                lack = needed + margin - free
                messages.append(
                    f"Espaço apertado em {g['folder']}. Libere cerca de {_fmt_gb(lack)} "
                    "para evitar falha no meio do processamento."
                )
        items.append(item)
        if item["level"] == BLOCKED:
            level = BLOCKED
        elif item["level"] == WARNING and level != BLOCKED:
            level = WARNING

    return {"level": level, "message": " ".join(messages), "items": items}
