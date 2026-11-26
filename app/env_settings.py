"""
Edição das configurações do arquivo .env pelo painel (item D2 do roadmap).

Este módulo guarda o catálogo das opções que o usuário pode mudar, lê os valores atuais do .env e valida o
que for enviado. A gravação usa first_run.upsert_env, que preserva comentários e as demais linhas.
Chaves de API e senha de acesso ficam de fora de propósito: as chaves têm campo próprio em Configurações
(guardadas fora do .env) e a senha de rede não deve ser mostrada nem editada pela interface.
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import first_run

TEXT, CHOICE, BOOL, FOLDER = "text", "choice", "bool", "folder"
MAX_TEXT_LEN = 200

_TRUE = ("1", "true", "yes", "sim", "on")
_FALSE = ("0", "false", "no", "nao", "não", "off")

LANGUAGES = [
    ("pt", "Português"), ("en", "Inglês"), ("es", "Espanhol"), ("fr", "Francês"),
    ("de", "Alemão"), ("it", "Italiano"), ("ja", "Japonês"), ("auto", "Detectar sozinho"),
]


def _field(key, label, kind, group, help_text, default="", options=None, optional=False, advanced=False):
    return {
        "key": key, "label": label, "kind": kind, "group": group, "help": help_text,
        "default": default, "options": options or [], "optional": optional, "advanced": advanced,
    }


FIELDS: List[Dict[str, Any]] = [
    _field("BANIDO_HOST_NAME", "Seu nome nas falas", TEXT, "Identidade",
           "Nome do dono do microfone (Faixa 2). Aparece nas transcrições e nos títulos.", "Host"),
    _field("BANIDO_GROUP_NAME", "Nome do canal ou do grupo", TEXT, "Identidade", "", "Banido Studio"),

    _field("BANIDO_OBS_DIR", "Gravações do OBS", FOLDER, "Pastas",
           "Pasta onde o OBS salva os vídeos (.mkv). O programa lista e processa o que estiver nela."),
    _field("BANIDO_CLIPS_DIR", "Cortes prontos", FOLDER, "Pastas", "Pasta onde os cortes exportados são salvos."),
    _field("BANIDO_VOICE_BANK_DIR", "Vozes dos amigos", FOLDER, "Pastas",
           "Amostras de voz usadas para reconhecer quem está falando."),
    _field("BANIDO_MODELS_DIR", "Modelos de IA baixados", FOLDER, "Pastas",
           "Os modelos são grandes (vários GB). Escolha um disco com espaço livre."),
    _field("BANIDO_TEMP_DIR", "Arquivos temporários", FOLDER, "Pastas",
           "Opcional. Um SSD rápido deixa o processamento mais ágil. Vazio usa a pasta temporária do Windows.",
           optional=True),

    _field("BANIDO_WHISPER_MODEL", "Modelo de transcrição", CHOICE, "Transcrição",
           "O large-v3 é o mais preciso e usa mais memória da placa de vídeo. Em computadores mais fracos, use medium.",
           "large-v3",
           [("large-v3", "large-v3 (melhor qualidade)"), ("large-v2", "large-v2"), ("medium", "medium (mais leve)"),
            ("small", "small"), ("base", "base"), ("tiny", "tiny (só para testes)")]),
    _field("BANIDO_WHISPER_DEVICE", "Onde transcrever", CHOICE, "Transcrição",
           "Automático usa a placa de vídeo NVIDIA quando existe. Processador (CPU) funciona, porém bem mais devagar.",
           "auto", [("auto", "Automático"), ("cuda", "Placa de vídeo (GPU)"), ("cpu", "Processador (CPU)")]),
    _field("BANIDO_WHISPER_COMPUTE_TYPE", "Precisão da transcrição", CHOICE, "Transcrição",
           "Se faltar memória da placa de vídeo, escolha int8_float16: usa menos memória e perde muito pouca qualidade.",
           "auto", [("auto", "Automática"), ("float16", "float16 (melhor qualidade)"),
                    ("int8_float16", "int8_float16 (usa menos memória)"), ("int8", "int8 (mais leve)"),
                    ("float32", "float32 (só para CPU)")]),
    _field("BANIDO_WHISPER_LANGUAGE", "Idioma das falas", CHOICE, "Transcrição",
           "Idioma falado nos vídeos. Detectar sozinho é mais lento e pode errar em trechos curtos.",
           "pt", LANGUAGES),
    _field("BANIDO_SEPARATE_TRACKS", "Transcrever o microfone e o Discord separados", BOOL, "Transcrição",
           "Detecta falas sobrepostas, mas transcreve uma vez por faixa (mais lento). Desligado usa o mix único.", "1"),
    _field("BANIDO_KEEP_ORIG_AUDIO_BACKUP", "Guardar o áudio original ao normalizar o volume", BOOL, "Transcrição",
           "Mantém uma cópia do mix original dentro do vídeo. Desligar economiza espaço, mas não há como desfazer.", "1"),

    _field("BANIDO_AUTO_PAUSE_ON_RECORDING", "Pausar enquanto o OBS grava", BOOL, "Pausa automática",
           "Evita que o processamento atrapalhe a gravação ou o jogo.", "1"),
    _field("BANIDO_PAUSE_PROCESSES", "Jogos que também pausam o processamento", TEXT, "Pausa automática",
           "Opcional. Nomes dos programas separados por vírgula, por exemplo: cs2.exe, valorant.exe",
           optional=True),

    _field("BANIDO_OBS_RESOLUTION", "Resolução e FPS", TEXT, "Ficha técnica na descrição do vídeo",
           "Estas informações entram na descrição gerada para o YouTube.", "3840x2160 (4K UHD) @ 60 FPS",
           optional=True, advanced=True),
    _field("BANIDO_OBS_ENCODER", "Codificador do OBS", TEXT, "Ficha técnica na descrição do vídeo", "",
           "NVENC AV1 / HEVC Hardware", optional=True, advanced=True),
    _field("BANIDO_OBS_RATE_CONTROL", "Controle de taxa", TEXT, "Ficha técnica na descrição do vídeo", "",
           "CQP (Qualidade Constante)", optional=True, advanced=True),
    _field("BANIDO_OBS_PRESET", "Predefinição do codificador", TEXT, "Ficha técnica na descrição do vídeo", "",
           "Alta Qualidade | Multipass", optional=True, advanced=True),
    _field("BANIDO_OBS_COLOR", "Formato de cor", TEXT, "Ficha técnica na descrição do vídeo", "",
           "Rec. 709 | NV12", optional=True, advanced=True),
    _field("BANIDO_GPU_LABEL", "Placa de vídeo", TEXT, "Ficha técnica na descrição do vídeo",
           "Vazio: o programa detecta sozinho.", optional=True, advanced=True),
    _field("BANIDO_CPU_LABEL", "Processador", TEXT, "Ficha técnica na descrição do vídeo",
           "Vazio: o programa detecta sozinho.", optional=True, advanced=True),
    _field("BANIDO_RAM_LABEL", "Memória RAM", TEXT, "Ficha técnica na descrição do vídeo",
           "Vazio: o programa detecta sozinho.", optional=True, advanced=True),
]

FIELDS_BY_KEY = {f["key"]: f for f in FIELDS}


def read_env_file(path: Optional[Path] = None) -> Dict[str, str]:
    """Valores gravados hoje no .env (sem aspas). Linhas comentadas são ignoradas."""
    env_path = path or first_run.ENV_PATH
    values: Dict[str, str] = {}
    if not env_path.is_file():
        return values
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", raw)
        if not match:
            continue
        val = match.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[match.group(1)] = val
    return values


def _normalize_bool(raw: str, default: str) -> str:
    text = (raw or "").strip().lower()
    if text in _TRUE:
        return "1"
    if text in _FALSE:
        return "0"
    return default


def current_values(path: Optional[Path] = None) -> Dict[str, str]:
    """Valor atual de cada opção. Opções fora do .env aparecem com o valor padrão."""
    saved = read_env_file(path)
    out: Dict[str, str] = {}
    for f in FIELDS:
        raw = saved.get(f["key"])
        if f["kind"] == BOOL:
            out[f["key"]] = _normalize_bool(raw if raw is not None else f["default"], f["default"])
        elif raw is None or not raw.strip():
            out[f["key"]] = "" if f["optional"] or f["kind"] == FOLDER else f["default"]
        else:
            out[f["key"]] = raw.strip()
    return out


def describe(effective_folders: Dict[str, str], path: Optional[Path] = None) -> Dict[str, Any]:
    """Catálogo + valores para a tela. Pastas sem valor no .env mostram a pasta realmente em uso."""
    values = current_values(path)
    for key, folder in effective_folders.items():
        if key in values and not values[key]:
            values[key] = folder
    return {"fields": FIELDS, "values": values}


def validate(updates: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Valida o que veio da tela. Devolve (valores prontos para gravar, erros por opção)."""
    clean: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    for key, raw in (updates or {}).items():
        field = FIELDS_BY_KEY.get(key)
        if field is None:
            errors[key] = "Opção desconhecida."
            continue
        text = "" if raw is None else str(raw)
        label = field["label"]
        if field["kind"] == BOOL:
            norm = _normalize_bool(text, "")
            if not norm:
                errors[key] = f"“{label}”: escolha Ligado ou Desligado."
            else:
                clean[key] = norm
        elif field["kind"] == CHOICE:
            if text.strip() not in [v for v, _ in field["options"]]:
                errors[key] = f"“{label}”: escolha uma das opções da lista."
            else:
                clean[key] = text.strip()
        elif field["kind"] == FOLDER:
            if not text.strip():
                if field["optional"]:
                    clean[key] = ""
                else:
                    errors[key] = f"“{label}”: escolha uma pasta."
                continue
            folder, err = first_run.validate_folder(text)
            if err:
                errors[key] = f"“{label}”: {err}"
            else:
                clean[key] = str(folder)
        else:
            value = re.sub(r"\s+", " ", re.sub(r"[\r\n\t\"'#]", " ", text)).strip()
            if len(value) > MAX_TEXT_LEN:
                errors[key] = f"“{label}”: texto longo demais (máximo {MAX_TEXT_LEN} letras)."
            elif not value and not field["optional"]:
                errors[key] = f"“{label}” não pode ficar vazio."
            else:
                clean[key] = value
    return clean, errors


def changed_only(clean: Dict[str, str], baseline: Dict[str, str]) -> Dict[str, str]:
    """Só o que mudou em relação ao que a tela mostrava, para não reescrever o .env à toa."""
    out = {}
    for key, val in clean.items():
        old = baseline.get(key, "")
        if FIELDS_BY_KEY[key]["kind"] == FOLDER and val and old and Path(val) == Path(old):
            continue
        if old != val:
            out[key] = val
    return out
