# Gerdoo AI Bale Bot (UAG Edition)

A production-oriented Bale AI chat bot with centralized root `.env`, MySQL storage, phpMyAdmin, and **Ajil Unified AI Gateway (UAG)** as upstream AI gateway.

## What You Get

- `uag_server/` as a git submodule (source: `../Ajil_Unified_AI_Gateway`)
- UAG provider stack (Gemini/Groq/Pollinations) with router + fallback
- Bale bot with long-polling + keyboard UX
- No user-side model selection (all models from env)
- Photo + voice analysis in normal chat
- Dedicated routes for image generation and voice-to-text
- Image prompts are auto-converted/enhanced to English before generation
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
- `bot_events`
- `v_user_stats` (view for quick user/activity stats)
- `v_bot_event_daily_stats` (daily ops/error/latency aggregates)

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
- Bot admin panel: `http://127.0.0.1:${BOT_ADMIN_PANEL_PORT_HOST:-18083}/admin`
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

3. Optional: run admin panel:

```bash
./scripts/start_admin_panel.sh
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
- `💬 چت هوشمند` return to normal chat flow
- `🖼️ تولید تصویر` open image generation flow (send prompt next)
- `🎙️ تبدیل ویس به متن` open STT flow (send voice next)
- `💬 گفتگوی جدید` clear chat memory
- `❌ لغو عملیات` exit flow mode (image/STT)
- `❓ راهنما` show usage help
- In normal chat:
  - text -> AI chat
  - photo -> image description
  - voice -> transcription + audio description
- Voice duration limit is configurable via `MEDIA_MAX_VOICE_SEC` (default example: 600 = 10 minutes)
- Routing and model chains are configured from root `.env` per capability
- Per capability you can tune independently:
  - `*_MODELS`
  - `*_PROVIDERS`
  - `*_ROUTER_*` (strategy/mode/providers/timeout/attempts)
- STT provider/model are controlled by:
  - `AI_TRANSCRIPTION_PROVIDER`
  - `AI_TRANSCRIPTION_MODELS`
  - plus UAG Groq retry/rotation envs (`UAG_GROQ_*`) for load-safety

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
- If voice-to-text fails with auth error:
  - set `UAG_GROQ_API_KEYS` in root `.env`
  - rebuild/restart bot + gateway
- If analytics report cannot access UAG admin endpoints:
  - ensure `UAG_ADMIN_ENABLED=true`
  - set non-empty `UAG_ADMIN_TOKEN`
  - same token is used automatically by `scripts/analytics_report.py`
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

Additionally, bot runtime now persists structured debug events in DB table `bot_events`
and daily aggregates in view `v_bot_event_daily_stats`.

## Analytics Snapshot

Generate a combined bot/UAG analytics JSON report:

```bash
PYTHONPATH=src ./scripts/analytics_report.py --hours 24
```

Output includes:
- active users
- bot event success/failure and latency stats
- AI request counts by model
- UAG health/router/usage/log summaries (when admin endpoints are reachable)

## Admin Panel (New)

The project now includes a dedicated admin dashboard (gray theme, UAG-style layout) for:

- interactive KPI cards and charts
- daily usage trends (users/requests/errors/images/voice)
- per-model and per-capability usage
- top users and behavioral activity
- filtered recent log events
- UAG operational snapshot in the same panel

Panel auth is token-based and reads from root `.env`:

- `BOT_ADMIN_PANEL_ENABLED`
- `BOT_ADMIN_PANEL_USERNAME`
- `BOT_ADMIN_PANEL_PASSWORD`
- `BOT_ADMIN_PANEL_TOKEN` (fallback to `UAG_ADMIN_TOKEN` when empty)
- `BOT_ADMIN_PANEL_HEADER_NAME`
- `BOT_ADMIN_PANEL_PORT_HOST`

## Verified Behavior (target)

- Root `.env` drives bot and UAG runtime
- Root env can sync to all provider submodule `.env` files
- MySQL schema auto-creates on startup
- User list and chat logs are queryable in phpMyAdmin
