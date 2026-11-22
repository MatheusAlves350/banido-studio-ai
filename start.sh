#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"
export PYTHONPATH="$APP_DIR"

if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source ".env"
    set +a
fi

HOST="${BANIDO_HOST:-127.0.0.1}"
PORT="${BANIDO_PORT:-8765}"

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
