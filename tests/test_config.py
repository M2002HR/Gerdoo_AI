from __future__ import annotations

from pathlib import Path

from gerdoo_ai_bot.config import load_settings


def test_load_settings_defaults(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "chat.db"
    monkeypatch.setenv("BALE_BOT_TOKEN", "token")
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_path}")

    settings = load_settings(env_file="/non/existent/path.env")
    assert settings.bale_bot_token == "token"
    assert settings.gemini_default_model in settings.gemini_available_models
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


def test_load_settings_normalizes_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALE_BOT_TOKEN", "token")
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("GEMINI_PROXY_ENDPOINT", "proxy/gemini")

    settings = load_settings(env_file="/non/existent/path.env")
    assert settings.gemini_proxy_endpoint == "/proxy/gemini"
