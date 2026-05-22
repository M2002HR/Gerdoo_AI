from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from gerdoo_ai_bot.service import ACTION_IMAGE_PROMPT, ACTION_STT_AUDIO, BaleAIBotService
from gerdoo_ai_bot.storage import ChatStorage
from gerdoo_ai_bot.types import ChatMessage, ContentType, IncomingMessage, Platform
from gerdoo_ai_bot.ui import BTN_IMAGE_GENERATION, BTN_TRANSCRIBE


class DummyBaleClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []
        self.sent_photos: list[dict] = []
        self.sent_documents: list[dict] = []
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

    async def send_photo(
        self,
        chat_id: str,
        *,
        photo_bytes=None,
        photo_url=None,
        filename="image.png",
        caption=None,
        reply_markup=None,
        reply_to_message_id=None,
    ):
        self.sent_photos.append(
            {
                "chat_id": chat_id,
                "photo_bytes": photo_bytes,
                "photo_url": photo_url,
                "filename": filename,
                "caption": caption,
                "reply_markup": reply_markup,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return {"message_id": 2}

    async def send_document(
        self,
        chat_id: str,
        *,
        document_bytes: bytes,
        filename: str,
        mime_type: str = "text/markdown",
        caption=None,
        reply_markup=None,
        reply_to_message_id=None,
    ):
        self.sent_documents.append(
            {
                "chat_id": chat_id,
                "document_bytes": document_bytes,
                "filename": filename,
                "mime_type": mime_type,
                "caption": caption,
                "reply_markup": reply_markup,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return {"message_id": 3}

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None):
        self.answered_callbacks.append({"callback_query_id": callback_query_id, "text": text})
        return {"ok": True}

    async def delete_message(self, chat_id: str, message_id: int):
        self.deleted_messages.append({"chat_id": chat_id, "message_id": message_id})
        return {"ok": True}

    async def get_file(self, file_id: str):
        if "voice" in file_id:
            return {"file_path": f"{file_id}.ogg", "file_size": 10}
        return {"file_path": f"{file_id}.png", "file_size": 10}

    async def download_file(self, file_path: str, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if file_path.endswith(".ogg"):
            output_path.write_bytes(b"OggS\x00\x02\x00")
        else:
            output_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00")
        return output_path

    async def get_updates(self, offset, timeout, allowed_updates):
        return []

    async def aclose(self):
        return None


class DummyAIClient:
    def __init__(self) -> None:
        self.chat_calls: list[dict] = []
        self.image_calls: list[dict] = []
        self.transcription_calls: list[dict] = []
        self.events: list[str] = []
        self.reply_text = "پاسخ تست"
        self.transcript_text = "این یک متن تبدیل‌شده از ویس است"

    async def generate_reply(
        self,
        *,
        models: list[str],
        providers: list[str],
        user_message: str,
        history: list[ChatMessage],
        system_prompt: str,
        router,
        image_inline_data: dict | None = None,
    ) -> str:
        self.events.append("chat")
        self.chat_calls.append(
            {
                "models": models,
                "providers": providers,
                "user_message": user_message,
                "history": [item.content for item in history],
                "system_prompt": system_prompt,
                "image_inline_data": image_inline_data,
                "router_mode": getattr(router, "mode", ""),
            }
        )
        return self.reply_text

    async def generate_image(self, *, models, providers, prompt, router, size, quality, n, seed=None):
        self.events.append("image")
        self.image_calls.append(
            {
                "models": models,
                "providers": providers,
                "prompt": prompt,
                "router_mode": getattr(router, "mode", ""),
                "size": size,
                "quality": quality,
                "n": n,
                "seed": seed,
            }
        )

        class _ImageResult:
            image_bytes = b"\x89PNG\r\n\x1a\n\x00"
            image_url = None
            revised_prompt = ""

        return _ImageResult()

    async def transcribe_audio(self, *, audio_bytes, filename, mime_type, provider, language, model_preferences):
        self.events.append("transcribe")
        self.transcription_calls.append(
            {
                "filename": filename,
                "mime_type": mime_type,
                "provider": provider,
                "language": language,
                "model_preferences": model_preferences,
                "bytes_len": len(audio_bytes),
            }
        )
        return self.transcript_text

    async def aclose(self):
        return None


@dataclass(slots=True)
class DummyRouter:
    providers: list[str] = field(default_factory=list)
    strategy: str = "fallback_chain"
    mode: str = "limit_safe"
    timeout_sec: float = 25.0
    max_attempts: int = 8


@dataclass(slots=True)
class DummySettings:
    bale_poll_timeout_sec: int = 30
    bale_allowed_updates: list[str] = field(default_factory=lambda: ["message", "callback_query"])

    ai_chat_models: list[str] = field(default_factory=lambda: ["gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"])
    ai_chat_providers: list[str] = field(default_factory=lambda: ["gemini", "groq"])
    ai_image_generation_models: list[str] = field(default_factory=lambda: ["pollinations/flux"])
    ai_image_generation_providers: list[str] = field(default_factory=lambda: ["pollinations"])
    ai_image_prompt_enhancer_models: list[str] = field(default_factory=lambda: ["gemini/gemini-2.5-flash"])
    ai_image_prompt_enhancer_providers: list[str] = field(default_factory=lambda: ["gemini"])
    ai_image_analysis_models: list[str] = field(default_factory=lambda: ["gemini/gemini-2.5-flash"])
    ai_image_analysis_providers: list[str] = field(default_factory=lambda: ["gemini"])
    ai_audio_analysis_models: list[str] = field(default_factory=lambda: ["gemini/gemini-2.5-flash"])
    ai_audio_analysis_providers: list[str] = field(default_factory=lambda: ["gemini"])
    ai_transcript_cleanup_models: list[str] = field(default_factory=lambda: ["gemini/gemini-2.5-flash"])
    ai_transcript_cleanup_providers: list[str] = field(default_factory=lambda: ["gemini"])
    ai_transcription_provider: str = "groq"
    ai_transcription_models: list[str] = field(default_factory=lambda: ["groq/whisper-large-v3-turbo", "groq/whisper-large-v3"])

    ai_image_generation_size: str = "1024x1024"
    ai_image_generation_quality: str = "medium"
    ai_image_generation_count: int = 1

    chat_history_max_messages: int = 10
    chat_system_prompt: str = "system prompt"
    image_prompt_enhancer_system_prompt: str = "enhance image prompt in english"
    image_analysis_system_prompt: str = "vision prompt"
    audio_analysis_system_prompt: str = "audio prompt"
    transcript_cleanup_system_prompt: str = "cleanup transcript"

    media_tmp_dir: str = "/tmp"
    media_max_image_mb: int = 10
    media_max_voice_sec: int = 300

    ui_thinking_text: str = "thinking"
    ui_show_help_on_start: bool = True
    ui_image_stage_processing_text: str = "processing"
    ui_image_stage_enhancing_text: str = "enhancing"
    ui_image_stage_generating_text: str = "generating"
    ui_audio_topic_question_text: str = "topic?"
    ui_audio_stage_transcribing_text: str = "transcribing"
    ui_audio_stage_cleaning_text: str = "cleaning"
    ui_audio_stage_analyzing_text: str = "analyzing"
    ui_audio_stage_ready_text: str = "ready"
    audio_topic_ask_enabled: bool = False
    audio_topic_default_text: str = "نامشخص"
    audio_transcript_cleanup_enabled: bool = True
    ai_image_regen_seed_min: int = 1000
    ai_image_regen_seed_max: int = 999999999

    uag_base_url: str = "http://127.0.0.1:8080"
    uag_chat_endpoint: str = "/v1/chat/completions"
    uag_groq_api_keys: list[str] = field(default_factory=lambda: ["test-key"])

    app_env: str = "development"
    log_flow_enabled: bool = True
    log_text_preview_chars: int = 160

    router_chat: DummyRouter = field(default_factory=DummyRouter)
    router_image_generation: DummyRouter = field(default_factory=DummyRouter)
    router_image_prompt_enhancer: DummyRouter = field(default_factory=DummyRouter)
    router_image_analysis: DummyRouter = field(default_factory=DummyRouter)
    router_audio_analysis: DummyRouter = field(default_factory=DummyRouter)
    router_transcript_cleanup: DummyRouter = field(default_factory=DummyRouter)


@pytest.mark.asyncio
async def test_start_command_sends_welcome(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyAIClient(), storage)

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

    assert any("گفتگوی تازه" in item["text"] for item in svc.bale_client.sent_messages)


@pytest.mark.asyncio
async def test_chat_uses_configured_chat_models(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyAIClient(), storage)

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

    assert svc.ai_client.chat_calls
    assert svc.ai_client.chat_calls[0]["models"] == ["gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"]
    assert svc.ai_client.chat_calls[0]["providers"] == ["gemini", "groq"]


@pytest.mark.asyncio
async def test_photo_message_uses_image_analysis_mode(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyAIClient(), storage)

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

    assert svc.ai_client.chat_calls
    assert svc.ai_client.chat_calls[0]["image_inline_data"] is not None
    assert svc.ai_client.chat_calls[0]["models"] == ["gemini/gemini-2.5-flash"]


@pytest.mark.asyncio
async def test_image_generation_flow_from_button(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    bale = DummyBaleClient()
    ai = DummyAIClient()
    svc = BaleAIBotService(DummySettings(), bale, ai, storage)

    open_mode = IncomingMessage(
        platform=Platform.BALE,
        update_id=8,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=1,
        content_type=ContentType.TEXT,
        text=BTN_IMAGE_GENERATION,
    )
    await svc.handle_incoming(open_mode)
    assert svc._pending_actions.get("u1") == ACTION_IMAGE_PROMPT

    prompt_msg = IncomingMessage(
        platform=Platform.BALE,
        update_id=9,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=2,
        content_type=ContentType.TEXT,
        text="یک پوستر مینیمال از کوهستان",
    )
    await svc.handle_incoming(prompt_msg)

    assert ai.chat_calls
    assert any(call["system_prompt"] == "enhance image prompt in english" for call in ai.chat_calls)
    assert ai.image_calls
    assert ai.image_calls[0]["providers"] == ["pollinations"]
    assert ai.image_calls[0]["prompt"] == ai.reply_text
    assert bale.sent_photos
    assert svc._pending_actions.get("u1") is None


@pytest.mark.asyncio
async def test_stt_mode_rejects_long_voice(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    bale = DummyBaleClient()
    ai = DummyAIClient()
    settings = DummySettings(media_max_voice_sec=300)
    svc = BaleAIBotService(settings, bale, ai, storage)

    open_mode = IncomingMessage(
        platform=Platform.BALE,
        update_id=10,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=1,
        content_type=ContentType.TEXT,
        text=BTN_TRANSCRIBE,
    )
    await svc.handle_incoming(open_mode)
    assert svc._pending_actions.get("u1") == ACTION_STT_AUDIO

    long_voice = IncomingMessage(
        platform=Platform.BALE,
        update_id=11,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=2,
        content_type=ContentType.AUDIO,
        source_file_id="voice-big",
        source_duration_sec=301,
        source_mime_type="audio/ogg",
    )
    await svc.handle_incoming(long_voice)

    assert not ai.transcription_calls
    assert any("حداکثر زمان" in msg["text"] for msg in bale.sent_messages)


@pytest.mark.asyncio
async def test_audio_rejects_when_stt_key_missing(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    bale = DummyBaleClient()
    ai = DummyAIClient()
    settings = DummySettings(uag_groq_api_keys=[])
    svc = BaleAIBotService(settings, bale, ai, storage)

    voice_msg = IncomingMessage(
        platform=Platform.BALE,
        update_id=50,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=2,
        content_type=ContentType.AUDIO,
        source_file_id="voice-ok",
        source_duration_sec=30,
        source_mime_type="audio/ogg",
    )
    await svc.handle_incoming(voice_msg)

    assert not ai.transcription_calls
    assert any("کلید سرویس تبدیل ویس به متن" in msg["text"] for msg in bale.sent_messages)


@pytest.mark.asyncio
async def test_audio_analysis_in_chat_uses_transcription_then_reply(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    bale = DummyBaleClient()
    ai = DummyAIClient()
    svc = BaleAIBotService(DummySettings(), bale, ai, storage)

    voice_msg = IncomingMessage(
        platform=Platform.BALE,
        update_id=12,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=2,
        content_type=ContentType.AUDIO,
        source_file_id="voice-ok",
        source_duration_sec=30,
        source_mime_type="audio/ogg",
        caption="خلاصه کن",
    )
    await svc.handle_incoming(voice_msg)

    assert ai.transcription_calls
    assert ai.chat_calls  # transcript cleanup + chat reply
    assert ai.events.index("transcribe") < ai.events.index("chat")
    assert all(call["system_prompt"] != svc.settings.audio_analysis_system_prompt for call in ai.chat_calls)
    assert ai.transcription_calls[0]["provider"] == svc.settings.ai_transcription_provider
    assert ai.transcription_calls[0]["model_preferences"] == svc.settings.ai_transcription_models
    assert "فقط متن نهایی ساده را بده" in ai.chat_calls[0]["user_message"]
    assert ai.transcript_text in ai.chat_calls[0]["user_message"]
    assert any(msg["text"] == ai.reply_text for msg in bale.sent_messages)
    assert not bale.sent_documents


@pytest.mark.asyncio
async def test_audio_in_chat_does_not_ask_topic_even_if_enabled(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    bale = DummyBaleClient()
    ai = DummyAIClient()
    settings = DummySettings(audio_topic_ask_enabled=True)
    svc = BaleAIBotService(settings, bale, ai, storage)

    voice_msg = IncomingMessage(
        platform=Platform.BALE,
        update_id=16,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=2,
        content_type=ContentType.AUDIO,
        source_file_id="voice-ok",
        source_duration_sec=30,
        source_mime_type="audio/ogg",
    )
    await svc.handle_incoming(voice_msg)

    assert ai.transcription_calls
    assert not any("topic?" in msg["text"] for msg in bale.sent_messages)


@pytest.mark.asyncio
async def test_audio_duration_in_ms_is_not_rejected_as_too_long(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    bale = DummyBaleClient()
    ai = DummyAIClient()
    settings = DummySettings(media_max_voice_sec=600, audio_topic_ask_enabled=False)
    svc = BaleAIBotService(settings, bale, ai, storage)

    voice_msg = IncomingMessage(
        platform=Platform.BALE,
        update_id=14,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=2,
        content_type=ContentType.AUDIO,
        source_file_id="voice-ok",
        source_duration_sec=2000,  # reported in ms by some clients
        source_file_size=14440,
        source_mime_type="audio/ogg",
        caption="تحلیل کن",
    )
    await svc.handle_incoming(voice_msg)

    assert ai.transcription_calls
    assert not any("طول ویس بیشتر از حد مجاز" in msg["text"] for msg in bale.sent_messages)


@pytest.mark.asyncio
async def test_audio_huge_meta_duration_with_tiny_file_is_not_rejected(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    bale = DummyBaleClient()
    ai = DummyAIClient()
    settings = DummySettings(media_max_voice_sec=600, audio_topic_ask_enabled=False)
    svc = BaleAIBotService(settings, bale, ai, storage)

    voice_msg = IncomingMessage(
        platform=Platform.BALE,
        update_id=15,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=2,
        content_type=ContentType.AUDIO,
        source_file_id="voice-ok",
        source_duration_sec=2_147_483_647,  # bad metadata sample
        source_file_size=14_440,  # tiny file => should not be rejected as >10 min
        source_mime_type="audio/ogg",
    )
    await svc.handle_incoming(voice_msg)

    assert ai.transcription_calls
    assert not any("طول ویس بیشتر از حد مجاز" in msg["text"] for msg in bale.sent_messages)


def test_normalize_audio_upload_metadata_from_unknown_extension() -> None:
    from gerdoo_ai_bot.service import BaleAIBotService

    filename, mime = BaleAIBotService._normalize_audio_upload_metadata("voice", "application/octet-stream")
    assert filename.endswith(".ogg")
    assert mime == "audio/ogg"


def test_normalize_audio_upload_metadata_oga_alias() -> None:
    from gerdoo_ai_bot.service import BaleAIBotService

    filename, mime = BaleAIBotService._normalize_audio_upload_metadata("sample.oga", "application/ogg")
    assert filename.endswith(".ogg")
    assert mime == "audio/ogg"


@pytest.mark.asyncio
async def test_history_filters_meta_instruction_dump(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()

    await storage.ensure_user(
        user_id="u1",
        chat_id="c1",
        username="user",
        display_name="User",
        default_model="gemini/gemini-2.5-flash",
    )
    await storage.append_chat_message(
        "u1",
        "assistant",
        '* Input: "سلام"\\n* Context: user in Bale\\n* Goal: concise reply\\n* Language: Persian',
        keep_last=10,
    )
    await storage.append_chat_message("u1", "user", "پیام سالم قبلی", keep_last=10)

    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyAIClient(), storage)
    incoming = IncomingMessage(
        platform=Platform.BALE,
        update_id=13,
        chat_id="c1",
        user_id="u1",
        username="user",
        display_name="User",
        message_id=1,
        content_type=ContentType.TEXT,
        text="سلام",
    )
    await svc.handle_incoming(incoming)

    assert svc.ai_client.chat_calls
    sent_history = svc.ai_client.chat_calls[0]["history"]
    assert "پیام سالم قبلی" in sent_history
    assert all("Input:" not in item for item in sent_history)


@pytest.mark.asyncio
async def test_friendly_error_prioritizes_auth_over_limit_keyword(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()
    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyAIClient(), storage)

    msg = svc._friendly_ai_error(
        Exception("UAG error (401): {'mode': 'limit_safe', 'detail': 'Unauthorized'}"),
        feature="chat",
    )
    assert "دسترسی" in msg
    assert "ترافیک بالاست" not in msg


@pytest.mark.asyncio
async def test_friendly_error_rate_limit_for_429(tmp_path) -> None:
    storage = ChatStorage(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    await storage.init()
    svc = BaleAIBotService(DummySettings(), DummyBaleClient(), DummyAIClient(), storage)

    msg = svc._friendly_ai_error(
        Exception("UAG error (429): {'detail': 'Too Many Requests'}"),
        feature="chat",
    )
    assert "ترافیک بالاست" in msg
