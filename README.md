# Gerdoo AI Bale Bot (UAG Edition)

A production-oriented Bale AI chat bot with centralized root `.env`, MySQL storage, phpMyAdmin, and **Ajil Unified AI Gateway (UAG)** as upstream AI gateway.

## What You Get

- `uag_server/` as a git submodule (source: `../Ajil_Unified_AI_Gateway`)
- UAG provider stack (Gemini/Groq/Pollinations) with router + fallback
- Bale bot with long-polling + keyboard UX
- Per-user model selection
- Photo + caption support (for image-capable models)
- Per-user chat memory (configurable, default 10)
- MySQL persistence for users/history/request logs
- phpMyAdmin panel for DB inspection
- Single root `.env` for bot + gateway + provider modules
- Env sync into submodule env files (`scripts/sync_uag_env.sh`)
- Test coverage for config/parser/storage/client/service flow

## Project Layout

- `uag_server/` Unified AI Gateway stack as submodule
- `src/gerdoo_ai_bot/` Bale bot source
- `docker-compose.yml` full stack (`mysql + phpmyadmin + uag-redis + uag-gateway + bot`)
- `.env` root centralized config for all services

## Database Schema (visible in phpMyAdmin)

The bot initializes these objects automatically:

- `users`
- `chat_history`
- `ai_requests`
- `v_user_stats` (view for quick user/activity stats)

## One-time Setup

```bash
git submodule update --init --recursive
```

## Root Environment

Everything is configured from root `.env`.

If needed:

```bash
cp .env.example .env
```

## Run Full Stack (Docker)

```bash
docker compose up --build -d
```

Services:

- UAG gateway: `http://127.0.0.1:${UAG_PORT_HOST:-18080}`
- phpMyAdmin: `http://127.0.0.1:${PHPMYADMIN_PORT_HOST:-28082}`
- MySQL host port: `${MYSQL_PORT_HOST:-23306}`

## phpMyAdmin Login

- Server: `mysql`
- Username: `root`
- Password: value of `MYSQL_ROOT_PASSWORD` in root `.env`

## Local Run (No Docker)

1. Start UAG on port `18000`:

```bash
./scripts/start_uag_server.sh
```

2. In a second terminal, run Bale bot:

```bash
./scripts/start.sh
```

Local mode expects:

- `UAG_BASE_URL=http://127.0.0.1:18000`
- `UAG_CHAT_ENDPOINT=/v1/chat/completions`

## UAG Env Sync (Root `.env` -> Submodule `.env` files)

To sync provider and gateway env files inside `uag_server` from root env:

```bash
./scripts/sync_uag_env.sh
```

This updates:

- `uag_server/.env`
- `uag_server/modules/gemini_proxy/.env`
- `uag_server/modules/groq_proxy/.env`
- `uag_server/modules/pollinations_proxy/.env`

## Bale Bot UX

- `/start` show menu and bot intro
- `🧹 چت جدید` clear chat memory
- `🧠 انتخاب مدل` choose active model
- `📊 وضعیت` show current model, memory depth, and image support
- `❓ راهنما` show usage help
- Any text message is sent to UAG chat completions endpoint
- Photo messages are supported and caption text is included in the prompt

## Testing

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src pytest -q
```

## Troubleshooting

- If bot cannot answer: `docker compose logs bale-ai-bot`
- If gateway errors: `docker compose logs uag-gateway`
- If gateway module env mismatch: run `./scripts/sync_uag_env.sh`
- If DB connection fails: confirm `mysql` health and `DB_URL` credentials
- If phpMyAdmin login fails: verify `MYSQL_ROOT_PASSWORD`

## Debug Logging

Use these env variables for deeper diagnostics:

- `LOG_LEVEL=DEBUG`
- `LOG_FLOW_ENABLED=true` (bot flow events)
- `LOG_HTTP_ENABLED=true` (HTTP request/response logs for Bale and UAG)
- `LOG_TEXT_PREVIEW_CHARS=160`
- `LOG_HTTP_BODY_PREVIEW_CHARS=500`

## Verified Behavior (target)

- Root `.env` drives bot and UAG runtime
- Root env can sync to all provider submodule `.env` files
- MySQL schema auto-creates on startup
- User list and chat logs are queryable in phpMyAdmin
