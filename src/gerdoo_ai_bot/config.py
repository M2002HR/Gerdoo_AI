from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_BALE_ALLOWED_UPDATES = ["message", "callback_query"]


def _raw(*names: str) -> str | None:
    for name in names:
        if name in os.environ:
            return os.getenv(name)
    return None


def _bool(default: bool, *names: str) -> bool:
    value = _raw(*names)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(default: int, *names: str) -> int:
    value = _raw(*names)
    if value is None or value == "":
        return default
    return int(value)


def _float(default: float, *names: str) -> float:
    value = _raw(*names)
    if value is None or value == "":
        return default
    return float(value)


def _str(default: str, *names: str) -> str:
    value = _raw(*names)
    return default if value is None else value


def _csv(default: list[str], *names: str) -> list[str]:
    value = _raw(*names)
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

    uag_base_url: str
    uag_chat_endpoint: str
    uag_timeout_sec: int
    uag_auth_enabled: bool
    uag_auth_token: str
    uag_auth_header_name: str

    ai_default_model: str
    ai_available_models: list[str]
    ai_temperature: float
    ai_top_p: float
    ai_max_output_tokens: int
    ai_image_capable_models: list[str]

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
        app_env=_str("development", "APP_ENV"),
        log_level=_str("INFO", "LOG_LEVEL").upper(),
        log_flow_enabled=_bool(True, "LOG_FLOW_ENABLED"),
        log_http_enabled=_bool(True, "LOG_HTTP_ENABLED"),
        log_text_preview_chars=max(20, _int(160, "LOG_TEXT_PREVIEW_CHARS")),
        log_http_body_preview_chars=max(80, _int(500, "LOG_HTTP_BODY_PREVIEW_CHARS")),
        bale_bot_token=_str("", "BALE_BOT_TOKEN").strip(),
        bale_api_base_url=_str("https://tapi.bale.ai", "BALE_API_BASE_URL").strip().rstrip("/"),
        bale_file_base_url=_str("https://tapi.bale.ai/file", "BALE_FILE_BASE_URL").strip().rstrip("/"),
        bale_poll_timeout_sec=max(5, _int(30, "BALE_POLL_TIMEOUT_SEC")),
        bale_allowed_updates=_csv(DEFAULT_BALE_ALLOWED_UPDATES, "BALE_ALLOWED_UPDATES"),
        uag_base_url=_str("http://127.0.0.1:8080", "UAG_BASE_URL", "GEMINI_PROXY_BASE_URL").strip().rstrip("/"),
        uag_chat_endpoint=_str("/v1/chat/completions", "UAG_CHAT_ENDPOINT", "GEMINI_PROXY_ENDPOINT").strip(),
        uag_timeout_sec=max(5, _int(90, "UAG_TIMEOUT_SEC", "GEMINI_PROXY_TIMEOUT_SEC")),
        uag_auth_enabled=_bool(False, "UAG_AUTH_ENABLED"),
        uag_auth_token=_str("", "UAG_AUTH_TOKEN").strip(),
        uag_auth_header_name=_str("x-api-token", "UAG_AUTH_HEADER_NAME").strip(),
        ai_default_model=_str("gemini/gemini-2.5-flash", "AI_DEFAULT_MODEL", "GEMINI_DEFAULT_MODEL").strip(),
        ai_available_models=_csv(
            [
                "gemini/gemini-2.5-flash",
                "gemini/gemini-2.5-pro",
                "gemini/gemini-2.0-flash",
                "groq/llama-3.3-70b-versatile",
            ],
            "AI_AVAILABLE_MODELS",
            "GEMINI_AVAILABLE_MODELS",
        ),
        ai_temperature=max(0.0, _float(0.4, "AI_TEMPERATURE", "GEMINI_TEMPERATURE")),
        ai_top_p=max(0.0, _float(0.95, "AI_TOP_P", "GEMINI_TOP_P")),
        ai_max_output_tokens=max(128, _int(1024, "AI_MAX_OUTPUT_TOKENS", "GEMINI_MAX_OUTPUT_TOKENS")),
        ai_image_capable_models=_csv(
            [
                "gemini/gemma-4-26b-a4b-it",
                "gemini/gemma-4-31b-it",
                "gemini/gemini-3.1-flash-lite",
                "gemini/gemini-2.5-flash",
                "gemini/gemini-2.5-flash-lite",
                "gemini/gemini-2.5-pro",
                "gemini/gemini-2.0-flash",
                "gemini/gemini-2.0-flash-lite",
            ],
            "AI_IMAGE_CAPABLE_MODELS",
            "GEMINI_IMAGE_CAPABLE_MODELS",
        ),
        db_url=_str("sqlite+aiosqlite:///./data/chat_history.db", "DB_URL").strip(),
        chat_history_max_messages=max(1, _int(10, "CHAT_HISTORY_MAX_MESSAGES")),
        chat_system_prompt=_str(
            "You are a helpful assistant inside Bale messenger. Keep answers concise and practical.",
            "CHAT_SYSTEM_PROMPT",
        ).strip(),
        media_tmp_dir=_str("./data/tmp_media", "MEDIA_TMP_DIR").strip(),
        media_max_image_mb=max(1, _int(10, "MEDIA_MAX_IMAGE_MB")),
        ui_thinking_text=_str("⏳ در حال فکر کردن...", "UI_THINKING_TEXT").strip(),
        ui_show_help_on_start=_bool(True, "UI_SHOW_HELP_ON_START"),
    )

    if not settings.bale_bot_token:
        raise ValueError("BALE_BOT_TOKEN is required")

    if not settings.ai_available_models:
        settings.ai_available_models = [settings.ai_default_model]

    if settings.ai_default_model not in settings.ai_available_models:
        settings.ai_available_models.insert(0, settings.ai_default_model)

    settings.ai_image_capable_models = [m.strip() for m in settings.ai_image_capable_models if m.strip()]

    endpoint = settings.uag_chat_endpoint
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    settings.uag_chat_endpoint = endpoint

    if settings.db_url.startswith(("sqlite:///", "sqlite+aiosqlite:///")):
        raw_path = settings.db_url.split("///", 1)[1]
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.media_tmp_dir).mkdir(parents=True, exist_ok=True)
    return settings
