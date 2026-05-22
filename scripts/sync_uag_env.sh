#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UAG_DIR="$ROOT_DIR/uag_server"
ROOT_ENV="${ENV_FILE:-$ROOT_DIR/.env}"

if [[ ! -d "$UAG_DIR" ]]; then
  echo "[sync_uag_env] uag_server directory not found"
  exit 1
fi

if [[ ! -f "$ROOT_ENV" ]]; then
  echo "[sync_uag_env] root env not found: $ROOT_ENV"
  exit 1
fi

python3 - "$ROOT_ENV" "$UAG_DIR" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

root_env = Path(sys.argv[1])
uag_dir = Path(sys.argv[2])


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out


def merge_env_file(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    seen: set[str] = set()
    out: list[str] = []
    for raw in existing_lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in raw:
            out.append(raw)
            continue
        key, _ = raw.split("=", 1)
        key = key.strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(raw)

    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")


def get(env: dict[str, str], key: str, default: str = "") -> str:
    return env.get(key, default)


def pick(env: dict[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        value = env.get(key)
        if value is not None and str(value).strip() != "":
            return str(value)
    return default


def get_bool(env: dict[str, str], key: str, default: str = "false") -> str:
    return str(env.get(key, default)).strip().lower()


def pick_bool(env: dict[str, str], *keys: str, default: str = "false") -> str:
    return str(pick(env, *keys, default=default)).strip().lower()


env = load_env(root_env)

uag_updates: dict[str, str] = {
    "UAG_ENV_FILE": "../.env",
}
for key, value in env.items():
    if key.startswith("UAG_"):
        uag_updates[key] = value

uag_defaults = {
    "UAG_APP_HOST": pick(env, "APP_HOST", default="0.0.0.0"),
    "UAG_APP_PORT": pick(env, "APP_PORT", default="8080"),
    "UAG_APP_LOG_LEVEL": pick(env, "APP_LOG_LEVEL", default="INFO"),
    "UAG_APP_DOCS_ENABLED": pick_bool(env, "APP_ENABLE_DOCS", default="true"),
    "UAG_AUTH_ENABLED": "false",
    "UAG_AUTH_TOKEN": "",
    "UAG_AUTH_HEADER_NAME": "x-api-token",
    "UAG_GEMINI_ENABLED": "true",
    "UAG_GEMINI_MODE": pick(env, "PROXY_MODE", default="gemini_direct"),
    "UAG_GEMINI_BASE_URL": pick(env, "GEMINI_BASE_URL", default="https://generativelanguage.googleapis.com"),
    "UAG_GEMINI_API_VERSION": pick(env, "GEMINI_API_VERSION", default="v1beta"),
    "UAG_GEMINI_DEFAULT_MODEL": pick(env, "GEMINI_DEFAULT_MODEL", default="gemini-2.5-flash"),
    "UAG_GEMINI_API_KEYS": pick(env, "UAG_GEMINI_API_KEYS", "GEMINI_API_KEYS", default=""),
    "UAG_GEMINI_RETRY_ON_429": pick_bool(env, "PROXY_RETRY_ON_429", default="true"),
    "UAG_GEMINI_RETRY_ON_5XX": pick_bool(env, "PROXY_RETRY_ON_5XX", default="true"),
    "UAG_GEMINI_MAX_RETRIES_PER_KEY": pick(env, "PROXY_MAX_RETRIES_PER_KEY", default="2"),
    "UAG_GEMINI_MAX_RETRIES_ON_5XX": pick(env, "PROXY_MAX_RETRIES_ON_5XX", default="4"),
    "UAG_GEMINI_RETRY_BACKOFF_SEC": pick(env, "PROXY_RETRY_BACKOFF_SEC", default="0.35"),
    "UAG_GEMINI_COOLOFF_SEC": pick(env, "PROXY_COOLOFF_SEC", default="20"),
    "UAG_GEMINI_MIN_INTERVAL_SEC": pick(env, "PROXY_MIN_INTERVAL_SEC", default="0"),
    "UAG_GEMINI_WORKER_BASE_URLS": pick(env, "CLOUDFLARE_WORKER_BASE_URLS", default=""),
    "UAG_GEMINI_WORKER_ROUTE_PREFIX": pick(env, "CLOUDFLARE_WORKER_ROUTE_PREFIX", default="/gemini"),
    "UAG_GEMINI_WORKER_AUTH_TOKEN": pick(env, "CLOUDFLARE_AUTH_TOKEN", default=""),
    "UAG_GEMINI_WORKER_AUTH_HEADER_NAME": pick(env, "CLOUDFLARE_AUTH_HEADER_NAME", default="x-worker-auth"),
    "UAG_GROQ_ENABLED": "true",
    "UAG_GROQ_BASE_URL": pick(env, "GROQ_BASE_URL", default="https://api.groq.com/openai/v1"),
    "UAG_GROQ_API_KEYS": pick(env, "GROQ_API_KEYS", "GROQ_API_KEY", default=""),
    "UAG_POLLINATIONS_ENABLED": "true",
    "UAG_POLLINATIONS_BASE_URL": pick(env, "POLLINATIONS_BASE_URL", default="https://gen.pollinations.ai"),
    "UAG_POLLINATIONS_API_KEYS": pick(env, "POLLINATIONS_API_KEYS", "POLLINATIONS_API_KEY", default=""),
}
for key, value in uag_defaults.items():
    if key not in uag_updates:
        uag_updates[key] = value

merge_env_file(uag_dir / ".env", uag_updates)

# Gemini module .env
module_gemini = {
    "APP_CONFIG_FILE": "config/config.yml",
    "APP_HOST": pick(env, "UAG_APP_HOST", "APP_HOST", default="0.0.0.0"),
    "APP_PORT": "8000",
    "APP_LOG_LEVEL": pick(env, "UAG_APP_LOG_LEVEL", "APP_LOG_LEVEL", default="INFO"),
    "APP_REQUEST_TIMEOUT_SEC": "120",
    "APP_ENABLE_DOCS": pick_bool(env, "UAG_APP_DOCS_ENABLED", "APP_ENABLE_DOCS", default="true"),
    "APP_DOCS_URL": "/docs",
    "APP_REDOC_URL": "/redoc",
    "APP_OPENAPI_URL": "/openapi.json",
    "PROXY_MODE": pick(env, "UAG_GEMINI_MODE", "PROXY_MODE", default="gemini_direct"),
    "PROXY_TRUST_ENV_PROXY": "true",
    "PROXY_RETRY_ON_429": pick_bool(env, "UAG_GEMINI_RETRY_ON_429", "PROXY_RETRY_ON_429", default="true"),
    "PROXY_RETRY_ON_5XX": pick_bool(env, "UAG_GEMINI_RETRY_ON_5XX", "PROXY_RETRY_ON_5XX", default="true"),
    "PROXY_MAX_RETRIES_PER_KEY": pick(env, "UAG_GEMINI_MAX_RETRIES_PER_KEY", "PROXY_MAX_RETRIES_PER_KEY", default="2"),
    "PROXY_MAX_RETRIES_ON_5XX": pick(env, "UAG_GEMINI_MAX_RETRIES_ON_5XX", "PROXY_MAX_RETRIES_ON_5XX", default="4"),
    "PROXY_RETRY_BACKOFF_SEC": pick(env, "UAG_GEMINI_RETRY_BACKOFF_SEC", "PROXY_RETRY_BACKOFF_SEC", default="0.35"),
    "PROXY_COOLOFF_SEC": pick(env, "UAG_GEMINI_COOLOFF_SEC", "PROXY_COOLOFF_SEC", default="20"),
    "PROXY_MIN_INTERVAL_SEC": pick(env, "UAG_GEMINI_MIN_INTERVAL_SEC", "PROXY_MIN_INTERVAL_SEC", default="0"),
    "GEMINI_BASE_URL": pick(env, "UAG_GEMINI_BASE_URL", "GEMINI_BASE_URL", default="https://generativelanguage.googleapis.com"),
    "GEMINI_API_VERSION": pick(env, "UAG_GEMINI_API_VERSION", "GEMINI_API_VERSION", default="v1beta"),
    "GEMINI_DEFAULT_MODEL": pick(env, "UAG_GEMINI_DEFAULT_MODEL", "GEMINI_DEFAULT_MODEL", default="gemini-2.5-flash"),
    "GEMINI_API_KEYS": pick(env, "UAG_GEMINI_API_KEYS", "GEMINI_API_KEYS", default=""),
    "CLOUDFLARE_WORKER_BASE_URLS": pick(env, "UAG_GEMINI_WORKER_BASE_URLS", "CLOUDFLARE_WORKER_BASE_URLS", default=""),
    "CLOUDFLARE_WORKER_ROUTE_PREFIX": pick(env, "UAG_GEMINI_WORKER_ROUTE_PREFIX", "CLOUDFLARE_WORKER_ROUTE_PREFIX", default="/gemini"),
    "CLOUDFLARE_AUTH_TOKEN": pick(env, "UAG_GEMINI_WORKER_AUTH_TOKEN", "CLOUDFLARE_AUTH_TOKEN", default=""),
    "CLOUDFLARE_AUTH_HEADER_NAME": pick(env, "UAG_GEMINI_WORKER_AUTH_HEADER_NAME", "CLOUDFLARE_AUTH_HEADER_NAME", default="x-worker-auth"),
    "ADMIN_ENABLED": "false",
    "ADMIN_REQUIRE_AUTH": "false",
    "ADMIN_TOKEN": "",
    "ADMIN_HEADER_NAME": "x-admin-token",
}
merge_env_file(uag_dir / "modules/gemini_proxy/.env", module_gemini)

# Groq module .env
module_groq = {
    "APP_HOST": pick(env, "UAG_APP_HOST", "APP_HOST", default="0.0.0.0"),
    "APP_PORT": "18010",
    "APP_LOG_LEVEL": pick(env, "UAG_APP_LOG_LEVEL", "APP_LOG_LEVEL", default="INFO"),
    "APP_REQUEST_TIMEOUT_SEC": "90",
    "APP_ENABLE_DOCS": pick_bool(env, "UAG_APP_DOCS_ENABLED", "APP_ENABLE_DOCS", default="true"),
    "APP_DOCS_URL": "/docs",
    "APP_REDOC_URL": "/redoc",
    "APP_OPENAPI_URL": "/openapi.json",
    "PROXY_RETRY_ON_429": get_bool(env, "UAG_GROQ_RETRY_ON_429", "true"),
    "PROXY_RETRY_ON_5XX": get_bool(env, "UAG_GROQ_RETRY_ON_5XX", "true"),
    "PROXY_MAX_RETRIES_PER_KEY": get(env, "UAG_GROQ_MAX_RETRIES_PER_KEY", "2"),
    "PROXY_MAX_RETRIES_ON_5XX": get(env, "UAG_GROQ_MAX_RETRIES_ON_5XX", "4"),
    "PROXY_RETRY_BACKOFF_SEC": get(env, "UAG_GROQ_RETRY_BACKOFF_SEC", "0.35"),
    "PROXY_COOLOFF_SEC": get(env, "UAG_GROQ_COOLOFF_SEC", "20"),
    "PROXY_MIN_INTERVAL_SEC": get(env, "UAG_GROQ_MIN_INTERVAL_SEC", "0"),
    "GROQ_BASE_URL": pick(env, "UAG_GROQ_BASE_URL", "GROQ_BASE_URL", default="https://api.groq.com/openai/v1"),
    "GROQ_API_KEYS": pick(env, "UAG_GROQ_API_KEYS", "GROQ_API_KEYS", "GROQ_API_KEY", default=""),
    "GROQ_STT_PRIMARY_MODEL": get(env, "UAG_GROQ_STT_PRIMARY_MODEL", "whisper-large-v3-turbo"),
    "GROQ_STT_FALLBACK_MODEL": get(env, "UAG_GROQ_STT_FALLBACK_MODEL", "whisper-large-v3"),
    "GROQ_STT_LANGUAGE": get(env, "UAG_GROQ_STT_LANGUAGE", "fa"),
    "GROQ_STT_TEMPERATURE": get(env, "UAG_GROQ_STT_TEMPERATURE", "0"),
    "GROQ_STT_RESPONSE_FORMAT": get(env, "UAG_GROQ_STT_RESPONSE_FORMAT", "verbose_json"),
    "GROQ_STT_PROMPT": get(env, "UAG_GROQ_STT_PROMPT", ""),
    "GROQ_TTS_DEFAULT_MODEL": get(env, "UAG_GROQ_TTS_DEFAULT_MODEL", "canopylabs/orpheus-v1-english"),
    "GROQ_TTS_DEFAULT_VOICE": get(env, "UAG_GROQ_TTS_DEFAULT_VOICE", "diana"),
    "GROQ_TTS_DEFAULT_RESPONSE_FORMAT": get(env, "UAG_GROQ_TTS_DEFAULT_RESPONSE_FORMAT", "wav"),
    "ADMIN_ENABLED": "false",
    "ADMIN_REQUIRE_AUTH": "false",
    "ADMIN_TOKEN": "",
    "ADMIN_HEADER_NAME": "x-admin-token",
}
merge_env_file(uag_dir / "modules/groq_proxy/.env", module_groq)

# Pollinations module .env
module_pollinations = {
    "APP_HOST": pick(env, "UAG_APP_HOST", "APP_HOST", default="0.0.0.0"),
    "APP_PORT": "8000",
    "APP_LOG_LEVEL": pick(env, "UAG_APP_LOG_LEVEL", "APP_LOG_LEVEL", default="INFO"),
    "APP_DOCS_ENABLED": pick_bool(env, "UAG_APP_DOCS_ENABLED", "APP_ENABLE_DOCS", default="true"),
    "APP_REQUEST_TIMEOUT_SEC": "120",
    "POLLINATIONS_BASE_URL": pick(env, "UAG_POLLINATIONS_BASE_URL", "POLLINATIONS_BASE_URL", default="https://gen.pollinations.ai"),
    "POLLINATIONS_DEFAULT_IMAGE_MODEL": pick(env, "UAG_POLLINATIONS_DEFAULT_IMAGE_MODEL", "POLLINATIONS_DEFAULT_IMAGE_MODEL", default="flux"),
    "POLLINATIONS_API_KEYS": pick(env, "UAG_POLLINATIONS_API_KEYS", "POLLINATIONS_API_KEYS", "POLLINATIONS_API_KEY", default=""),
    "POLLINATIONS_USE_PROXY_2080": get_bool(env, "UAG_PROXY_ENABLED", "false"),
    "POLLINATIONS_PROXY_2080_URL": get(env, "UAG_PROXY_URL", "socks5://127.0.0.1:2080"),
    "POLLINATIONS_TRUST_ENV_PROXY": get_bool(env, "UAG_POLLINATIONS_TRUST_ENV_PROXY", "false"),
    "POLLINATIONS_MAX_ATTEMPTS_PER_REQUEST": get(env, "UAG_POLLINATIONS_MAX_ATTEMPTS_PER_REQUEST", "6"),
    "POLLINATIONS_RETRY_STATUS_CODES": get(env, "UAG_POLLINATIONS_RETRY_STATUS_CODES", "402,429,500,502,503,504"),
    "POLLINATIONS_RETRY_BACKOFF_SEC": get(env, "UAG_POLLINATIONS_RETRY_BACKOFF_SEC", "0.35"),
    "POLLINATIONS_KEY_COOLDOWN_SEC": get(env, "UAG_POLLINATIONS_KEY_COOLDOWN_SEC", "20"),
    "IMAGE_DEFAULT_N": get(env, "UAG_IMAGE_DEFAULT_N", "1"),
    "IMAGE_DEFAULT_SIZE": get(env, "UAG_IMAGE_DEFAULT_SIZE", "1024x1024"),
    "IMAGE_DEFAULT_QUALITY": get(env, "UAG_IMAGE_DEFAULT_QUALITY", "medium"),
    "IMAGE_DEFAULT_RESPONSE_FORMAT": get(env, "UAG_IMAGE_DEFAULT_RESPONSE_FORMAT", "b64_json"),
    "ADMIN_ENABLED": "false",
    "ADMIN_REQUIRE_TOKEN": "false",
    "ADMIN_TOKEN": "",
    "ADMIN_HEADER_NAME": "x-admin-token",
    "ADMIN_MODELS_CACHE_TTL_SEC": "180",
}
merge_env_file(uag_dir / "modules/pollinations_proxy/.env", module_pollinations)

print("[sync_uag_env] synced:")
print(" - uag_server/.env")
print(" - uag_server/modules/gemini_proxy/.env")
print(" - uag_server/modules/groq_proxy/.env")
print(" - uag_server/modules/pollinations_proxy/.env")
PY
