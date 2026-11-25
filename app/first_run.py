"""
Assistente de primeira configuração.

Na primeira abertura do painel, o usuário informa o essencial (pasta das gravações do OBS, nome do
apresentador e do canal, chave do Gemini) sem editar o arquivo .env à mão. Este módulo valida as
respostas e grava os valores no .env (preservando comentários e as demais linhas).
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.config import PROJECT_ROOT

ENV_PATH = PROJECT_ROOT / ".env"
SETUP_DONE_KEY = "setup_done"
MAX_NAME_LEN = 60


def clean_name(raw: Optional[str], default: str) -> str:
    """Nome curto e seguro para o .env: sem quebras de linha, aspas ou '#'."""
    text = re.sub(r"[\r\n\t\"'#]", " ", raw or "")
    text = re.sub(r"\s+", " ", text).strip()[:MAX_NAME_LEN].strip()
    return text or default


def validate_folder(raw: Optional[str]) -> Tuple[Optional[Path], str]:
    """Aceita só caminho completo. Cria a pasta se não existir. Devolve (pasta, mensagem de erro)."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return None, "Informe a pasta onde o OBS salva as gravações."
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None, "Informe o caminho completo da pasta, por exemplo E:\\Videos\\OBS."
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return None, f"Não consegui acessar ou criar essa pasta: {e}"
    if not path.is_dir():
        return None, "O caminho informado não é uma pasta."
    return path.resolve(), ""


def _format_value(value: str) -> str:
    return '"' + value.replace("\\", "/").replace('"', "") + '"'


def upsert_env(updates: Dict[str, str], path: Optional[Path] = None) -> None:
    """Atualiza (ou acrescenta) chaves no .env sem mexer em comentários e nas outras linhas."""
    env_path = path or ENV_PATH
    lines = []
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    pending = dict(updates)
    out = []
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key in pending:
            out.append(f"{key}={_format_value(pending.pop(key))}")
        else:
            out.append(line)
    for key, value in pending.items():
        out.append(f"{key}={_format_value(value)}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(env_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + "\n")
        os.replace(tmp_name, env_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def pick_folder_windows(title: str = "Escolha a pasta") -> str:
    """Abre a janela de pastas do Windows (no próprio computador do servidor). Vazio se cancelar."""
    if os.name != "nt":
        return ""
    script = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$d.Description = '{title.replace(chr(39), '')}';"
        "$d.ShowNewFolderButton = $true;"
        "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }"
    )
    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
        capture_output=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return (res.stdout or b"").decode("utf-8", errors="replace").strip()
