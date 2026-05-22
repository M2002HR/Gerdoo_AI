from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import json
import logging
import mimetypes
import random
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

from gerdoo_ai_bot.clients.bale_api import BaleBotApiClient
from gerdoo_ai_bot.clients.uag import UAGRouterConfig, UnifiedAIGatewayClient
from gerdoo_ai_bot.config import RouterSettings, Settings
from gerdoo_ai_bot.parser import parse_update
from gerdoo_ai_bot.storage import ChatStorage
from gerdoo_ai_bot.types import AIBackendError, ChatMessage, ContentType, IncomingMessage, PlatformApiError
from gerdoo_ai_bot.ui import (
    BTN_CANCEL,
    BTN_CHAT,
    BTN_HELP,
    BTN_IMAGE_GENERATION,
    BTN_NEW_CHAT,
    BTN_SKIP_AUDIO_TOPIC,
    BTN_TRANSCRIBE,
    audio_topic_menu,
    cancel_only_menu,
    help_text,
    image_result_inline,
    main_menu,
    welcome_text,
)

logger = logging.getLogger(__name__)

META_STYLE_MARKERS = (
    "input:",
    "context:",
    "goal:",
    "language:",
    "tone:",
    "role:",
    "style:",
    "user says:",
    "acknowledge the greeting",
    "offer assistance",
    "option 1:",
    "option 2:",
    "option 3:",
    "گزینه 1",
    "گزینه 2",
    "گزینه 3",
    "keep it short",
)

ACTION_IMAGE_PROMPT = "image_prompt"
ACTION_STT_AUDIO = "stt_audio"
CALLBACK_IMAGE_REGEN = "img:regen:"
CALLBACK_IMAGE_PROMPT = "img:prompt:"
CALLBACK_IMAGE_FEEDBACK = "img:fb:"


@dataclass(slots=True)
class PendingAudioContext:
    incoming: IncomingMessage
    mode: str  # transcription_only | audio_analysis


class BaleAIBotService:
    def __init__(
        self,
        settings: Settings,
        bale_client: BaleBotApiClient,
        ai_client: UnifiedAIGatewayClient,
        storage: ChatStorage,
    ) -> None:
        self.settings = settings
        self.bale_client = bale_client
        self.ai_client = ai_client
        self.storage = storage
        self._stop_event = asyncio.Event()
        self._flow_logs = bool(getattr(settings, "log_flow_enabled", True))
        self._text_preview_chars = int(getattr(settings, "log_text_preview_chars", 160))
        self._pending_actions: dict[str, str] = {}
        self._pending_audio_context: dict[str, PendingAudioContext] = {}

    async def run(self) -> None:
        await self.storage.init()
        if not self._is_transcription_provider_ready():
            logger.warning(
                "stt provider is not ready: provider=%s; set UAG_GROQ_API_KEYS/GROQ_API_KEY for audio features",
                self.settings.ai_transcription_provider,
            )
        self._log_flow(
            "service_started",
            app_env=self.settings.app_env,
            chat_models=self.settings.ai_chat_models,
            history_limit=self.settings.chat_history_max_messages,
            chat_proxy=f"{self.settings.uag_base_url}{self.settings.uag_chat_endpoint}",
        )

        offset: int | None = None
        while not self._stop_event.is_set():
            try:
                updates = await self.bale_client.get_updates(
                    offset=offset,
                    timeout=self.settings.bale_poll_timeout_sec,
                    allowed_updates=self.settings.bale_allowed_updates,
                )
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    parsed = parse_update(update)
                    if parsed is None:
                        continue
                    self._log_flow(
                        "incoming_update",
                        update_id=parsed.update_id,
                        user_id=parsed.user_id,
                        chat_id=parsed.chat_id,
                        content_type=parsed.content_type.value,
                    )
                    await self.handle_incoming(parsed)
            except PlatformApiError as exc:
                logger.warning("Bale poll error: %s", exc)
                await self._record_system_event(
                    event_type="poll_loop_error",
                    status="failed",
                    error_code="bale_api_error",
                    details={"error": str(exc)},
                )
                await asyncio.sleep(2)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                logger.warning("Network error while polling Bale: %s", exc)
                await self._record_system_event(
                    event_type="poll_loop_error",
                    status="failed",
                    error_code="network_error",
                    details={"error": str(exc)},
                )
                await asyncio.sleep(1)
            except Exception:
                logger.exception("Unexpected error in poll loop")
                await self._record_system_event(
                    event_type="poll_loop_error",
                    status="failed",
                    error_code="unexpected_error",
                    details={"error": "unexpected error in poll loop"},
                )
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._stop_event.set()
        await self.storage.aclose()
        await self.ai_client.aclose()
        await self.bale_client.aclose()

    async def handle_incoming(self, incoming: IncomingMessage) -> None:
        await self.storage.ensure_user(
            user_id=incoming.user_id,
            chat_id=incoming.chat_id,
            username=incoming.username,
            display_name=incoming.display_name,
            default_model=self.settings.ai_chat_models[0],
        )

        if incoming.content_type == ContentType.CALLBACK:
            await self._handle_callback(incoming)
            return

        if incoming.content_type == ContentType.PHOTO:
            await self._handle_photo_message(incoming)
            return

        if incoming.content_type == ContentType.AUDIO:
            await self._handle_audio_message(incoming)
            return

        if incoming.content_type != ContentType.TEXT:
            await self._record_bot_event(
                event_type="unsupported_message",
                status="failed",
                incoming=incoming,
                error_code="unsupported_content_type",
            )
            raw_message = incoming.raw.get("message") if isinstance(incoming.raw, dict) else {}
            if isinstance(raw_message, dict):
                self._log_flow(
                    "unsupported_message",
                    user_id=incoming.user_id,
                    chat_id=incoming.chat_id,
                    keys=sorted(raw_message.keys()),
                )
            await self._send_message(
                incoming.chat_id,
                "این نوع پیام پشتیبانی نمی‌شود. لطفاً متن، عکس یا ویس ارسال کنید.",
            )
            return

        text = (incoming.text or "").strip()
        if not text:
            return
        self._log_flow(
            "incoming_text",
            user_id=incoming.user_id,
            message_id=incoming.message_id,
            text_preview=self._preview(text),
            text_len=len(text),
            pending_action=self._pending_actions.get(incoming.user_id, ""),
        )

        if text.startswith("/"):
            await self._handle_command(incoming, text)
            return

        if text in {BTN_NEW_CHAT, BTN_CHAT, BTN_IMAGE_GENERATION, BTN_TRANSCRIBE, BTN_CANCEL, BTN_HELP}:
            await self._handle_menu_action(incoming, text)
            return

        if incoming.user_id in self._pending_audio_context:
            await self._handle_audio_topic_reply(incoming, text)
            return

        pending_action = self._pending_actions.get(incoming.user_id)
        if pending_action == ACTION_IMAGE_PROMPT:
            await self._handle_image_generation_prompt(incoming, text)
            return
        if pending_action == ACTION_STT_AUDIO:
            await self._send_message(
                incoming.chat_id,
                "در مسیر تبدیل ویس به متن هستی. لطفاً ویس بفرست یا «❌ لغو عملیات» را بزن.",
                reply_markup=cancel_only_menu(),
            )
            return

        await self._handle_chat_text(incoming, text)

    async def _handle_command(self, incoming: IncomingMessage, text: str) -> None:
        cmd = text.split(maxsplit=1)[0].lower()
        if cmd == "/start":
            await self._clear_pending(incoming.user_id)
            await self._show_welcome(incoming.chat_id)
            if self.settings.ui_show_help_on_start:
                await self._send_message(
                    incoming.chat_id,
                    help_text(max_voice_minutes=max(1, self.settings.media_max_voice_sec // 60)),
                )
            return

        if cmd == "/help":
            await self._send_message(
                incoming.chat_id,
                help_text(max_voice_minutes=max(1, self.settings.media_max_voice_sec // 60)),
            )
            return

        if cmd in {"/new", "/clear"}:
            await self.storage.clear_chat(incoming.user_id)
            await self._send_message(incoming.chat_id, "تاریخچه این گفتگو پاک شد.")
            return

        if cmd in {"/image", "/img"}:
            await self._enter_image_mode(incoming.chat_id, incoming.user_id)
            return

        if cmd in {"/stt", "/transcribe"}:
            await self._enter_stt_mode(incoming.chat_id, incoming.user_id)
            return

        if cmd in {"/chat", "/normal"}:
            await self._clear_pending(incoming.user_id)
            await self._send_message(incoming.chat_id, "به حالت چت هوشمند برگشتی 💬")
            return

        if cmd in {"/cancel", "/back"}:
            await self._clear_pending(incoming.user_id)
            await self._send_message(incoming.chat_id, "عملیات مرحله‌ای لغو شد.")
            return

        await self._send_message(incoming.chat_id, "دستور ناشناخته است. /help را بزنید.")

    async def _handle_menu_action(self, incoming: IncomingMessage, text: str) -> None:
        if text == BTN_NEW_CHAT:
            await self.storage.clear_chat(incoming.user_id)
            await self._send_message(incoming.chat_id, "تاریخچه این گفتگو پاک شد.")
            return

        if text == BTN_CHAT:
            await self._clear_pending(incoming.user_id)
            await self._send_message(incoming.chat_id, "حالت چت هوشمند فعال شد 💬")
            return

        if text == BTN_IMAGE_GENERATION:
            await self._enter_image_mode(incoming.chat_id, incoming.user_id)
            return

        if text == BTN_TRANSCRIBE:
            await self._enter_stt_mode(incoming.chat_id, incoming.user_id)
            return

        if text == BTN_CANCEL:
            await self._clear_pending(incoming.user_id)
            await self._send_message(incoming.chat_id, "عملیات مرحله‌ای لغو شد.")
            return

        if text == BTN_HELP:
            await self._send_message(
                incoming.chat_id,
                help_text(max_voice_minutes=max(1, self.settings.media_max_voice_sec // 60)),
            )

    async def _handle_callback(self, incoming: IncomingMessage) -> None:
        data = (incoming.callback_data or "").strip()
        if not data:
            await self.bale_client.answer_callback_query(incoming.callback_query_id or "")
            return

        if data.startswith(CALLBACK_IMAGE_REGEN):
            generation_id = data[len(CALLBACK_IMAGE_REGEN) :].strip()
            await self.bale_client.answer_callback_query(incoming.callback_query_id or "", text="در حال تولید مجدد تصویر...")
            await self._handle_image_regenerate(incoming, generation_id)
            return

        if data.startswith(CALLBACK_IMAGE_PROMPT):
            generation_id = data[len(CALLBACK_IMAGE_PROMPT) :].strip()
            await self.bale_client.answer_callback_query(incoming.callback_query_id or "", text="پرامپت بهبود‌یافته ارسال شد.")
            await self._handle_image_prompt_show(incoming, generation_id)
            return

        if data.startswith(CALLBACK_IMAGE_FEEDBACK):
            payload = data[len(CALLBACK_IMAGE_FEEDBACK) :].strip()
            parts = payload.split(":", 1)
            if len(parts) != 2:
                await self.bale_client.answer_callback_query(incoming.callback_query_id or "", text="داده دکمه نامعتبر است.")
                return
            feedback_type, generation_id = parts[0].strip(), parts[1].strip()
            ok = await self._handle_image_feedback(incoming, generation_id, feedback_type)
            await self.bale_client.answer_callback_query(
                incoming.callback_query_id or "",
                text="فیدبک ثبت شد 🙏" if ok else "ثبت فیدبک انجام نشد.",
            )
            return

        await self.bale_client.answer_callback_query(
            incoming.callback_query_id or "",
            text="دکمه نامشخص است.",
        )

    async def _enter_image_mode(self, chat_id: str, user_id: str) -> None:
        self._pending_actions[user_id] = ACTION_IMAGE_PROMPT
        await self._send_message(
            chat_id,
            "حالت تولید تصویر فعال شد 🖼️\n"
            "پرامپتت را بفرست تا تصویر بسازم.\n"
            "برای خروج: «❌ لغو عملیات»",
            reply_markup=cancel_only_menu(),
        )

    async def _enter_stt_mode(self, chat_id: str, user_id: str) -> None:
        self._pending_actions[user_id] = ACTION_STT_AUDIO
        await self._send_message(
            chat_id,
            "حالت تبدیل ویس به متن فعال شد 🎙️\n"
            f"ویس را بفرست (حداکثر {self.settings.media_max_voice_sec // 60} دقیقه).\n"
            "برای خروج: «❌ لغو عملیات»",
            reply_markup=cancel_only_menu(),
        )

    async def _clear_pending(self, user_id: str) -> None:
        self._pending_actions.pop(user_id, None)
        self._pending_audio_context.pop(user_id, None)

    async def _handle_photo_message(self, incoming: IncomingMessage) -> None:
        caption = (incoming.caption or "").strip()
        prompt_text = caption or "این تصویر را دقیق، خلاصه و کاربردی تشریح کن."
        self._log_flow(
            "incoming_photo",
            user_id=incoming.user_id,
            message_id=incoming.message_id,
            has_caption=bool(caption),
            caption_preview=self._preview(caption),
            file_id=incoming.source_file_id or "",
            file_size=incoming.source_file_size or 0,
        )

        await self._handle_chat_message(
            incoming,
            text=prompt_text,
            models=self.settings.ai_image_analysis_models,
            providers=self.settings.ai_image_analysis_providers,
            router=self.settings.router_image_analysis,
            system_prompt=self.settings.image_analysis_system_prompt,
            use_image=True,
            history_user_content=caption or "[photo uploaded for analysis]",
        )

    async def _handle_audio_message(self, incoming: IncomingMessage) -> None:
        if not self._is_transcription_provider_ready():
            await self._send_message(
                incoming.chat_id,
                "کلید سرویس تبدیل ویس به متن تنظیم نشده یا نامعتبر است. لطفاً به ادمین اطلاع بده.",
            )
            await self._record_bot_event(
                event_type="audio_message",
                status="failed",
                incoming=incoming,
                error_code="stt_provider_not_configured",
            )
            return

        mode = "transcription_only" if self._pending_actions.get(incoming.user_id) == ACTION_STT_AUDIO else "audio_analysis"
        self._pending_audio_context[incoming.user_id] = PendingAudioContext(incoming=incoming, mode=mode)

        if mode == "audio_analysis":
            await self._consume_pending_audio_with_topic(
                incoming.user_id,
                getattr(self.settings, "audio_topic_default_text", "نامشخص"),
            )
            return

        if bool(getattr(self.settings, "audio_topic_ask_enabled", False)):
            await self._send_message(
                incoming.chat_id,
                getattr(
                    self.settings,
                    "ui_audio_topic_question_text",
                    "🎙️ ویس دریافت شد. لطفاً بگو این ویس دربارهٔ چیه؟",
                ),
                reply_markup=audio_topic_menu(),
            )
            return

        await self._consume_pending_audio_with_topic(
            incoming.user_id,
            getattr(self.settings, "audio_topic_default_text", "نامشخص"),
        )

    async def _handle_chat_text(self, incoming: IncomingMessage, text: str) -> None:
        await self._handle_chat_message(
            incoming,
            text=text,
            models=self.settings.ai_chat_models,
            providers=self.settings.ai_chat_providers,
            router=self.settings.router_chat,
            system_prompt=self.settings.chat_system_prompt,
            use_image=False,
            history_user_content=text,
        )

    async def _handle_audio_topic_reply(self, incoming: IncomingMessage, text: str) -> None:
        topic = (text or "").strip()
        if topic == BTN_SKIP_AUDIO_TOPIC:
            topic = getattr(self.settings, "audio_topic_default_text", "نامشخص")
        await self._consume_pending_audio_with_topic(
            incoming.user_id,
            topic or getattr(self.settings, "audio_topic_default_text", "نامشخص"),
        )

    async def _consume_pending_audio_with_topic(self, user_id: str, topic: str) -> None:
        pending = self._pending_audio_context.pop(user_id, None)
        if pending is None:
            return

        if pending.mode == "transcription_only":
            await self._handle_audio_transcription_only(pending.incoming, topic=topic)
            return
        await self._handle_audio_analysis(pending.incoming, topic=topic)

    async def _handle_image_generation_prompt(self, incoming: IncomingMessage, prompt: str) -> None:
        started = time.monotonic()
        stage_message_id = await self._send_thinking_message(
            incoming.chat_id,
            getattr(self.settings, "ui_image_stage_processing_text", "🧠 در حال پردازش پرامپت..."),
        )
        self._log_flow(
            "image_generation_start",
            user_id=incoming.user_id,
            models=self.settings.ai_image_generation_models,
            prompt_preview=self._preview(prompt),
        )

        try:
            stage_message_id = await self._advance_stage(
                incoming.chat_id,
                stage_message_id,
                getattr(self.settings, "ui_image_stage_enhancing_text", "✨ در حال بهبود پرامپت..."),
            )
            optimized_prompt = await self._enhance_image_prompt(prompt)
            stage_message_id = await self._advance_stage(
                incoming.chat_id,
                stage_message_id,
                getattr(self.settings, "ui_image_stage_generating_text", "🎨 در حال تولید تصویر..."),
            )
            seed = random.randint(
                int(getattr(self.settings, "ai_image_regen_seed_min", 1000)),
                int(getattr(self.settings, "ai_image_regen_seed_max", 999999999)),
            )
            result = await self.ai_client.generate_image(
                models=self.settings.ai_image_generation_models,
                providers=self.settings.ai_image_generation_providers,
                prompt=optimized_prompt,
                router=self._router(self.settings.router_image_generation),
                size=self.settings.ai_image_generation_size,
                quality=self.settings.ai_image_generation_quality,
                n=self.settings.ai_image_generation_count,
                seed=seed,
            )
        except AIBackendError as exc:
            await self._safe_delete_message(incoming.chat_id, stage_message_id)
            await self._send_message(incoming.chat_id, self._friendly_ai_error(exc, feature="image_generation"))
            await self._record_bot_event(
                event_type="image_generation",
                status="failed",
                incoming=incoming,
                error_code=self._classify_error_code(exc),
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return
        except Exception:
            logger.exception("Unexpected image generation failure")
            await self._safe_delete_message(incoming.chat_id, stage_message_id)
            await self._send_message(incoming.chat_id, "خطای غیرمنتظره در تولید تصویر رخ داد. دوباره تلاش کن.")
            await self._record_bot_event(
                event_type="image_generation",
                status="failed",
                incoming=incoming,
                error_code="unexpected_error",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return

        await self._safe_delete_message(incoming.chat_id, stage_message_id)
        await self._clear_pending(incoming.user_id)

        generation_id = uuid4().hex[:16]
        await self.storage.save_image_generation(
            generation_id=generation_id,
            user_id=incoming.user_id,
            chat_id=incoming.chat_id,
            original_prompt=prompt,
            enhanced_prompt=optimized_prompt,
            revised_prompt=result.revised_prompt or None,
            model=(self.settings.ai_image_generation_models[0] if self.settings.ai_image_generation_models else None),
            provider=(self.settings.ai_image_generation_providers[0] if self.settings.ai_image_generation_providers else None),
            image_size=self.settings.ai_image_generation_size,
            image_quality=self.settings.ai_image_generation_quality,
            image_seed=seed,
        )

        try:
            if result.image_bytes:
                await self._send_photo(
                    incoming.chat_id,
                    image_bytes=result.image_bytes,
                    caption="✅ تصویر آماده شد",
                    reply_markup=image_result_inline(generation_id),
                )
            elif result.image_url:
                await self._send_photo(
                    incoming.chat_id,
                    image_url=result.image_url,
                    caption="✅ تصویر آماده شد",
                    reply_markup=image_result_inline(generation_id),
                )
            else:
                await self._send_message(incoming.chat_id, "تصویر تولید شد، ولی خروجی قابل ارسال نبود. لطفاً دوباره تلاش کن.")
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("send photo failed: %s", exc)
            await self._send_message(
                incoming.chat_id,
                "تصویر تولید شد ولی ارسال عکس ناموفق بود.\n"
                "اگر خواستی دوباره تلاش می‌کنم.",
            )
            await self._record_bot_event(
                event_type="image_generation",
                status="failed",
                incoming=incoming,
                error_code="send_photo_failed",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return
        await self._record_bot_event(
            event_type="image_generation",
            status="ok",
            incoming=incoming,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )

    async def _handle_audio_transcription_only(self, incoming: IncomingMessage, *, topic: str) -> None:
        started = time.monotonic()
        if not self._is_transcription_provider_ready():
            await self._send_message(
                incoming.chat_id,
                "کلید سرویس تبدیل ویس به متن تنظیم نشده یا نامعتبر است. لطفاً به ادمین اطلاع بده.",
            )
            await self._record_bot_event(
                event_type="audio_transcription_only",
                status="failed",
                incoming=incoming,
                error_code="stt_provider_not_configured",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return

        stage_message_id = await self._send_thinking_message(
            incoming.chat_id,
            getattr(self.settings, "ui_audio_stage_transcribing_text", "📝 در حال تبدیل ویس به متن..."),
        )

        try:
            audio_bytes, filename, mime_type, measured_duration_sec = await self._prepare_audio_binary(incoming)
            if self._is_voice_too_long(incoming, measured_duration_sec=measured_duration_sec):
                self._log_flow(
                    "voice_rejected_too_long",
                    user_id=incoming.user_id,
                    message_id=incoming.message_id,
                    measured_duration_sec=measured_duration_sec or 0,
                    meta_duration_sec=incoming.source_duration_sec or 0,
                    file_size=incoming.source_file_size or 0,
                    mime=incoming.source_mime_type or "",
                )
                await self._safe_delete_message(incoming.chat_id, stage_message_id)
                await self._send_message(
                    incoming.chat_id,
                    f"⛔️ طول ویس بیشتر از حد مجاز است. حداکثر زمان: {self.settings.media_max_voice_sec // 60} دقیقه.",
                )
                await self._record_bot_event(
                    event_type="audio_transcription_only",
                    status="failed",
                    incoming=incoming,
                    error_code="voice_too_long",
                    latency_ms=(time.monotonic() - started) * 1000.0,
                )
                return
            transcript = await self.ai_client.transcribe_audio(
                audio_bytes=audio_bytes,
                filename=filename,
                mime_type=mime_type,
                provider=self.settings.ai_transcription_provider,
                language="fa",
                model_preferences=self.settings.ai_transcription_models,
            )
            stage_message_id = await self._advance_stage(
                incoming.chat_id,
                stage_message_id,
                getattr(self.settings, "ui_audio_stage_cleaning_text", "🧹 در حال حذف نویز و پاک‌سازی متن..."),
            )
            cleaned_text = await self._cleanup_transcript(raw_transcript=transcript, topic=topic)
        except AIBackendError as exc:
            await self._safe_delete_message(incoming.chat_id, stage_message_id)
            await self._send_message(incoming.chat_id, self._friendly_ai_error(exc, feature="transcription"))
            await self._record_bot_event(
                event_type="audio_transcription_only",
                status="failed",
                incoming=incoming,
                error_code=self._classify_error_code(exc),
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return
        except Exception as exc:
            logger.warning("Audio transcription failed: %s", exc)
            await self._safe_delete_message(incoming.chat_id, stage_message_id)
            await self._send_message(incoming.chat_id, "خواندن ویس انجام نشد. لطفاً دوباره تلاش کن.")
            await self._record_bot_event(
                event_type="audio_transcription_only",
                status="failed",
                incoming=incoming,
                error_code="unexpected_error",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return

        await self._safe_delete_message(incoming.chat_id, stage_message_id)
        await self._clear_pending(incoming.user_id)

        clean_text = cleaned_text.strip()
        if not clean_text:
            await self._send_message(incoming.chat_id, "متن قابل تشخیص از ویس به دست نیامد. لطفاً واضح‌تر بفرست.")
            await self._record_bot_event(
                event_type="audio_transcription_only",
                status="failed",
                incoming=incoming,
                error_code="empty_transcript",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return

        request_id = uuid4().hex[:16]
        await self.storage.save_voice_transcription(
            request_id=request_id,
            user_id=incoming.user_id,
            chat_id=incoming.chat_id,
            mode="transcription_only",
            topic=topic,
            raw_transcript=transcript.strip(),
            cleaned_transcript=clean_text,
            analysis_reply=None,
        )
        await self._send_message(incoming.chat_id, clean_text)
        await self._send_text_as_markdown_file(
            incoming.chat_id,
            text=clean_text,
            filename=f"voice_transcript_{request_id}.txt",
        )
        await self._record_bot_event(
            event_type="audio_transcription_only",
            status="ok",
            incoming=incoming,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )

    async def _handle_audio_analysis(self, incoming: IncomingMessage, *, topic: str) -> None:
        started = time.monotonic()
        if not self._is_transcription_provider_ready():
            await self._send_message(
                incoming.chat_id,
                "کلید سرویس تبدیل ویس به متن تنظیم نشده یا نامعتبر است. لطفاً به ادمین اطلاع بده.",
            )
            await self._record_bot_event(
                event_type="audio_analysis",
                status="failed",
                incoming=incoming,
                error_code="stt_provider_not_configured",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return

        stage_message_id = await self._send_thinking_message(incoming.chat_id)

        raw_history = await self.storage.get_recent_chat(incoming.user_id, self.settings.chat_history_max_messages)
        history = self._sanitize_history(raw_history)

        try:
            audio_bytes, filename, mime_type, measured_duration_sec = await self._prepare_audio_binary(incoming)
            if self._is_voice_too_long(incoming, measured_duration_sec=measured_duration_sec):
                self._log_flow(
                    "voice_rejected_too_long",
                    user_id=incoming.user_id,
                    message_id=incoming.message_id,
                    measured_duration_sec=measured_duration_sec or 0,
                    meta_duration_sec=incoming.source_duration_sec or 0,
                    file_size=incoming.source_file_size or 0,
                    mime=incoming.source_mime_type or "",
                )
                await self._safe_delete_message(incoming.chat_id, stage_message_id)
                await self._send_message(
                    incoming.chat_id,
                    f"⛔️ طول ویس بیشتر از حد مجاز است. حداکثر زمان: {self.settings.media_max_voice_sec // 60} دقیقه.",
                )
                await self._record_bot_event(
                    event_type="audio_analysis",
                    status="failed",
                    incoming=incoming,
                    error_code="voice_too_long",
                    latency_ms=(time.monotonic() - started) * 1000.0,
                )
                return
            transcript = await self.ai_client.transcribe_audio(
                audio_bytes=audio_bytes,
                filename=filename,
                mime_type=mime_type,
                provider=self.settings.ai_transcription_provider,
                language="fa",
                model_preferences=self.settings.ai_transcription_models,
            )
            cleaned_transcript = await self._cleanup_transcript(raw_transcript=transcript, topic=topic)
            reply = await self.ai_client.generate_reply(
                models=self.settings.ai_chat_models,
                providers=self.settings.ai_chat_providers,
                user_message=cleaned_transcript,
                history=history,
                system_prompt=self.settings.chat_system_prompt,
                router=self._router(self.settings.router_chat),
                image_inline_data=None,
            )
        except AIBackendError as exc:
            await self._safe_delete_message(incoming.chat_id, stage_message_id)
            await self._send_message(incoming.chat_id, self._friendly_ai_error(exc, feature="audio_analysis"))
            await self._record_bot_event(
                event_type="audio_analysis",
                status="failed",
                incoming=incoming,
                error_code=self._classify_error_code(exc),
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return
        except Exception:
            logger.exception("Unexpected audio cleanup failure")
            await self._safe_delete_message(incoming.chat_id, stage_message_id)
            await self._send_message(incoming.chat_id, "خطای غیرمنتظره در پردازش ویس رخ داد. دوباره تلاش کن.")
            await self._record_bot_event(
                event_type="audio_analysis",
                status="failed",
                incoming=incoming,
                error_code="unexpected_error",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return

        cleaned_transcript = cleaned_transcript.strip()
        if not cleaned_transcript:
            await self._safe_delete_message(incoming.chat_id, stage_message_id)
            await self._send_message(incoming.chat_id, "متن قابل تشخیص از ویس به دست نیامد. لطفاً واضح‌تر بفرست.")
            await self._record_bot_event(
                event_type="audio_analysis",
                status="failed",
                incoming=incoming,
                error_code="empty_transcript",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return

        request_id = uuid4().hex[:16]
        await self.storage.save_voice_transcription(
            request_id=request_id,
            user_id=incoming.user_id,
            chat_id=incoming.chat_id,
            mode="audio_analysis",
            topic=topic,
            raw_transcript=transcript.strip(),
            cleaned_transcript=cleaned_transcript,
            analysis_reply=reply,
        )
        await self.storage.append_chat_message(
            incoming.user_id,
            role="user",
            content=f"[voice topic={topic or 'نامشخص'}]\n{cleaned_transcript}",
            keep_last=self.settings.chat_history_max_messages,
        )
        await self.storage.append_chat_message(
            incoming.user_id,
            role="assistant",
            content=reply,
            keep_last=self.settings.chat_history_max_messages,
        )
        await self.storage.log_ai_request(
            user_id=incoming.user_id,
            model="|".join(self.settings.ai_chat_models),
            user_prompt=cleaned_transcript,
            assistant_reply=reply,
            error_text=None,
        )

        await self._safe_delete_message(incoming.chat_id, stage_message_id)
        await self._send_message(incoming.chat_id, self._sanitize_model_reply(reply))
        await self._record_bot_event(
            event_type="audio_analysis",
            status="ok",
            incoming=incoming,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )

    async def _handle_chat_message(
        self,
        incoming: IncomingMessage,
        *,
        text: str,
        models: list[str],
        providers: list[str],
        router: RouterSettings,
        system_prompt: str,
        use_image: bool,
        history_user_content: str,
    ) -> None:
        started = time.monotonic()
        thinking_message_id = await self._send_thinking_message(incoming.chat_id)

        raw_history = await self.storage.get_recent_chat(incoming.user_id, self.settings.chat_history_max_messages)
        history = self._sanitize_history(raw_history)
        self._log_flow(
            "chat_message_start",
            user_id=incoming.user_id,
            models=models,
            providers=providers,
            use_image=use_image,
            history_raw_len=len(raw_history),
            history_clean_len=len(history),
            prompt_preview=self._preview(text),
        )
        image_inline_data: dict | None = None

        try:
            if use_image:
                image_inline_data = await self._prepare_image_inline_data(incoming)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Image preparation failed: %s", exc)
            await self._safe_delete_message(incoming.chat_id, thinking_message_id)
            await self._send_message(
                incoming.chat_id,
                "خواندن تصویر انجام نشد. لطفاً عکس را دوباره ارسال کن.",
            )
            return

        try:
            reply = await self.ai_client.generate_reply(
                models=models,
                providers=providers,
                user_message=text,
                history=history,
                system_prompt=system_prompt,
                router=self._router(router),
                image_inline_data=image_inline_data,
            )
        except AIBackendError as exc:
            logger.warning("uag generation failed: %s", exc)
            await self.storage.log_ai_request(
                user_id=incoming.user_id,
                model="|".join(models),
                user_prompt=text,
                assistant_reply=None,
                error_text=str(exc),
            )
            await self._safe_delete_message(incoming.chat_id, thinking_message_id)
            await self._send_message(incoming.chat_id, self._friendly_ai_error(exc, feature="chat"))
            await self._record_bot_event(
                event_type="chat_response",
                status="failed",
                incoming=incoming,
                error_code=self._classify_error_code(exc),
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return
        except Exception:
            logger.exception("Unexpected UAG failure")
            await self._safe_delete_message(incoming.chat_id, thinking_message_id)
            await self._send_message(
                incoming.chat_id,
                "یک خطای غیرمنتظره رخ داد. لطفاً دوباره تلاش کن.",
            )
            await self._record_bot_event(
                event_type="chat_response",
                status="failed",
                incoming=incoming,
                error_code="unexpected_error",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            return

        await self.storage.append_chat_message(
            incoming.user_id,
            role="user",
            content=history_user_content,
            keep_last=self.settings.chat_history_max_messages,
        )
        await self.storage.append_chat_message(
            incoming.user_id,
            role="assistant",
            content=reply,
            keep_last=self.settings.chat_history_max_messages,
        )
        await self.storage.log_ai_request(
            user_id=incoming.user_id,
            model="|".join(models),
            user_prompt=text,
            assistant_reply=reply,
            error_text=None,
        )

        reply = self._sanitize_model_reply(reply)
        self._log_flow(
            "chat_message_done",
            user_id=incoming.user_id,
            models=models,
            providers=providers,
            reply_len=len(reply),
            reply_preview=self._preview(reply),
        )
        await self._safe_delete_message(incoming.chat_id, thinking_message_id)
        await self._send_message(incoming.chat_id, reply)
        await self._record_bot_event(
            event_type="chat_response",
            status="ok",
            incoming=incoming,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )

    async def _show_welcome(self, chat_id: str) -> None:
        await self._send_message(
            chat_id,
            welcome_text(
                history_limit=self.settings.chat_history_max_messages,
            ),
        )

    async def _send_thinking_message(self, chat_id: str, text: str | None = None) -> int | None:
        try:
            result = await self.bale_client.send_message(
                chat_id,
                text or self.settings.ui_thinking_text,
                reply_markup=None,
            )
            return int(result.get("message_id", 0)) or None
        except Exception:  # noqa: BLE001
            return None

    async def _safe_delete_message(self, chat_id: str, message_id: int | None) -> None:
        if not message_id:
            return
        try:
            await self.bale_client.delete_message(chat_id, message_id)
        except Exception:  # noqa: BLE001
            return

    async def _prepare_image_inline_data(self, incoming: IncomingMessage) -> dict[str, str]:
        if not incoming.source_file_id:
            raise ValueError("missing photo file id")

        file_meta = await self.bale_client.get_file(incoming.source_file_id)
        file_path = str(file_meta.get("file_path") or "").strip()
        if not file_path:
            raise ValueError("file_path not returned by getFile")

        file_size = int(file_meta.get("file_size") or incoming.source_file_size or 0)
        if file_size > self.settings.media_max_image_mb * 1024 * 1024:
            raise ValueError("image size exceeds configured limit")

        suffix = Path(file_path).suffix or ".bin"
        tmp_path = Path(self.settings.media_tmp_dir) / f"{incoming.user_id}_{incoming.message_id}_{uuid4().hex}{suffix}"
        await self.bale_client.download_file(file_path, tmp_path)
        raw = tmp_path.read_bytes()
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

        mime_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
        return {
            "mimeType": mime_type,
            "data": base64.b64encode(raw).decode("ascii"),
        }

    async def _prepare_audio_binary(self, incoming: IncomingMessage) -> tuple[bytes, str, str, int | None]:
        if not incoming.source_file_id:
            raise ValueError("missing audio file id")

        file_meta = await self.bale_client.get_file(incoming.source_file_id)
        file_path = str(file_meta.get("file_path") or "").strip()
        if not file_path:
            raise ValueError("file_path not returned by getFile")

        suffix = Path(file_path).suffix or ".ogg"
        raw_filename = incoming.source_file_name or Path(file_path).name or f"voice{suffix}"
        tmp_path = Path(self.settings.media_tmp_dir) / f"{incoming.user_id}_{incoming.message_id}_{uuid4().hex}{suffix}"

        await self.bale_client.download_file(file_path, tmp_path)
        measured_duration_sec = self._probe_audio_duration_seconds(tmp_path)
        raw = tmp_path.read_bytes()
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

        mime_hint = incoming.source_mime_type or mimetypes.guess_type(raw_filename)[0] or ""
        filename, mime_type = self._normalize_audio_upload_metadata(raw_filename, mime_hint)
        return raw, filename, mime_type, measured_duration_sec

    @staticmethod
    def _normalize_audio_upload_metadata(filename: str, mime_type: str) -> tuple[str, str]:
        allowed = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".opus", ".wav", ".webm"}
        ext_to_mime = {
            ".flac": "audio/flac",
            ".mp3": "audio/mpeg",
            ".mp4": "audio/mp4",
            ".mpeg": "audio/mpeg",
            ".mpga": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".ogg": "audio/ogg",
            ".opus": "audio/ogg",
            ".wav": "audio/wav",
            ".webm": "audio/webm",
        }
        mime_to_ext = {
            "audio/flac": ".flac",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/mp4": ".mp4",
            "audio/x-m4a": ".m4a",
            "audio/aac": ".m4a",
            "audio/ogg": ".ogg",
            "audio/opus": ".opus",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/webm": ".webm",
            "application/ogg": ".ogg",
        }

        name = (filename or "voice").strip()
        mime = (mime_type or "").strip().lower()
        ext = Path(name).suffix.lower()

        # Normalize aliases not accepted by Groq validation.
        if ext == ".oga":
            ext = ".ogg"

        if ext not in allowed:
            ext = mime_to_ext.get(mime, ".ogg")
            base = Path(name).stem or "voice"
            name = f"{base}{ext}"
        elif Path(name).suffix.lower() != ext:
            base = Path(name).stem or "voice"
            name = f"{base}{ext}"

        if not mime.startswith("audio/"):
            mime = ext_to_mime.get(ext, "audio/ogg")
        elif mime == "application/ogg":
            mime = "audio/ogg"

        return name, mime

    def _is_voice_too_long(self, incoming: IncomingMessage, *, measured_duration_sec: int | None = None) -> bool:
        limit = int(self.settings.media_max_voice_sec)
        if measured_duration_sec is not None and measured_duration_sec > 0:
            duration = int(measured_duration_sec)
        else:
            duration = self._normalized_audio_duration_seconds(incoming, limit)
        if duration <= 0:
            return False
        if measured_duration_sec is None and duration > limit:
            size_bytes = int(incoming.source_file_size or 0)
            # With metadata-only duration, avoid false positives on very small files.
            # At 2 kbps, a file below ~limit*250 bytes cannot realistically exceed the limit.
            if 0 < size_bytes < (limit * 250):
                return False
        return duration > limit

    @staticmethod
    def _normalized_audio_duration_seconds(incoming: IncomingMessage, limit: int) -> int:
        raw = int(incoming.source_duration_sec or 0)
        if raw <= 0:
            return 0
        if raw <= limit:
            return raw

        size_bytes = int(incoming.source_file_size or 0)
        # Some clients/providers may send duration in ms/us/ns.
        for divisor in (1_000, 1_000_000, 1_000_000_000):
            if raw < divisor or raw % divisor != 0:
                continue
            candidate = raw // divisor
            if candidate <= 0 or candidate > limit:
                continue

            if size_bytes > 0:
                bitrate_bps = (size_bytes * 8.0) / max(candidate, 1)
                # Reject obviously impossible bitrate after normalization.
                if bitrate_bps < 2_000 or bitrate_bps > 1_500_000:
                    continue
            else:
                # Without size hint, only trust down-scaling when raw is far above limit.
                if raw < (limit * 3):
                    continue
            return candidate
        return raw

    def _probe_audio_duration_seconds(self, file_path: Path) -> int | None:
        try:
            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(file_path),
                ],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except Exception:
            return None

        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            payload = json.loads(proc.stdout)
            duration_raw = ((payload.get("format") or {}).get("duration")) if isinstance(payload, dict) else None
            duration = float(duration_raw)
            if duration <= 0:
                return None
            return int(round(duration))
        except Exception:
            return None

    @staticmethod
    def _router(settings: RouterSettings) -> UAGRouterConfig:
        return UAGRouterConfig(
            providers=list(settings.providers),
            strategy=settings.strategy,
            mode=settings.mode,
            timeout_sec=settings.timeout_sec,
            max_attempts=settings.max_attempts,
        )

    async def _enhance_image_prompt(self, user_prompt: str) -> str:
        prompt = (user_prompt or "").strip()
        if not prompt:
            return "High-quality cinematic portrait, detailed lighting, clean composition, 4k."

        try:
            enhanced = await self.ai_client.generate_reply(
                models=self.settings.ai_image_prompt_enhancer_models,
                providers=self.settings.ai_image_prompt_enhancer_providers,
                user_message=prompt,
                history=[],
                system_prompt=self.settings.image_prompt_enhancer_system_prompt,
                router=self._router(self.settings.router_image_prompt_enhancer),
                image_inline_data=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("image prompt enhancement failed, using original prompt: %s", exc)
            return prompt

        cleaned = " ".join((enhanced or "").split()).strip().strip('"')
        if not cleaned:
            return prompt
        if re.search(r"[آ-ی]", cleaned):
            try:
                second_pass = await self.ai_client.generate_reply(
                    models=self.settings.ai_image_prompt_enhancer_models,
                    providers=self.settings.ai_image_prompt_enhancer_providers,
                    user_message=f"Translate and optimize this for image generation in English only: {cleaned}",
                    history=[],
                    system_prompt="Return only one final English image-generation prompt. No explanation.",
                    router=self._router(self.settings.router_image_prompt_enhancer),
                    image_inline_data=None,
                )
                cleaned_second = " ".join((second_pass or "").split()).strip().strip('"')
                if cleaned_second and not re.search(r"[آ-ی]", cleaned_second):
                    cleaned = cleaned_second
            except Exception:  # noqa: BLE001
                pass
        return cleaned

    async def _cleanup_transcript(self, *, raw_transcript: str, topic: str | None) -> str:
        text = (raw_transcript or "").strip()
        if not text:
            return ""
        if not bool(getattr(self.settings, "audio_transcript_cleanup_enabled", True)):
            return text

        topic_text = (topic or "").strip() or "نامشخص"
        prompt_template = (
            "تو یک ویرایشگر حرفه‌ای متن ترنسکریپت هستی.\n\n"
            "من خروجی خام تبدیل گفتار را می‌دهم. فقط همان متن را با حداقل دخالت اصلاح کن و به صورت متن ساده برگردان.\n\n"
            "موضوع تقریبی متن:\n"
            f"- {topic_text}\n\n"
            "\n"
            "قوانین:\n"
            "1) هیچ خلاصه‌سازی یا تحلیل انجام نده.\n"
            "2) هیچ بخشی از محتوا را حذف نکن و چیزی اضافه نکن.\n"
            "3) ترتیب و معنای جملات را دقیقاً حفظ کن.\n"
            "4) فقط کلمات شکسته، غلط املایی، یا واژه‌های واضحاً اشتباه را با توجه به بافت اصلاح کن.\n"
            "5) اگر یک واژه واضحاً اشتباه تشخیص شده بود، نزدیک‌ترین حدس معقول را جایگزین کن.\n"
            "6) از بازنویسی ادبی، تیترگذاری، بولت‌گذاری و تغییر سبک نوشتار خودداری کن.\n\n"
            "الزامات خروجی:\n"
            "- فقط متن نهایی ساده را بده.\n"
            "- توضیح اضافه نده.\n\n"
            "متن خام:\n"
            "<<<RAW_TRANSCRIPTION_TEXT>>>"
        )
        prompt = prompt_template.replace("<<<RAW_TRANSCRIPTION_TEXT>>>", text)

        try:
            cleaned = await self.ai_client.generate_reply(
                models=self.settings.ai_transcript_cleanup_models,
                providers=self.settings.ai_transcript_cleanup_providers,
                user_message=prompt,
                history=[],
                system_prompt="",
                router=self._router(self.settings.router_transcript_cleanup),
                image_inline_data=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("transcript cleanup failed; using raw transcript: %s", exc)
            return text

        normalized = (cleaned or "").strip()
        return normalized or text

    async def _handle_image_regenerate(self, incoming: IncomingMessage, generation_id: str) -> None:
        row = await self.storage.get_image_generation(generation_id, incoming.user_id)
        if not row:
            await self._send_message(incoming.chat_id, "رکورد این تصویر پیدا نشد یا دسترسی نداری.")
            return

        stage_message_id = await self._send_thinking_message(
            incoming.chat_id,
            getattr(self.settings, "ui_image_stage_generating_text", "🎨 در حال تولید تصویر..."),
        )
        try:
            seed = random.randint(
                int(getattr(self.settings, "ai_image_regen_seed_min", 1000)),
                int(getattr(self.settings, "ai_image_regen_seed_max", 999999999)),
            )
            result = await self.ai_client.generate_image(
                models=self.settings.ai_image_generation_models,
                providers=self.settings.ai_image_generation_providers,
                prompt=str(row.get("enhanced_prompt") or row.get("original_prompt") or ""),
                router=self._router(self.settings.router_image_generation),
                size=self.settings.ai_image_generation_size,
                quality=self.settings.ai_image_generation_quality,
                n=self.settings.ai_image_generation_count,
                seed=seed,
            )
        except AIBackendError as exc:
            await self._safe_delete_message(incoming.chat_id, stage_message_id)
            await self._send_message(incoming.chat_id, self._friendly_ai_error(exc, feature="image_generation"))
            return
        except Exception:
            logger.exception("image regenerate failed")
            await self._safe_delete_message(incoming.chat_id, stage_message_id)
            await self._send_message(incoming.chat_id, "تولید مجدد تصویر ناموفق بود. دوباره تلاش کن.")
            return

        await self._safe_delete_message(incoming.chat_id, stage_message_id)
        new_generation_id = uuid4().hex[:16]
        await self.storage.save_image_generation(
            generation_id=new_generation_id,
            user_id=incoming.user_id,
            chat_id=incoming.chat_id,
            original_prompt=str(row.get("original_prompt") or ""),
            enhanced_prompt=str(row.get("enhanced_prompt") or ""),
            revised_prompt=result.revised_prompt or None,
            model=(self.settings.ai_image_generation_models[0] if self.settings.ai_image_generation_models else None),
            provider=(self.settings.ai_image_generation_providers[0] if self.settings.ai_image_generation_providers else None),
            image_size=self.settings.ai_image_generation_size,
            image_quality=self.settings.ai_image_generation_quality,
            image_seed=seed,
        )

        if result.image_bytes:
            await self._send_photo(
                incoming.chat_id,
                image_bytes=result.image_bytes,
                caption="✅ تصویر آماده شد (تولید مجدد)",
                reply_markup=image_result_inline(new_generation_id),
            )
            return
        if result.image_url:
            await self._send_photo(
                incoming.chat_id,
                image_url=result.image_url,
                caption="✅ تصویر آماده شد (تولید مجدد)",
                reply_markup=image_result_inline(new_generation_id),
            )
            return
        await self._send_message(incoming.chat_id, "تولید مجدد انجام شد ولی خروجی قابل ارسال نبود.")

    async def _handle_image_prompt_show(self, incoming: IncomingMessage, generation_id: str) -> None:
        row = await self.storage.get_image_generation(generation_id, incoming.user_id)
        if not row:
            await self._send_message(incoming.chat_id, "رکورد این تصویر پیدا نشد یا دسترسی نداری.")
            return
        await self._send_message(
            incoming.chat_id,
            "🧾 پرامپت بهبود‌یافته:\n\n"
            f"{str(row.get('enhanced_prompt') or '').strip()}",
        )

    async def _handle_image_feedback(self, incoming: IncomingMessage, generation_id: str, feedback_type: str) -> bool:
        normalized = feedback_type.strip().lower()
        if normalized not in {"like", "dislike"}:
            await self._send_message(incoming.chat_id, "نوع فیدبک نامعتبر است.")
            return False

        row = await self.storage.get_image_generation(generation_id, incoming.user_id)
        if not row:
            await self._send_message(incoming.chat_id, "رکورد تصویر برای ثبت فیدبک پیدا نشد.")
            return False

        await self.storage.log_user_feedback(
            user_id=incoming.user_id,
            chat_id=incoming.chat_id,
            target_type="image_generation",
            target_id=generation_id,
            feedback_type=normalized,
            details={
                "model": row.get("model"),
                "provider": row.get("provider"),
                "message_id": incoming.message_id,
                "display_name": incoming.display_name,
                "username": incoming.username,
            },
        )
        return True

    def _friendly_ai_error(self, exc: Exception, *, feature: str) -> str:
        raw = str(exc)
        text = raw.lower()
        status_code = self._extract_http_status(raw)

        if "invalid api key" in text:
            if feature in {"audio_analysis", "transcription"}:
                return "کلید سرویس تبدیل ویس به متن نامعتبر است. لطفاً به ادمین اطلاع بده."
            return "کلید سرویس هوش مصنوعی نامعتبر است. لطفاً به ادمین اطلاع بده."
        if status_code in {401, 403}:
            return "دسترسی به سرویس هوش مصنوعی برقرار نیست. لطفاً به ادمین اطلاع بده."
        if status_code == 429:
            return "در حال حاضر ترافیک بالاست و به سقف درخواست رسیدیم. لطفاً یک دقیقه بعد دوباره تلاش کن."
        if status_code == 504 or "timeout" in text:
            return "پاسخ‌گیری طول کشید. لطفاً دوباره تلاش کن یا درخواستت را کوتاه‌تر بفرست."
        if status_code in {502, 503}:
            return "سرویس هوش مصنوعی موقتاً در دسترس نیست. لطفاً کمی بعد دوباره تلاش کن."
        if "rate limit" in text or "too many requests" in text or "quota" in text:
            return "در حال حاضر ترافیک بالاست و به سقف درخواست رسیدیم. لطفاً یک دقیقه بعد دوباره تلاش کن."

        mapping = {
            "chat": "خطا در تولید پاسخ متنی. لطفاً دوباره تلاش کن.",
            "image_generation": "خطا در تولید تصویر. لطفاً با پرامپت کوتاه‌تر دوباره امتحان کن.",
            "transcription": "خطا در تبدیل ویس به متن. لطفاً دوباره تلاش کن.",
            "audio_analysis": "خطا در تحلیل ویس. لطفاً دوباره تلاش کن.",
        }
        return mapping.get(feature, "خطا در ارتباط با هوش مصنوعی. لطفاً دوباره تلاش کن.")

    def _classify_error_code(self, exc: Exception) -> str:
        raw = str(exc)
        text = raw.lower()
        status_code = self._extract_http_status(raw)

        if "invalid api key" in text:
            return "invalid_api_key"
        if status_code in {401, 403}:
            return "auth_error"
        if status_code == 429:
            return "rate_limited"
        if status_code == 504 or "timeout" in text:
            return "upstream_timeout"
        if status_code in {502, 503}:
            return "upstream_unavailable"
        return "ai_backend_error"

    @staticmethod
    def _extract_http_status(text: str) -> int | None:
        raw = str(text or "")
        for pattern in (
            r"UAG error \((\d{3})\)",
            r"\bHTTP\s*(\d{3})\b",
            r"\bstatus(?:_code)?[=: ]+(\d{3})\b",
            r"\b(\d{3})\s+(?:Unauthorized|Forbidden|Too Many Requests|Bad Gateway|Service Unavailable|Gateway Timeout)\b",
        ):
            match = re.search(pattern, raw, flags=re.IGNORECASE)
            if not match:
                continue
            try:
                code = int(match.group(1))
            except Exception:
                continue
            if 100 <= code <= 599:
                return code
        return None

    def _is_transcription_provider_ready(self) -> bool:
        provider = (self.settings.ai_transcription_provider or "").strip().lower()
        if provider != "groq":
            return True
        return bool(self.settings.uag_groq_api_keys)

    async def _record_bot_event(
        self,
        *,
        event_type: str,
        status: str,
        incoming: IncomingMessage,
        error_code: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        try:
            await self.storage.log_bot_event(
                event_type=event_type,
                status=status,
                user_id=incoming.user_id,
                chat_id=incoming.chat_id,
                content_type=incoming.content_type.value,
                error_code=error_code,
                latency_ms=latency_ms,
                details={
                    "message_id": incoming.message_id,
                    "update_id": incoming.update_id,
                },
            )
        except Exception:
            logger.exception("failed to persist bot event")

    async def _record_system_event(
        self,
        *,
        event_type: str,
        status: str,
        error_code: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        try:
            await self.storage.log_bot_event(
                event_type=event_type,
                status=status,
                user_id=None,
                chat_id=None,
                content_type=None,
                error_code=error_code,
                latency_ms=None,
                details=details or {},
            )
        except Exception:
            logger.exception("failed to persist system event")

    def _sanitize_history(self, history: list[ChatMessage]) -> list[ChatMessage]:
        cleaned = [item for item in history if not self._looks_like_meta_instruction(item.content)]
        return cleaned[-self.settings.chat_history_max_messages :]

    @staticmethod
    def _looks_like_meta_instruction(text: str) -> bool:
        lower = (text or "").lower()
        hits = sum(1 for marker in META_STYLE_MARKERS if marker in lower)
        return hits >= 2

    def _sanitize_model_reply(self, reply: str) -> str:
        text = (reply or "").strip()
        if not text:
            return "متوجه شدم. لطفاً دقیق‌تر بپرس."
        if not self._looks_like_meta_instruction(text):
            return text

        lines = [ln.strip(" -*\t") for ln in text.splitlines() if ln.strip()]
        candidate_lines = [ln for ln in lines if not self._line_has_meta_marker(ln)]
        persian_lines = [ln for ln in candidate_lines if re.search(r"[آ-ی]", ln)]

        if persian_lines:
            for ln in reversed(persian_lines):
                if 4 <= len(ln) <= 180 and (ln.endswith("؟") or ln.endswith("!") or ln.endswith(".")):
                    return ln
            return persian_lines[-1]

        option_matches = re.findall(r"(?im)^(?:option|گزینه)\s*\d+\s*:\s*(.+)$", text)
        option_matches = [m.strip() for m in option_matches if re.search(r"[آ-ی]", m)]
        if option_matches:
            for item in option_matches:
                if item.endswith(("؟", "!", ".")):
                    return item
            return option_matches[0]

        quoted_persian = re.findall(r'"([^\"]*[آ-ی][^\"]*)"', text)
        quoted_persian = [q.strip() for q in quoted_persian if len(q.strip()) >= 4 and " " in q.strip()]
        if quoted_persian:
            return quoted_persian[-1]

        return "پاسخ آماده است؛ اگر بخواهی کوتاه‌تر یا دقیق‌ترش می‌کنم."

    @staticmethod
    def _line_has_meta_marker(text: str) -> bool:
        lower = (text or "").lower()
        return any(marker in lower for marker in META_STYLE_MARKERS)

    async def _send_photo(
        self,
        chat_id: str,
        *,
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        caption: str | None = None,
        reply_markup: dict | None = None,
    ) -> None:
        await self.bale_client.send_photo(
            chat_id,
            photo_bytes=image_bytes,
            photo_url=image_url,
            caption=caption,
            reply_markup=reply_markup or main_menu(),
        )

    async def _send_text_as_markdown_file(self, chat_id: str, *, text: str, filename: str) -> None:
        payload = (text or "").strip()
        if not payload:
            return
        try:
            mime = "text/plain"
            if filename.lower().endswith(".md"):
                mime = "text/markdown"
            await self.bale_client.send_document(
                chat_id,
                document_bytes=payload.encode("utf-8"),
                filename=filename,
                mime_type=mime,
                caption="📄 فایل متن خروجی",
                reply_markup=main_menu(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("send markdown file failed: %s", exc)

    async def _advance_stage(self, chat_id: str, current_message_id: int | None, next_text: str) -> int | None:
        await self._safe_delete_message(chat_id, current_message_id)
        return await self._send_thinking_message(chat_id, next_text)

    async def _send_message(self, chat_id: str, text: str, reply_markup: dict | None = None) -> None:
        text = text.strip()
        if not text:
            return
        self._log_flow(
            "send_message",
            chat_id=chat_id,
            text_len=len(text),
            text_preview=self._preview(text),
            has_markup=bool(reply_markup),
        )

        limit = 3900
        if len(text) <= limit:
            await self.bale_client.send_message(chat_id, text, reply_markup=reply_markup or main_menu())
            return

        chunks = [text[i : i + limit] for i in range(0, len(text), limit)]
        for index, chunk in enumerate(chunks):
            markup = reply_markup if index == len(chunks) - 1 else None
            await self.bale_client.send_message(chat_id, chunk, reply_markup=markup or main_menu())

    def _preview(self, text: str) -> str:
        cleaned = (text or "").replace("\n", "\\n")
        if len(cleaned) <= self._text_preview_chars:
            return cleaned
        return cleaned[: self._text_preview_chars] + "..."

    def _log_flow(self, event: str, **fields: object) -> None:
        if not self._flow_logs:
            return
        details = " ".join(f"{k}={fields[k]!r}" for k in sorted(fields))
        logger.info("event=%s %s", event, details)
