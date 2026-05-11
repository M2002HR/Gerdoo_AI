#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GEMINI_DIR="$ROOT_DIR/gemini_server"
PORT="${GEMINI_SERVER_PORT:-18000}"

cd "$GEMINI_DIR"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

if [[ ! -f ".env" ]]; then
  cp .env.example .env
fi

# Keep proxy runtime vars in sync with root .env when available
if [[ -f "$ROOT_DIR/.env" ]]; then
  python3 - <<'PY'
from pathlib import Path
root = Path("../.env")
sub = Path(".env")
keys = {
    "APP_CONFIG_FILE","APP_HOST","APP_PORT","APP_LOG_LEVEL","APP_LOG_FORMAT","APP_REQUEST_TIMEOUT_SEC","APP_ENABLE_DOCS","APP_DOCS_URL","APP_REDOC_URL","APP_OPENAPI_URL","APP_RELOAD",
    "PROXY_MODE","PROXY_TRUST_ENV_PROXY","PROXY_RETRY_ON_429","PROXY_RETRY_ON_5XX","PROXY_MAX_RETRIES_PER_KEY","PROXY_MAX_RETRIES_ON_5XX","PROXY_COOLOFF_SEC","PROXY_RETRY_BACKOFF_SEC","PROXY_MIN_INTERVAL_SEC",
    "GEMINI_BASE_URL","GEMINI_API_VERSION","GEMINI_DEFAULT_MODEL","GEMINI_API_KEYS","GEMINI_DEFAULT_REQUEST_JSON",
    "CLOUDFLARE_WORKER_BASE_URLS","CLOUDFLARE_WORKER_ROUTE_PREFIX","CLOUDFLARE_AUTH_TOKEN","CLOUDFLARE_AUTH_HEADER_NAME","CLOUDFLARE_ACCESS_CLIENT_ID","CLOUDFLARE_ACCESS_CLIENT_SECRET","CLOUDFLARE_PASS_TRACE_HEADERS",
    "ADMIN_ENABLED","ADMIN_REQUIRE_AUTH","ADMIN_TOKEN","ADMIN_HEADER_NAME","ADMIN_MODELS_CACHE_TTL_SEC","ADMIN_MAX_RECENT_REQUESTS","ADMIN_MAX_INCIDENTS",
}
root_lines = root.read_text(encoding="utf-8").splitlines() if root.exists() else []
vals = {}
for line in root_lines:
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    k,v = line.split("=",1)
    if k in keys:
        vals[k]=v
vals["APP_PORT"] = "18000"
existing=[]
if sub.exists():
    existing=sub.read_text(encoding="utf-8").splitlines()
out=[]
seen=set()
for line in existing:
    if not line or line.lstrip().startswith("#") or "=" not in line:
        out.append(line)
        continue
    k,_=line.split("=",1)
    if k in vals:
        out.append(f"{k}={vals[k]}")
        seen.add(k)
    else:
        out.append(line)
for k,v in vals.items():
    if k not in seen:
        out.append(f"{k}={v}")
sub.write_text("\n".join(out).strip()+"\n", encoding="utf-8")
print("synced .env")
PY
fi

. .venv/bin/activate
pip install -q -r requirements.txt

exec uvicorn api.app.main:app --host 0.0.0.0 --port "$PORT"
