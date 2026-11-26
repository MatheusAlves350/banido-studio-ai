"""Ambiente de execução: DLLs CUDA, memória e carregamento e liberação do modelo Whisper."""
import os
import time
from pathlib import Path
from typing import List, Any
from app.config import (
    MODELS_DIR, WHISPER_MODEL_SIZE,
    WHISPER_DEVICE, WHISPER_COMPUTE_TYPE
)


_whisper_model = None


_DLL_DIR_HANDLES: List[Any] = []


def _register_cuda_dll_dirs():
    """
    Registra os diretórios de DLLs CUDA (ex: torch/lib, nvidia/*/bin) no processo Windows
    e pré-carrega torch/lib/cudnn64_9.dll (0.4 MB) ANTES do ctranslate2.
    Isso evita que o ctranslate2 carregue sua versão reduzida de cudnn64_9.dll (sem o símbolo
    cudnnGetLibConfig), que causava falha ao carregar o PyTorch/SpeechBrain depois do Whisper.
    """
    if os.name != "nt":
        return
    import sys
    import ctypes
    sp = Path(sys.prefix) / "Lib" / "site-packages"
    torch_lib = sp / "torch" / "lib"
    candidate_dirs = [torch_lib]
    nvidia_dir = sp / "nvidia"
    if nvidia_dir.exists():
        candidate_dirs.extend(sorted(nvidia_dir.glob("*/bin")))

    cur_path = os.environ.get("PATH", "")
    added_paths = []
    for d in candidate_dirs:
        if d.exists():
            d_str = str(d)
            try:
                if hasattr(os, "add_dll_directory"):
                    _DLL_DIR_HANDLES.append(os.add_dll_directory(d_str))
            except Exception:
                pass
            if d_str.lower() not in cur_path.lower():
                added_paths.append(d_str)
    if added_paths:
        os.environ["PATH"] = ";".join(added_paths) + ";" + cur_path

    # Pré-carregar cudnn64_9.dll completo do torch/lib (~400 KB) sem importar o módulo torch!
    torch_cudnn = torch_lib / "cudnn64_9.dll"
    if torch_cudnn.exists():
        try:
            ctypes.CDLL(str(torch_cudnn))
        except Exception:
            pass


def format_hhmmss(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def _log_memory_status(prefix: str = "[MEMÓRIA]"):
    try:
        if os.name == "nt":
            import ctypes
            M = type("M", (ctypes.Structure,), {"_fields_": [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]})
            m = M()
            m.dwLength = ctypes.sizeof(m)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            g = 1024 ** 3
            print(
                f"{prefix} RAM livre: {m.ullAvailPhys / g:.1f}/{m.ullTotalPhys / g:.1f} GB ({m.dwMemoryLoad}% uso) | "
                f"Commit disponível: {m.ullAvailPageFile / g:.1f}/{m.ullTotalPageFile / g:.1f} GB",
                flush=True
            )
        elif Path("/proc/meminfo").is_file():
            info = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = int(v.strip().split()[0])
            tot = info.get("MemTotal", 0) / (1024 ** 2)
            avail = info.get("MemAvailable", 0) / (1024 ** 2)
            if tot > 0:
                print(f"{prefix} RAM disponível: {avail:.1f}/{tot:.1f} GB", flush=True)
    except Exception:
        pass


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        _register_cuda_dll_dirs()
        import ctranslate2
        from faster_whisper import WhisperModel
        model_dir = MODELS_DIR / "whisper"
        model_dir.mkdir(parents=True, exist_ok=True)

        # Usar ctranslate2 em vez de importar PyTorch aqui economiza ~1.5-2 GB de RAM/commit durante o Whisper!
        has_cuda = False
        try:
            has_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            has_cuda = False

        if WHISPER_DEVICE in ("cuda", "cpu"):
            device = WHISPER_DEVICE
        else:
            device = "cuda" if has_cuda else "cpu"

        if WHISPER_COMPUTE_TYPE != "auto":
            compute_type = WHISPER_COMPUTE_TYPE
        else:
            compute_type = "float16" if device == "cuda" else "int8"

        print(f"[WHISPER] Carregando modelo '{WHISPER_MODEL_SIZE}' em {device.upper()} ({compute_type})...", flush=True)

        t0 = time.time()
        _whisper_model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=device,
            compute_type=compute_type,
            download_root=str(model_dir)
        )
        print(f"[WHISPER] Modelo '{WHISPER_MODEL_SIZE}' pronto em {time.time() - t0:.1f}s!", flush=True)
    return _whisper_model


def release_whisper_model():
    """Libera o modelo Whisper carregado (VRAM/RAM) antes de carregar outros modelos."""
    global _whisper_model
    _whisper_model = None
