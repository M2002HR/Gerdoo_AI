from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import re
from pathlib import Path
from uuid import uuid4

import httpx

from gerdoo_ai_bot.clients.bale_api import BaleBotApiClient
from gerdoo_ai_bot.clients.gemini_proxy import GeminiProxyClient
from gerdoo_ai_bot.config import Settings
from gerdoo_ai_bot.parser import parse_update
from gerdoo_ai_bot.storage import ChatStorage
from gerdoo_ai_bot.types import (
    ChatMessage,
    ContentType,
    GeminiProxyError,
    IncomingMessage,
    ModelCapabilityError,
    PlatformApiError,
)
from gerdoo_ai_bot.ui import (
    BTN_HELP,
    BTN_MODEL,
    BTN_NEW_CHAT,
    BTN_STATUS,
    help_text,
    main_menu,
    model_selection_menu,
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


class BaleAIBotService:
    def __init__(
        self,
        settings: Settings,
        bale_client: BaleBotApiClient,
        gemini_client: GeminiProxyClient,
        storage: ChatStorage,
    ) -> None:
        self.settings = settings
        self.bale_client = bale_client
        self.gemini_client = gemini_client
        self.storage = storage
        self._stop_event = asyncio.Event()
        self._flow_logs = bool(getattr(settings, "log_flow_enabled", True))
        self._text_preview_chars = int(getattr(settings, "log_text_preview_chars", 160))

    async def run(self) -> None:
        await self.storage.init()
        self._log_flow(
            "service_started",
            app_env=self.settings.app_env,
            model_default=self.settings.gemini_default_model,
            history_limit=self.settings.chat_history_max_messages,
            proxy=f"{self.settings.gemini_proxy_base_url}{self.settings.gemini_proxy_endpoint}",
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
                await asyncio.sleep(2)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                logger.warning("Network error while polling Bale: %s", exc)
                await asyncio.sleep(1)
            except Exception:
                logger.exception("Unexpected error in poll loop")
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._stop_event.set()
        await self.storage.aclose()
        await self.gemini_client.aclose()
        await self.bale_client.aclose()

    async def handle_incoming(self, incoming: IncomingMessage) -> None:
        await self.storage.ensure_user(
            user_id=incoming.user_id,
            chat_id=incoming.chat_id,
            username=incoming.username,
            display_name=incoming.display_name,
            default_model=self.settings.gemini_default_model,
        )

        if incoming.content_type == ContentType.CALLBACK:
            await self._handle_callback(incoming)
            return

        if incoming.content_type == ContentType.PHOTO:
            caption = (incoming.caption or "").strip()
            prompt_text = caption or "این تصویر را کوتاه و دقیق توضیح بده."
            self._log_flow(
                "incoming_photo",
                user_id=incoming.user_id,
                message_id=incoming.message_id,
                has_caption=bool(caption),
                caption_preview=self._preview(caption),
                file_id=incoming.source_file_id or "",
                file_size=incoming.source_file_size or 0,
            )
            await self._handle_chat_message(incoming, prompt_text, use_image=True)
            return

        if incoming.content_type != ContentType.TEXT:
            await self._send_message(
                incoming.chat_id,
                "این نوع پیام پشتیبانی نمی‌شود. لطفاً پیام متنی ارسال کنید.",
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
        )

        if text.startswith("/"):
            await self._handle_command(incoming, text)
            return

        if text in {BTN_NEW_CHAT, BTN_MODEL, BTN_STATUS, BTN_HELP}:
            await self._handle_menu_action(incoming, text)
            return

        await self._handle_chat_message(incoming, text, use_image=False)

    async def _handle_command(self, incoming: IncomingMessage, text: str) -> None:
        cmd = text.split(maxsplit=1)[0].lower()
        if cmd == "/start":
            await self._show_welcome(incoming.chat_id)
            if self.settings.ui_show_help_on_start:
                await self._send_message(incoming.chat_id, help_text())
            return

        if cmd == "/help":
            await self._send_message(incoming.chat_id, help_text())
            return

        if cmd in {"/new", "/clear"}:
            await self.storage.clear_chat(incoming.user_id)
            await self._send_message(incoming.chat_id, "تاریخچه این گفتگو پاک شد.")
            return

        if cmd in {"/model", "/models"}:
            await self._show_model_selector(incoming.chat_id, incoming.user_id)
            return

        if cmd in {"/status", "/me"}:
            await self._show_status(incoming.chat_id, incoming.user_id)
            return

        await self._send_message(incoming.chat_id, "دستور ناشناخته است. /help را بزنید.")

    async def _handle_menu_action(self, incoming: IncomingMessage, text: str) -> None:
        if text == BTN_NEW_CHAT:
            await self.storage.clear_chat(incoming.user_id)
            await self._send_message(incoming.chat_id, "تاریخچه این گفتگو پاک شد.")
            return

        if text == BTN_MODEL:
            await self._show_model_selector(incoming.chat_id, incoming.user_id)
            return

        if text == BTN_STATUS:
            await self._show_status(incoming.chat_id, incoming.user_id)
            return

        if text == BTN_HELP:
            await self._send_message(incoming.chat_id, help_text())

    async def _handle_callback(self, incoming: IncomingMessage) -> None:
        data = incoming.callback_data or ""
        if data == "mdl:close":
            await self.bale_client.answer_callback_query(incoming.callback_query_id or "")
            await self._show_welcome(incoming.chat_id)
            return

        if data.startswith("mdl:set:"):
            try:
                index = int(data.rsplit(":", 1)[-1])
            except ValueError:
                await self.bale_client.answer_callback_query(incoming.callback_query_id or "", text="گزینه نامعتبر")
                return

            if index < 0 or index >= len(self.settings.gemini_available_models):
                await self.bale_client.answer_callback_query(incoming.callback_query_id or "", text="گزینه نامعتبر")
                return

            model = self.settings.gemini_available_models[index]
            await self.storage.set_selected_model(incoming.user_id, model)
            await self.bale_client.answer_callback_query(
                incoming.callback_query_id or "",
                text=f"مدل فعال شد: {model}",
            )
            await self._send_message(incoming.chat_id, f"مدل شما روی `{model}` تنظیم شد.")
            return

        await self.bale_client.answer_callback_query(incoming.callback_query_id or "", text="انجام شد")

    async def _handle_chat_message(self, incoming: IncomingMessage, text: str, use_image: bool) -> None:
        thinking_message_id = await self._send_thinking_message(incoming.chat_id)

        model = await self.storage.get_selected_model(incoming.user_id, self.settings.gemini_default_model)
        raw_history = await self.storage.get_recent_chat(incoming.user_id, self.settings.chat_history_max_messages)
        history = self._sanitize_history(raw_history)
        self._log_flow(
            "chat_message_start",
            user_id=incoming.user_id,
            model=model,
            use_image=use_image,
            history_raw_len=len(raw_history),
            history_clean_len=len(history),
            prompt_preview=self._preview(text),
        )
        image_inline_data: dict | None = None

        try:
            if use_image:
                image_inline_data = await self._prepare_image_inline_data(incoming)
        except Exception as exc:
            logger.warning("Image preparation failed: %s", exc)
            await self._safe_delete_message(incoming.chat_id, thinking_message_id)
            await self._send_message(
                incoming.chat_id,
                "خواندن تصویر انجام نشد. لطفاً عکس را دوباره ارسال کنید.",
            )
            return

        try:
            reply = await self.gemini_client.generate_reply(
                model=model,
                user_message=text,
                history=history,
                system_prompt=self.settings.chat_system_prompt,
                image_inline_data=image_inline_data,
            )
        except ModelCapabilityError:
            await self._safe_delete_message(incoming.chat_id, thinking_message_id)
            await self._send_message(
                incoming.chat_id,
                "مدل فعال از ورودی تصویر پشتیبانی نمی‌کند. لطفاً یک مدل تصویری انتخاب کنید.",
            )
            return
        except GeminiProxyError as exc:
            logger.warning("Gemini generation failed: %s", exc)
            await self.storage.log_ai_request(
                user_id=incoming.user_id,
                model=model,
                user_prompt=text,
                assistant_reply=None,
                error_text=str(exc),
            )
            await self._safe_delete_message(incoming.chat_id, thinking_message_id)
            await self._send_message(
                incoming.chat_id,
                "خطا در ارتباط با هوش مصنوعی. لطفاً چند لحظه بعد دوباره تلاش کنید.",
            )
            return
        except Exception:
            logger.exception("Unexpected Gemini failure")
            await self._safe_delete_message(incoming.chat_id, thinking_message_id)
            await self._send_message(
                incoming.chat_id,
                "یک خطای غیرمنتظره رخ داد. لطفاً دوباره تلاش کنید.",
            )
            return

        await self.storage.append_chat_message(
            incoming.user_id,
            role="user",
            content=text,
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
            model=model,
            user_prompt=text,
            assistant_reply=reply,
            error_text=None,
        )

        reply = self._sanitize_model_reply(reply)
        self._log_flow(
            "chat_message_done",
            user_id=incoming.user_id,
            model=model,
            reply_len=len(reply),
            reply_preview=self._preview(reply),
        )
        await self._safe_delete_message(incoming.chat_id, thinking_message_id)
        await self._send_message(incoming.chat_id, reply)

    async def _show_welcome(self, chat_id: str) -> None:
        await self._send_message(
            chat_id,
            welcome_text(
                default_model=self.settings.gemini_default_model,
                history_limit=self.settings.chat_history_max_messages,
            ),
        )

    async def _show_model_selector(self, chat_id: str, user_id: str) -> None:
        current = await self.storage.get_selected_model(user_id, self.settings.gemini_default_model)
        await self._send_message(
            chat_id,
            "مدل فعال خود را انتخاب کنید:",
            reply_markup=model_selection_menu(self.settings.gemini_available_models, current),
        )

    async def _show_status(self, chat_id: str, user_id: str) -> None:
        model = await self.storage.get_selected_model(user_id, self.settings.gemini_default_model)
        image_support = "بله" if self._model_supports_images(model) else "خیر"
        text = (
            "وضعیت فعلی:\n"
            f"- مدل فعال: {model}\n"
            f"- تعداد پیام در حافظه: {self.settings.chat_history_max_messages}\n"
            f"- پشتیبانی تصویر در مدل فعال: {image_support}"
        )
        await self._send_message(chat_id, text)

    async def _send_thinking_message(self, chat_id: str) -> int | None:
        try:
            result = await self.bale_client.send_message(chat_id, self.settings.ui_thinking_text, reply_markup=None)
            return int(result.get("message_id", 0)) or None
        except Exception:
            return None

    async def _safe_delete_message(self, chat_id: str, message_id: int | None) -> None:
        if not message_id:
            return
        try:
            await self.bale_client.delete_message(chat_id, message_id)
        except Exception:
            return

    async def _prepare_image_inline_data(self, incoming: IncomingMessage) -> dict:
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
        except Exception:
            pass

        mime_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
        return {
            "mimeType": mime_type,
            "data": base64.b64encode(raw).decode("ascii"),
        }

    def _model_supports_images(self, model: str) -> bool:
        model_id = model.split("/", 1)[1] if model.startswith("models/") else model
        return model_id in self.gemini_client.image_capable_models

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
        # Keep only non-meta lines
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

        # Fallback for all-English meta dumps with quoted Persian options.
        quoted_persian = re.findall(r"\"([^\"]*[آ-ی][^\"]*)\"", text)
        quoted_persian = [q.strip() for q in quoted_persian if len(q.strip()) >= 4 and " " in q.strip()]
        if quoted_persian:
            return quoted_persian[-1]

        return "پاسخ آماده است؛ اگر بخواهی کوتاه‌تر یا دقیق‌ترش می‌کنم."

    @staticmethod
    def _line_has_meta_marker(text: str) -> bool:
        lower = (text or "").lower()
        return any(marker in lower for marker in META_STYLE_MARKERS)

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
