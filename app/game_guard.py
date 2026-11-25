"""
Proteção de desempenho durante partidas/gravações (D4).

- Reduz a prioridade de CPU do worker (BELOW_NORMAL_PRIORITY_CLASS no Windows / nice +10 no Linux)
  para nunca engasgar o jogo nem o Discord.
- Detecta automaticamente gravações ativas do OBS em OBS_RECORDINGS_DIR (zero configuração —
  não exige WebSocket do OBS) e processos opcionais listados em BANIDO_PAUSE_PROCESSES.
- Pausa o processamento de forma segura ENTRE blocos de 5 minutos (respeitando checkpoints JSON)
  e retoma sozinho ao fim da gravação/jogo — ou imediatamente quando o usuário clica em
  "Continuar mesmo assim" (criando force_resume.flag).
"""
import csv
import io
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def lower_current_process_priority() -> bool:
    """Coloca o processo atual em prioridade abaixo do normal para priorizar o jogo/sistema."""
    try:
        if os.name == "nt":
            import ctypes
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            return bool(ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS))
        if hasattr(os, "nice"):
            os.nice(10)
            return True
    except Exception:
        return False
    return False


def detect_active_obs_recording(
    obs_dir: Path,
    exclude_path: Optional[Path] = None,
    recent_sec: float = 15.0,
    now: Optional[float] = None,
) -> Optional[str]:
    """
    Detecta se há um arquivo .mkv sendo gravado pelo OBS neste momento em `obs_dir`
    (modificado nos últimos `recent_sec` segundos, ignorando arquivos ocultos temporários
    e o próprio vídeo em processamento).
    """
    if not obs_dir or not obs_dir.exists():
        return None
    cur_now = time.time() if now is None else now
    excluded_resolved = None
    if exclude_path is not None:
        try:
            excluded_resolved = exclude_path.resolve()
        except Exception:
            excluded_resolved = exclude_path

    try:
        for p in obs_dir.iterdir():
            if not p.is_file() or p.suffix.lower() != ".mkv" or p.name.startswith("."):
                continue
            if excluded_resolved is not None:
                try:
                    if p.resolve() == excluded_resolved:
                        continue
                except Exception:
                    if p == excluded_resolved:
                        continue
            try:
                age = cur_now - p.stat().st_mtime
            except OSError:
                continue
            if -2.0 <= age <= recent_sec:
                return p.name
    except OSError:
        return None
    return None


def _list_running_process_names() -> set:
    try:
        if os.name == "nt":
            res = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=3, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if res.returncode != 0:
                return set()
            names = set()
            for row in csv.reader(io.StringIO(res.stdout or "")):
                if row and row[0].strip():
                    names.add(row[0].strip().lower())
            return names
        res = subprocess.run(
            ["ps", "-eo", "comm="],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3,
        )
        if res.returncode != 0:
            return set()
        return {ln.strip().lower() for ln in (res.stdout or "").splitlines() if ln.strip()}
    except Exception:
        return set()


def detect_watched_process(process_names: Iterable[str]) -> Optional[str]:
    targets = [str(n or "").strip() for n in process_names if str(n or "").strip()]
    if not targets:
        return None
    running = _list_running_process_names()
    if not running:
        return None
    for t in targets:
        tl = t.lower()
        if tl in running or (tl.endswith(".exe") and tl[:-4] in running):
            return t
    return None


def detect_pause_reason(
    obs_dir: Path,
    exclude_path: Optional[Path] = None,
    watched_processes: Optional[Iterable[str]] = None,
    recent_sec: float = 15.0,
) -> Optional[str]:
    """Retorna motivo legível para pausar (gravação ativa do OBS ou processo de jogo), ou None."""
    rec_name = detect_active_obs_recording(obs_dir, exclude_path=exclude_path, recent_sec=recent_sec)
    if rec_name:
        return f"gravação ativa do OBS ({rec_name})"
    if watched_processes:
        proc = detect_watched_process(watched_processes)
        if proc:
            return f"jogo/processo aberto ({proc})"
    return None


def wait_while_paused(
    obs_dir: Path,
    resume_flag_path: Path,
    exclude_path: Optional[Path] = None,
    watched_processes: Optional[Iterable[str]] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    poll_sec: float = 5.0,
    recent_sec: float = 15.0,
) -> bool:
    """
    Chamado entre blocos de 5 minutos. Se houver gravação ativa do OBS ou processo vigiado
    e `resume_flag_path` não existir, aguarda até o fim da gravação/jogo ou override manual.
    Retorna True se chegou a pausar, False se seguiu direto.
    """
    if resume_flag_path.exists():
        return False

    reason = detect_pause_reason(
        obs_dir, exclude_path=exclude_path, watched_processes=watched_processes, recent_sec=recent_sec
    )
    if not reason:
        return False

    msg = f"⏸️ Pausado: {reason}. Retoma automaticamente ao encerrar (ou clique em Continuar mesmo assim)."
    print(f"[GAME GUARD] {msg}", flush=True)
    if progress_cb:
        progress_cb(msg)

    while True:
        time.sleep(poll_sec)
        if resume_flag_path.exists():
            print("[GAME GUARD] ▶️ Override manual recebido — retomando processamento imediatamente!", flush=True)
            return True
        cur_reason = detect_pause_reason(
            obs_dir, exclude_path=exclude_path, watched_processes=watched_processes, recent_sec=recent_sec
        )
        if not cur_reason:
            print("[GAME GUARD] ▶️ Gravação/jogo encerrado — retomando processamento automaticamente!", flush=True)
            return True
