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


def module_pick(env: dict[str, str], module: str, key: str, *fallback: str, default: str = "") -> str:
    return pick(env, f"UAG_{module}_MODULE_{key}", *fallback, default=default)


def module_pick_bool(env: dict[str, str], module: str, key: str, *fallback: str, default: str = "false") -> str:
    return str(module_pick(env, module, key, *fallback, default=default)).strip().lower()


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
    "APP_CONFIG_FILE": module_pick(env, "GEMINI", "APP_CONFIG_FILE", "APP_CONFIG_FILE", default="config/config.yml"),
    "APP_HOST": module_pick(env, "GEMINI", "APP_HOST", "UAG_APP_HOST", "APP_HOST", default="0.0.0.0"),
    "APP_PORT": module_pick(env, "GEMINI", "APP_PORT", default="8000"),
    "APP_LOG_LEVEL": module_pick(env, "GEMINI", "APP_LOG_LEVEL", "UAG_APP_LOG_LEVEL", "APP_LOG_LEVEL", default="INFO"),
    "APP_REQUEST_TIMEOUT_SEC": module_pick(env, "GEMINI", "APP_REQUEST_TIMEOUT_SEC", default="120"),
    "APP_ENABLE_DOCS": module_pick_bool(env, "GEMINI", "APP_ENABLE_DOCS", "UAG_APP_DOCS_ENABLED", "APP_ENABLE_DOCS", default="true"),
    "APP_DOCS_URL": module_pick(env, "GEMINI", "APP_DOCS_URL", default="/docs"),
    "APP_REDOC_URL": module_pick(env, "GEMINI", "APP_REDOC_URL", default="/redoc"),
    "APP_OPENAPI_URL": module_pick(env, "GEMINI", "APP_OPENAPI_URL", default="/openapi.json"),
    "PROXY_MODE": pick(env, "UAG_GEMINI_MODE", "PROXY_MODE", default="gemini_direct"),
    "PROXY_TRUST_ENV_PROXY": module_pick_bool(env, "GEMINI", "PROXY_TRUST_ENV_PROXY", "PROXY_TRUST_ENV_PROXY", default="true"),
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
    "CLOUDFLARE_ACCESS_CLIENT_ID": module_pick(env, "GEMINI", "CLOUDFLARE_ACCESS_CLIENT_ID", "CLOUDFLARE_ACCESS_CLIENT_ID", default=""),
    "CLOUDFLARE_ACCESS_CLIENT_SECRET": module_pick(env, "GEMINI", "CLOUDFLARE_ACCESS_CLIENT_SECRET", "CLOUDFLARE_ACCESS_CLIENT_SECRET", default=""),
    "CLOUDFLARE_PASS_TRACE_HEADERS": module_pick_bool(env, "GEMINI", "CLOUDFLARE_PASS_TRACE_HEADERS", "CLOUDFLARE_PASS_TRACE_HEADERS", default="true"),
    "ADMIN_ENABLED": module_pick_bool(env, "GEMINI", "ADMIN_ENABLED", default="false"),
    "ADMIN_REQUIRE_AUTH": module_pick_bool(env, "GEMINI", "ADMIN_REQUIRE_AUTH", default="false"),
    "ADMIN_TOKEN": module_pick(env, "GEMINI", "ADMIN_TOKEN", default=""),
    "ADMIN_HEADER_NAME": module_pick(env, "GEMINI", "ADMIN_HEADER_NAME", "ADMIN_HEADER_NAME", default="x-admin-token"),
    "ADMIN_MODELS_CACHE_TTL_SEC": module_pick(env, "GEMINI", "ADMIN_MODELS_CACHE_TTL_SEC", "ADMIN_MODELS_CACHE_TTL_SEC", default="300"),
    "ADMIN_MAX_RECENT_REQUESTS": module_pick(env, "GEMINI", "ADMIN_MAX_RECENT_REQUESTS", "ADMIN_MAX_RECENT_REQUESTS", default="2000"),
    "ADMIN_MAX_INCIDENTS": module_pick(env, "GEMINI", "ADMIN_MAX_INCIDENTS", "ADMIN_MAX_INCIDENTS", default="500"),
}
merge_env_file(uag_dir / "modules/gemini_proxy/.env", module_gemini)

# Groq module .env
module_groq = {
    "APP_HOST": module_pick(env, "GROQ", "APP_HOST", "UAG_APP_HOST", "APP_HOST", default="0.0.0.0"),
    "APP_PORT": module_pick(env, "GROQ", "APP_PORT", default="18010"),
    "APP_LOG_LEVEL": module_pick(env, "GROQ", "APP_LOG_LEVEL", "UAG_APP_LOG_LEVEL", "APP_LOG_LEVEL", default="INFO"),
    "APP_REQUEST_TIMEOUT_SEC": module_pick(env, "GROQ", "APP_REQUEST_TIMEOUT_SEC", default="90"),
    "APP_ENABLE_DOCS": module_pick_bool(env, "GROQ", "APP_ENABLE_DOCS", "UAG_APP_DOCS_ENABLED", "APP_ENABLE_DOCS", default="true"),
    "APP_DOCS_URL": module_pick(env, "GROQ", "APP_DOCS_URL", default="/docs"),
    "APP_REDOC_URL": module_pick(env, "GROQ", "APP_REDOC_URL", default="/redoc"),
    "APP_OPENAPI_URL": module_pick(env, "GROQ", "APP_OPENAPI_URL", default="/openapi.json"),
    "APP_RELOAD": module_pick_bool(env, "GROQ", "APP_RELOAD", default="false"),
    "PROXY_RETRY_ON_429": get_bool(env, "UAG_GROQ_RETRY_ON_429", "true"),
    "PROXY_RETRY_ON_5XX": get_bool(env, "UAG_GROQ_RETRY_ON_5XX", "true"),
    "PROXY_MAX_RETRIES_PER_KEY": module_pick(env, "GROQ", "PROXY_MAX_RETRIES_PER_KEY", "UAG_GROQ_MAX_RETRIES_PER_KEY", default="2"),
    "PROXY_MAX_RETRIES_ON_5XX": module_pick(env, "GROQ", "PROXY_MAX_RETRIES_ON_5XX", "UAG_GROQ_MAX_RETRIES_ON_5XX", default="4"),
    "PROXY_RETRY_BACKOFF_SEC": module_pick(env, "GROQ", "PROXY_RETRY_BACKOFF_SEC", "UAG_GROQ_RETRY_BACKOFF_SEC", default="0.35"),
    "PROXY_COOLOFF_SEC": module_pick(env, "GROQ", "PROXY_COOLOFF_SEC", "UAG_GROQ_COOLOFF_SEC", default="20"),
    "PROXY_MIN_INTERVAL_SEC": module_pick(env, "GROQ", "PROXY_MIN_INTERVAL_SEC", "UAG_GROQ_MIN_INTERVAL_SEC", default="0"),
    "GROQ_BASE_URL": pick(env, "UAG_GROQ_BASE_URL", "GROQ_BASE_URL", default="https://api.groq.com/openai/v1"),
    "GROQ_API_KEYS": pick(env, "UAG_GROQ_API_KEYS", "GROQ_API_KEYS", "GROQ_API_KEY", default=""),
    "GROQ_API_KEY": module_pick(env, "GROQ", "GROQ_API_KEY", "GROQ_API_KEY", default=""),
    "GROQ_STT_PRIMARY_MODEL": get(env, "UAG_GROQ_STT_PRIMARY_MODEL", "whisper-large-v3-turbo"),
    "GROQ_STT_FALLBACK_MODEL": get(env, "UAG_GROQ_STT_FALLBACK_MODEL", "whisper-large-v3"),
    "GROQ_STT_LANGUAGE": get(env, "UAG_GROQ_STT_LANGUAGE", "fa"),
    "GROQ_STT_TEMPERATURE": get(env, "UAG_GROQ_STT_TEMPERATURE", "0"),
    "GROQ_STT_RESPONSE_FORMAT": get(env, "UAG_GROQ_STT_RESPONSE_FORMAT", "verbose_json"),
    "GROQ_STT_PROMPT": get(env, "UAG_GROQ_STT_PROMPT", ""),
    "GROQ_TTS_DEFAULT_MODEL": get(env, "UAG_GROQ_TTS_DEFAULT_MODEL", "canopylabs/orpheus-v1-english"),
    "GROQ_TTS_DEFAULT_VOICE": get(env, "UAG_GROQ_TTS_DEFAULT_VOICE", "diana"),
    "GROQ_TTS_DEFAULT_RESPONSE_FORMAT": get(env, "UAG_GROQ_TTS_DEFAULT_RESPONSE_FORMAT", "wav"),
    "ADMIN_ENABLED": module_pick_bool(env, "GROQ", "ADMIN_ENABLED", default="false"),
    "ADMIN_REQUIRE_AUTH": module_pick_bool(env, "GROQ", "ADMIN_REQUIRE_AUTH", default="false"),
    "ADMIN_TOKEN": module_pick(env, "GROQ", "ADMIN_TOKEN", default=""),
    "ADMIN_HEADER_NAME": module_pick(env, "GROQ", "ADMIN_HEADER_NAME", "ADMIN_HEADER_NAME", default="x-admin-token"),
}
merge_env_file(uag_dir / "modules/groq_proxy/.env", module_groq)

# Pollinations module .env
module_pollinations = {
    "APP_HOST": module_pick(env, "POLLINATIONS", "APP_HOST", "UAG_APP_HOST", "APP_HOST", default="0.0.0.0"),
    "APP_PORT": module_pick(env, "POLLINATIONS", "APP_PORT", default="8000"),
    "APP_LOG_LEVEL": module_pick(env, "POLLINATIONS", "APP_LOG_LEVEL", "UAG_APP_LOG_LEVEL", "APP_LOG_LEVEL", default="INFO"),
    "APP_DOCS_ENABLED": module_pick_bool(env, "POLLINATIONS", "APP_DOCS_ENABLED", "UAG_APP_DOCS_ENABLED", "APP_ENABLE_DOCS", default="true"),
    "APP_REQUEST_TIMEOUT_SEC": module_pick(env, "POLLINATIONS", "APP_REQUEST_TIMEOUT_SEC", default="120"),
    "POLLINATIONS_BASE_URL": pick(env, "UAG_POLLINATIONS_BASE_URL", "POLLINATIONS_BASE_URL", default="https://gen.pollinations.ai"),
    "POLLINATIONS_DEFAULT_IMAGE_MODEL": pick(env, "UAG_POLLINATIONS_DEFAULT_IMAGE_MODEL", "POLLINATIONS_DEFAULT_IMAGE_MODEL", default="flux"),
    "POLLINATIONS_API_KEYS": pick(env, "UAG_POLLINATIONS_API_KEYS", "POLLINATIONS_API_KEYS", "POLLINATIONS_API_KEY", default=""),
    "POLLINATIONS_API_KEY": module_pick(env, "POLLINATIONS", "POLLINATIONS_API_KEY", "POLLINATIONS_API_KEY", default=""),
    "POLLINATIONS_USE_PROXY_2080": module_pick_bool(env, "POLLINATIONS", "POLLINATIONS_USE_PROXY_2080", "UAG_POLLINATIONS_USE_PROXY_2080", "UAG_PROXY_ENABLED", default="false"),
    "POLLINATIONS_PROXY_2080_URL": module_pick(env, "POLLINATIONS", "POLLINATIONS_PROXY_2080_URL", "UAG_POLLINATIONS_PROXY_2080_URL", "UAG_PROXY_URL", default="socks5://127.0.0.1:2080"),
    "POLLINATIONS_PROXY_URL": module_pick(env, "POLLINATIONS", "POLLINATIONS_PROXY_URL", default=""),
    "POLLINATIONS_TRUST_ENV_PROXY": module_pick_bool(env, "POLLINATIONS", "POLLINATIONS_TRUST_ENV_PROXY", "UAG_POLLINATIONS_TRUST_ENV_PROXY", default="false"),
    "POLLINATIONS_MAX_ATTEMPTS_PER_REQUEST": module_pick(env, "POLLINATIONS", "POLLINATIONS_MAX_ATTEMPTS_PER_REQUEST", "UAG_POLLINATIONS_MAX_ATTEMPTS_PER_REQUEST", default="6"),
    "POLLINATIONS_RETRY_STATUS_CODES": module_pick(env, "POLLINATIONS", "POLLINATIONS_RETRY_STATUS_CODES", "UAG_POLLINATIONS_RETRY_STATUS_CODES", default="402,429,500,502,503,504"),
    "POLLINATIONS_RETRY_BACKOFF_SEC": module_pick(env, "POLLINATIONS", "POLLINATIONS_RETRY_BACKOFF_SEC", "UAG_POLLINATIONS_RETRY_BACKOFF_SEC", default="0.35"),
    "POLLINATIONS_KEY_COOLDOWN_SEC": module_pick(env, "POLLINATIONS", "POLLINATIONS_KEY_COOLDOWN_SEC", "UAG_POLLINATIONS_KEY_COOLDOWN_SEC", default="20"),
    "IMAGE_DEFAULT_N": module_pick(env, "POLLINATIONS", "IMAGE_DEFAULT_N", "UAG_IMAGE_DEFAULT_N", default="1"),
    "IMAGE_DEFAULT_SIZE": module_pick(env, "POLLINATIONS", "IMAGE_DEFAULT_SIZE", "UAG_IMAGE_DEFAULT_SIZE", default="1024x1024"),
    "IMAGE_DEFAULT_QUALITY": module_pick(env, "POLLINATIONS", "IMAGE_DEFAULT_QUALITY", "UAG_IMAGE_DEFAULT_QUALITY", default="medium"),
    "IMAGE_DEFAULT_RESPONSE_FORMAT": module_pick(env, "POLLINATIONS", "IMAGE_DEFAULT_RESPONSE_FORMAT", "UAG_IMAGE_DEFAULT_RESPONSE_FORMAT", default="b64_json"),
    "ADMIN_ENABLED": module_pick_bool(env, "POLLINATIONS", "ADMIN_ENABLED", default="false"),
    "ADMIN_REQUIRE_TOKEN": module_pick_bool(env, "POLLINATIONS", "ADMIN_REQUIRE_TOKEN", default="false"),
    "ADMIN_TOKEN": module_pick(env, "POLLINATIONS", "ADMIN_TOKEN", default=""),
    "ADMIN_HEADER_NAME": module_pick(env, "POLLINATIONS", "ADMIN_HEADER_NAME", "ADMIN_HEADER_NAME", default="x-admin-token"),
    "ADMIN_MODELS_CACHE_TTL_SEC": module_pick(env, "POLLINATIONS", "ADMIN_MODELS_CACHE_TTL_SEC", "ADMIN_MODELS_CACHE_TTL_SEC", default="180"),
}
merge_env_file(uag_dir / "modules/pollinations_proxy/.env", module_pollinations)

print("[sync_uag_env] synced:")
print(" - uag_server/.env")
print(" - uag_server/modules/gemini_proxy/.env")
print(" - uag_server/modules/groq_proxy/.env")
print(" - uag_server/modules/pollinations_proxy/.env")
PY
