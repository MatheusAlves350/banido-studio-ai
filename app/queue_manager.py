"""
Fila de vários vídeos e processamento noturno (item E3 do roadmap).

Regras:
- Um vídeo por vez (a placa de vídeo de 8 GB não aguenta dois).
- Se um vídeo falha, a fila segue para o próximo e a falha fica registrada com o motivo em linguagem simples.
- A fila fica no banco. Se o programa fechar, ao abrir de novo os itens que estavam rodando voltam para
  "aguardando" e retomam dos checkpoints que o worker já salva.
- Horário de início opcional (o relógio do PC é a referência).
- Ao final, o usuário pode pedir para suspender ou desligar o PC. Há um aviso de 60 segundos com botão de
  cancelar. Desligar e suspender exigem confirmação explícita na primeira vez e começam desligados.

O "relógio" do agendador é a função tick(), chamada a cada poucos segundos por uma thread (start_scheduler).
Tudo que toca o Windows ou o worker entra por parâmetro, para os testes rodarem sem nada disso.
"""
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

from app import notifications
from app.database import (
    get_connection, get_job_by_id, get_setting, get_video_by_id, now_str, save_setting,
)
from app.friendly_errors import friendly_text

WAITING, RUNNING, DONE, FAILED = "waiting", "running", "done", "failed"
POWER_ACTIONS = ("none", "sleep", "shutdown")
POWER_GRACE_SEC = 60
DEFAULT_START_TIME = "23:00"
TICK_SEC = 5

# Chaves na tabela settings (o painel de Configurações não as expõe).
K_START_AT = "queue_start_at"          # "2026-11-27 23:00" (horário local) ou vazio
K_AFTER = "queue_after"                # none | sleep | shutdown
K_POWER_OK = "queue_power_confirmed"   # "1" depois da primeira confirmação
K_ACTIVE = "queue_batch_active"        # "1" enquanto há uma rodada em andamento
K_POWER_DEADLINE = "queue_power_deadline"  # timestamp (segundos) em que a ação será feita
K_POWER_ACTION = "queue_power_action"

_lock = threading.Lock()


def ensure_table() -> None:
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS process_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting',
            engine TEXT DEFAULT 'gemini',
            model TEXT DEFAULT '',
            normalize_audio INTEGER DEFAULT 0,
            separate_tracks INTEGER,
            job_id INTEGER,
            error TEXT DEFAULT '',
            added_at TEXT,
            finished_at TEXT
        )
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- leitura

def list_items() -> List[Dict[str, Any]]:
    ensure_table()
    conn = get_connection()
    rows = conn.execute("""
        SELECT q.*, v.filename AS filename
        FROM process_queue q LEFT JOIN videos v ON v.id = q.video_id
        ORDER BY q.position, q.id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def settings_snapshot() -> Dict[str, Any]:
    return {
        "start_at": get_setting(K_START_AT, ""),
        "after": get_setting(K_AFTER, "none") if get_setting(K_AFTER, "none") in POWER_ACTIONS else "none",
        "power_confirmed": get_setting(K_POWER_OK, "") == "1",
        "default_start_time": DEFAULT_START_TIME,
    }


def pending_power(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    action = get_setting(K_POWER_ACTION, "")
    deadline = get_setting(K_POWER_DEADLINE, "")
    if not action or not deadline:
        return None
    now = time.time() if now is None else now
    return {"action": action, "seconds_left": max(0, int(float(deadline) - now))}


def snapshot() -> Dict[str, Any]:
    items = list_items()
    for it in items:
        it["error_friendly"] = friendly_text(it["error"]) if it.get("error") else ""
    return {
        "items": items,
        "settings": settings_snapshot(),
        "pending_power": pending_power(),
        "active": get_setting(K_ACTIVE, "") == "1",
    }


# ---------------------------------------------------------------- escrita

def next_start_datetime(hhmm: str, now: Optional[datetime] = None) -> datetime:
    """Próxima vez que o relógio marca hhmm: hoje se ainda não passou, senão amanhã."""
    try:
        hour, minute = (int(x) for x in hhmm.strip().split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Horário inválido. Use o formato 23:00.")
    now = now or datetime.now()
    alvo = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if alvo <= now:
        alvo += timedelta(days=1)
    return alvo


def add_to_queue(
    video_ids: List[int], engine: str = "gemini", model: str = "", normalize_audio: bool = False,
    separate_tracks: Optional[bool] = None, start_time: Optional[str] = None, after: str = "none",
    confirm_power: bool = False, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    ensure_table()
    if not video_ids:
        raise HTTPException(status_code=400, detail="Marque pelo menos uma gravação.")
    if after not in POWER_ACTIONS:
        raise HTTPException(status_code=400, detail="Escolha nada, suspender ou desligar.")
    if after != "none" and not confirm_power and get_setting(K_POWER_OK, "") != "1":
        raise HTTPException(
            status_code=400,
            detail="Para suspender ou desligar o PC no fim da fila, confirme que entendeu o aviso."
        )
    start_at = ""
    if start_time:
        start_at = next_start_datetime(start_time, now).strftime("%Y-%m-%d %H:%M")

    with _lock:
        conn = get_connection()
        ja_na_fila = {r["video_id"] for r in conn.execute(
            "SELECT video_id FROM process_queue WHERE status IN (?, ?)", (WAITING, RUNNING)).fetchall()}
        pos = (conn.execute("SELECT COALESCE(MAX(position), 0) FROM process_queue").fetchone()[0] or 0)
        added, skipped = [], []
        for vid in video_ids:
            video = get_video_by_id(vid, include_data=False)
            if not video:
                skipped.append({"video_id": vid, "reason": "gravação não encontrada"})
            elif vid in ja_na_fila:
                skipped.append({"video_id": vid, "reason": "já está na fila"})
            elif video.get("status") == "processing":
                skipped.append({"video_id": vid, "reason": "já está sendo processada"})
            else:
                pos += 1
                conn.execute(
                    "INSERT INTO process_queue (video_id, position, status, engine, model, normalize_audio,"
                    " separate_tracks, added_at) VALUES (?, ?, 'waiting', ?, ?, ?, ?, ?)",
                    (vid, pos, engine or "gemini", model or "", 1 if normalize_audio else 0,
                     None if separate_tracks is None else (1 if separate_tracks else 0), now_str()),
                )
                ja_na_fila.add(vid)
                added.append(vid)
        conn.commit()
        conn.close()

    if added:
        save_setting(K_START_AT, start_at)
        save_setting(K_AFTER, after)
        if after != "none":
            save_setting(K_POWER_OK, "1")
        save_setting(K_ACTIVE, "1")
    return {"success": True, "added": added, "skipped": skipped, "start_at": start_at if added else ""}


def remove_item(item_id: int) -> bool:
    ensure_table()
    with _lock:
        conn = get_connection()
        cur = conn.execute("DELETE FROM process_queue WHERE id = ? AND status = ?", (item_id, WAITING))
        conn.commit()
        conn.close()
    return cur.rowcount > 0


def clear_finished() -> int:
    ensure_table()
    with _lock:
        conn = get_connection()
        cur = conn.execute("DELETE FROM process_queue WHERE status IN (?, ?)", (DONE, FAILED))
        conn.commit()
        conn.close()
    return cur.rowcount


def cancel_waiting() -> int:
    """Esvazia o que ainda não começou e desliga o início agendado e a ação final."""
    ensure_table()
    with _lock:
        conn = get_connection()
        cur = conn.execute("DELETE FROM process_queue WHERE status = ?", (WAITING,))
        conn.commit()
        conn.close()
    save_setting(K_START_AT, "")
    return cur.rowcount


def start_now() -> None:
    save_setting(K_START_AT, "")


def cancel_power() -> bool:
    had = pending_power() is not None
    save_setting(K_POWER_ACTION, "")
    save_setting(K_POWER_DEADLINE, "")
    save_setting(K_AFTER, "none")
    return had


def recover_on_start() -> int:
    """Ao abrir o programa: o que estava rodando volta para a fila e retoma dos checkpoints."""
    ensure_table()
    conn = get_connection()
    cur = conn.execute("UPDATE process_queue SET status = ?, job_id = NULL WHERE status = ?", (WAITING, RUNNING))
    conn.commit()
    conn.close()
    return cur.rowcount


# ---------------------------------------------------------------- ações do Windows

def run_power_action(action: str) -> bool:
    """Suspende ou desliga o PC pelo comando padrão do Windows. Fora do Windows não faz nada."""
    if os.name != "nt":
        return False
    try:
        if action == "shutdown":
            cmd = ["shutdown", "/s", "/t", "0"]
        elif action == "sleep":
            cmd = ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
        else:
            return False
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- agendador

def _finish_running_items(active_video_ids) -> None:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM process_queue WHERE status = ?", (RUNNING,)).fetchall()
    for r in rows:
        if r["video_id"] in active_video_ids:
            continue
        job = get_job_by_id(r["job_id"]) if r["job_id"] else None
        ok = bool(job and job.get("status") == "completed")
        erro = ""
        if not ok:
            erro = (job or {}).get("error_detail") or (job or {}).get("last_step") or "O processamento terminou sem concluir."
        conn.execute(
            "UPDATE process_queue SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (DONE if ok else FAILED, erro, now_str(), r["id"]),
        )
    conn.commit()
    conn.close()


def tick(
    now: Optional[datetime] = None,
    launcher: Optional[Callable[..., Any]] = None,
    power: Callable[[str], bool] = run_power_action,
    notifier: Callable[[str, str], Any] = notifications.notify,
    active_video_ids: Optional[Callable[[], Any]] = None,
) -> str:
    """Um passo do agendador. Devolve o que fez (útil nos testes): idle, waiting_time, busy, started, finished, power."""
    from app import worker_control
    now = now or datetime.now()
    ensure_table()
    launcher = launcher or worker_control.launch_worker_subprocess
    worker_control.reconcile_worker_processes()
    ativos = set(active_video_ids() if active_video_ids else worker_control.ACTIVE_PROCESSES.keys())

    with _lock:
        _finish_running_items(ativos)

        # Ação final já marcada: espera o prazo e confere se continua tudo parado.
        acao = get_setting(K_POWER_ACTION, "")
        if acao:
            if ativos or any(i["status"] in (WAITING, RUNNING) for i in list_items()):
                save_setting(K_POWER_ACTION, "")
                save_setting(K_POWER_DEADLINE, "")
                return "busy"
            prazo = get_setting(K_POWER_DEADLINE, "")
            if prazo and time.time() >= float(prazo):
                save_setting(K_POWER_ACTION, "")
                save_setting(K_POWER_DEADLINE, "")
                save_setting(K_AFTER, "none")
                power(acao)
                return "power"
            return "waiting_power"

        if ativos:
            return "busy"

        itens = list_items()
        aguardando = [i for i in itens if i["status"] == WAITING]
        if aguardando:
            start_at = get_setting(K_START_AT, "")
            if start_at:
                try:
                    if now < datetime.strptime(start_at, "%Y-%m-%d %H:%M"):
                        return "waiting_time"
                except ValueError:
                    pass
                save_setting(K_START_AT, "")
            item = aguardando[0]
            video = get_video_by_id(item["video_id"], include_data=False) or {}
            try:
                resp = launcher(
                    video_id=item["video_id"], mode="full",
                    manual_game=video.get("game_name") or "", engine=item["engine"] or "gemini",
                    custom_model=item["model"] or "", extra_context=video.get("session_context") or "",
                    normalize_audio=bool(item["normalize_audio"]),
                    separate_tracks=None if item["separate_tracks"] is None else bool(item["separate_tracks"]),
                    notify_end=False,
                )
            except HTTPException as e:
                if e.status_code == 409:
                    return "busy"
                resp = {"error": str(e.detail)}
            except Exception as e:
                resp = {"error": str(e)}
            conn = get_connection()
            if resp.get("job_id") and not resp.get("already_running"):
                conn.execute("UPDATE process_queue SET status = ?, job_id = ? WHERE id = ?",
                             (RUNNING, resp["job_id"], item["id"]))
            else:
                conn.execute("UPDATE process_queue SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                             (FAILED, resp.get("error") or resp.get("message") or "Não foi possível iniciar.", now_str(), item["id"]))
            conn.commit()
            conn.close()
            return "started"

        # Fila vazia: se havia uma rodada em andamento, ela acabou.
        if get_setting(K_ACTIVE, "") == "1":
            save_setting(K_ACTIVE, "")
            feitos = sum(1 for i in itens if i["status"] == DONE)
            falhas = sum(1 for i in itens if i["status"] == FAILED)
            depois = get_setting(K_AFTER, "none")
            texto = f"{feitos} vídeo(s) concluído(s) e {falhas} com falha."
            if depois in ("sleep", "shutdown"):
                nome = "suspender" if depois == "sleep" else "desligar"
                texto += f" O PC vai {nome} em 1 minuto. Para cancelar, abra o Banido Studio e use o botão Cancelar."
                save_setting(K_POWER_ACTION, depois)
                save_setting(K_POWER_DEADLINE, str(time.time() + POWER_GRACE_SEC))
            notifier("Banido Studio: fila concluída", texto)
            return "finished"
        return "idle"


_scheduler_started = False


def start_scheduler() -> None:
    """Liga a thread que chama tick() a cada poucos segundos. Chamada uma vez no início do servidor."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    recover_on_start()

    def _loop():
        while True:
            try:
                tick()
            except Exception as e:  # o agendador nunca pode morrer por um erro isolado
                print(f"[FILA] Erro no agendador: {type(e).__name__}", flush=True)
            time.sleep(TICK_SEC)

    threading.Thread(target=_loop, name="banido-fila", daemon=True).start()
