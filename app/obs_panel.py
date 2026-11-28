"""
Conferir e ajustar o OBS pela interface, com edição antes de aplicar (item E5 do roadmap).

Esta é uma camada por cima do `obs_fixer.py`, que continua exatamente como está. Ela:
1. LÊ o arquivo de cenas do OBS (somente leitura) e descreve cada fonte de áudio em linguagem simples;
2. monta um PLANO a partir do que a pessoa escolheu (layout de faixas, função de cada fonte, filtros de voz);
3. mostra o que mudaria (diferença entre o estado atual e o plano), sem tocar em nada;
4. só grava quando a pessoa confirma: faz cópia de segurança antes, recusa se o OBS estiver aberto ou se o arquivo
   mudou desde que a tela foi aberta, e oferece "Desfazer" com o último backup.

O plano padrão (layout de 6 faixas, funções detectadas pelo nome, filtros ligados) é o mesmo que o
`obs_fixer.apply_obs_audio_routing()` aplicaria; um teste compara os dois para o roteamento não divergir.
O obs_fixer não aceita parâmetros, por isso a gravação com valores escolhidos é feita aqui, reaproveitando as
funções auxiliares dele (filtros, verificação do OBS aberto, pastas de backup).

Não usa o WebSocket do OBS: lê o arquivo salvo. O OBS grava esse arquivo ao fechar; para ver o estado mais
recente, feche o OBS antes de abrir a tela.
"""
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import obs_fixer

# Funções que cada fonte de áudio pode ter.
ROLE_MIC, ROLE_DISCORD, ROLE_GAME, ROLE_DESKTOP, ROLE_SOUNDPAD, ROLE_KEEP = (
    "mic", "discord", "game", "desktop", "soundpad", "keep")
ROLES = [
    {"id": ROLE_MIC, "label": "Meu microfone"},
    {"id": ROLE_DISCORD, "label": "Discord (voz dos amigos)"},
    {"id": ROLE_GAME, "label": "Som do jogo"},
    {"id": ROLE_DESKTOP, "label": "Navegador e outros sons do PC"},
    {"id": ROLE_SOUNDPAD, "label": "Soundpad"},
    {"id": ROLE_KEEP, "label": "Não mexer nesta fonte"},
]
ROLE_LABELS = {r["id"]: r["label"] for r in ROLES}

# Faixas (1 a 6) por função em cada layout. A faixa 1 é sempre o mix geral.
LAYOUTS = [
    {"id": "6", "label": "6 faixas (recomendado)",
     "help": "Mix, seu microfone, Discord, jogo, navegador e Soundpad ficam em faixas separadas. É o que dá a melhor transcrição e o melhor reconhecimento de vozes."},
    {"id": "2", "label": "2 faixas",
     "help": "Mix geral e seu microfone. Os amigos do Discord ficam só no mix: dá para transcrever, mas fica mais difícil separar quem falou."},
    {"id": "1", "label": "1 faixa",
     "help": "Só o mix geral. É o mais simples, porém a separação de vozes perde qualidade."},
]
_TRACKS = {
    "6": {ROLE_MIC: [1, 2], ROLE_DISCORD: [1, 3], ROLE_GAME: [1, 4], ROLE_DESKTOP: [1, 5], ROLE_SOUNDPAD: [1, 6]},
    "2": {ROLE_MIC: [1, 2], ROLE_DISCORD: [1], ROLE_GAME: [1], ROLE_DESKTOP: [1], ROLE_SOUNDPAD: [1]},
    "1": {ROLE_MIC: [1], ROLE_DISCORD: [1], ROLE_GAME: [1], ROLE_DESKTOP: [1], ROLE_SOUNDPAD: [1]},
}

_AUDIO_ID_PREFIXES = ("wasapi_", "pulse_", "coreaudio_", "alsa_", "jack_", "oss_", "sndio_")
_AUDIO_IDS = ("browser_source", "ffmpeg_source", "vlc_source")
_GLOBAL_LABELS = {"AuxAudioDevice1": "Microfone (Mic/Aux)", "DesktopAudioDevice1": "Áudio do desktop"}


# ---------------------------------------------------------------- utilidades

def tracks_from_mask(mask: Any) -> List[int]:
    try:
        m = int(mask)
    except (TypeError, ValueError):
        return []
    return [i + 1 for i in range(6) if m & (1 << i)]


def mask_from_tracks(tracks: List[int]) -> int:
    return sum(1 << (t - 1) for t in tracks)


def tracks_text(tracks: List[int]) -> str:
    if not tracks:
        return "nenhuma"
    nomes = [str(t) for t in tracks]
    if len(nomes) == 1:
        return f"faixa {nomes[0]}"
    return "faixas " + ", ".join(nomes[:-1]) + " e " + nomes[-1]


def file_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def detect_role(key: str, name: str, source: Optional[dict] = None) -> str:
    """Mesmas regras do obs_fixer: pelo nome da fonte."""
    if key == "AuxAudioDevice1":
        return ROLE_MIC
    if key == "DesktopAudioDevice1":
        return ROLE_DESKTOP
    low = (name or "").lower()
    sid = (source or {}).get("id")
    if low == "discord" or ("discord" in low and sid == "wasapi_process_output_capture"):
        return ROLE_DISCORD
    if any(k in low for k in ["jogo", "game", "overwatch", "forza", "captura de áudio de aplicativo"]):
        return ROLE_GAME
    if "soundpad" in low:
        return ROLE_SOUNDPAD
    if any(k in low for k in ["janela", "navegador", "browser", "monitor"]) and (source or {}).get("mixers", 0) != 0:
        return ROLE_DESKTOP
    return ROLE_KEEP


def _is_audio_source(src: dict) -> bool:
    sid = str(src.get("id", ""))
    return sid.startswith(_AUDIO_ID_PREFIXES) or sid in _AUDIO_IDS


# ---------------------------------------------------------------- leitura

def list_audio_sources(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fontes de áudio do arquivo de cenas, com a função detectada e as faixas atuais."""
    out: List[Dict[str, Any]] = []
    for key in sorted(k for k in data if k.startswith(("AuxAudioDevice", "DesktopAudioDevice"))):
        src = data[key]
        if not isinstance(src, dict):
            continue
        name = src.get("name") or _GLOBAL_LABELS.get(key, key)
        out.append({"key": key, "name": name, "kind": "global", "role": detect_role(key, name, src),
                    "tracks_now": tracks_from_mask(src.get("mixers", 0)),
                    "filters": [f.get("name", "") for f in (src.get("filters") or []) if isinstance(f, dict)]})
    for i, src in enumerate(data.get("sources", []) or []):
        if not isinstance(src, dict):
            continue
        name = src.get("name", "")
        role = detect_role(f"s{i}", name, src)
        if not (_is_audio_source(src) or role != ROLE_KEEP):
            continue
        out.append({"key": f"s{i}", "name": name, "kind": "source", "role": role,
                    "tracks_now": tracks_from_mask(src.get("mixers", 0)),
                    "filters": [f.get("name", "") for f in (src.get("filters") or []) if isinstance(f, dict)]})
    return out


def _target(data: Dict[str, Any], key: str) -> Optional[dict]:
    if key.startswith("s") and key[1:].isdigit():
        idx = int(key[1:])
        srcs = data.get("sources", []) or []
        return srcs[idx] if idx < len(srcs) and isinstance(srcs[idx], dict) else None
    val = data.get(key)
    return val if isinstance(val, dict) else None


def _filters_for(role: str) -> List[dict]:
    """Filtros que o obs_fixer acrescenta (mesmos valores)."""
    mk = obs_fixer._make_obs_filter
    if role == ROLE_MIC:
        return [
            mk("Compressor (Voz)", "compressor_filter", {
                "ratio": 3.0, "threshold": -22.0, "attack_time": 6, "release_time": 80,
                "output_gain": 6.0, "sidechain_source": "none"}),
            mk("Limitador (Segurança)", "limiter_filter", {"threshold": -1.5, "release_time": 60}),
        ]
    if role == ROLE_DISCORD:
        return [
            mk("Ganho (Discord)", "gain_filter", {"db": 20.0}),
            mk("Compressor (Discord)", "compressor_filter", {
                "ratio": 4.0, "threshold": -24.0, "attack_time": 6, "release_time": 80,
                "output_gain": 12.0, "sidechain_source": "none"}),
            mk("Limitador (Segurança)", "limiter_filter", {"threshold": -1.5, "release_time": 60}),
        ]
    return []


# ---------------------------------------------------------------- plano e diferença

def normalize_choice(layout: Any, roles: Optional[Dict[str, str]]) -> tuple:
    layout = str(layout or "6")
    if layout not in _TRACKS:
        raise ValueError("Escolha 6, 2 ou 1 faixa.")
    roles = dict(roles or {})
    for key, role in roles.items():
        if role not in ROLE_LABELS:
            raise ValueError(f"Função desconhecida para a fonte {key}.")
    return layout, roles


def build_plan(data: Dict[str, Any], layout: Any = "6", roles: Optional[Dict[str, str]] = None,
               add_filters: bool = True) -> List[Dict[str, Any]]:
    """Calcula o que mudaria em cada fonte, sem alterar `data`."""
    layout, roles = normalize_choice(layout, roles)
    plan: List[Dict[str, Any]] = []
    for s in list_audio_sources(data):
        role = roles.get(s["key"], s["role"])
        src = _target(data, s["key"])
        if role == ROLE_KEEP or src is None:
            continue
        new_tracks = _TRACKS[layout][role]
        changes: List[str] = []
        if s["tracks_now"] != new_tracks:
            changes.append(f"vai de {tracks_text(s['tracks_now'])} para {tracks_text(new_tracks)}")
        reset_volume = role in (ROLE_MIC, ROLE_DISCORD) and src.get("volume", 1.0) != 1.0
        if reset_volume:
            changes.append(f"o volume volta de {round(float(src.get('volume', 1.0)) * 100)}% para 100%")
        novos = []
        if add_filters:
            have = {str(f.get("name", "")).strip().lower() for f in (src.get("filters") or []) if isinstance(f, dict)}
            novos = [f for f in _filters_for(role) if f["name"].strip().lower() not in have]
            for f in novos:
                changes.append(f"ganha o filtro “{f['name']}”")
        plan.append({
            "key": s["key"], "name": s["name"], "role": role, "role_label": ROLE_LABELS[role],
            "tracks_now": s["tracks_now"], "tracks_new": new_tracks,
            "reset_volume": reset_volume, "new_filters": [f["name"] for f in novos],
            "changes": changes,
        })
    return plan


def diff_lines(plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"key": p["key"], "name": p["name"], "role_label": p["role_label"], "changes": p["changes"]}
            for p in plan if p["changes"]]


# ---------------------------------------------------------------- arquivo de cenas

def _scene_file() -> Optional[Path]:
    path = obs_fixer.get_active_scene_file()
    return path if path and path.exists() else None


def read_state() -> Dict[str, Any]:
    scene = _scene_file()
    base = {
        "layouts": LAYOUTS, "roles": ROLES, "obs_running": obs_fixer._is_obs_running(),
        "can_undo": bool(_last_backup(scene)) if scene else False,
    }
    if not scene:
        return {**base, "found": False,
                "message": "Não achei nenhuma configuração do OBS neste computador. Abra o OBS uma vez, feche e tente de novo."}
    data = json.loads(scene.read_text(encoding="utf-8"))
    plan = build_plan(data, "6", None, True)
    return {**base, "found": True, "scene_name": scene.name, "base_hash": file_hash(scene),
            "sources": list_audio_sources(data), "default_layout": "6", "diff": diff_lines(plan)}


def preview(layout: Any, roles: Optional[Dict[str, str]], add_filters: bool) -> Dict[str, Any]:
    scene = _scene_file()
    if not scene:
        raise FileNotFoundError("Nenhuma configuração do OBS encontrada.")
    data = json.loads(scene.read_text(encoding="utf-8"))
    plan = build_plan(data, layout, roles, add_filters)
    return {"plan": plan, "diff": diff_lines(plan), "base_hash": file_hash(scene)}


def _backups(scene: Path) -> List[Path]:
    if not obs_fixer.OBS_BACKUP_DIR.exists():
        return []
    prefix = f"{scene.stem}_"
    return sorted(p for p in obs_fixer.OBS_BACKUP_DIR.glob(f"{scene.stem}_*.json")
                  if p.name.startswith(prefix) and not p.stem.endswith("_desfeito"))


def _last_backup(scene: Optional[Path]) -> Optional[Path]:
    if not scene:
        return None
    found = _backups(scene)
    return found[-1] if found else None


class ObsPanelError(Exception):
    """Erro com mensagem pronta para a tela e o código HTTP sugerido."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def apply(layout: Any, roles: Optional[Dict[str, str]], add_filters: bool, base_hash: str, confirm: bool) -> Dict[str, Any]:
    """Grava o plano no arquivo de cenas. Nada é gravado sem confirm=True."""
    if not confirm:
        raise ObsPanelError("Confirme para aplicar. Nada foi alterado.")
    if obs_fixer._is_obs_running():
        raise ObsPanelError("O OBS está aberto. Feche o OBS e tente de novo: se ele estiver aberto, "
                            "as suas alterações são desfeitas quando ele fecha.", 409)
    scene = _scene_file()
    if not scene:
        raise ObsPanelError("Não achei nenhuma configuração do OBS neste computador.", 404)
    if base_hash and file_hash(scene) != base_hash:
        raise ObsPanelError("A configuração do OBS mudou desde que você abriu esta tela. "
                            "Feche a tela, abra de novo e confira o que mudou.", 409)

    data = json.loads(scene.read_text(encoding="utf-8"))
    plan = build_plan(data, layout, roles, add_filters)
    if not any(p["changes"] for p in plan):
        return {"success": True, "changed": False, "message": "Nada a alterar: o OBS já está assim."}

    obs_fixer.OBS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = obs_fixer.OBS_BACKUP_DIR / f"{scene.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    shutil.copy2(scene, backup)

    for item in plan:
        if not item["changes"]:
            continue
        src = _target(data, item["key"])
        src["mixers"] = mask_from_tracks(item["tracks_new"])
        if item["reset_volume"]:
            src["volume"] = 1.0
        if item["new_filters"]:
            wanted = [f for f in _filters_for(item["role"]) if f["name"] in item["new_filters"]]
            src["filters"] = obs_fixer._merge_obs_filters(src.get("filters"), wanted)

    scene.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    return {"success": True, "changed": True, "scene": scene.name, "backup": backup.name,
            "changes": len([p for p in plan if p["changes"]]),
            "message": "Pronto. A cópia de segurança foi guardada e dá para desfazer."}


def undo(confirm: bool) -> Dict[str, Any]:
    """Restaura a última cópia de segurança. Antes, guarda o estado de agora (para não perder nada)."""
    if not confirm:
        raise ObsPanelError("Confirme para desfazer. Nada foi alterado.")
    if obs_fixer._is_obs_running():
        raise ObsPanelError("O OBS está aberto. Feche o OBS e tente de novo.", 409)
    scene = _scene_file()
    if not scene:
        raise ObsPanelError("Não achei nenhuma configuração do OBS neste computador.", 404)
    last = _last_backup(scene)
    if not last:
        raise ObsPanelError("Não há cópia de segurança para restaurar.", 404)
    shutil.copy2(scene, obs_fixer.OBS_BACKUP_DIR / f"{scene.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_desfeito.json")
    shutil.copy2(last, scene)
    return {"success": True, "restored": last.name, "message": "A configuração anterior do OBS foi restaurada."}
