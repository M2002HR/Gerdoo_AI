from __future__ import annotations

import pytest

from gerdoo_ai_bot.storage import ChatStorage


@pytest.mark.asyncio
async def test_storage_model_roundtrip(tmp_path) -> None:
    db = tmp_path / "chat.db"
    storage = ChatStorage(f"sqlite+aiosqlite:///{db}")
    await storage.init()

    current = await storage.get_selected_model("u1", "default")
    assert current == "default"

    await storage.set_selected_model("u1", "gemini-2.5-pro")
    current = await storage.get_selected_model("u1", "default")
    assert current == "gemini-2.5-pro"


@pytest.mark.asyncio
async def test_storage_history_trim(tmp_path) -> None:
    db = tmp_path / "chat.db"
    storage = ChatStorage(f"sqlite+aiosqlite:///{db}")
    await storage.init()

    for idx in range(6):
        await storage.append_chat_message("u1", "user", f"m{idx}", keep_last=4)

    history = await storage.get_recent_chat("u1", limit=10)
    assert [item.content for item in history] == ["m2", "m3", "m4", "m5"]

    await storage.clear_chat("u1")
    history = await storage.get_recent_chat("u1", limit=10)
    assert history == []
