"""
Gerador de relatórios de diagnóstico / bug report com sanitização de privacidade.

Remove automaticamente chaves de API, tokens, senhas, nomes de usuário em caminhos
locais, endereços IP e (por padrão) falas transcritas da call antes de exibir ao
usuário para revisão, download (.txt) ou abertura de Issue pré-preenchida no GitHub.
"""
import platform
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from app.config import (
    PC_SPECS,
    SEPARATE_TRACK_TRANSCRIPTION,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL_SIZE,
)
from app import updater

_SECRET_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z\-_]{20,}"), "<GOOGLE_API_KEY>"),
    (re.compile(r"nvapi-[0-9A-Za-z\-_]{10,}"), "<NVIDIA_API_KEY>"),
    (re.compile(r"(?:ghp|gho|ghu|ghs|ghr|github_pat)_[0-9A-Za-z_]{10,}"), "<GITHUB_TOKEN>"),
    (re.compile(r"hf_[0-9A-Za-z]{10,}"), "<HF_TOKEN>"),
    (re.compile(r"sk-[0-9A-Za-z\-_]{16,}"), "<API_KEY>"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{12,}", re.IGNORECASE), r"\1<TOKEN>"),
    (re.compile(r"https://(?:(?:canary|ptb)\.)?(?:discord|discordapp)\.com/api/webhooks/\d+/[\w-]+", re.IGNORECASE), "<DISCORD_WEBHOOK>"),
    (
        re.compile(
            r"((?:GOOGLE_API_KEY|NVIDIA_API_KEY|BANIDO_API_TOKEN|BANIDO_ACCESS_PASSWORD|X-Banido-Token)\s*[:=]\s*['\"]?)([^\s'\",;}{]+)",
            re.IGNORECASE,
        ),
        r"\1<REDACTED>",
    ),
]

_WIN_USER_PATH_RE = re.compile(r"([A-Za-z]:\\Users\\)([^\\/\r\n\"'<>|]+)", re.IGNORECASE)
_POSIX_USER_PATH_RE = re.compile(r"(/(?:home|Users)/)([^/\r\n\"'<>|\s]+)")
_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_TRANSCRIPT_LINE_RE = re.compile(r"^(?:\[\d{2}:\d{2}:\d{2}\]\s*)?\[FALA #\d+\].*$")
_MEDIA_FILENAME_RE = re.compile(r"[^\\/\r\n\"'<>|:*?]+\.(mkv|mp4|srt|wav)\b", re.IGNORECASE)


def _redact_ip(match: re.Match) -> str:
    ip = match.group(1)
    if ip in ("127.0.0.1", "0.0.0.0"):
        return ip
    return "<IP>"


def sanitize_log_text(
    text: str,
    *,
    strip_transcript_lines: bool = True,
    redact_filenames: bool = False,
) -> str:
    """
    Limpa informações sensíveis de um log antes de compartilhamento externo.
    - Mascara chaves de API, tokens, senhas e IPs não-locais.
    - Oculta o nome de usuário em caminhos locais (C:\\Users\\... / /home/...).
    - Por padrão, substitui linhas '[FALA #...]' por um resumo numérico para não expor conversas.
    - Opcionalmente oculta nomes de arquivos .mkv/.mp4/.srt/.wav.
    """
    if not text:
        return ""

    out_lines = []
    omitted_speech = 0

    for raw_line in text.splitlines():
        if strip_transcript_lines and _TRANSCRIPT_LINE_RE.match(raw_line.strip()):
            omitted_speech += 1
            continue
        if omitted_speech > 0:
            out_lines.append(f"[... {omitted_speech} fala(s) transcrita(s) omitida(s) por privacidade ...]")
            omitted_speech = 0

        line = raw_line
        for pat, repl in _SECRET_PATTERNS:
            line = pat.sub(repl, line)
        line = _WIN_USER_PATH_RE.sub(r"\1<USER>", line)
        line = _POSIX_USER_PATH_RE.sub(r"\1<USER>", line)
        line = _IPV4_RE.sub(_redact_ip, line)
        if redact_filenames:
            line = _MEDIA_FILENAME_RE.sub(lambda m: f"<ARQUIVO.{m.group(1).lower()}>", line)
        out_lines.append(line)

    if omitted_speech > 0:
        out_lines.append(f"[... {omitted_speech} fala(s) transcrita(s) omitida(s) por privacidade ...]")

    return "\n".join(out_lines)


def _read_tail_file(path_str: str, max_lines: int) -> str:
    if not path_str:
        return ""
    p = Path(path_str)
    if not p.is_file():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if max_lines > 0 and len(lines) > max_lines:
            omitted = len(lines) - max_lines
            return f"... ({omitted} linhas anteriores omitidas) ...\n" + "\n".join(lines[-max_lines:])
        return "\n".join(lines)
    except Exception as e:
        return f"[Não foi possível ler o arquivo de log: {e}]"


def _safe_version_label() -> str:
    local = updater.get_local_version()
    if local.get("mode") == "git" and local.get("sha"):
        return f"{local['sha']} ({local.get('branch', 'main')})"
    return "Instalador Windows / ZIP (sem Git)"


def build_diagnostic_report(
    job: Optional[Dict[str, Any]] = None,
    *,
    strip_transcript_lines: bool = True,
    redact_filenames: bool = False,
    max_log_lines: int = 220,
) -> Dict[str, Any]:
    """
    Monta um relatório de diagnóstico pronto para revisão pelo usuário, download .txt
    ou abertura de Issue pré-preenchida no GitHub.
    """
    has_ffmpeg = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    has_mkvpropedit = bool(shutil.which("mkvpropedit"))

    env_lines = [
        f"- **Data/Hora:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Versão:** {_safe_version_label()}",
        f"- **Sistema:** {platform.system()} {platform.release()} ({platform.machine()})",
        f"- **Python:** {sys.version.split()[0]}",
        f"- **GPU:** {PC_SPECS.get('gpu', 'Desconhecida')}",
        f"- **FFmpeg:** {'OK' if has_ffmpeg else 'Não encontrado'} | **mkvpropedit:** {'OK' if has_mkvpropedit else 'Ausente'}",
        (
            f"- **Whisper:** `{WHISPER_MODEL_SIZE}` "
            f"(device=`{WHISPER_DEVICE}`, compute=`{WHISPER_COMPUTE_TYPE}`, "
            f"faixas_separadas=`{'1' if SEPARATE_TRACK_TRANSCRIPTION else '0'}`)"
        ),
    ]

    job_section = []
    log_sanitized = ""
    issue_title = "Relatório de bug / diagnóstico"

    if job:
        status = str(job.get("status") or "unknown").upper()
        engine = job.get("engine") or "-"
        model = job.get("model") or "-"
        elapsed = job.get("elapsed_sec")
        elapsed_txt = f"{float(elapsed):.1f}s" if elapsed is not None else "-"
        fname = "<ARQUIVO.mkv>" if redact_filenames else str(job.get("filename") or "-")
        err_detail = sanitize_log_text(
            str(job.get("error_detail") or ""),
            strip_transcript_lines=strip_transcript_lines,
            redact_filenames=redact_filenames,
        )
        last_step = sanitize_log_text(
            str(job.get("last_step") or ""),
            strip_transcript_lines=strip_transcript_lines,
            redact_filenames=redact_filenames,
        )
        job_section = [
            "### Execução selecionada",
            f"- **Job:** #{job.get('id', '?')} | **Status:** `{status}` | **Duração:** {elapsed_txt}",
            f"- **Arquivo:** `{fname}`",
            f"- **Engine / Modelo:** `{engine}` / `{model}`",
            f"- **Última etapa:** {last_step or '-'}",
        ]
        if err_detail:
            job_section.append(f"- **Erro registrado:** `{err_detail}`")
            issue_title = f"Erro ({engine}): {err_detail[:65]}"
        else:
            issue_title = f"Diagnóstico Job #{job.get('id', '?')} ({status})"

        raw_log = _read_tail_file(str(job.get("log_file") or ""), max_lines=max_log_lines)
        log_sanitized = sanitize_log_text(
            raw_log,
            strip_transcript_lines=strip_transcript_lines,
            redact_filenames=redact_filenames,
        )

    parts = [
        "### O que aconteceu?",
        "<!-- Descreva brevemente o problema ou o que você esperava que acontecesse -->",
        "",
        "### Ambiente",
        *env_lines,
    ]
    if job_section:
        parts.extend(["", *job_section])
    if log_sanitized:
        parts.extend(["", "### Log sanitizado", "```text", log_sanitized, "```"])

    report_md = "\n".join(parts)

    # Limita o body na URL do GitHub para evitar estouro de tamanho de URL (~5500 chars)
    url_body = report_md
    if len(url_body) > 5200:
        url_body = url_body[:5000] + "\n```\n\n*(Log truncado por limite de URL — se necessário, anexe o arquivo .txt baixado pelo painel.)*"

    qs = urlencode({"title": issue_title, "labels": "bug", "body": url_body})
    github_issue_url = f"https://github.com/{updater.UPDATE_REPO}/issues/new?{qs}"

    return {
        "issue_title": issue_title,
        "report_markdown": report_md,
        "github_issue_url": github_issue_url,
        "has_job": job is not None,
        "job_id": job.get("id") if job else None,
        "strip_transcripts": strip_transcript_lines,
        "redact_filenames": redact_filenames,
        "privacy_notice": (
            "Chaves de API, tokens, senhas, IPs e seu nome de usuário nos caminhos foram "
            "removidos automaticamente. Mesmo assim, revise o texto antes de enviar e apague "
            "qualquer nome de arquivo, apelido ou trecho pessoal que não queira compartilhar."
        ),
    }
