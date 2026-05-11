from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_BALE_ALLOWED_UPDATES = ["message", "callback_query"]


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None else value


def _csv(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(slots=True)
class Settings:
    app_env: str
    log_level: str
    log_flow_enabled: bool
    log_http_enabled: bool
    log_text_preview_chars: int
    log_http_body_preview_chars: int

    bale_bot_token: str
    bale_api_base_url: str
    bale_file_base_url: str
    bale_poll_timeout_sec: int
    bale_allowed_updates: list[str]

    gemini_proxy_base_url: str
    gemini_proxy_endpoint: str
    gemini_proxy_timeout_sec: int
    gemini_default_model: str
    gemini_available_models: list[str]
    gemini_temperature: float
    gemini_top_p: float
    gemini_max_output_tokens: int
    gemini_image_capable_models: list[str]

    db_url: str
    chat_history_max_messages: int
    chat_system_prompt: str
    media_tmp_dir: str
    media_max_image_mb: int

    ui_thinking_text: str
    ui_show_help_on_start: bool


def load_settings(env_file: str = ".env") -> Settings:
    # Do not override environment values already provided by runtime (e.g. docker-compose).
    load_dotenv(env_file, override=False)

    settings = Settings(
        app_env=_str("APP_ENV", "development"),
        log_level=_str("LOG_LEVEL", "INFO").upper(),
        log_flow_enabled=_bool("LOG_FLOW_ENABLED", True),
        log_http_enabled=_bool("LOG_HTTP_ENABLED", True),
        log_text_preview_chars=max(20, _int("LOG_TEXT_PREVIEW_CHARS", 160)),
        log_http_body_preview_chars=max(80, _int("LOG_HTTP_BODY_PREVIEW_CHARS", 500)),
        bale_bot_token=_str("BALE_BOT_TOKEN", "").strip(),
        bale_api_base_url=_str("BALE_API_BASE_URL", "https://tapi.bale.ai").strip().rstrip("/"),
        bale_file_base_url=_str("BALE_FILE_BASE_URL", "https://tapi.bale.ai/file").strip().rstrip("/"),
        bale_poll_timeout_sec=max(5, _int("BALE_POLL_TIMEOUT_SEC", 30)),
        bale_allowed_updates=_csv("BALE_ALLOWED_UPDATES", DEFAULT_BALE_ALLOWED_UPDATES),
        gemini_proxy_base_url=_str("GEMINI_PROXY_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/"),
        gemini_proxy_endpoint=_str("GEMINI_PROXY_ENDPOINT", "/proxy/gemini").strip(),
        gemini_proxy_timeout_sec=max(5, _int("GEMINI_PROXY_TIMEOUT_SEC", 90)),
        gemini_default_model=_str("GEMINI_DEFAULT_MODEL", "gemma-4-26b-a4b-it").strip(),
        gemini_available_models=_csv(
            "GEMINI_AVAILABLE_MODELS",
            ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
        ),
        gemini_temperature=max(0.0, _float("GEMINI_TEMPERATURE", 0.4)),
        gemini_top_p=max(0.0, _float("GEMINI_TOP_P", 0.95)),
        gemini_max_output_tokens=max(128, _int("GEMINI_MAX_OUTPUT_TOKENS", 1024)),
        gemini_image_capable_models=_csv(
            "GEMINI_IMAGE_CAPABLE_MODELS",
            [
                "gemma-4-26b-a4b-it",
                "gemma-4-31b-it",
                "gemini-3.1-flash-lite",
                "gemini-3.1-flash-lite-preview",
                "gemini-2.5-flash",
                "gemini-2.5-flash-lite",
                "gemini-2.5-pro",
                "gemini-2.0-flash",
                "gemini-2.0-flash-lite",
            ],
        ),
        db_url=_str("DB_URL", "sqlite+aiosqlite:///./data/chat_history.db").strip(),
        chat_history_max_messages=max(1, _int("CHAT_HISTORY_MAX_MESSAGES", 10)),
        chat_system_prompt=_str(
            "CHAT_SYSTEM_PROMPT",
            "You are a helpful assistant inside Bale messenger. Keep answers concise and practical.",
        ).strip(),
        media_tmp_dir=_str("MEDIA_TMP_DIR", "./data/tmp_media").strip(),
        media_max_image_mb=max(1, _int("MEDIA_MAX_IMAGE_MB", 10)),
        ui_thinking_text=_str("UI_THINKING_TEXT", "⏳ در حال فکر کردن...").strip(),
        ui_show_help_on_start=_bool("UI_SHOW_HELP_ON_START", True),
    )

    if not settings.bale_bot_token:
        raise ValueError("BALE_BOT_TOKEN is required")

    if not settings.gemini_available_models:
        settings.gemini_available_models = [settings.gemini_default_model]

    if settings.gemini_default_model not in settings.gemini_available_models:
        settings.gemini_available_models.insert(0, settings.gemini_default_model)
    settings.gemini_image_capable_models = [m.strip() for m in settings.gemini_image_capable_models if m.strip()]

    endpoint = settings.gemini_proxy_endpoint
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    settings.gemini_proxy_endpoint = endpoint

    if settings.db_url.startswith(("sqlite:///", "sqlite+aiosqlite:///")):
        raw_path = settings.db_url.split("///", 1)[1]
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.media_tmp_dir).mkdir(parents=True, exist_ok=True)
    return settings
