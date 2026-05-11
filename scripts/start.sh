#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[start] env file not found: $ENV_FILE"
  echo "[start] copy .env.example to .env first"
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

python -m gerdoo_ai_bot.main
