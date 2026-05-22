from __future__ import annotations

import pytest

from gerdoo_ai_bot.storage import ChatStorage


@pytest.mark.asyncio
async def test_admin_overview_and_series(tmp_path) -> None:
    db = tmp_path / "chat.db"
    storage = ChatStorage(f"sqlite+aiosqlite:///{db}")
    await storage.init()

    await storage.ensure_user(
        user_id="u1",
        chat_id="c1",
        username="user1",
        display_name="User One",
        default_model="groq/openai/gpt-oss-120b",
    )
    await storage.log_ai_request(
        user_id="u1",
        model="groq/openai/gpt-oss-120b",
        user_prompt="hello",
        assistant_reply="hi",
        error_text=None,
    )
    await storage.log_bot_event(
        event_type="chat_response",
        status="ok",
        user_id="u1",
        chat_id="c1",
        content_type="TEXT",
        latency_ms=120.0,
        details={"x": 1},
    )
    await storage.save_image_generation(
        generation_id="g1",
        user_id="u1",
        chat_id="c1",
        original_prompt="a cat",
        enhanced_prompt="a cute fluffy cat",
        revised_prompt=None,
        model="pollinations/flux",
        provider="pollinations",
        image_size="1024x1024",
        image_quality="high",
        image_seed=123,
    )
    await storage.save_voice_transcription(
        request_id="v1",
        user_id="u1",
        chat_id="c1",
        mode="transcription_only",
        topic="test",
        raw_transcript="raw",
        cleaned_transcript="clean",
        analysis_reply=None,
    )

    overview = await storage.admin_overview(since_minutes=1440)
    assert overview["users"]["total"] == 1
    assert overview["requests"]["window_total"] == 1
    assert overview["events"]["window_total"] >= 1

    daily = await storage.admin_daily_series(days=7)
    assert len(daily) >= 1

    models = await storage.admin_model_usage(since_minutes=1440)
    assert models["chat"][0]["model"] == "groq/openai/gpt-oss-120b"
    assert models["image_generation"][0]["provider"] == "pollinations"
    assert models["voice"][0]["mode"] == "transcription_only"


@pytest.mark.asyncio
async def test_admin_recent_events_filter(tmp_path) -> None:
    db = tmp_path / "chat.db"
    storage = ChatStorage(f"sqlite+aiosqlite:///{db}")
    await storage.init()

    await storage.log_bot_event(
        event_type="chat_response",
        status="ok",
        user_id="u1",
        chat_id="c1",
        content_type="TEXT",
        details={"a": 1},
    )
    await storage.log_bot_event(
        event_type="audio_analysis",
        status="failed",
        user_id="u2",
        chat_id="c2",
        content_type="AUDIO",
        error_code="upstream_timeout",
        details={"b": 2},
    )

    rows = await storage.admin_recent_events(since_minutes=1440, limit=10)
    assert len(rows) == 2

    failed_rows = await storage.admin_recent_events(
        since_minutes=1440,
        limit=10,
        status="failed",
    )
    assert len(failed_rows) == 1
    assert failed_rows[0]["event_type"] == "audio_analysis"
