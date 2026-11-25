"""
Atualizador integrado ao GitHub.

Funciona de duas formas:

1. Instalação com `git clone` (caso recomendado no README):
   - `git fetch` da branch configurada no remoto `origin`;
   - compara HEAD local com `origin/<branch>` e lista as novidades (commits);
   - aplica com `git merge --ff-only` (nunca sobrescreve alterações locais nem cria merges);
   - se `requirements.txt` mudou, roda `pip install -r requirements.txt` no Python atual;
   - pede ao launcher (Iniciar_Banido_Studio.bat / start.sh) para reiniciar o servidor (código de saída 3).

2. Instalação pelo BanidoStudio-Setup.exe (sem pasta .git, com arquivo VERSION): consulta a última release do GitHub
   e, ao aplicar, baixa e executa o novo instalador em modo silencioso (ver app/release_updater.py).

Quando o projeto foi baixado como ZIP (sem .git e sem VERSION), o atualizador apenas consulta a API
pública do GitHub e mostra o link para baixar a versão nova manualmente.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import release_updater
from app.config import PROJECT_ROOT, LOGS_DIR

UPDATE_REPO = (os.getenv("BANIDO_UPDATE_REPO") or "MatheusAlves350/banido-studio-ai").strip()
UPDATE_BRANCH = (os.getenv("BANIDO_UPDATE_BRANCH") or "main").strip()
UPDATE_REMOTE = (os.getenv("BANIDO_UPDATE_REMOTE") or "origin").strip()
CHECK_CACHE_SEC = 600  # evita ir à rede a cada recarga da página
RESTART_EXIT_CODE = 3  # os launchers reiniciam o servidor quando recebem este código

_FIELD_SEP = "\x1f"
_state_lock = threading.Lock()
_check_cache: Dict[str, Any] = {"ts": 0.0, "result": None}
_apply_state: Dict[str, Any] = {
    "state": "idle",  # idle | running | done | error
    "step": "",
    "started_at": "",
    "finished_at": "",
    "log": [],
    "needs_restart": False,
    "deps_updated": False,
    "installer_started": False,
}


# ------------------------------------------------------------------------------
# Utilitários de git
# ------------------------------------------------------------------------------

def _git(args: List[str], timeout: float = 30.0, root: Optional[Path] = None) -> subprocess.CompletedProcess:
    kwargs: Dict[str, Any] = {
        "cwd": str(root or PROJECT_ROOT),
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        # Nunca abrir prompts de senha/credencial (repositório público não precisa)
        "env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(["git", *args], **kwargs)


def _git_out(args: List[str], timeout: float = 30.0, root: Optional[Path] = None) -> str:
    res = _git(args, timeout=timeout, root=root)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout or "").strip() or f"git {' '.join(args)} falhou")
    return (res.stdout or "").strip()


def is_git_checkout(root: Optional[Path] = None) -> bool:
    base = root or PROJECT_ROOT
    return shutil.which("git") is not None and (base / ".git").exists()


def is_installer_install(root: Optional[Path] = None) -> bool:
    """Cópia feita pelo BanidoStudio-Setup.exe: sem Git e com arquivo VERSION."""
    return (not is_git_checkout(root)) and bool(release_updater.read_installed_version(root))


def _parse_log_lines(raw: str) -> List[Dict[str, str]]:
    commits = []
    for line in raw.splitlines():
        parts = line.split(_FIELD_SEP)
        if len(parts) < 3:
            continue
        commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return commits


def get_local_version(root: Optional[Path] = None) -> Dict[str, Any]:
    """Versão instalada: commit atual (sha curto, data e mensagem) e branch, ou a versão do instalador."""
    if not is_git_checkout(root):
        version = release_updater.read_installed_version(root)
        if version:
            return {"mode": "installed", "version": version, "sha": "", "date": "", "subject": "", "branch": ""}
        return {"mode": "zip", "sha": "", "date": "", "subject": "", "branch": ""}
    try:
        raw = _git_out(["log", "-1", f"--format=%h{_FIELD_SEP}%cI{_FIELD_SEP}%s"], root=root)
        info = _parse_log_lines(raw)
        head = info[0] if info else {"sha": "", "date": "", "subject": ""}
        branch = _git_out(["rev-parse", "--abbrev-ref", "HEAD"], root=root)
        return {"mode": "git", "branch": branch, **head}
    except Exception as e:
        return {"mode": "git", "sha": "", "date": "", "subject": "", "branch": "", "error": str(e)}


def _fetch_github_latest_commit() -> Optional[Dict[str, str]]:
    """Consulta a API pública do GitHub (usada só para instalações via ZIP)."""
    url = f"https://api.github.com/repos/{UPDATE_REPO}/commits/{UPDATE_BRANCH}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "banido-studio-updater"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    commit = data.get("commit") or {}
    return {
        "sha": str(data.get("sha", ""))[:7],
        "date": str((commit.get("committer") or {}).get("date", "")),
        "subject": str(commit.get("message", "")).splitlines()[0] if commit.get("message") else "",
    }


# ------------------------------------------------------------------------------
# Verificação de novidades
# ------------------------------------------------------------------------------

def check_for_updates(force: bool = False, root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Retorna um resumo amigável:
      update_available, behind (commits novos), ahead (commits locais não publicados),
      dirty (arquivos do projeto alterados localmente), can_apply + motivo, lista de novidades.
    """
    now = time.time()
    use_cache = root is None
    if use_cache and not force:
        with _state_lock:
            cached = _check_cache.get("result")
            if cached and (now - _check_cache.get("ts", 0.0)) < CHECK_CACHE_SEC:
                return cached

    result: Dict[str, Any] = {
        "repo": UPDATE_REPO,
        "repo_url": f"https://github.com/{UPDATE_REPO}",
        "branch": UPDATE_BRANCH,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "local": get_local_version(root),
        "update_available": False,
        "behind": 0,
        "ahead": 0,
        "dirty_files": [],
        "requirements_changed": False,
        "commits": [],
        "can_apply": False,
        "reason": "",
        "error": "",
    }

    if not is_git_checkout(root):
        installed = release_updater.read_installed_version(root)
        if installed:
            # Instalação pelo BanidoStudio-Setup.exe: compara com a última release do GitHub.
            result.update(release_updater.check(UPDATE_REPO, installed))
            _store_cache(result, use_cache)
            return result

        result["reason"] = (
            "Esta cópia foi baixada como ZIP (sem Git). Para atualizar, baixe a versão nova no GitHub "
            "ou instale pelo BanidoStudio-Setup.exe (página de Releases) para ganhar a atualização em 1 clique."
        )
        try:
            latest = _fetch_github_latest_commit()
            result["remote"] = latest
        except Exception as e:
            result["error"] = f"Não foi possível consultar o GitHub: {e}"
        _store_cache(result, use_cache)
        return result

    try:
        fetch = _git(["fetch", "--quiet", UPDATE_REMOTE, UPDATE_BRANCH], timeout=60, root=root)
        if fetch.returncode != 0:
            raise RuntimeError((fetch.stderr or "").strip() or "git fetch falhou")

        remote_ref = f"{UPDATE_REMOTE}/{UPDATE_BRANCH}"
        behind = int(_git_out(["rev-list", "--count", f"HEAD..{remote_ref}"], root=root) or 0)
        ahead = int(_git_out(["rev-list", "--count", f"{remote_ref}..HEAD"], root=root) or 0)
        # Saída "XY caminho": não usar strip() no texto inteiro, senão a 1ª linha perde a coluna X
        status = _git(["status", "--porcelain", "--untracked-files=no"], root=root)
        if status.returncode != 0:
            raise RuntimeError((status.stderr or "").strip() or "git status falhou")
        dirty_files = [ln[3:].strip() for ln in (status.stdout or "").splitlines() if len(ln) > 3]

        commits: List[Dict[str, str]] = []
        changed_files: List[str] = []
        if behind > 0:
            commits = _parse_log_lines(_git_out(
                ["log", "-n", "30", f"--format=%h{_FIELD_SEP}%cI{_FIELD_SEP}%s", f"HEAD..{remote_ref}"],
                root=root
            ))
            changed_files = [f for f in _git_out(["diff", "--name-only", f"HEAD...{remote_ref}"], root=root).splitlines() if f]

        result.update({
            "update_available": behind > 0,
            "behind": behind,
            "ahead": ahead,
            "dirty_files": dirty_files,
            "commits": commits,
            "requirements_changed": "requirements.txt" in changed_files,
        })

        local_branch = result["local"].get("branch", "")
        if behind == 0:
            result["reason"] = "Você já está na versão mais recente."
        elif local_branch != UPDATE_BRANCH:
            result["reason"] = f"A cópia local está na branch '{local_branch}', não em '{UPDATE_BRANCH}'. Atualize pelo terminal."
        elif dirty_files:
            result["reason"] = (
                f"Há {len(dirty_files)} arquivo(s) do projeto modificado(s) localmente. "
                "Para não perder suas alterações, a atualização automática foi bloqueada."
            )
        elif ahead > 0:
            result["reason"] = (
                f"A cópia local tem {ahead} commit(s) que não estão no GitHub. Atualize pelo terminal (git pull)."
            )
        else:
            result["can_apply"] = True
    except Exception as e:
        result["error"] = f"Não foi possível verificar atualizações: {e}"

    _store_cache(result, use_cache)
    return result


def _store_cache(result: Dict[str, Any], use_cache: bool):
    if not use_cache:
        return
    with _state_lock:
        _check_cache["ts"] = time.time()
        _check_cache["result"] = result


def invalidate_cache():
    with _state_lock:
        _check_cache["ts"] = 0.0
        _check_cache["result"] = None


# ------------------------------------------------------------------------------
# Aplicação da atualização (em segundo plano)
# ------------------------------------------------------------------------------

def get_apply_state() -> Dict[str, Any]:
    with _state_lock:
        st = dict(_apply_state)
        st["log"] = list(_apply_state["log"][-200:])
        return st


def _log(msg: str, log_fh=None):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    with _state_lock:
        _apply_state["log"].append(line)
        _apply_state["step"] = msg
    if log_fh:
        try:
            log_fh.write(line + "\n")
            log_fh.flush()
        except Exception:
            pass


def _run_apply(root: Optional[Path] = None, python_exe: Optional[str] = None):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    with open(log_path, "w", encoding="utf-8") as log_fh:
        try:
            info = check_for_updates(force=True, root=root)
            if info.get("error"):
                raise RuntimeError(info["error"])
            if not info.get("can_apply"):
                raise RuntimeError(info.get("reason") or "Atualização indisponível.")

            old_sha = info["local"].get("sha", "")
            _log(f"Baixando {info['behind']} novidade(s) de {UPDATE_REPO} ({UPDATE_BRANCH})...", log_fh)
            merge = _git(["merge", "--ff-only", f"{UPDATE_REMOTE}/{UPDATE_BRANCH}"], timeout=120, root=root)
            if merge.returncode != 0:
                raise RuntimeError(f"git merge --ff-only falhou: {(merge.stderr or merge.stdout).strip()[-400:]}")
            new_sha = get_local_version(root).get("sha", "")
            _log(f"Código atualizado: {old_sha} → {new_sha}", log_fh)

            deps_updated = False
            if info.get("requirements_changed"):
                _log("As dependências mudaram. Instalando com pip (pode levar alguns minutos)...", log_fh)
                pip_cmd = [python_exe or sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
                pip_kwargs: Dict[str, Any] = {
                    "cwd": str(root or PROJECT_ROOT),
                    "stdout": log_fh,
                    "stderr": subprocess.STDOUT,
                    "timeout": 1800,
                }
                if os.name == "nt":
                    pip_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                pip_res = subprocess.run(pip_cmd, **pip_kwargs)
                if pip_res.returncode != 0:
                    raise RuntimeError(
                        f"pip install terminou com código {pip_res.returncode}. "
                        f"O código já foi atualizado; veja o log em {log_path.name}."
                    )
                deps_updated = True
                _log("Dependências instaladas.", log_fh)

            with _state_lock:
                _apply_state.update({
                    "state": "done",
                    "needs_restart": True,
                    "deps_updated": deps_updated,
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "log_file": str(log_path),
                })
            _log("Atualização concluída. Reinicie o Banido Studio para usar a nova versão.", log_fh)
        except Exception as e:
            with _state_lock:
                _apply_state.update({
                    "state": "error",
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "log_file": str(log_path),
                    "error": str(e),
                })
            _log(f"ERRO: {e}", log_fh)
        finally:
            invalidate_cache()


def _run_apply_installer(root: Optional[Path] = None, python_exe: Optional[str] = None):
    """Instalação pelo .exe: baixa o novo instalador, abre em modo silencioso e encerra o servidor."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    with open(log_path, "w", encoding="utf-8") as log_fh:
        try:
            info = check_for_updates(force=True, root=root)
            if info.get("error"):
                raise RuntimeError(info["error"])
            if not info.get("can_apply"):
                raise RuntimeError(info.get("reason") or "Atualização indisponível.")

            _log(f"Baixando a versão {info.get('latest_version', '')} de {UPDATE_REPO}...", log_fh)
            exe = release_updater.download_installer(info["asset_url"], UPDATE_REPO, progress=lambda m: _log(m, log_fh))
            _log("Abrindo o instalador de atualização. Este painel vai fechar e reabrir sozinho quando terminar (alguns minutos).", log_fh)
            release_updater.launch_installer(exe)

            with _state_lock:
                _apply_state.update({
                    "state": "done",
                    "needs_restart": False,
                    "installer_started": True,
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "log_file": str(log_path),
                })
            # O servidor precisa fechar para liberar os arquivos; o instalador reabre o painel ao terminar.
            schedule_restart(delay_sec=2.5, code=0)
        except Exception as e:
            with _state_lock:
                _apply_state.update({
                    "state": "error",
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "log_file": str(log_path),
                    "error": str(e),
                })
            _log(f"ERRO: {e}", log_fh)
        finally:
            invalidate_cache()


def start_apply(root: Optional[Path] = None, python_exe: Optional[str] = None, background: bool = True) -> Dict[str, Any]:
    with _state_lock:
        if _apply_state["state"] == "running":
            return {"started": False, "message": "Uma atualização já está em andamento."}
        _apply_state.update({
            "state": "running",
            "step": "Preparando atualização...",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
            "log": [],
            "needs_restart": False,
            "deps_updated": False,
            "installer_started": False,
            "error": "",
        })
    target = _run_apply_installer if is_installer_install(root) else _run_apply
    if background:
        threading.Thread(target=target, args=(root, python_exe), daemon=True).start()
    else:
        target(root, python_exe)
    return {"started": True, "message": "Atualização iniciada."}


def launched_by_launcher() -> bool:
    """O reinício automático só funciona quando o servidor foi aberto pelos launchers do projeto."""
    return (os.getenv("BANIDO_LAUNCHER") or "").strip() == "1"


def schedule_restart(delay_sec: float = 0.8, code: int = RESTART_EXIT_CODE):
    """Encerra o processo com o código dado; com o código de reinício, o launcher sobe o servidor de novo."""
    def _exit():
        time.sleep(delay_sec)
        os._exit(code)
    threading.Thread(target=_exit, daemon=True).start()
