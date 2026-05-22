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


def _normalize_endpoint(path: str, default: str) -> str:
    endpoint = (path or default).strip() or default
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return endpoint


@dataclass(slots=True)
class RouterSettings:
    providers: list[str]
    strategy: str
    mode: str
    timeout_sec: float
    max_attempts: int


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
    uag_image_endpoint: str
    uag_audio_transcriptions_endpoint: str
    uag_timeout_sec: int
    uag_auth_enabled: bool
    uag_auth_token: str
    uag_auth_header_name: str
    uag_groq_api_keys: list[str]

    ai_temperature: float
    ai_top_p: float
    ai_max_output_tokens: int

    ai_chat_models: list[str]
    ai_chat_providers: list[str]
    ai_image_generation_models: list[str]
    ai_image_generation_providers: list[str]
    ai_image_prompt_enhancer_models: list[str]
    ai_image_prompt_enhancer_providers: list[str]
    ai_image_analysis_models: list[str]
    ai_image_analysis_providers: list[str]
    ai_audio_analysis_models: list[str]
    ai_audio_analysis_providers: list[str]
    ai_transcript_cleanup_models: list[str]
    ai_transcript_cleanup_providers: list[str]
    ai_transcription_provider: str
    ai_transcription_models: list[str]

    ai_image_generation_size: str
    ai_image_generation_quality: str
    ai_image_generation_count: int

    chat_system_prompt: str
    image_prompt_enhancer_system_prompt: str
    image_analysis_system_prompt: str
    audio_analysis_system_prompt: str
    transcript_cleanup_system_prompt: str

    router_chat: RouterSettings
    router_image_generation: RouterSettings
    router_image_prompt_enhancer: RouterSettings
    router_image_analysis: RouterSettings
    router_audio_analysis: RouterSettings
    router_transcript_cleanup: RouterSettings

    db_url: str
    chat_history_max_messages: int
    media_tmp_dir: str
    media_max_image_mb: int
    media_max_voice_sec: int

    ui_thinking_text: str
    ui_show_help_on_start: bool
    ui_image_stage_processing_text: str
    ui_image_stage_enhancing_text: str
    ui_image_stage_generating_text: str
    ui_audio_topic_question_text: str
    ui_audio_stage_transcribing_text: str
    ui_audio_stage_cleaning_text: str
    ui_audio_stage_analyzing_text: str
    ui_audio_stage_ready_text: str
    audio_topic_ask_enabled: bool
    audio_topic_default_text: str
    audio_transcript_cleanup_enabled: bool
    ai_image_regen_seed_min: int
    ai_image_regen_seed_max: int
    admin_panel_enabled: bool
    admin_panel_username: str
    admin_panel_password: str
    admin_panel_token: str
    admin_panel_header_name: str
    admin_panel_host: str
    admin_panel_port: int
    admin_panel_default_since_minutes: int
    admin_panel_default_days: int
    admin_panel_uag_enabled: bool
    admin_panel_uag_timeout_sec: float


def _router_settings(prefix: str, *, default_mode: str = "limit_safe") -> RouterSettings:
    providers = [p.strip().lower() for p in _csv([], f"{prefix}_PROVIDERS", "AI_ROUTER_PROVIDERS") if p.strip()]
    return RouterSettings(
        providers=providers,
        strategy=_str("fallback_chain", f"{prefix}_STRATEGY", "AI_ROUTER_STRATEGY"),
        mode=_str(default_mode, f"{prefix}_MODE", "AI_ROUTER_MODE"),
        timeout_sec=max(5.0, _float(28.0, f"{prefix}_TIMEOUT_SEC", "AI_ROUTER_TIMEOUT_SEC")),
        max_attempts=max(1, _int(8, f"{prefix}_MAX_ATTEMPTS", "AI_ROUTER_MAX_ATTEMPTS")),
    )


def _default_model_chain() -> list[str]:
    legacy_default = _str("gemini/gemini-2.5-flash", "AI_DEFAULT_MODEL", "GEMINI_DEFAULT_MODEL").strip()
    legacy_available = _csv([], "AI_AVAILABLE_MODELS", "GEMINI_AVAILABLE_MODELS")
    out: list[str] = []
    if legacy_default:
        out.append(legacy_default)
    for item in legacy_available:
        if item not in out:
            out.append(item)
    if not out:
        out = [
            "gemini/gemini-2.5-flash",
            "groq/llama-3.3-70b-versatile",
        ]
    return out


def _normalize_model_entry(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "/" in raw or raw.startswith("models/"):
        return raw

    low = raw.lower()
    if any(token in low for token in ("llama", "mixtral", "whisper", "playai", "orpheus")):
        return f"groq/{raw}"
    if any(token in low for token in ("flux", "seedream", "kontext")):
        return f"pollinations/{raw}"
    return f"gemini/{raw}"


def load_settings(env_file: str = ".env") -> Settings:
    # Do not override environment values already provided by runtime (e.g. docker-compose).
    load_dotenv(env_file, override=False)

    default_chain = _default_model_chain()

    chat_models = _csv(default_chain, "AI_CHAT_MODELS")
    chat_providers = [p.strip().lower() for p in _csv(["gemini", "groq"], "AI_CHAT_PROVIDERS") if p.strip()]
    image_generation_models = _csv(["pollinations/flux"], "AI_IMAGE_GENERATION_MODELS")
    image_generation_providers = [
        p.strip().lower() for p in _csv(["pollinations"], "AI_IMAGE_GENERATION_PROVIDERS") if p.strip()
    ]
    image_prompt_enhancer_models = _csv(chat_models, "AI_IMAGE_PROMPT_ENHANCER_MODELS")
    image_prompt_enhancer_providers = [
        p.strip().lower() for p in _csv(chat_providers, "AI_IMAGE_PROMPT_ENHANCER_PROVIDERS") if p.strip()
    ]
    image_analysis_models = _csv(chat_models, "AI_IMAGE_ANALYSIS_MODELS")
    image_analysis_providers = [p.strip().lower() for p in _csv(chat_providers, "AI_IMAGE_ANALYSIS_PROVIDERS") if p.strip()]
    audio_analysis_models = _csv(chat_models, "AI_AUDIO_ANALYSIS_MODELS")
    audio_analysis_providers = [p.strip().lower() for p in _csv(chat_providers, "AI_AUDIO_ANALYSIS_PROVIDERS") if p.strip()]
    transcript_cleanup_models = _csv(chat_models, "AI_TRANSCRIPT_CLEANUP_MODELS")
    transcript_cleanup_providers = [
        p.strip().lower() for p in _csv(chat_providers, "AI_TRANSCRIPT_CLEANUP_PROVIDERS") if p.strip()
    ]
    transcription_models = _csv(
        [
            _str("groq/whisper-large-v3-turbo", "UAG_GROQ_STT_PRIMARY_MODEL"),
            _str("groq/whisper-large-v3", "UAG_GROQ_STT_FALLBACK_MODEL"),
        ],
        "AI_TRANSCRIPTION_MODELS",
    )

    # Keep non-empty and unique ordering.
    def _uniq(values: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            norm = _normalize_model_entry(value)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append(norm)
        return out

    chat_models = _uniq(chat_models)
    image_generation_models = _uniq(image_generation_models)
    image_prompt_enhancer_models = _uniq(image_prompt_enhancer_models)
    image_analysis_models = _uniq(image_analysis_models)
    audio_analysis_models = _uniq(audio_analysis_models)
    transcript_cleanup_models = _uniq(transcript_cleanup_models)
    transcription_models = _uniq(transcription_models)

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
        uag_chat_endpoint=_normalize_endpoint(
            _str("/v1/chat/completions", "UAG_CHAT_ENDPOINT", "GEMINI_PROXY_ENDPOINT"),
            "/v1/chat/completions",
        ),
        uag_image_endpoint=_normalize_endpoint(_str("/v1/images/generations", "UAG_IMAGE_ENDPOINT"), "/v1/images/generations"),
        uag_audio_transcriptions_endpoint=_normalize_endpoint(
            _str("/v1/audio/transcriptions", "UAG_AUDIO_TRANSCRIPTIONS_ENDPOINT"),
            "/v1/audio/transcriptions",
        ),
        uag_timeout_sec=max(5, _int(90, "UAG_TIMEOUT_SEC", "GEMINI_PROXY_TIMEOUT_SEC")),
        uag_auth_enabled=_bool(False, "UAG_AUTH_ENABLED"),
        uag_auth_token=_str("", "UAG_AUTH_TOKEN").strip(),
        uag_auth_header_name=_str("x-api-token", "UAG_AUTH_HEADER_NAME").strip(),
        uag_groq_api_keys=_csv([], "UAG_GROQ_API_KEYS", "GROQ_API_KEYS", "GROQ_API_KEY"),
        ai_temperature=max(0.0, _float(0.4, "AI_TEMPERATURE", "GEMINI_TEMPERATURE")),
        ai_top_p=max(0.0, _float(0.95, "AI_TOP_P", "GEMINI_TOP_P")),
        ai_max_output_tokens=max(0, _int(1024, "AI_MAX_OUTPUT_TOKENS", "GEMINI_MAX_OUTPUT_TOKENS")),
        ai_chat_models=chat_models,
        ai_chat_providers=chat_providers,
        ai_image_generation_models=image_generation_models,
        ai_image_generation_providers=image_generation_providers,
        ai_image_prompt_enhancer_models=image_prompt_enhancer_models,
        ai_image_prompt_enhancer_providers=image_prompt_enhancer_providers,
        ai_image_analysis_models=image_analysis_models,
        ai_image_analysis_providers=image_analysis_providers,
        ai_audio_analysis_models=audio_analysis_models,
        ai_audio_analysis_providers=audio_analysis_providers,
        ai_transcript_cleanup_models=transcript_cleanup_models,
        ai_transcript_cleanup_providers=transcript_cleanup_providers,
        ai_transcription_provider=_str("groq", "AI_TRANSCRIPTION_PROVIDER").strip().lower(),
        ai_transcription_models=transcription_models,
        ai_image_generation_size=_str("1024x1024", "AI_IMAGE_GENERATION_SIZE").strip(),
        ai_image_generation_quality=_str("medium", "AI_IMAGE_GENERATION_QUALITY").strip(),
        ai_image_generation_count=max(1, _int(1, "AI_IMAGE_GENERATION_COUNT")),
        chat_system_prompt=_str(
            "You are a helpful assistant inside Bale messenger. Keep answers concise and practical.",
            "CHAT_SYSTEM_PROMPT",
        ).strip(),
        image_prompt_enhancer_system_prompt=_str(
            (
                "You are an expert prompt engineer for text-to-image models. "
                "Convert the user intent to clear, high-quality English prompt optimized for image generation. "
                "Return only one final English prompt with concrete visual details, style, lighting, camera/framing, "
                "and avoid unsafe content."
            ),
            "IMAGE_PROMPT_ENHANCER_SYSTEM_PROMPT",
        ).strip(),
        image_analysis_system_prompt=_str(
            "You are a vision assistant. Describe images precisely in Persian and answer user question if provided.",
            "IMAGE_ANALYSIS_SYSTEM_PROMPT",
        ).strip(),
        audio_analysis_system_prompt=_str(
            "You are an audio analysis assistant. Use the transcript to explain content, key points, and actionable summary in Persian.",
            "AUDIO_ANALYSIS_SYSTEM_PROMPT",
        ).strip(),
        transcript_cleanup_system_prompt=_str(
            (
                "تو یک ویرایشگر حرفه‌ای متن ترنسکریپت هستی.\n"
                "من خروجی خام تبدیل گفتار را می‌دهم. آن را به یک متن کامل، روان و قابل ارائه تبدیل کن.\n"
                "قوانین:\n"
                "1) هیچ خلاصه‌سازی انجام نده و کل محتوا را نگه دار.\n"
                "2) غلط‌های تایپی و واژه‌های شکسته را با توجه به بافت اصلاح کن.\n"
                "3) معنا و ترتیب کلی متن حفظ شود.\n"
                "4) نویزها و تکرارهای بی‌معنا حذف شوند.\n"
                "5) اگر واژه‌ای مبهم بود، با توجه به متن حدس بزن.\n"
                "6) فقط متن نهایی تمیز را برگردان و توضیح اضافه نده."
            ),
            "TRANSCRIPT_CLEANUP_SYSTEM_PROMPT",
        ).strip(),
        router_chat=_router_settings("AI_CHAT_ROUTER", default_mode="limit_safe"),
        router_image_generation=_router_settings("AI_IMAGE_GENERATION_ROUTER", default_mode="limit_safe"),
        router_image_prompt_enhancer=_router_settings("AI_IMAGE_PROMPT_ENHANCER_ROUTER", default_mode="limit_safe"),
        router_image_analysis=_router_settings("AI_IMAGE_ANALYSIS_ROUTER", default_mode="limit_safe"),
        router_audio_analysis=_router_settings("AI_AUDIO_ANALYSIS_ROUTER", default_mode="limit_safe"),
        router_transcript_cleanup=_router_settings("AI_TRANSCRIPT_CLEANUP_ROUTER", default_mode="limit_safe"),
        db_url=_str("sqlite+aiosqlite:///./data/chat_history.db", "DB_URL").strip(),
        chat_history_max_messages=max(1, _int(10, "CHAT_HISTORY_MAX_MESSAGES")),
        media_tmp_dir=_str("./data/tmp_media", "MEDIA_TMP_DIR").strip(),
        media_max_image_mb=max(1, _int(10, "MEDIA_MAX_IMAGE_MB")),
        media_max_voice_sec=max(30, _int(300, "MEDIA_MAX_VOICE_SEC")),
        ui_thinking_text=_str("در حال پردازش درخواستت هستم...", "UI_THINKING_TEXT").strip(),
        ui_show_help_on_start=_bool(True, "UI_SHOW_HELP_ON_START"),
        ui_image_stage_processing_text=_str("🧠 در حال پردازش پرامپت...", "UI_IMAGE_STAGE_PROCESSING_TEXT").strip(),
        ui_image_stage_enhancing_text=_str("✨ در حال بهبود پرامپت...", "UI_IMAGE_STAGE_ENHANCING_TEXT").strip(),
        ui_image_stage_generating_text=_str("🎨 در حال تولید تصویر...", "UI_IMAGE_STAGE_GENERATING_TEXT").strip(),
        ui_audio_topic_question_text=_str(
            "🎙️ ویس دریافت شد. لطفاً بگو این ویس دربارهٔ چیه؟ (مثال: کلاس شیمی، جلسه کاری، پادکست)",
            "UI_AUDIO_TOPIC_QUESTION_TEXT",
        ).strip(),
        ui_audio_stage_transcribing_text=_str("📝 در حال تبدیل ویس به متن...", "UI_AUDIO_STAGE_TRANSCRIBING_TEXT").strip(),
        ui_audio_stage_cleaning_text=_str("🧹 در حال حذف نویز و پاک‌سازی متن...", "UI_AUDIO_STAGE_CLEANING_TEXT").strip(),
        ui_audio_stage_analyzing_text=_str("🤖 در حال تحلیل متن...", "UI_AUDIO_STAGE_ANALYZING_TEXT").strip(),
        ui_audio_stage_ready_text=_str("✅ متن آماده شد", "UI_AUDIO_STAGE_READY_TEXT").strip(),
        audio_topic_ask_enabled=_bool(True, "AUDIO_TOPIC_ASK_ENABLED"),
        audio_topic_default_text=_str("نامشخص", "AUDIO_TOPIC_DEFAULT_TEXT").strip(),
        audio_transcript_cleanup_enabled=_bool(True, "AUDIO_TRANSCRIPT_CLEANUP_ENABLED"),
        ai_image_regen_seed_min=_int(1000, "AI_IMAGE_REGEN_SEED_MIN"),
        ai_image_regen_seed_max=_int(999999999, "AI_IMAGE_REGEN_SEED_MAX"),
        admin_panel_enabled=_bool(True, "BOT_ADMIN_PANEL_ENABLED"),
        admin_panel_username=_str("admin", "BOT_ADMIN_PANEL_USERNAME").strip(),
        admin_panel_password=_str("", "BOT_ADMIN_PANEL_PASSWORD").strip(),
        admin_panel_token=_str("", "BOT_ADMIN_PANEL_TOKEN", "UAG_ADMIN_TOKEN").strip(),
        admin_panel_header_name=_str("x-admin-token", "BOT_ADMIN_PANEL_HEADER_NAME", "UAG_ADMIN_HEADER_NAME").strip()
        or "x-admin-token",
        admin_panel_host=_str("0.0.0.0", "BOT_ADMIN_PANEL_HOST").strip() or "0.0.0.0",
        admin_panel_port=max(1, _int(8090, "BOT_ADMIN_PANEL_PORT")),
        admin_panel_default_since_minutes=max(5, _int(1440, "BOT_ADMIN_PANEL_DEFAULT_SINCE_MINUTES")),
        admin_panel_default_days=max(1, _int(30, "BOT_ADMIN_PANEL_DEFAULT_DAYS")),
        admin_panel_uag_enabled=_bool(True, "BOT_ADMIN_PANEL_UAG_ENABLED"),
        admin_panel_uag_timeout_sec=max(2.0, _float(8.0, "BOT_ADMIN_PANEL_UAG_TIMEOUT_SEC")),
    )

    if not settings.bale_bot_token:
        raise ValueError("BALE_BOT_TOKEN is required")

    if not settings.ai_chat_models:
        settings.ai_chat_models = ["gemini/gemini-2.5-flash"]
    if not settings.ai_chat_providers:
        settings.ai_chat_providers = ["gemini", "groq"]
    if not settings.ai_image_generation_models:
        settings.ai_image_generation_models = ["pollinations/flux"]
    if not settings.ai_image_generation_providers:
        settings.ai_image_generation_providers = ["pollinations"]
    if not settings.ai_image_prompt_enhancer_models:
        settings.ai_image_prompt_enhancer_models = list(settings.ai_chat_models)
    if not settings.ai_image_prompt_enhancer_providers:
        settings.ai_image_prompt_enhancer_providers = list(settings.ai_chat_providers)
    if not settings.ai_image_analysis_models:
        settings.ai_image_analysis_models = list(settings.ai_chat_models)
    if not settings.ai_image_analysis_providers:
        settings.ai_image_analysis_providers = list(settings.ai_chat_providers)
    if not settings.ai_audio_analysis_models:
        settings.ai_audio_analysis_models = list(settings.ai_chat_models)
    if not settings.ai_audio_analysis_providers:
        settings.ai_audio_analysis_providers = list(settings.ai_chat_providers)
    if not settings.ai_transcript_cleanup_models:
        settings.ai_transcript_cleanup_models = list(settings.ai_chat_models)
    if not settings.ai_transcript_cleanup_providers:
        settings.ai_transcript_cleanup_providers = list(settings.ai_chat_providers)
    if not settings.ai_transcription_provider:
        settings.ai_transcription_provider = "groq"

    if not settings.router_chat.providers:
        settings.router_chat.providers = list(settings.ai_chat_providers)
    if not settings.router_image_generation.providers:
        settings.router_image_generation.providers = list(settings.ai_image_generation_providers)
    if not settings.router_image_prompt_enhancer.providers:
        settings.router_image_prompt_enhancer.providers = list(settings.ai_image_prompt_enhancer_providers)
    if not settings.router_image_analysis.providers:
        settings.router_image_analysis.providers = list(settings.ai_image_analysis_providers)
    if not settings.router_audio_analysis.providers:
        settings.router_audio_analysis.providers = list(settings.ai_audio_analysis_providers)
    if not settings.router_transcript_cleanup.providers:
        settings.router_transcript_cleanup.providers = list(settings.ai_transcript_cleanup_providers)

    if settings.ai_image_regen_seed_max < settings.ai_image_regen_seed_min:
        settings.ai_image_regen_seed_max = settings.ai_image_regen_seed_min

    if not settings.admin_panel_token:
        settings.admin_panel_token = _str("", "UAG_ADMIN_TOKEN").strip()
    if not settings.admin_panel_header_name:
        settings.admin_panel_header_name = _str("x-admin-token", "UAG_ADMIN_HEADER_NAME").strip() or "x-admin-token"

    if settings.db_url.startswith(("sqlite:///", "sqlite+aiosqlite:///")):
        raw_path = settings.db_url.split("///", 1)[1]
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.media_tmp_dir).mkdir(parents=True, exist_ok=True)
    return settings
