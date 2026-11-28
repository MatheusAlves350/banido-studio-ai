"""F3: "Gerar relatório de problema": um .zip pronto para enviar, sem chaves, vídeos ou caminhos completos.

Analogia: o formulário com fotos do painel do carro. Quem recebe vê o que importa
(logs, versões, histórico, queda), e nada que seja pessoal.

Segurança em duas camadas:
1. Todo texto passa por `sanitize_log_text` (chaves, tokens, IPs, nome de usuário, falas,
   nomes de arquivo) e pelos apelidos de pasta (`<BASE_DIR>`, `<OBS_DIR>`...).
2. Antes de liberar, uma varredura final confere o conteúdo pronto: se achar padrão de
   chave, fala transcrita, caminho conhecido ou o valor de uma chave salva, o relatório é
   bloqueado (nada é entregue).
"""
import io
import json
import platform
import re
import sys
import zipfile
from datetime import datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List

from app import config
from app.diagnostics import _safe_version_label, build_diagnostic_report, sanitize_log_text
from app.database import get_job_history, get_setting

MAX_LOG_BYTES_TOTAL = 2 * 1024 * 1024   # "últimas 24 horas, ou os últimos 2 MB"
LOG_WINDOW = timedelta(hours=24)
MAX_LOG_FILES = 15
CRASH_WINDOW = timedelta(days=7)
MAX_CRASH_REPORTS = 5
MAX_CRASH_BYTES = 200 * 1024
HISTORY_LIMIT = 20

PACKAGES = [
    "fastapi", "uvicorn", "pydantic", "faster-whisper", "ctranslate2", "speechbrain", "torch",
    "torchaudio", "google-genai", "openai", "numpy", "scipy", "scikit-learn", "soundfile",
    "pillow", "huggingface-hub",
]
SAFE_SETTING_KEYS = ("ai_provider", "google_model", "nvidia_model")
SECRET_SETTING_KEYS = ("google_api_key", "nvidia_api_key")


class ReportBlocked(Exception):
    """A varredura final achou algo que não pode sair do computador."""


def _known_dirs() -> List[tuple]:
    pares = [
        (config.OBS_RECORDINGS_DIR, "<OBS_DIR>"),
        (config.MODELS_DIR, "<MODELS_DIR>"),
        (config.VOICE_BANK_DIR, "<VOICE_BANK_DIR>"),
        (config.CLIPS_DIR, "<CLIPS_DIR>"),
        (config.TEMP_DIR, "<TEMP_DIR>"),
        (config.BASE_DIR, "<BASE_DIR>"),
        (config.PROJECT_ROOT, "<PROGRAM_DIR>"),
    ]
    variantes = []
    for caminho, apelido in pares:
        p = Path(caminho)
        for texto in {str(p), p.as_posix(), str(p).replace("\\", "\\\\")}:
            if len(texto) > 3:  # evita trocar "C:\" ou "/" sozinhos
                variantes.append((texto, apelido))
    variantes.sort(key=lambda par: len(par[0]), reverse=True)  # o mais específico primeiro
    return variantes


def mask_known_paths(text: str) -> str:
    """Troca as pastas configuradas por apelidos (<BASE_DIR>, <OBS_DIR>...)."""
    for texto, apelido in _known_dirs():
        text = re.sub(re.escape(texto), lambda _m, a=apelido: a, text, flags=re.IGNORECASE)
    return text


def clean(text: str) -> str:
    """Apelidos de pasta + sanitização padrão (sem falas e sem nomes de arquivo de gravação)."""
    return sanitize_log_text(mask_known_paths(text or ""), strip_transcript_lines=True, redact_filenames=True)


# ------------------------------------------------------------------ conteúdo

def _read_tail(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
        data = fh.read().decode("utf-8", errors="replace")
    prefix = "... (início do log omitido pelo limite de tamanho) ...\n" if size > max_bytes else ""
    return prefix + data


def collect_logs(logs_dir: Path, now: datetime) -> Dict[str, str]:
    """Logs das últimas 24 h, do mais novo ao mais antigo, até 2 MB no total."""
    out: Dict[str, str] = {}
    budget = MAX_LOG_BYTES_TOTAL
    arquivos = []
    for p in logs_dir.glob("job_*.log"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if now - mtime <= LOG_WINDOW:
            arquivos.append((mtime, p))
    for _, p in sorted(arquivos, reverse=True)[:MAX_LOG_FILES]:
        if budget <= 0:
            break
        try:
            txt = _read_tail(p, budget)
        except OSError:
            continue
        budget -= len(txt.encode("utf-8", errors="replace"))
        out[f"logs/{p.name}"] = clean(txt)
    return out


def collect_crash_reports(logs_dir: Path, now: datetime) -> Dict[str, str]:
    """Relatórios de queda do F1 (job_<id>_crash_report.txt)."""
    out: Dict[str, str] = {}
    achados = []
    for p in logs_dir.glob("job_*_crash_report.txt"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if now - mtime <= CRASH_WINDOW:
            achados.append((mtime, p))
    for _, p in sorted(achados, reverse=True)[:MAX_CRASH_REPORTS]:
        try:
            out[f"queda/{p.name}"] = clean(_read_tail(p, MAX_CRASH_BYTES))
        except OSError:
            continue
    return out


def _versions_text() -> str:
    linhas = [
        f"Programa: {_safe_version_label()}",
        f"Sistema: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {sys.version.split()[0]}",
        f"GPU: {config.PC_SPECS.get('gpu', 'desconhecida')} | RAM: {config.PC_SPECS.get('ram', '?')} | CPU: {config.PC_SPECS.get('cpu', '?')}",
        "",
        "Bibliotecas principais:",
    ]
    for nome in PACKAGES:
        try:
            linhas.append(f"- {nome}: {metadata.version(nome)}")
        except Exception:
            linhas.append(f"- {nome}: não instalado")
    return "\n".join(linhas)


def _setup_check_text() -> str:
    try:
        from app.setup_check import run_setup_check
        res = run_setup_check()
    except Exception as e:  # o relatório sai mesmo se a checagem falhar
        return f"Não foi possível rodar a verificação: {e}"
    linhas = [f"Pronto para usar: {'sim' if res.get('ready') else 'não'} | erros: {res.get('errors')} | avisos: {res.get('warnings')}", ""]
    for item in res.get("items", []):
        linhas.append(f"[{str(item.get('status', '?')).upper()}] {item.get('title', '')}")
        for k in ("detail", "fix"):
            if item.get(k):
                linhas.append(f"    {item[k]}")
    return "\n".join(linhas)


def _history_json() -> str:
    jobs = []
    for j in get_job_history(limit=HISTORY_LIMIT) or []:
        jobs.append({
            "id": j.get("id"),
            "status": j.get("status"),
            "engine": j.get("engine"),
            "model": j.get("model"),
            "started_at": j.get("started_at"),
            "elapsed_sec": j.get("elapsed_sec"),
            "last_step": clean(str(j.get("last_step") or "")),
            "error_detail": clean(str(j.get("error_detail") or "")),
        })
    return json.dumps(jobs, ensure_ascii=False, indent=2)


def _settings_json() -> str:
    """Só opções que não são segredo. Chaves de API aparecem apenas como 'configurada: sim/não'."""
    cfg = {
        "whisper_modelo": config.WHISPER_MODEL_SIZE,
        "whisper_dispositivo": config.WHISPER_DEVICE,
        "whisper_compute_type": config.WHISPER_COMPUTE_TYPE,
        "whisper_idioma": config.WHISPER_LANGUAGE,
        "faixas_separadas": config.SEPARATE_TRACK_TRANSCRIPTION,
        "pausar_ao_gravar": config.AUTO_PAUSE_ON_RECORDING,
        "servidor_em_rede": config.SERVER_HOST not in ("127.0.0.1", "localhost", "::1"),
        "porta": config.SERVER_PORT,
        "pastas": {
            "BASE_DIR": "<BASE_DIR>", "OBS_DIR": "<OBS_DIR>", "TEMP_DIR": "<TEMP_DIR>",
            "MODELS_DIR": "<MODELS_DIR>", "VOICE_BANK_DIR": "<VOICE_BANK_DIR>", "CLIPS_DIR": "<CLIPS_DIR>",
        },
    }
    for k in SAFE_SETTING_KEYS:
        cfg[k] = clean(get_setting(k, ""))
    for k in SECRET_SETTING_KEYS:
        cfg[f"{k}_configurada"] = bool(get_setting(k, ""))
    return json.dumps(cfg, ensure_ascii=False, indent=2)


def _diagnostic_markdown() -> str:
    recentes = get_job_history(limit=1)
    rep = build_diagnostic_report(job=recentes[0] if recentes else None, strip_transcript_lines=True, redact_filenames=True)
    return clean(rep["report_markdown"])


LEIA_ME = """Relatório de problema do Banido Studio
Gerado em {agora}

O que tem aqui:
- ambiente.txt: versões do programa, do Python e das bibliotecas principais.
- diagnostico.txt: o resultado da verificação do programa e o resumo da última execução.
- historico_jobs.json: as últimas execuções (etapa, duração, resultado e erro).
- configuracoes.json: opções do programa, sem nenhuma chave ou senha.
- logs/: registros das últimas 24 horas (no máximo 2 MB), sem falas transcritas.
- queda/: relatório de queda do processo, quando houve uma.

O que NÃO tem aqui: chaves de API, senhas, vídeos, áudios, transcrições, nomes dos arquivos
de gravação e caminhos completos de pasta (trocados por <BASE_DIR>, <OBS_DIR> e semelhantes).
Antes de gerar este arquivo, uma verificação automática conferiu isso.
"""


def build_report_files(now: datetime = None) -> Dict[str, str]:
    now = now or datetime.now()
    files: Dict[str, str] = {
        "LEIA-ME.txt": LEIA_ME.format(agora=now.strftime("%Y-%m-%d %H:%M:%S")),
        "ambiente.txt": clean(_versions_text()),
        "diagnostico.txt": clean(_setup_check_text()) + "\n\n" + _diagnostic_markdown(),
        "historico_jobs.json": _history_json(),
        "configuracoes.json": _settings_json(),
    }
    files.update(collect_logs(config.LOGS_DIR, now))
    files.update(collect_crash_reports(config.LOGS_DIR, now))
    return files


# ------------------------------------------------------------------ varredura final

def scan_for_leaks(files: Dict[str, str]) -> List[str]:
    """Lista o que ainda parece vazamento (nome do arquivo + motivo; nunca o conteúdo)."""
    segredos = [v for v in (get_setting(k, "") for k in SECRET_SETTING_KEYS) if v and len(v) >= 8]
    achados: List[str] = []
    dirs = [t for t, _ in _known_dirs()]
    for nome, texto in files.items():
        if nome == "LEIA-ME.txt":
            continue
        # <ARQUIVO.mkv> é o apelido que a própria limpeza escreve; sem isso ela o trocaria de novo.
        neutro = re.sub(r"<ARQUIVO\.\w+>", "<ARQ>", texto).rstrip("\n")
        if sanitize_log_text(neutro, strip_transcript_lines=True, redact_filenames=True) != neutro:
            achados.append(f"{nome}: padrão de chave, caminho de usuário, IP, fala ou nome de arquivo")
        baixo = texto.lower()
        if any(d.lower() in baixo for d in dirs):
            achados.append(f"{nome}: caminho completo de pasta")
        if any(s in texto for s in segredos):
            achados.append(f"{nome}: chave de API salva")
    return achados


def build_problem_zip(now: datetime = None) -> tuple:
    """Devolve (bytes do .zip, nome do arquivo). Levanta ReportBlocked se a varredura achar algo."""
    now = now or datetime.now()
    files = build_report_files(now)
    achados = scan_for_leaks(files)
    if achados:
        raise ReportBlocked(
            "O relatório foi bloqueado por segurança porque a verificação achou conteúdo que não deve sair: "
            + "; ".join(achados)
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for nome, texto in files.items():
            zf.writestr(nome, texto)
    return buf.getvalue(), f"banido-relatorio-{now.strftime('%Y%m%d-%H%M%S')}.zip"


def describe_contents() -> Dict[str, Any]:
    """Lista curta do que entra no relatório (mostrada na tela antes de baixar)."""
    return {
        "inclui": [
            "Logs das últimas 24 horas (até 2 MB), sem falas transcritas",
            "Resultado da verificação do programa e versões",
            "Histórico das últimas execuções e relatório de queda, se houver",
            "Opções do programa, sem chaves nem senhas",
        ],
        "nao_inclui": "Nenhum vídeo, áudio, transcrição, chave de API ou caminho completo de pasta é incluído.",
    }
