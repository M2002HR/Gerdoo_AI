from __future__ import annotations

from pathlib import Path

from gerdoo_ai_bot.config import load_settings


def test_load_settings_defaults(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "chat.db"
    monkeypatch.setenv("BALE_BOT_TOKEN", "token")
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_path}")

    settings = load_settings(env_file="/non/existent/path.env")
    assert settings.bale_bot_token == "token"
    assert settings.ai_chat_models
    assert settings.ai_chat_providers
    assert settings.chat_history_max_messages == 10
    assert settings.db_url.endswith(str(db_path))
    assert Path(db_path).parent.exists()


def test_load_settings_requires_token(monkeypatch) -> None:
    monkeypatch.delenv("BALE_BOT_TOKEN", raising=False)
    try:
        load_settings(env_file="/non/existent/path.env")
    except ValueError as exc:
        assert "BALE_BOT_TOKEN" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_load_settings_normalizes_endpoints(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALE_BOT_TOKEN", "token")
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("UAG_CHAT_ENDPOINT", "v1/chat/completions")
    monkeypatch.setenv("UAG_IMAGE_ENDPOINT", "v1/images/generations")

    settings = load_settings(env_file="/non/existent/path.env")
    assert settings.uag_chat_endpoint == "/v1/chat/completions"
    assert settings.uag_image_endpoint == "/v1/images/generations"


def test_load_settings_supports_legacy_env_names(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALE_BOT_TOKEN", "token")
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("GEMINI_PROXY_BASE_URL", "http://127.0.0.1:18000")
    monkeypatch.setenv("GEMINI_PROXY_ENDPOINT", "/proxy/gemini")

    settings = load_settings(env_file="/non/existent/path.env")
    assert settings.uag_base_url == "http://127.0.0.1:18000"
    assert settings.uag_chat_endpoint == "/proxy/gemini"


def test_load_settings_chat_model_fallbacks_to_legacy(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALE_BOT_TOKEN", "token")
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("AI_DEFAULT_MODEL", "gemini/gemini-2.5-flash")
    monkeypatch.setenv("AI_AVAILABLE_MODELS", "groq/llama-3.3-70b-versatile")

    settings = load_settings(env_file="/non/existent/path.env")
    assert settings.ai_chat_models[0] == "gemini/gemini-2.5-flash"
    assert "groq/llama-3.3-70b-versatile" in settings.ai_chat_models


def test_load_settings_per_feature_providers(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALE_BOT_TOKEN", "token")
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("AI_IMAGE_GENERATION_PROVIDERS", "pollinations")
    monkeypatch.setenv("AI_IMAGE_PROMPT_ENHANCER_PROVIDERS", "gemini,groq")

    settings = load_settings(env_file="/non/existent/path.env")
    assert settings.ai_image_generation_providers == ["pollinations"]
    assert settings.ai_image_prompt_enhancer_providers == ["gemini", "groq"]
