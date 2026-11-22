#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"
export PYTHONPATH="$APP_DIR"

# Lê apenas BANIDO_HOST e BANIDO_PORT de forma segura (sem executar source no .env)
read_env_var() {
    local key="$1"
    local default_val="$2"
    local val=""
    if [ -f ".env" ]; then
        val="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" .env | tail -n 1 | cut -d '=' -f 2- || true)"
        val="$(printf '%s' "$val" | sed -e 's/[[:space:]]*#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
    fi
    if [ -n "$val" ]; then
        printf '%s' "$val"
    else
        printf '%s' "$default_val"
    fi
}

HOST="${BANIDO_HOST:-$(read_env_var BANIDO_HOST 127.0.0.1)}"
PORT="${BANIDO_PORT:-$(read_env_var BANIDO_PORT 8765)}"

if [ -x "$APP_DIR/.venv/bin/python" ]; then
    PYTHON_EXE="$APP_DIR/.venv/bin/python"
elif [ -x "$APP_DIR/venv/bin/python" ]; then
    PYTHON_EXE="$APP_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_EXE="python3"
else
    echo "[ERRO] Python 3 não encontrado. Crie uma venv e instale requirements.txt."
    exit 1
fi

echo "======================================================="
echo "          INICIANDO BANIDO STUDIO AI"
echo "======================================================="
echo "Diretório: $APP_DIR"
echo "Python:    $PYTHON_EXE"
echo "URL:       http://${HOST}:${PORT}"
echo "======================================================="

exec "$PYTHON_EXE" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
