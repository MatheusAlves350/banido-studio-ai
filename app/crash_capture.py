"""F1: registra o que acontecia no momento em que o processo worker cai (ex.: 0xC0000409).

Analogia: a "caixa-preta" de um avião. Em voo normal ela só grava e depois é apagada;
se houver acidente, quem investiga tem o que olhar.

Dois lados:
- Dentro do worker: `start()` liga o faulthandler (pilha do Python), grava um cabeçalho com
  as versões e roda um monitor leve que, a cada 5 s, anota RAM, VRAM e a etapa em curso
  nos últimos 2 minutos. Tudo vai para arquivos, porque uma queda nativa não deixa o
  Python fazer limpeza.
- No programa principal: `collect_crash_info()` junta esses arquivos, o código de saída e
  (no Windows) o módulo que falhou segundo o Registro de Eventos, em um relatório só.

Limite conhecido: o 0xC0000409 costuma vir de `__fastfail`, que encerra o processo sem
passar pelo faulthandler. Por isso o monitor e o Registro de Eventos importam tanto quanto a pilha.
"""
import ctypes
import faulthandler
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

SAMPLE_INTERVAL_SEC = 5
KEEP_SAMPLES = 24  # 24 x 5 s = 2 minutos

# Códigos de saída do Windows mais comuns em quedas de bibliotecas nativas.
EXIT_CODE_MEANINGS = {
    0xC0000409: "falha interna de memória em uma biblioteca nativa (STACK_BUFFER_OVERRUN; costuma vir do FFmpeg, CUDA ou PyTorch)",
    0xC0000005: "acesso inválido à memória (ACCESS_VIOLATION)",
    0xC0000374: "memória corrompida (HEAP_CORRUPTION)",
    0xC0000142: "uma biblioteca (DLL) não conseguiu iniciar",
    0xC000013A: "encerrado pelo usuário (Ctrl+C)",
    0xC0000017: "memória insuficiente",
    0xC00000FD: "estouro de pilha (STACK_OVERFLOW)",
}

_state: Dict[str, Any] = {"fh": None, "stage": "", "thread": None, "stop": None, "samples": None, "files": None}


def crash_files(job_id: int, logs_dir: Path) -> Dict[str, Path]:
    return {
        "crash": logs_dir / f"job_{job_id}_crash.log",
        "resources": logs_dir / f"job_{job_id}_resources.log",
        "report": logs_dir / f"job_{job_id}_crash_report.txt",
    }


def has_pending_crash_files(job_id: int, logs_dir: Path) -> bool:
    """True se ainda existem arquivos brutos da caixa-preta (worker morreu sem passar pelo finally)."""
    files = crash_files(job_id, logs_dir)
    return files["crash"].exists() or files["resources"].exists()


# ------------------------------------------------------------------ medições simples

def _version(pkg: str) -> str:
    try:
        return metadata.version(pkg)
    except Exception:
        return "não instalado"


def _run_quiet(cmd: List[str], timeout: int = 5) -> str:
    try:
        kwargs: Dict[str, Any] = {"capture_output": True, "text": True, "timeout": timeout, "errors": "replace"}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(cmd, **kwargs)
        return (res.stdout or "").strip() if res.returncode == 0 else ""
    except Exception:
        return ""


def gpu_memory_mb() -> Optional[Dict[str, int]]:
    """VRAM usada/total em MB via nvidia-smi; None se não houver GPU NVIDIA."""
    out = _run_quiet(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"], timeout=4)
    if not out:
        return None
    try:
        used, total = [int(x.strip()) for x in out.splitlines()[0].split(",")[:2]]
        return {"used": used, "total": total}
    except Exception:
        return None


def ram_mb() -> Dict[str, Optional[int]]:
    """RAM do PC livre e RAM usada por este processo, em MB (None quando não dá para medir)."""
    free = proc = None
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                free = int(st.ullAvailPhys // (1024 * 1024))

            class PMC(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            get_cur = ctypes.windll.kernel32.GetCurrentProcess
            get_cur.restype = ctypes.c_void_p
            get_mem = ctypes.windll.psapi.GetProcessMemoryInfo
            get_mem.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
            get_mem.restype = ctypes.c_int
            handle = get_cur()
            if get_mem(handle, ctypes.byref(pmc), pmc.cb):
                proc = int(pmc.WorkingSetSize // (1024 * 1024))
        else:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    free = int(line.split()[1]) // 1024
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    proc = int(line.split()[1]) // 1024
    except Exception:
        pass
    return {"free": free, "proc": proc}


def build_header() -> str:
    """Versões e ambiente, gravados no início de cada job."""
    ffmpeg = _run_quiet(["ffmpeg", "-version"], timeout=5).splitlines()
    smi = _run_quiet(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"], timeout=4)
    lines = [
        f"Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Sistema: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {sys.version.split()[0]}",
        f"PyTorch: {_version('torch')} | faster-whisper: {_version('faster-whisper')} | "
        f"ctranslate2: {_version('ctranslate2')} | speechbrain: {_version('speechbrain')}",
        f"FFmpeg: {ffmpeg[0] if ffmpeg else 'não encontrado'}",
        f"GPU (nome, driver, VRAM): {smi or 'nenhuma GPU NVIDIA detectada'}",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ lado do worker

def set_stage(text: str) -> None:
    """Informa a etapa em curso (ex.: 'Whisper bloco 12/40') para constar nas amostras."""
    _state["stage"] = str(text or "")[:160]


def _format_sample(stage: str) -> str:
    gpu = gpu_memory_mb()
    ram = ram_mb()
    gpu_txt = f"VRAM {gpu['used']}/{gpu['total']} MB" if gpu else "VRAM n/d"
    free = f"{ram['free']} MB" if ram["free"] is not None else "n/d"
    proc = f"{ram['proc']} MB" if ram["proc"] is not None else "n/d"
    return f"[{datetime.now().strftime('%H:%M:%S')}] {gpu_txt} | RAM livre {free} | RAM do worker {proc} | etapa: {stage or '-'}"


def _monitor_loop(path: Path, stop: threading.Event, samples: Deque[str]) -> None:
    while not stop.is_set():
        try:
            samples.append(_format_sample(_state["stage"]))
            # Reescreve o arquivo inteiro (poucas linhas): sobrevive a uma queda sem aviso.
            path.write_text("\n".join(samples) + "\n", encoding="utf-8")
        except Exception:
            pass
        stop.wait(SAMPLE_INTERVAL_SEC)


def start(job_id: int, logs_dir: Path) -> None:
    """Liga a caixa-preta dentro do worker. Nunca deve derrubar o processamento."""
    try:
        files = crash_files(job_id, logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        fh = open(files["crash"], "w", encoding="utf-8", errors="replace")
        fh.write("=== Cabeçalho do job ===\n" + build_header() + "\n\n=== Pilha do Python em caso de falha fatal ===\n")
        fh.flush()
        faulthandler.enable(file=fh, all_threads=True)
        _state.update(fh=fh, files=files)

        stop = threading.Event()
        samples: Deque[str] = deque(maxlen=KEEP_SAMPLES)
        t = threading.Thread(target=_monitor_loop, args=(files["resources"], stop, samples), daemon=True, name="crash-monitor")
        _state.update(thread=t, stop=stop, samples=samples)
        t.start()
    except Exception:
        pass


def stop(success: bool) -> None:
    """Desliga a caixa-preta. Em job bem-sucedido apaga os arquivos; em falha, mantém."""
    try:
        if _state["stop"]:
            _state["stop"].set()
        if _state["thread"]:
            _state["thread"].join(timeout=2)
        faulthandler.disable()
        if _state["fh"]:
            _state["fh"].close()
        files = _state.get("files")
        if success and files:
            for key in ("crash", "resources"):
                files[key].unlink(missing_ok=True)
    except Exception:
        pass
    finally:
        _state.update(fh=None, thread=None, stop=None, samples=None, files=None, stage="")


# ------------------------------------------------------------------ lado do programa principal

def describe_exit_code(code: int) -> str:
    """'0xC0000409 (falha interna de memória ...)'; códigos comuns viram texto simples."""
    unsigned = code & 0xFFFFFFFF if code >= 0 else code
    if code < 0:
        return f"sinal {-code} (encerrado pelo sistema)"
    meaning = EXIT_CODE_MEANINGS.get(unsigned)
    hex_txt = f"0x{unsigned:08X}"
    return f"{hex_txt} ({meaning})" if meaning else (hex_txt if unsigned > 255 else str(unsigned))


def parse_application_error_xml(xml_text: str, pid: int) -> Optional[Dict[str, str]]:
    """
    Lê um evento 'Application Error' (Windows) em XML e devolve o módulo que falhou.
    Independe do idioma do Windows porque usa a ordem dos campos <Data>:
    0 aplicativo, 3 módulo, 6 código da exceção, 7 deslocamento, 8 id do processo (hex).
    """
    for event in re.findall(r"<Event[ >].*?</Event>", xml_text, flags=re.S):
        data = re.findall(r"<Data[^>]*>(.*?)</Data>", event, flags=re.S)
        if len(data) < 9:
            continue
        try:
            if int(data[8].strip(), 16) != pid:
                continue
        except ValueError:
            continue
        return {"app": data[0].strip(), "module": data[3].strip(), "exception": data[6].strip(), "offset": data[7].strip()}
    return None


def windows_fault_module(pid: int) -> Optional[Dict[str, str]]:
    """No Windows, pergunta ao Registro de Eventos qual módulo derrubou o processo (últimos 10 min)."""
    if os.name != "nt" or not shutil.which("wevtutil"):
        return None
    xml = _run_quiet([
        "wevtutil", "qe", "Application",
        "/q:*[System[Provider[@Name='Application Error'] and TimeCreated[timediff(@SystemTime) <= 600000]]]",
        "/c:10", "/rd:true", "/f:xml",
    ], timeout=10)
    return parse_application_error_xml(xml, pid) if xml else None


def collect_crash_info(job_id: int, exit_code: int, logs_dir: Path, pid: int = 0, stage: str = "") -> Dict[str, Any]:
    """
    Junta tudo sobre uma queda em `job_<id>_crash_report.txt` e apaga os arquivos brutos.
    Devolve {"summary": texto curto para o usuário, "report_path": Path|None}.
    """
    files = crash_files(job_id, logs_dir)
    parts = [
        f"=== Relatório de queda do job #{job_id} ===",
        f"Código de saída: {describe_exit_code(exit_code)}",
    ]
    if stage:
        parts.append(f"Última etapa registrada: {stage}")

    fault = None
    try:
        fault = windows_fault_module(pid) if pid else None
    except Exception:
        fault = None
    if fault:
        parts.append(
            f"Registro de Eventos do Windows: módulo que falhou = {fault['module']} "
            f"(exceção {fault['exception']}, deslocamento {fault['offset']}, aplicativo {fault['app']})"
        )

    for label, key in (("Cabeçalho e pilha do Python", "crash"), ("Últimos 2 minutos de RAM/VRAM (de 5 em 5 s)", "resources")):
        try:
            txt = files[key].read_text(encoding="utf-8", errors="replace").strip() if files[key].exists() else ""
        except Exception:
            txt = ""
        parts.append(f"\n--- {label} ---\n{txt or 'sem registro (o worker caiu antes de gravar)'}")

    report_text = "\n".join(parts) + "\n"
    report_path: Optional[Path] = files["report"]
    try:
        report_path.write_text(report_text, encoding="utf-8")
    except Exception:
        report_path = None
    for key in ("crash", "resources"):
        try:
            files[key].unlink(missing_ok=True)
        except Exception:
            pass

    summary = f"código {describe_exit_code(exit_code)}"
    if fault:
        summary += f"; módulo que falhou: {fault['module']}"
    if stage:
        summary += f"; etapa: {stage}"
    return {"summary": summary, "report_path": report_path, "report_text": report_text}


def last_known_stage(job_id: int, logs_dir: Path) -> str:
    """Etapa da última amostra do monitor (útil quando o banco já perdeu a etapa)."""
    try:
        lines = crash_files(job_id, logs_dir)["resources"].read_text(encoding="utf-8", errors="replace").strip().splitlines()
        m = re.search(r"etapa: (.*)$", lines[-1]) if lines else None
        stage = m.group(1).strip() if m else ""
        return "" if stage == "-" else stage
    except Exception:
        return ""
