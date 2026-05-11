# Gerdoo AI Bale Bot

A production-oriented Bale AI chat bot with centralized `.env` configuration, Gemini proxy submodule, MySQL storage, and phpMyAdmin.

## What You Get

- `gemini_server` as a **git submodule** (same upstream source)
- Bale bot with long-polling + keyboard UX
- Per-user model selection
- Photo + caption support for image-capable models
- Per-user chat memory (configurable, default 10)
- MySQL persistence for users/history/request logs
- phpMyAdmin panel for DB inspection
- Single root `.env` for both bot and proxy
- Test coverage for config/parser/storage/client/service flow

## Project Layout

- `gemini_server/` upstream Gemini proxy stack as submodule
- `src/gerdoo_ai_bot/` Bale bot source
- `docker-compose.yml` full stack (mysql + phpmyadmin + proxy + bot)
- `.env` root centralized config for all services

## Database Schema (visible in phpMyAdmin)

The bot initializes these objects automatically:

- `users`
- `chat_history`
- `ai_requests`
- `v_user_stats` (view for quick user/activity stats)

This gives you direct access to user list, selected model per user, conversation history, and AI request logs.

## One-time Setup

```bash
git submodule update --init --recursive
```

## Root Environment

Everything is configured from root `.env`.

- Bot variables
- MySQL/phpMyAdmin variables
- Gemini proxy variables (submodule runtime)

If you need a template:

```bash
cp .env.example .env
```

## Run Full Stack

```bash
docker compose up --build -d
```

Services:

- Gemini proxy: `http://127.0.0.1:8000`
- phpMyAdmin: `http://127.0.0.1:${PHPMYADMIN_PORT_HOST:-28082}`
- MySQL host port: `${MYSQL_PORT_HOST:-23306}`

## phpMyAdmin Login

- Server: `mysql`
- Username: `root`
- Password: value of `MYSQL_ROOT_PASSWORD` in root `.env`

## Bale Bot UX

- `/start` show menu and bot intro
- `🧹 چت جدید` clear chat memory
- `🧠 انتخاب مدل` choose active model
- `📊 وضعیت` show current model, memory depth, and whether current model supports image
- `❓ راهنما` show usage help
- Any text message is sent to Gemini via proxy
- Photo messages are supported and caption text is included in the prompt

## MySQL Runtime Notes

- `DB_URL` must point to the docker service name `mysql` for container runtime
- Default used:
  - `mysql+aiomysql://gerdoo_user:gerdoo_pass@mysql:3306/gerdoo_ai`

## Run Locally (No Docker)

1. Start Gemini proxy on port `18000`:

```bash
./scripts/start_gemini_server.sh
```

2. In a second terminal, run Bale bot:

```bash
./scripts/start.sh
```

Local mode expects:

- `GEMINI_PROXY_BASE_URL=http://127.0.0.1:18000`

## Testing

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src pytest -q
```

## Troubleshooting

- If bot cannot answer: check `docker compose logs bale-ai-bot`
- If proxy errors: check `docker compose logs gemini-proxy`
- If proxy crashes on startup with logging format errors, set:
  - `APP_LOG_FORMAT=%(asctime)s %(levelname)s %(name)s %(message)s`
- If DB connection fails: confirm `mysql` health and `DB_URL` credentials
- If phpMyAdmin login fails: verify `MYSQL_ROOT_PASSWORD`

## Debug Logging

Use these env variables for deeper diagnostics:

- `LOG_LEVEL=DEBUG`
- `LOG_FLOW_ENABLED=true` (bot flow events: incoming/update/model/reply)
- `LOG_HTTP_ENABLED=true` (HTTP request/response logs for Bale and Gemini proxy)
- `LOG_TEXT_PREVIEW_CHARS=160` (max preview size for message text in logs)
- `LOG_HTTP_BODY_PREVIEW_CHARS=500` (max response-body preview for failed HTTP calls)

## Verified Behavior

- Root `.env` drives both bot and proxy containers
- MySQL schema auto-creates on startup
- User list and chat logs are queryable in phpMyAdmin
