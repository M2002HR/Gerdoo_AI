#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[admin] env file not found: $ENV_FILE"
  exit 1
fi

cd "$ROOT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --quiet -r requirements.txt

export ENV_FILE
export PYTHONPATH="$ROOT_DIR/src"

HOST="${BOT_ADMIN_PANEL_HOST:-0.0.0.0}"
PORT="${BOT_ADMIN_PANEL_PORT:-8090}"

python -m uvicorn gerdoo_ai_bot.admin_panel:app --host "$HOST" --port "$PORT"
