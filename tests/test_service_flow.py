from __future__ import annotations

from dataclasses import dataclass

import pytest

from gerdoo_ai_bot.service import BaleAIBotService
from gerdoo_ai_bot.storage import ChatStorage
from gerdoo_ai_bot.types import ChatMessage, ContentType, IncomingMessage, Platform


class DummyBaleClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []
        self.answered_callbacks: list[dict] = []
        self.deleted_messages: list[dict] = []

    async def send_message(self, chat_id: str, text: str, reply_markup=None, reply_to_message_id=None):
        self.sent_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return {"message_id": 1}

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None):
        self.answered_callbacks.append({"callback_query_id": callback_query_id, "text": text})
        return {"ok": True}

    async def delete_message(self, chat_id: str, message_id: int):
        self.deleted_messages.append({"chat_id": chat_id, "message_id": message_id})
        return {"ok": True}

    async def get_file(self, file_id: str):
        return {"file_path": f"{file_id}.png", "file_size": 10}

    async def download_file(self, file_path: str, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00")
        return output_path

    async def get_updates(self, offset, timeout, allowed_updates):
        return []

    async def aclose(self):
        return None


class DummyGeminiClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.reply_text = "پاسخ تست"

    async def generate_reply(
        self,
        *,
        model: str,
        user_message: str,
        history: list[ChatMessage],
        system_prompt: str,
        image_inline_data: dict | None = None,
    ) -> str:
        self.calls.append(
            {
                "model": model,
                "user_message": user_message,
                "history": [item.content for item in history],
                "system_prompt": system_prompt,
                "image_inline_data": image_inline_data,
            }
        )
        return self.reply_text

    image_capable_models = {
        "gemma-4-26b-a4b-it",
        "gemma-4-31b-it",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    }

    async def aclose(self):
        return None


@dataclass(slots=True)
class DummySettings:
    bale_poll_timeout_sec: int = 30
    bale_allowed_updates: list[str] = None
    gemini_default_model: str = "gemma-4-26b-a4b-it"
    gemini_available_models: list[str] = None
    chat_history_max_messages: int = 10
    chat_system_prompt: str = "system prompt"
    media_tmp_dir: str = "/tmp"
    media_max_image_mb: int = 10
    ui_thinking_text: str = "thinking"
    ui_show_help_on_start: bool = True
    gemini_proxy_base_url: str = "http://127.0.0.1:8000"
    gemini_proxy_endpoint: str = "/proxy/gemini"

    def __post_init__(self) -> None:
        if self.bale_allowed_updates is None:
            self.bale_allowed_updates = ["message", "callback_query"]
        if self.gemini_available_models is None:
            self.gemini_available_models = ["gemma-4-26b-a4b-it", "gemini-3.1-flash-lite", "gemini-2.5-flash"]


@pytest.mark.asyncio
async def test_start_command_sends_welcome(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyGeminiClient(), storage)

    incoming = IncomingMessage(
        platform=Platform.BALE,
        update_id=1,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=1,
        content_type=ContentType.TEXT,
        text="/start",
    )
    await svc.handle_incoming(incoming)

    assert any("خوش آمدید" in item["text"] for item in svc.bale_client.sent_messages)


@pytest.mark.asyncio
async def test_model_change_via_callback(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyGeminiClient(), storage)

    incoming = IncomingMessage(
        platform=Platform.BALE,
        update_id=2,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=1,
        content_type=ContentType.CALLBACK,
        callback_data="mdl:set:1",
        callback_query_id="cb1",
    )
    await svc.handle_incoming(incoming)

    selected = await storage.get_selected_model("u1", "gemma-4-26b-a4b-it")
    assert selected == "gemini-3.1-flash-lite"


@pytest.mark.asyncio
async def test_chat_uses_selected_model_and_persists_history(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()
    await storage.set_selected_model("u1", "gemini-3.1-flash-lite")

    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyGeminiClient(), storage)

    incoming = IncomingMessage(
        platform=Platform.BALE,
        update_id=3,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=1,
        content_type=ContentType.TEXT,
        text="سلام",
    )
    await svc.handle_incoming(incoming)

    assert svc.gemini_client.calls
    assert svc.gemini_client.calls[0]["model"] == "gemini-3.1-flash-lite"


@pytest.mark.asyncio
async def test_photo_message_uses_inline_image_payload(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyGeminiClient(), storage)

    incoming = IncomingMessage(
        platform=Platform.BALE,
        update_id=4,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=1,
        content_type=ContentType.PHOTO,
        caption="این عکس چیه؟",
        source_file_id="f1",
    )
    await svc.handle_incoming(incoming)

    assert svc.gemini_client.calls
    assert svc.gemini_client.calls[0]["image_inline_data"] is not None

    history = await storage.get_recent_chat("u1", 10)
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].role == "assistant"


@pytest.mark.asyncio
async def test_history_filters_meta_instruction_dump(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    await storage.ensure_user(
        user_id="u1",
        chat_id="c1",
        username="user",
        display_name="User",
        default_model="gemma-4-26b-a4b-it",
    )
    await storage.append_chat_message(
        "u1",
        "assistant",
        '* Input: "سلام"\\n* Context: user in Bale\\n* Goal: concise reply\\n* Language: Persian',
        keep_last=10,
    )
    await storage.append_chat_message("u1", "user", "پیام سالم قبلی", keep_last=10)

    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyGeminiClient(), storage)
    incoming = IncomingMessage(
        platform=Platform.BALE,
        update_id=11,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=1,
        content_type=ContentType.TEXT,
        text="سلام",
    )
    await svc.handle_incoming(incoming)

    assert svc.gemini_client.calls
    sent_history = svc.gemini_client.calls[0]["history"]
    assert "پیام سالم قبلی" in sent_history
    assert all("Input:" not in item for item in sent_history)


@pytest.mark.asyncio
async def test_sanitize_meta_style_model_reply(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    bale = DummyBaleClient()
    gemini = DummyGeminiClient()
    gemini.reply_text = (
        'User says: "سلامسلام".\n'
        "Language: Persian.\n"
        "Tone: Friendly/Casual.\n"
        "Role: Helpful assistant.\n"
        "Style: Concise and practical.\n"
        "Option 1: سلام! چطور می‌توانم کمکتان کنم؟\n"
        "Option 2: سلام! در خدمت هستم.\n"
    )
    svc = BaleAIBotService(DummySettings(), bale, gemini, storage)

    incoming = IncomingMessage(
        platform=Platform.BALE,
        update_id=12,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=1,
        content_type=ContentType.TEXT,
        text="سلام",
    )
    await svc.handle_incoming(incoming)

    assert bale.sent_messages
    # last message is the assistant final reply
    assert "سلام! چطور می‌توانم کمکتان کنم؟" in bale.sent_messages[-1]["text"]
