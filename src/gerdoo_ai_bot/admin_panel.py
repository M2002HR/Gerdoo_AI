from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from gerdoo_ai_bot.config import Settings, load_settings
from gerdoo_ai_bot.storage import ChatStorage


ADMIN_UI_ROOT = Path(__file__).resolve().parent / "admin_ui"


class AdminState:
    def __init__(self, settings: Settings, storage: ChatStorage) -> None:
        self.settings = settings
        self.storage = storage


class WsFilters:
    def __init__(self, since_minutes: int, limit: int) -> None:
        self.since_minutes = max(5, int(since_minutes))
        self.limit = max(50, min(2000, int(limit)))
        self.level = ""
        self.event_type = ""
        self.request_id = ""
        self.search = ""
        self.user_id = ""


def _env_file() -> str:
    return os.getenv("ENV_FILE", ".env")


def _load_runtime_settings() -> Settings:
    return load_settings(_env_file())


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = _load_runtime_settings()
    storage = ChatStorage(settings.db_url)
    await storage.init()
    app.state.ctx = AdminState(settings=settings, storage=storage)
    try:
        yield
    finally:
        await storage.aclose()


app = FastAPI(
    title="Gerdoo AI Admin Panel",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def _ctx(request: Request) -> AdminState:
    return request.app.state.ctx


def _ctx_ws(websocket: WebSocket) -> AdminState:
    return websocket.app.state.ctx


def _admin_header_name(settings: Settings) -> str:
    return (settings.admin_panel_header_name or "x-admin-token").strip() or "x-admin-token"


def _admin_token(settings: Settings) -> str:
    return (settings.admin_panel_token or "").strip()


def _admin_username(settings: Settings) -> str:
    return (settings.admin_panel_username or "").strip()


def _admin_password(settings: Settings) -> str:
    return (settings.admin_panel_password or "").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_parts(model: str) -> tuple[str, str]:
    value = str(model or "").strip()
    if not value:
        return "unknown", "unknown"
    first = value.split("|", 1)[0].strip()
    if "/" in first:
        provider, model_name = first.split("/", 1)
        return provider.strip().lower() or "unknown", model_name.strip() or first
    return "unknown", first


def _event_level(status: str, error_code: str) -> str:
    st = str(status or "").strip().lower()
    if st == "ok":
        return "INFO"
    err = str(error_code or "").strip().lower()
    if err in {"invalid_api_key", "auth_error", "unexpected_error", "network_error", "bale_api_error"}:
        return "ERROR"
    return "WARNING"


def _event_message(item: dict[str, Any]) -> str:
    event_type = str(item.get("event_type") or "event")
    status = str(item.get("status") or "")
    error_code = str(item.get("error_code") or "")
    if status == "ok":
        return f"{event_type} completed"
    if error_code:
        return f"{event_type} failed ({error_code})"
    return f"{event_type} failed"


def _extract_request_id(item: dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    if isinstance(details, dict):
        rid = str(details.get("request_id") or "").strip()
        if rid:
            return rid
        msg_id = str(details.get("message_id") or "").strip()
        upd = str(details.get("update_id") or "").strip()
        if msg_id and upd:
            return f"u{upd}-m{msg_id}"
        if msg_id:
            return f"m{msg_id}"
    return ""


def _usage_row(group: str) -> dict[str, Any]:
    return {
        "group": group,
        "requests_total": 0,
        "success_total": 0,
        "error_total": 0,
        "status_429": 0,
        "latency_avg_ms": 0.0,
        "latency_p95_ms": 0.0,
        "tokens_total": 0,
        "last_error": "",
    }


async def require_admin_auth(
    request: Request,
    token_header: str | None = Header(default=None),
) -> None:
    ctx = _ctx(request)
    settings = ctx.settings

    if not settings.admin_panel_enabled:
        raise HTTPException(status_code=404, detail="admin panel disabled")

    expected = _admin_token(settings)
    if not expected:
        raise HTTPException(status_code=503, detail="admin panel token is not configured")

    header_name = _admin_header_name(settings)
    provided = request.headers.get(header_name) or token_header
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")


async def _fetch_uag_json(base_url: str, path: str, headers: dict[str, str], timeout_sec: float) -> dict[str, Any]:
    timeout = httpx.Timeout(connect=3.0, read=timeout_sec, write=timeout_sec, pool=timeout_sec)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.get(f"{base_url}{path}", headers=headers)
        raw = response.text
        try:
            parsed = response.json()
        except Exception:  # noqa: BLE001
            parsed = {"raw": raw}

        return {
            "status_code": response.status_code,
            "payload": parsed,
        }


async def _uag_snapshot(settings: Settings, since_minutes: int) -> dict[str, Any]:
    if not settings.admin_panel_uag_enabled:
        return {"enabled": False, "reachable": False, "base_url": settings.uag_base_url}

    base_url = settings.uag_base_url.rstrip("/")
    token = (os.getenv("UAG_ADMIN_TOKEN") or "").strip()
    header_name = (os.getenv("UAG_ADMIN_HEADER_NAME") or "x-admin-token").strip() or "x-admin-token"
    headers = {header_name: token} if token else {}

    try:
        health = await _fetch_uag_json(base_url, "/health", headers, settings.admin_panel_uag_timeout_sec)
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": True,
            "reachable": False,
            "base_url": base_url,
            "error": str(exc),
        }

    reachable = int(health.get("status_code") or 0) == 200
    out: dict[str, Any] = {
        "enabled": True,
        "reachable": reachable,
        "base_url": base_url,
        "health": health,
    }

    if not token:
        out["warning"] = "UAG_ADMIN_TOKEN is empty; only /health may be visible"
        return out

    try:
        out["router_stats"] = await _fetch_uag_json(base_url, "/admin/router/stats", headers, settings.admin_panel_uag_timeout_sec)
        out["usage_overview"] = await _fetch_uag_json(base_url, "/admin/usage/overview", headers, settings.admin_panel_uag_timeout_sec)
        out["usage_models"] = await _fetch_uag_json(
            base_url,
            f"/admin/usage/models?since_minutes={max(5, since_minutes)}",
            headers,
            settings.admin_panel_uag_timeout_sec,
        )
        out["usage_providers"] = await _fetch_uag_json(
            base_url,
            f"/admin/usage/providers?since_minutes={max(5, since_minutes)}",
            headers,
            settings.admin_panel_uag_timeout_sec,
        )
        out["logs_summary"] = await _fetch_uag_json(
            base_url,
            f"/admin/logs/summary?since_minutes={max(5, since_minutes)}",
            headers,
            settings.admin_panel_uag_timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)

    return out


async def _build_usage(ctx: AdminState, since_minutes: int) -> dict[str, Any]:
    models = await ctx.storage.admin_model_usage(since_minutes=since_minutes)
    overview = await ctx.storage.admin_overview(since_minutes=since_minutes)

    chat_rows = list(models.get("chat") or [])
    img_rows = list(models.get("image_generation") or [])
    voice_rows = list(models.get("voice") or [])

    by_provider: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    by_feature: dict[str, dict[str, Any]] = {}
    by_key: dict[str, dict[str, Any]] = {}

    for row in chat_rows:
        provider, model_name = _model_parts(str(row.get("model") or ""))
        count = int(row.get("count") or 0)
        ok_count = int(row.get("ok_count") or 0)

        p_item = by_provider.setdefault(provider, _usage_row(provider))
        p_item["requests_total"] += count
        p_item["success_total"] += ok_count
        p_item["error_total"] += max(0, count - ok_count)

        m_key = f"{provider}/{model_name}"
        m_item = by_model.setdefault(m_key, _usage_row(m_key))
        m_item["requests_total"] += count
        m_item["success_total"] += ok_count
        m_item["error_total"] += max(0, count - ok_count)

        f_item = by_feature.setdefault("chat", _usage_row("chat"))
        f_item["requests_total"] += count
        f_item["success_total"] += ok_count
        f_item["error_total"] += max(0, count - ok_count)

        k_item = by_key.setdefault("app-main", _usage_row("app-main"))
        k_item["requests_total"] += count
        k_item["success_total"] += ok_count
        k_item["error_total"] += max(0, count - ok_count)

    for row in img_rows:
        provider = str(row.get("provider") or "unknown").strip().lower() or "unknown"
        model_name = str(row.get("model") or "unknown").strip() or "unknown"
        count = int(row.get("count") or 0)

        p_item = by_provider.setdefault(provider, _usage_row(provider))
        p_item["requests_total"] += count
        p_item["success_total"] += count

        m_key = f"{provider}/{model_name}"
        m_item = by_model.setdefault(m_key, _usage_row(m_key))
        m_item["requests_total"] += count
        m_item["success_total"] += count

        f_item = by_feature.setdefault("image_generation", _usage_row("image_generation"))
        f_item["requests_total"] += count
        f_item["success_total"] += count

    for row in voice_rows:
        mode = str(row.get("mode") or "voice").strip() or "voice"
        count = int(row.get("count") or 0)
        f_item = by_feature.setdefault(mode, _usage_row(mode))
        f_item["requests_total"] += count
        f_item["success_total"] += count

    feature_usage = list(overview.get("feature_usage") or [])
    latency_by_event: dict[str, list[float]] = {}
    for item in feature_usage:
        evt = str(item.get("event_type") or "")
        lat = float(item.get("avg_latency_ms") or 0.0)
        latency_by_event.setdefault(evt, []).append(lat)

    def finalize(items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in items.values():
            req = int(item["requests_total"])
            succ = int(item["success_total"])
            err = int(item["error_total"])
            item["success_rate"] = float(succ / req) if req > 0 else 0.0
            out.append(item)
        out.sort(key=lambda x: int(x["requests_total"]), reverse=True)
        return out

    provider_rows = finalize(by_provider)
    model_rows = finalize(by_model)
    feature_rows = finalize(by_feature)
    key_rows = finalize(by_key)

    overall_requests = int(sum(int(x.get("requests_total") or 0) for x in provider_rows))
    overall_success = int(sum(int(x.get("success_total") or 0) for x in provider_rows))
    overall_errors = int(sum(int(x.get("error_total") or 0) for x in provider_rows))

    usage_overview = {
        "overall": {
            "requests_total": overall_requests,
            "success_total": overall_success,
            "error_total": overall_errors,
            "success_rate": float(overall_success / overall_requests) if overall_requests > 0 else 0.0,
            "latency_avg_ms": float(overview.get("latency", {}).get("window_avg_ms") or 0.0),
            "latency_p95_ms": float(overview.get("latency", {}).get("window_p95_ms") or 0.0),
            "status_429": int(
                sum(int(x.get("count") or 0) for x in (overview.get("errors") or []) if str(x.get("error_code") or "") == "rate_limited")
            ),
            "tokens_total": 0,
        },
        "providers_count": len(provider_rows),
        "models_count": len(model_rows),
        "window_minutes": since_minutes,
    }

    return {
        "overview": usage_overview,
        "providers": {"items": provider_rows, "window_minutes": since_minutes},
        "models": {"items": model_rows, "window_minutes": since_minutes},
        "features": {"items": feature_rows, "window_minutes": since_minutes},
        "keys": {"items": key_rows, "window_minutes": since_minutes},
    }


async def _build_events(
    ctx: AdminState,
    *,
    since_minutes: int,
    limit: int,
    level: str = "",
    event_type: str = "",
    request_id: str = "",
    search: str = "",
    user_id: str = "",
) -> dict[str, Any]:
    rows = await ctx.storage.admin_recent_events(
        since_minutes=since_minutes,
        limit=max(50, min(2000, int(limit))),
        event_type=event_type,
        status="",
        user_id=user_id,
    )

    norm_items: list[dict[str, Any]] = []
    search_l = str(search or "").strip().lower()
    level_u = str(level or "").strip().upper()
    req_filter = str(request_id or "").strip()

    for row in rows:
        lvl = _event_level(str(row.get("status") or ""), str(row.get("error_code") or ""))
        rid = _extract_request_id(row)
        item = {
            "seq": int(row.get("id") or 0),
            "ts": str(row.get("created_at") or ""),
            "level": lvl,
            "event_type": str(row.get("event_type") or ""),
            "request_id": rid,
            "message": _event_message(row),
            "data": {
                "user_id": str(row.get("user_id") or ""),
                "chat_id": str(row.get("chat_id") or ""),
                "content_type": str(row.get("content_type") or ""),
                "status": str(row.get("status") or ""),
                "error_code": str(row.get("error_code") or ""),
                "latency_ms": float(row.get("latency_ms") or 0.0),
                "details": row.get("details") if isinstance(row.get("details"), dict) else {},
            },
        }

        if level_u and str(item["level"]).upper() != level_u:
            continue
        if req_filter and str(item["request_id"]) != req_filter:
            continue
        if search_l:
            haystack = f"{item['message']} {item['event_type']} {item['request_id']} {item['data']}".lower()
            if search_l not in haystack:
                continue

        norm_items.append(item)

    norm_items.sort(key=lambda x: int(x.get("seq") or 0), reverse=True)
    return {
        "window_minutes": since_minutes,
        "count": len(norm_items),
        "items": norm_items[: max(1, min(2000, limit))],
    }


async def _build_http_stats(ctx: AdminState, *, since_minutes: int, group_by: str = "path") -> dict[str, Any]:
    events_payload = await _build_events(ctx, since_minutes=since_minutes, limit=1000)
    items = list(events_payload.get("items") or [])

    grouped: dict[str, dict[str, Any]] = {}
    for row in items:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        event_type = str(row.get("event_type") or "unknown")
        status = str(data.get("status") or "")
        lat = float(data.get("latency_ms") or 0.0)

        if group_by == "method":
            key = "bot"
        elif group_by == "status_code":
            key = "200" if status == "ok" else "500"
        elif group_by == "status_class":
            key = "2xx" if status == "ok" else "5xx"
        elif group_by == "path_method":
            key = f"{event_type}:bot"
        else:
            key = event_type

        box = grouped.setdefault(
            key,
            {
                "group": key,
                "requests_total": 0,
                "success_total": 0,
                "error_total": 0,
                "status_2xx": 0,
                "status_3xx": 0,
                "status_4xx": 0,
                "status_5xx": 0,
                "latencies": [],
            },
        )
        box["requests_total"] += 1
        box["latencies"].append(lat)
        if status == "ok":
            box["success_total"] += 1
            box["status_2xx"] += 1
        else:
            box["error_total"] += 1
            box["status_5xx"] += 1

    out_items: list[dict[str, Any]] = []
    for box in grouped.values():
        latencies = sorted([float(x) for x in box.get("latencies") or []])
        p95 = 0.0
        if latencies:
            p95 = latencies[int(round(0.95 * (len(latencies) - 1)))]
        req = int(box["requests_total"])
        succ = int(box["success_total"])
        out_items.append(
            {
                "group": str(box["group"]),
                "requests_total": req,
                "success_total": succ,
                "error_total": int(box["error_total"]),
                "success_rate": float(succ / req) if req > 0 else 0.0,
                "status_2xx": int(box["status_2xx"]),
                "status_3xx": int(box["status_3xx"]),
                "status_4xx": int(box["status_4xx"]),
                "status_5xx": int(box["status_5xx"]),
                "latency_avg_ms": float(sum(latencies) / len(latencies)) if latencies else 0.0,
                "latency_p95_ms": float(p95),
            }
        )

    out_items.sort(key=lambda x: int(x.get("requests_total") or 0), reverse=True)
    return {
        "window_minutes": since_minutes,
        "group_by": group_by,
        "count": len(out_items),
        "items": out_items,
    }


async def _build_router_scores(ctx: AdminState, *, since_minutes: int) -> dict[str, Any]:
    models = await ctx.storage.admin_model_usage(since_minutes=since_minutes)
    chat_rows = list(models.get("chat") or [])

    items: list[dict[str, Any]] = []
    for row in chat_rows:
        provider, model_name = _model_parts(str(row.get("model") or ""))
        total_calls = int(row.get("count") or 0)
        ok_count = int(row.get("ok_count") or 0)
        failures = max(0, total_calls - ok_count)
        items.append(
            {
                "provider": provider,
                "model": model_name,
                "total_calls": total_calls,
                "failures": failures,
                "rate_limited": 0,
                "avg_latency_ms": 0.0,
            }
        )

    items.sort(key=lambda x: int(x.get("total_calls") or 0), reverse=True)
    return {
        "window_minutes": since_minutes,
        "count": len(items),
        "items": items,
    }


async def _build_logs_summary(ctx: AdminState, *, since_minutes: int) -> dict[str, Any]:
    overview = await ctx.storage.admin_overview(since_minutes=since_minutes)
    usage = await _build_usage(ctx, since_minutes=since_minutes)
    http_stats = await _build_http_stats(ctx, since_minutes=since_minutes, group_by="path")
    router_scores = await _build_router_scores(ctx, since_minutes=since_minutes)

    by_level: dict[str, int] = {"INFO": 0, "WARNING": 0, "ERROR": 0}
    by_type: dict[str, int] = {}
    for row in list(overview.get("feature_usage") or []):
        event_type = str(row.get("event_type") or "unknown")
        status = str(row.get("status") or "")
        count = int(row.get("count") or 0)
        level = _event_level(status, "")
        by_level[level] = by_level.get(level, 0) + count
        by_type[event_type] = by_type.get(event_type, 0) + count

    http_items = list(http_stats.get("items") or [])
    req_total = int(sum(int(x.get("requests_total") or 0) for x in http_items))
    succ_total = int(sum(int(x.get("success_total") or 0) for x in http_items))
    err_total = int(sum(int(x.get("error_total") or 0) for x in http_items))
    lat_values = [float(x.get("latency_avg_ms") or 0.0) for x in http_items if float(x.get("latency_avg_ms") or 0.0) > 0]

    http_summary = {
        "requests_total": req_total,
        "success_total": succ_total,
        "error_total": err_total,
        "status_2xx": succ_total,
        "status_3xx": 0,
        "status_4xx": 0,
        "status_5xx": err_total,
        "success_rate": float(succ_total / req_total) if req_total > 0 else 0.0,
        "latency_avg_ms": float(sum(lat_values) / len(lat_values)) if lat_values else 0.0,
    }

    return {
        "generated_at": _now_iso(),
        "window_minutes": since_minutes,
        "events": {
            "total": int(overview.get("events", {}).get("window_total") or 0),
            "failed": int(overview.get("events", {}).get("window_failed") or 0),
            "by_level": by_level,
            "by_type": by_type,
            "latency_avg_ms": float(overview.get("latency", {}).get("window_avg_ms") or 0.0),
            "latency_p95_ms": float(overview.get("latency", {}).get("window_p95_ms") or 0.0),
            "http": http_summary,
        },
        "http": http_summary,
        "usage_overview": usage.get("overview"),
        "usage_by_provider": usage.get("providers"),
        "usage_by_model": usage.get("models"),
        "usage_by_key": usage.get("keys"),
        "router_scores": router_scores,
        "overview": overview,
    }


async def _build_project_overview(ctx: AdminState, *, since_minutes: int, days: int) -> dict[str, Any]:
    overview = await ctx.storage.admin_overview(since_minutes=since_minutes)
    daily = await ctx.storage.admin_daily_series(days=days)
    top_users = await ctx.storage.admin_top_users(since_minutes=since_minutes, limit=20)
    model_usage = await ctx.storage.admin_model_usage(since_minutes=since_minutes)
    user_model_usage = await ctx.storage.admin_user_model_usage(since_minutes=since_minutes, limit=400)
    feature_timeseries = await ctx.storage.admin_feature_timeseries(since_minutes=since_minutes, bucket="hour")
    model_timeseries = await ctx.storage.admin_model_timeseries(since_minutes=since_minutes, bucket="hour")

    return {
        "generated_at": _now_iso(),
        "window_minutes": since_minutes,
        "days": days,
        "overview": overview,
        "daily": daily,
        "top_users": top_users,
        "model_usage": model_usage,
        "user_model_usage": user_model_usage,
        "feature_timeseries": feature_timeseries,
        "model_timeseries": model_timeseries,
    }


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    try:
        await ctx.storage.analytics_snapshot(since_hours=1)
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "degraded",
            "db_ok": False,
            "db_error": str(exc),
            "admin_enabled": ctx.settings.admin_panel_enabled,
        }

    return {
        "status": "ok",
        "db_ok": db_ok,
        "admin_enabled": ctx.settings.admin_panel_enabled,
    }


@app.get("/")
async def index_redirect() -> Response:
    return RedirectResponse(url="/admin", status_code=307)


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def admin_panel_index() -> Response:
    return FileResponse(ADMIN_UI_ROOT / "index.html")


@app.post("/admin/api/login")
async def admin_login(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    settings = ctx.settings
    if not settings.admin_panel_enabled:
        raise HTTPException(status_code=404, detail="admin panel disabled")

    expected_username = _admin_username(settings)
    expected_password = _admin_password(settings)
    if not expected_username or not expected_password:
        raise HTTPException(status_code=503, detail="admin username/password is not configured")

    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc

    username = str((body or {}).get("username") or "").strip()
    password = str((body or {}).get("password") or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")

    if not (secrets.compare_digest(username, expected_username) and secrets.compare_digest(password, expected_password)):
        raise HTTPException(status_code=401, detail="invalid username or password")

    token = _admin_token(settings)
    if not token:
        raise HTTPException(status_code=503, detail="admin panel token is not configured")

    return {
        "ok": True,
        "token": token,
        "header_name": _admin_header_name(settings),
        "username": expected_username,
    }


@app.get("/admin/logs/summary", dependencies=[Depends(require_admin_auth)])
async def admin_logs_summary(
    request: Request,
    since_minutes: int = Query(default=60, ge=5, le=10080),
) -> dict[str, Any]:
    ctx = _ctx(request)
    return await _build_logs_summary(ctx, since_minutes=since_minutes)


@app.get("/admin/analytics/overview", dependencies=[Depends(require_admin_auth)])
async def admin_analytics_overview(
    request: Request,
    since_minutes: int = Query(default=1440, ge=5, le=10080),
    days: int = Query(default=30, ge=1, le=120),
) -> dict[str, Any]:
    ctx = _ctx(request)
    return await _build_project_overview(ctx, since_minutes=since_minutes, days=days)


@app.get("/admin/analytics/users", dependencies=[Depends(require_admin_auth)])
async def admin_analytics_users(
    request: Request,
    since_minutes: int = Query(default=1440, ge=5, le=10080),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    ctx = _ctx(request)
    top_users = await ctx.storage.admin_top_users(since_minutes=since_minutes, limit=limit)
    user_models = await ctx.storage.admin_user_model_usage(since_minutes=since_minutes, limit=max(200, limit * 4))
    return {
        "window_minutes": since_minutes,
        "top_users": top_users,
        "user_model_usage": user_models,
    }


@app.get("/admin/analytics/models", dependencies=[Depends(require_admin_auth)])
async def admin_analytics_models(
    request: Request,
    since_minutes: int = Query(default=1440, ge=5, le=10080),
) -> dict[str, Any]:
    ctx = _ctx(request)
    models = await ctx.storage.admin_model_usage(since_minutes=since_minutes)
    model_ts = await ctx.storage.admin_model_timeseries(since_minutes=since_minutes, bucket="hour")
    return {
        "window_minutes": since_minutes,
        "models": models,
        "model_timeseries": model_ts,
    }


@app.get("/admin/analytics/features", dependencies=[Depends(require_admin_auth)])
async def admin_analytics_features(
    request: Request,
    since_minutes: int = Query(default=1440, ge=5, le=10080),
) -> dict[str, Any]:
    ctx = _ctx(request)
    overview = await ctx.storage.admin_overview(since_minutes=since_minutes)
    feature_ts = await ctx.storage.admin_feature_timeseries(since_minutes=since_minutes, bucket="hour")
    return {
        "window_minutes": since_minutes,
        "feature_usage": overview.get("feature_usage", []),
        "feature_timeseries": feature_ts,
        "content_usage": overview.get("content_usage", []),
        "errors": overview.get("errors", []),
    }


@app.get("/admin/analytics/timeseries/features", dependencies=[Depends(require_admin_auth)])
async def admin_analytics_timeseries_features(
    request: Request,
    since_minutes: int = Query(default=10080, ge=5, le=525600),
    bucket: str = Query(default="hour"),
    user_id: str = Query(default=""),
    event_type: str = Query(default=""),
) -> dict[str, Any]:
    ctx = _ctx(request)
    items = await ctx.storage.admin_feature_timeseries(
        since_minutes=since_minutes,
        bucket=bucket,
        user_id=user_id.strip(),
        event_type=event_type.strip(),
    )
    return {
        "window_minutes": since_minutes,
        "bucket": "day" if str(bucket).strip().lower() == "day" else "hour",
        "count": len(items),
        "items": items,
    }


@app.get("/admin/analytics/timeseries/models", dependencies=[Depends(require_admin_auth)])
async def admin_analytics_timeseries_models(
    request: Request,
    since_minutes: int = Query(default=10080, ge=5, le=525600),
    bucket: str = Query(default="hour"),
    user_id: str = Query(default=""),
    model: str = Query(default=""),
) -> dict[str, Any]:
    ctx = _ctx(request)
    items = await ctx.storage.admin_model_timeseries(
        since_minutes=since_minutes,
        bucket=bucket,
        user_id=user_id.strip(),
        model=model.strip(),
    )
    return {
        "window_minutes": since_minutes,
        "bucket": "day" if str(bucket).strip().lower() == "day" else "hour",
        "count": len(items),
        "items": items,
    }


@app.get("/admin/analytics/timeseries/users", dependencies=[Depends(require_admin_auth)])
async def admin_analytics_timeseries_users(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    ctx = _ctx(request)
    items = await ctx.storage.admin_daily_series(days=days)
    return {
        "days": days,
        "count": len(items),
        "items": items,
    }


@app.get("/admin/analytics/user/{user_id}", dependencies=[Depends(require_admin_auth)])
async def admin_analytics_user_detail(
    request: Request,
    user_id: str,
    since_minutes: int = Query(default=10080, ge=5, le=525600),
) -> dict[str, Any]:
    ctx = _ctx(request)
    uid = user_id.strip()
    if not uid:
        raise HTTPException(status_code=400, detail="user_id is required")

    feature_ts = await ctx.storage.admin_feature_timeseries(
        since_minutes=since_minutes,
        bucket="hour",
        user_id=uid,
    )
    model_ts = await ctx.storage.admin_model_timeseries(
        since_minutes=since_minutes,
        bucket="hour",
        user_id=uid,
    )
    model_usage = await ctx.storage.admin_user_model_usage(
        since_minutes=since_minutes,
        limit=500,
        user_id=uid,
    )
    events = await ctx.storage.admin_recent_events(
        since_minutes=since_minutes,
        limit=500,
        user_id=uid,
    )
    return {
        "user_id": uid,
        "window_minutes": since_minutes,
        "feature_timeseries": feature_ts,
        "model_timeseries": model_ts,
        "model_usage": model_usage,
        "events": events,
    }


@app.get("/admin/logs/events", dependencies=[Depends(require_admin_auth)])
async def admin_logs_events(
    request: Request,
    since_minutes: int = Query(default=60, ge=5, le=10080),
    level: str = Query(default=""),
    event_type: str = Query(default=""),
    request_id: str = Query(default=""),
    search: str = Query(default=""),
    user_id: str = Query(default=""),
    limit: int = Query(default=300, ge=50, le=2000),
) -> dict[str, Any]:
    ctx = _ctx(request)
    return await _build_events(
        ctx,
        since_minutes=since_minutes,
        limit=limit,
        level=level,
        event_type=event_type,
        request_id=request_id,
        search=search,
        user_id=user_id,
    )


@app.get("/admin/logs/http-stats", dependencies=[Depends(require_admin_auth)])
async def admin_logs_http_stats(
    request: Request,
    since_minutes: int = Query(default=60, ge=5, le=10080),
    group_by: str = Query(default="path"),
) -> dict[str, Any]:
    ctx = _ctx(request)
    return await _build_http_stats(ctx, since_minutes=since_minutes, group_by=group_by)


@app.get("/admin/usage/overview", dependencies=[Depends(require_admin_auth)])
async def admin_usage_overview(
    request: Request,
    since_minutes: int = Query(default=60, ge=5, le=10080),
) -> dict[str, Any]:
    ctx = _ctx(request)
    usage = await _build_usage(ctx, since_minutes=since_minutes)
    return usage.get("overview") or {}


@app.get("/admin/usage/providers", dependencies=[Depends(require_admin_auth)])
async def admin_usage_providers(
    request: Request,
    since_minutes: int = Query(default=60, ge=5, le=10080),
) -> dict[str, Any]:
    ctx = _ctx(request)
    usage = await _build_usage(ctx, since_minutes=since_minutes)
    return usage.get("providers") or {"items": []}


@app.get("/admin/usage/models", dependencies=[Depends(require_admin_auth)])
async def admin_usage_models(
    request: Request,
    since_minutes: int = Query(default=60, ge=5, le=10080),
) -> dict[str, Any]:
    ctx = _ctx(request)
    usage = await _build_usage(ctx, since_minutes=since_minutes)
    return usage.get("models") or {"items": []}


@app.get("/admin/usage/keys", dependencies=[Depends(require_admin_auth)])
async def admin_usage_keys(
    request: Request,
    since_minutes: int = Query(default=60, ge=5, le=10080),
) -> dict[str, Any]:
    ctx = _ctx(request)
    usage = await _build_usage(ctx, since_minutes=since_minutes)
    return usage.get("keys") or {"items": []}


@app.get("/admin/usage/aggregate", dependencies=[Depends(require_admin_auth)])
async def admin_usage_aggregate(
    request: Request,
    group_by: str = Query(default="provider"),
    since_minutes: int = Query(default=60, ge=5, le=10080),
    provider: str = Query(default=""),
    model: str = Query(default=""),
    capability: str = Query(default=""),
) -> dict[str, Any]:
    del provider
    del model
    ctx = _ctx(request)
    usage = await _build_usage(ctx, since_minutes=since_minutes)

    source = "providers"
    if group_by in {"model", "provider_model"}:
        source = "models"
    elif group_by in {"key", "provider_model_key"}:
        source = "keys"
    elif group_by in {"capability", "feature"}:
        source = "features"

    out = dict(usage.get(source) or {"items": []})
    if capability:
        cap = capability.strip().lower()
        out["items"] = [x for x in list(out.get("items") or []) if cap in str(x.get("group") or "").lower()]
    out["group_by"] = group_by
    return out


@app.get("/admin/usage/events", dependencies=[Depends(require_admin_auth)])
async def admin_usage_events(
    request: Request,
    since_minutes: int = Query(default=60, ge=5, le=10080),
    provider: str = Query(default=""),
    model: str = Query(default=""),
    capability: str = Query(default=""),
    limit: int = Query(default=200, ge=50, le=2000),
) -> dict[str, Any]:
    del provider
    del model
    ctx = _ctx(request)
    events_payload = await _build_events(ctx, since_minutes=since_minutes, limit=limit)
    items = list(events_payload.get("items") or [])
    cap = capability.strip().lower()
    if cap:
        items = [x for x in items if cap in str(x.get("event_type") or "").lower()]

    out_items: list[dict[str, Any]] = []
    for item in items:
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        out_items.append(
            {
                "ts": item.get("ts"),
                "provider": "bot",
                "model": "",
                "capability": str(item.get("event_type") or ""),
                "key": {"id": "app-main"},
                "ok": str(data.get("status") or "") == "ok",
                "status_code": 200 if str(data.get("status") or "") == "ok" else 500,
                "latency_ms": float(data.get("latency_ms") or 0.0),
                "tokens": {"total_tokens": 0},
                "error": str(data.get("error_code") or ""),
            }
        )

    return {
        "window_minutes": since_minutes,
        "count": len(out_items),
        "items": out_items,
    }


@app.get("/admin/usage/key-limits/latest", dependencies=[Depends(require_admin_auth)])
async def admin_usage_key_limits_latest(
    request: Request,
    provider: str = Query(default=""),
) -> dict[str, Any]:
    del provider
    return {
        "count": 1,
        "items": [
            {
                "captured_at": _now_iso(),
                "provider": "bot",
                "model": "",
                "key_id": "app-main",
                "status_code": 200,
                "headers": {},
                "details": {"note": "No provider key-limit headers are available in bot DB; use UAG panel for key-level headers."},
            }
        ],
    }


@app.get("/admin/router/stats", dependencies=[Depends(require_admin_auth)])
async def admin_router_stats(
    request: Request,
    since_minutes: int = Query(default=60, ge=5, le=10080),
) -> dict[str, Any]:
    ctx = _ctx(request)
    return await _build_router_scores(ctx, since_minutes=since_minutes)


@app.get("/admin/api/summary", dependencies=[Depends(require_admin_auth)])
async def admin_summary(
    request: Request,
    since_minutes: int = Query(default=1440, ge=5, le=10080),
    days: int = Query(default=30, ge=1, le=120),
    user_limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    ctx = _ctx(request)
    overview = await ctx.storage.admin_overview(since_minutes=since_minutes)
    daily = await ctx.storage.admin_daily_series(days=days)
    users = await ctx.storage.admin_top_users(since_minutes=since_minutes, limit=user_limit)
    models = await ctx.storage.admin_model_usage(since_minutes=since_minutes)
    events = await ctx.storage.admin_recent_events(since_minutes=since_minutes, limit=200)
    uag = await _uag_snapshot(ctx.settings, since_minutes=since_minutes)

    return {
        "generated_at": _now_iso(),
        "window_minutes": since_minutes,
        "days": days,
        "overview": overview,
        "daily": daily,
        "users": users,
        "models": models,
        "recent_events": events,
        "uag": uag,
    }


@app.get("/admin/api/overview", dependencies=[Depends(require_admin_auth)])
async def admin_overview(request: Request, since_minutes: int = Query(default=1440, ge=5, le=10080)) -> dict[str, Any]:
    ctx = _ctx(request)
    return await ctx.storage.admin_overview(since_minutes=since_minutes)


@app.get("/admin/api/daily", dependencies=[Depends(require_admin_auth)])
async def admin_daily(request: Request, days: int = Query(default=30, ge=1, le=120)) -> list[dict[str, Any]]:
    ctx = _ctx(request)
    return await ctx.storage.admin_daily_series(days=days)


@app.get("/admin/api/models", dependencies=[Depends(require_admin_auth)])
async def admin_models(request: Request, since_minutes: int = Query(default=1440, ge=5, le=10080)) -> dict[str, Any]:
    ctx = _ctx(request)
    data = await ctx.storage.admin_model_usage(since_minutes=since_minutes)
    data["window_minutes"] = since_minutes
    return data


@app.get("/admin/api/users", dependencies=[Depends(require_admin_auth)])
async def admin_users(
    request: Request,
    since_minutes: int = Query(default=1440, ge=5, le=10080),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    ctx = _ctx(request)
    rows = await ctx.storage.admin_top_users(since_minutes=since_minutes, limit=limit)
    return {
        "window_minutes": since_minutes,
        "count": len(rows),
        "items": rows,
    }


@app.get("/admin/api/events", dependencies=[Depends(require_admin_auth)])
async def admin_events(
    request: Request,
    since_minutes: int = Query(default=1440, ge=5, le=10080),
    limit: int = Query(default=300, ge=1, le=2000),
    event_type: str = Query(default=""),
    status: str = Query(default=""),
    user_id: str = Query(default=""),
) -> dict[str, Any]:
    ctx = _ctx(request)
    rows = await ctx.storage.admin_recent_events(
        since_minutes=since_minutes,
        limit=limit,
        event_type=event_type,
        status=status,
        user_id=user_id,
    )
    return {
        "window_minutes": since_minutes,
        "count": len(rows),
        "items": rows,
    }


@app.get("/admin/api/uag", dependencies=[Depends(require_admin_auth)])
async def admin_uag(request: Request, since_minutes: int = Query(default=1440, ge=5, le=10080)) -> dict[str, Any]:
    ctx = _ctx(request)
    return await _uag_snapshot(ctx.settings, since_minutes=since_minutes)


@app.get("/admin/api/export", dependencies=[Depends(require_admin_auth)])
async def admin_export(request: Request, since_minutes: int = Query(default=1440, ge=5, le=10080)) -> Response:
    ctx = _ctx(request)
    overview = await ctx.storage.admin_overview(since_minutes=since_minutes)
    models = await ctx.storage.admin_model_usage(since_minutes=since_minutes)
    users = await ctx.storage.admin_top_users(since_minutes=since_minutes, limit=500)
    events = await ctx.storage.admin_recent_events(since_minutes=since_minutes, limit=2000)
    payload = {
        "window_minutes": since_minutes,
        "overview": overview,
        "models": models,
        "users": users,
        "events": events,
    }
    return JSONResponse(payload)


@app.websocket("/ws/admin")
async def ws_admin(websocket: WebSocket):
    await websocket.accept()
    ctx = _ctx_ws(websocket)
    settings = ctx.settings

    token = str(websocket.query_params.get("token") or "").strip()
    expected = _admin_token(settings)
    if not expected or token != expected:
        await websocket.send_json({"type": "error", "error": "unauthorized"})
        await websocket.close(code=4401)
        return

    filters = WsFilters(
        since_minutes=int(websocket.query_params.get("since_minutes") or 60),
        limit=int(websocket.query_params.get("limit") or 300),
    )

    async def build_ws_payload(kind: str) -> dict[str, Any]:
        summary = await _build_logs_summary(ctx, since_minutes=filters.since_minutes)
        project = await _build_project_overview(ctx, since_minutes=filters.since_minutes, days=30)
        events = await _build_events(
            ctx,
            since_minutes=filters.since_minutes,
            limit=filters.limit,
            level=filters.level,
            event_type=filters.event_type,
            request_id=filters.request_id,
            search=filters.search,
            user_id=filters.user_id,
        )
        usage = await _build_usage(ctx, since_minutes=filters.since_minutes)
        http_stats = await _build_http_stats(ctx, since_minutes=filters.since_minutes, group_by="path")
        router_scores = await _build_router_scores(ctx, since_minutes=filters.since_minutes)

        return {
            "type": kind,
            "server_time": _now_iso(),
            "summary": summary.get("events", {}),
            "usage_overview": usage.get("overview", {}),
            "usage_by_provider": usage.get("providers", {}),
            "usage_by_model": usage.get("models", {}),
            "usage_by_key": usage.get("keys", {}),
            "http": http_stats,
            "router_scores": router_scores,
            "events": events,
            "project_overview": project,
            "project_users": {
                "top_users": project.get("top_users", []),
                "user_model_usage": project.get("user_model_usage", []),
            },
            "project_models": {
                "model_usage": project.get("model_usage", {}),
                "model_timeseries": project.get("model_timeseries", []),
            },
            "project_features": {
                "feature_timeseries": project.get("feature_timeseries", []),
                "feature_usage": project.get("overview", {}).get("feature_usage", []),
            },
        }

    async def sender_loop() -> None:
        while True:
            payload = await build_ws_payload("tick")
            await websocket.send_json(payload)
            await asyncio.sleep(5.0)

    sender = asyncio.create_task(sender_loop())
    try:
        hello = await build_ws_payload("hello")
        await websocket.send_json(hello)

        while True:
            incoming = await websocket.receive_json()
            if not isinstance(incoming, dict):
                continue
            mtype = str(incoming.get("type") or "").strip().lower()
            if mtype == "filters":
                filters.since_minutes = max(5, int(incoming.get("since_minutes") or filters.since_minutes))
                filters.limit = max(50, min(2000, int(incoming.get("limit") or filters.limit)))
                filters.level = str(incoming.get("level") or "").strip().upper()
                filters.event_type = str(incoming.get("event_type") or "").strip()
                filters.request_id = str(incoming.get("request_id_filter") or "").strip()
                filters.search = str(incoming.get("search") or "").strip()
                filters.user_id = str(incoming.get("user_id") or "").strip()
                await websocket.send_json(await build_ws_payload("tick"))
            elif mtype == "ping":
                await websocket.send_json({"type": "pong", "server_time": _now_iso()})
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.send_json({"type": "error", "error": "websocket failure"})
        except Exception:
            pass
    finally:
        sender.cancel()
        with contextlib.suppress(Exception):
            await sender


@app.get("/admin/{asset_path:path}", include_in_schema=False)
async def admin_panel_assets(asset_path: str) -> Response:
    sanitized = (asset_path or "").strip().lstrip("/")
    if not sanitized:
        return FileResponse(ADMIN_UI_ROOT / "index.html")

    target = (ADMIN_UI_ROOT / sanitized).resolve()
    if not str(target).startswith(str(ADMIN_UI_ROOT.resolve())):
        raise HTTPException(status_code=404, detail="asset not found")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(target)
