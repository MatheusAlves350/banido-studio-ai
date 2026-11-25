import os
import sys
import shutil
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(env_path: Path) -> None:
    """Carrega variáveis de um arquivo .env local sem sobrescrever variáveis já definidas no ambiente."""
    if not env_path.is_file():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


_load_dotenv(PROJECT_ROOT / ".env")


def _resolve_env_path(env_var: str, default_path: Path) -> Path:
    raw = (os.getenv(env_var) or "").strip()
    if not raw:
        return default_path.resolve()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


# Identidade, rede e Whisper configuráveis por instância
HOST_SPEAKER_NAME = (os.getenv("BANIDO_HOST_NAME") or "Host").strip() or "Host"
GROUP_NAME = (os.getenv("BANIDO_GROUP_NAME") or "Banido Studio").strip() or "Banido Studio"
SERVER_HOST = (os.getenv("BANIDO_HOST") or "127.0.0.1").strip() or "127.0.0.1"
try:
    SERVER_PORT = int((os.getenv("BANIDO_PORT") or "8765").strip())
except ValueError:
    SERVER_PORT = 8765


def _build_allowed_hosts() -> list:
    extra = [h.strip() for h in (os.getenv("BANIDO_ALLOWED_HOSTS") or "").split(",") if h.strip()]
    if "*" in extra or SERVER_HOST == "0.0.0.0":
        return ["*"]
    hosts = ["localhost", "127.0.0.1", "[::1]", "testserver"]
    if SERVER_HOST not in hosts:
        hosts.append(SERVER_HOST)
    for h in extra:
        if h not in hosts:
            hosts.append(h)
    return hosts


ALLOWED_HOSTS = _build_allowed_hosts()

WHISPER_MODEL_SIZE = (os.getenv("BANIDO_WHISPER_MODEL") or "large-v3").strip() or "large-v3"
WHISPER_DEVICE = (os.getenv("BANIDO_WHISPER_DEVICE") or "auto").strip().lower() or "auto"
WHISPER_COMPUTE_TYPE = (os.getenv("BANIDO_WHISPER_COMPUTE_TYPE") or "auto").strip().lower() or "auto"
# Transcrição separada por faixa (microfone do anfitrião x Discord) quando há mic isolado.
# Detecta falas sobrepostas, mas roda o Whisper uma vez por faixa. "0" volta ao modo de mix único.
SEPARATE_TRACK_TRANSCRIPTION = (os.getenv("BANIDO_SEPARATE_TRACKS") or "1").strip().lower() not in ("0", "false", "no", "nao", "não")
_raw_lang = (os.getenv("BANIDO_WHISPER_LANGUAGE") or "pt").strip()
WHISPER_LANGUAGE: Optional[str] = None if _raw_lang.lower() in ("auto", "none", "") else _raw_lang

# Diretórios principais (portáveis por padrão, configuráveis via .env ou variáveis de ambiente)
BASE_DIR = _resolve_env_path("BANIDO_BASE_DIR", PROJECT_ROOT / "data")
OBS_RECORDINGS_DIR = _resolve_env_path("BANIDO_OBS_DIR", BASE_DIR / "obs_recordings")
MODELS_DIR = _resolve_env_path("BANIDO_MODELS_DIR", BASE_DIR / "models")
VOICE_BANK_DIR = _resolve_env_path("BANIDO_VOICE_BANK_DIR", BASE_DIR / "vozes_amigos")
CLIPS_DIR = _resolve_env_path("BANIDO_CLIPS_DIR", BASE_DIR / "Cortes")
LEGACY_TEMP_DIR = BASE_DIR / "temp"

_env_temp = (os.getenv("BANIDO_TEMP_DIR") or "").strip()
try:
    _default_temp = Path(_env_temp).expanduser().resolve() if _env_temp else (Path(tempfile.gettempdir()) / "BanidoStudioTemp")
    _default_temp.mkdir(parents=True, exist_ok=True)
    TEMP_DIR = _default_temp
except Exception:
    TEMP_DIR = LEGACY_TEMP_DIR

DATABASE_DIR = BASE_DIR / "database"
LOGS_DIR = DATABASE_DIR / "logs"
DB_PATH = DATABASE_DIR / "banido_studio.db"
SETTINGS_JSON_PATH = DATABASE_DIR / "settings.json"

_env_python = (os.getenv("BANIDO_PYTHON") or "").strip()
VENV_PYTHON = Path(_env_python).expanduser().resolve() if _env_python else Path(sys.executable)

# Garantir que todos os diretórios existem
for d in [BASE_DIR, OBS_RECORDINGS_DIR, MODELS_DIR, VOICE_BANK_DIR, CLIPS_DIR, LEGACY_TEMP_DIR, TEMP_DIR, DATABASE_DIR, LOGS_DIR]:
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _detect_gpu_name() -> str:
    env_gpu = (os.getenv("BANIDO_GPU_LABEL") or "").strip()
    if env_gpu:
        return env_gpu
    if shutil.which("nvidia-smi"):
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=2
            )
            line = (res.stdout or "").strip().splitlines()[0] if res.returncode == 0 and (res.stdout or "").strip() else ""
            if line:
                return line
        except Exception:
            pass
    return "CPU / GPU Local"


def _detect_ram_label() -> str:
    env_ram = (os.getenv("BANIDO_RAM_LABEL") or "").strip()
    if env_ram:
        return env_ram
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                gb = round(stat.ullTotalPhys / (1024 ** 3))
                return f"{gb} GB"
        elif Path("/proc/meminfo").is_file():
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return f"{round(kb / (1024 ** 2))} GB"
    except Exception:
        pass
    return "Auto-detectado"


def _detect_cpu_label() -> str:
    env_cpu = (os.getenv("BANIDO_CPU_LABEL") or "").strip()
    if env_cpu:
        return env_cpu
    cpu = platform.processor() or platform.machine() or "CPU"
    cores = os.cpu_count()
    return f"{cpu} ({cores} Threads)" if cores else cpu


# Ficha técnica da instância atual (detectada dinamicamente ou via .env)
PC_SPECS = {
    "cpu": _detect_cpu_label(),
    "gpu": _detect_gpu_name(),
    "ram": _detect_ram_label(),
    "os": f"{platform.system()} {platform.release()}".strip(),
}

# Configurações de Gravação do OBS
OBS_SPECS = {
    "resolution": os.getenv("BANIDO_OBS_RESOLUTION", "3840x2160 (4K UHD) @ 60 FPS"),
    "encoder": os.getenv("BANIDO_OBS_ENCODER", "NVENC AV1 / HEVC Hardware"),
    "rate_control": os.getenv("BANIDO_OBS_RATE_CONTROL", "CQP (Qualidade Constante)"),
    "preset": os.getenv("BANIDO_OBS_PRESET", "Alta Qualidade | Multipass"),
    "color_format": os.getenv("BANIDO_OBS_COLOR", "Rec. 709 | NV12"),
    "audio": f"Faixas Independentes (Mix Geral, {HOST_SPEAKER_NAME}, Discord, Jogo, Desktop, Soundpad)",
}

# Configurações de IA
AI_SPECS = {
    "whisper": f"OpenAI Whisper {WHISPER_MODEL_SIZE} (Beam Size 5, CUDA float16 / fallback CPU)",
    "diarization": "FFmpeg Multichannel Energy + SpeechBrain ECAPA-TDNN Voice Bank",
    "cutting": "Lossless Stream Copy (ffmpeg -c:v copy) sem re-renderização",
}
