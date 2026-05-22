#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UAG_DIR="$ROOT_DIR/uag_server"
PORT="${UAG_SERVER_PORT:-18000}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

if [[ ! -d "$UAG_DIR" ]]; then
  echo "[start_uag_server] uag_server directory not found. Initialize submodules first."
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[start_uag_server] env file not found: $ENV_FILE"
  echo "[start_uag_server] copy .env.example to .env first"
  exit 1
fi

# Ensure nested provider submodules are present.
if [[ ! -f "$UAG_DIR/modules/gemini_proxy/requirements.txt" || ! -f "$UAG_DIR/modules/groq_proxy/requirements.txt" || ! -f "$UAG_DIR/modules/pollinations_proxy/requirements.txt" ]]; then
  git -C "$ROOT_DIR" submodule update --init --recursive uag_server
  git -C "$UAG_DIR" submodule update --init --recursive || true
  if [[ -d "$UAG_DIR/modules/pollinations_proxy/.git" ]]; then
    git -C "$UAG_DIR/modules/pollinations_proxy" checkout -f main || true
  fi
fi

"$ROOT_DIR/scripts/sync_uag_env.sh"

cd "$ROOT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r "$UAG_DIR/unified_gateway/requirements.txt"
pip install -q -r "$UAG_DIR/modules/gemini_proxy/requirements.txt"
pip install -q -r "$UAG_DIR/modules/groq_proxy/requirements.txt"
pip install -q -r "$UAG_DIR/modules/pollinations_proxy/requirements.txt"

export UAG_ENV_FILE="$ENV_FILE"
export UAG_APP_PORT="$PORT"
export PYTHONPATH="$ROOT_DIR:$UAG_DIR"

exec python -m uvicorn unified_gateway.app.main:app --host "${UAG_APP_HOST:-0.0.0.0}" --port "$PORT"
