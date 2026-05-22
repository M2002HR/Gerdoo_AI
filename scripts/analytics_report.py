#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
from urllib.parse import quote, urlparse
from typing import Any

import httpx
from dotenv import load_dotenv

from gerdoo_ai_bot.storage import ChatStorage


def _fallback_db_url(db_url: str) -> str:
    value = (db_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"mysql", "mysql+aiomysql"}:
        return value
    host = (parsed.hostname or "").strip().lower()
    if host != "mysql":
        return value

    port = int((os.getenv("MYSQL_PORT_HOST") or "33306").strip() or "33306")
    user = quote(parsed.username or "")
    password = quote(parsed.password or "")
    database = parsed.path.lstrip("/")
    return f"mysql+aiomysql://{user}:{password}@127.0.0.1:{port}/{database}"


def _candidate_uag_urls() -> list[str]:
    out: list[str] = []
    base = (os.getenv("UAG_BASE_URL") or "").strip().rstrip("/")
    if base:
        out.append(base)
    host_port = (os.getenv("UAG_PORT_HOST") or "18080").strip()
    out.append(f"http://127.0.0.1:{host_port}")
    seen: set[str] = set()
    unique: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


async def _fetch_uag_admin(hours: int) -> dict[str, Any]:
    token = (os.getenv("UAG_ADMIN_TOKEN") or "").strip()
    header_name = (os.getenv("UAG_ADMIN_HEADER_NAME") or "x-admin-token").strip() or "x-admin-token"
    headers = {header_name: token} if token else {}
    timeout = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=10.0)

    paths = {
        "health": "/health",
        "router_stats": "/admin/router/stats",
        "usage_overview": "/admin/usage/overview",
        "usage_providers": f"/admin/usage/providers?since_minutes={max(60, hours * 60)}",
        "usage_models": f"/admin/usage/models?since_minutes={max(60, hours * 60)}",
        "logs_summary": "/admin/logs/summary",
    }

    result: dict[str, Any] = {"source": None, "data": {}, "errors": []}
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        for base in _candidate_uag_urls():
            try:
                health = await client.get(f"{base}{paths['health']}", headers=headers)
                if health.status_code != 200:
                    result["errors"].append(f"{base} health status={health.status_code}")
                    continue
                result["source"] = base
                result["data"]["health"] = health.json()
                for key, path in paths.items():
                    if key == "health":
                        continue
                    resp = await client.get(f"{base}{path}", headers=headers)
                    try:
                        parsed = resp.json()
                    except Exception:
                        parsed = {"raw": resp.text}
                    result["data"][key] = {"status_code": resp.status_code, "payload": parsed}
                return result
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"{base} error={exc}")
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description="Gerdoo AI analytics snapshot")
    parser.add_argument("--env-file", default=".env", help="Path to env file")
    parser.add_argument("--hours", type=int, default=24, help="Window in hours for bot DB analytics")
    args = parser.parse_args()

    load_dotenv(args.env_file, override=False)
    db_url = (os.getenv("DB_URL") or "").strip()
    if not db_url:
        raise SystemExit("DB_URL is missing")

    storage = ChatStorage(db_url)
    try:
        await storage.init()
    except Exception:  # noqa: BLE001
        fallback_url = _fallback_db_url(db_url)
        if fallback_url == db_url:
            raise
        storage = ChatStorage(fallback_url)
        await storage.init()
    try:
        bot_snapshot = await storage.analytics_snapshot(since_hours=max(1, args.hours))
    finally:
        await storage.aclose()

    uag_snapshot = await _fetch_uag_admin(hours=max(1, args.hours))

    payload = {
        "window_hours": max(1, args.hours),
        "bot": bot_snapshot,
        "uag": uag_snapshot,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
