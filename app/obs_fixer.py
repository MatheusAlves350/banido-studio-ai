import configparser
import json
import os
import subprocess
import sys
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List
from app.config import BASE_DIR


def _resolve_obs_config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "obs-studio"


OBS_BASE_DIR = _resolve_obs_config_dir()
OBS_SCENES_DIR = OBS_BASE_DIR / "basic" / "scenes"
OBS_USER_INI = OBS_BASE_DIR / "user.ini"
OBS_BACKUP_DIR = BASE_DIR / "obs_backups"

# Bitmasks padrão de 6 faixas do OBS Studio (bits 0..5 -> faixas 1..6)
MIXER_TRACK_1_2 = 1 | 2    # 3  (Mix Geral + Microfone)
MIXER_TRACK_1_3 = 1 | 4    # 5  (Mix Geral + Discord)
MIXER_TRACK_1_4 = 1 | 8    # 9  (Mix Geral + Jogo)
MIXER_TRACK_1_5 = 1 | 16   # 17 (Mix Geral + Desktop/Navegador)
MIXER_TRACK_1_6 = 1 | 32   # 33 (Mix Geral + Soundpad)


def _is_obs_running() -> bool:
    try:
        if os.name == "nt":
            res = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"],
                capture_output=True, text=True, timeout=3
            )
            if "obs64.exe" in (res.stdout or "").lower():
                return True
            res32 = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq obs32.exe", "/NH"],
                capture_output=True, text=True, timeout=3
            )
            return "obs32.exe" in (res32.stdout or "").lower()
        res = subprocess.run(["pgrep", "-x", "obs"], capture_output=True, text=True, timeout=3)
        return res.returncode == 0
    except Exception:
        return False


def _make_obs_filter(name: str, fid: str, settings: dict) -> dict:
    return {
        "prev_ver": 503447554,
        "name": name,
        "uuid": str(uuid.uuid4()),
        "id": fid,
        "versioned_id": fid,
        "settings": settings,
        "mixers": 63,
        "sync": 0,
        "flags": 0,
        "volume": 1.0,
        "balance": 0.5,
        "enabled": True,
        "muted": False,
        "push-to-mute": False,
        "push-to-mute-delay": 0,
        "push-to-talk": False,
        "push-to-talk-delay": 0,
        "hotkeys": {},
        "deinterlace_mode": 0,
        "deinterlace_field_order": 0,
        "monitoring_type": 0,
        "private_settings": {}
    }


def _merge_obs_filters(existing_filters: List[dict], desired_filters: List[dict]) -> List[dict]:
    """Preserva filtros já configurados pelo usuário (ex: RNNoise, Noise Gate) e adiciona apenas os ausentes."""
    merged = list(existing_filters or [])
    existing_names = {str(f.get("name", "")).strip().lower() for f in merged if isinstance(f, dict)}
    for f in desired_filters:
        if str(f.get("name", "")).strip().lower() not in existing_names:
            merged.append(f)
    return merged


def get_active_scene_file():
    if OBS_USER_INI.exists():
        cfg = configparser.ConfigParser(interpolation=None)
        try:
            cfg.read(OBS_USER_INI, encoding="utf-8")
            sc_file = cfg.get("Basic", "SceneCollectionFile", fallback="").strip()
            if sc_file:
                cand = OBS_SCENES_DIR / sc_file
                if cand.exists():
                    return cand
        except Exception:
            pass
    if not OBS_SCENES_DIR.exists():
        return None
    cands = sorted(OBS_SCENES_DIR.glob("Cenas_*.json")) or sorted(OBS_SCENES_DIR.glob("*.json"))
    return cands[0] if cands else None


def apply_obs_audio_routing():
    if _is_obs_running():
        return {
            "success": False,
            "message": "OBS Studio está aberto. Feche o OBS antes de aplicar o roteamento para evitar que suas alterações sejam sobrescritas ao sair."
        }

    scene_file = get_active_scene_file()
    if not scene_file or not scene_file.exists():
        return {"success": False, "message": "Nenhum arquivo de cenas ativo do OBS encontrado."}

    OBS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = OBS_BACKUP_DIR / f"{scene_file.stem}_{ts}.json"
    shutil.copy2(scene_file, backup_file)

    with open(scene_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    modified = False

    # 1. Dispositivos globais
    if "AuxAudioDevice1" in data and isinstance(data["AuxAudioDevice1"], dict):
        mic = data["AuxAudioDevice1"]
        mic["mixers"] = MIXER_TRACK_1_2
        mic["volume"] = 1.0
        mic["filters"] = _merge_obs_filters(mic.get("filters"), [
            _make_obs_filter("Compressor (Voz)", "compressor_filter", {
                "ratio": 3.0,
                "threshold": -22.0,
                "attack_time": 6,
                "release_time": 80,
                "output_gain": 6.0,
                "sidechain_source": "none"
            }),
            _make_obs_filter("Limitador (Segurança)", "limiter_filter", {
                "threshold": -1.5,
                "release_time": 60
            })
        ])
        modified = True

    if "DesktopAudioDevice1" in data and isinstance(data["DesktopAudioDevice1"], dict):
        data["DesktopAudioDevice1"]["mixers"] = MIXER_TRACK_1_5
        modified = True

    # 2. Fontes individuais
    for source in data.get("sources", []):
        name = source.get("name", "")
        name_low = name.lower()

        if name_low == "discord" or ("discord" in name_low and source.get("id") == "wasapi_process_output_capture"):
            source["mixers"] = MIXER_TRACK_1_3
            source["volume"] = 1.0
            source["filters"] = _merge_obs_filters(source.get("filters"), [
                _make_obs_filter("Ganho (Discord)", "gain_filter", {"db": 20.0}),
                _make_obs_filter("Compressor (Discord)", "compressor_filter", {
                    "ratio": 4.0,
                    "threshold": -24.0,
                    "attack_time": 6,
                    "release_time": 80,
                    "output_gain": 12.0,
                    "sidechain_source": "none"
                }),
                _make_obs_filter("Limitador (Segurança)", "limiter_filter", {
                    "threshold": -1.5,
                    "release_time": 60
                })
            ])
            modified = True
        elif any(k in name_low for k in ["jogo", "game", "overwatch", "forza", "captura de áudio de aplicativo"]):
            source["mixers"] = MIXER_TRACK_1_4
            modified = True
        elif "soundpad" in name_low:
            source["mixers"] = MIXER_TRACK_1_6
            modified = True
        elif any(k in name_low for k in ["janela", "navegador", "browser", "monitor"]):
            if source.get("mixers", 0) != 0:
                source["mixers"] = MIXER_TRACK_1_5
                modified = True

    if modified:
        with open(scene_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return {"success": True, "scene": scene_file.name, "backup": str(backup_file)}

    return {"success": True, "scene": scene_file.name, "message": "Nenhuma alteração necessária."}


if __name__ == "__main__":
    print(apply_obs_audio_routing())
