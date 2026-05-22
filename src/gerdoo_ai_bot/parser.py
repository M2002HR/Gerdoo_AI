from __future__ import annotations

import re

from gerdoo_ai_bot.types import ContentType, IncomingMessage, Platform


def _is_audio_document(document: dict) -> bool:
    mime_type = str(document.get("mime_type") or document.get("mimeType") or "").strip().lower()
    file_name = str(document.get("file_name") or document.get("name") or "").strip().lower()
    if mime_type.startswith("audio/"):
        return True
    return file_name.endswith((".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".oga"))


def _parse_duration_seconds(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = int(value)
        return parsed if parsed > 0 else None

    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        parsed = int(raw)
        return parsed if parsed > 0 else None

    # Supports mm:ss and hh:mm:ss forms.
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", raw):
        parts = [int(p) for p in raw.split(":")]
        if len(parts) == 2:
            mm, ss = parts
            return mm * 60 + ss
        hh, mm, ss = parts
        return hh * 3600 + mm * 60 + ss
    return None


def parse_update(update: dict) -> IncomingMessage | None:
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        from_user = callback.get("from") or {}
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_type = chat.get("type")
        if chat_type not in {None, "private"}:
            return None

        user_id = from_user.get("id")
        chat_id = chat.get("id")
        if user_id is None or chat_id is None:
            return None

        first_name = str(from_user.get("first_name") or "").strip()
        last_name = str(from_user.get("last_name") or "").strip()
        display_name = f"{first_name} {last_name}".strip() or str(from_user.get("username") or "unknown")

        return IncomingMessage(
            platform=Platform.BALE,
            update_id=int(update["update_id"]),
            chat_id=str(chat_id),
            user_id=str(user_id),
            username=from_user.get("username"),
            display_name=display_name,
            message_id=int(message.get("message_id") or 0),
            content_type=ContentType.CALLBACK,
            callback_data=str(callback.get("data") or ""),
            callback_query_id=str(callback.get("id") or ""),
            raw=update,
        )

    message = update.get("message")
    if not isinstance(message, dict):
        return None

    chat = message.get("chat") or {}
    sender = message.get("from") or {}

    if not sender:
        return None

    chat_type = chat.get("type")
    if chat_type not in {None, "private"}:
        return None

    user_id = sender.get("id")
    chat_id = chat.get("id")
    if user_id is None or chat_id is None:
        return None

    first_name = str(sender.get("first_name") or "").strip()
    last_name = str(sender.get("last_name") or "").strip()
    display_name = f"{first_name} {last_name}".strip() or str(sender.get("username") or "unknown")

    if isinstance(message.get("text"), str):
        return IncomingMessage(
            platform=Platform.BALE,
            update_id=int(update["update_id"]),
            chat_id=str(chat_id),
            user_id=str(user_id),
            username=sender.get("username"),
            display_name=display_name,
            message_id=int(message.get("message_id") or 0),
            content_type=ContentType.TEXT,
            text=message["text"],
            raw=update,
        )

    if isinstance(message.get("photo"), list) and message["photo"]:
        largest = message["photo"][-1]
        file_id = largest.get("file_id")
        if not file_id:
            return None
        return IncomingMessage(
            platform=Platform.BALE,
            update_id=int(update["update_id"]),
            chat_id=str(chat_id),
            user_id=str(user_id),
            username=sender.get("username"),
            display_name=display_name,
            message_id=int(message.get("message_id") or 0),
            content_type=ContentType.PHOTO,
            caption=str(message.get("caption") or "").strip() or None,
            source_file_id=str(file_id),
            source_file_size=int(largest.get("file_size") or 0) or None,
            raw=update,
        )

    voice = message.get("voice")
    if isinstance(voice, dict):
        file_id = str(voice.get("file_id") or "").strip()
        if not file_id:
            return None
        return IncomingMessage(
            platform=Platform.BALE,
            update_id=int(update["update_id"]),
            chat_id=str(chat_id),
            user_id=str(user_id),
            username=sender.get("username"),
            display_name=display_name,
            message_id=int(message.get("message_id") or 0),
            content_type=ContentType.AUDIO,
            caption=str(message.get("caption") or "").strip() or None,
            source_file_id=file_id,
            source_file_size=int(voice.get("file_size") or 0) or None,
            source_mime_type=str(voice.get("mime_type") or "").strip() or None,
            source_duration_sec=_parse_duration_seconds(voice.get("duration")),
            raw=update,
        )

    audio = message.get("audio")
    if isinstance(audio, dict):
        file_id = str(audio.get("file_id") or "").strip()
        if not file_id:
            return None
        return IncomingMessage(
            platform=Platform.BALE,
            update_id=int(update["update_id"]),
            chat_id=str(chat_id),
            user_id=str(user_id),
            username=sender.get("username"),
            display_name=display_name,
            message_id=int(message.get("message_id") or 0),
            content_type=ContentType.AUDIO,
            caption=str(message.get("caption") or "").strip() or None,
            source_file_id=file_id,
            source_file_size=int(audio.get("file_size") or 0) or None,
            source_file_name=str(audio.get("file_name") or "").strip() or None,
            source_mime_type=str(audio.get("mime_type") or "").strip() or None,
            source_duration_sec=_parse_duration_seconds(audio.get("duration")),
            raw=update,
        )

    document = message.get("document")
    if isinstance(document, dict) and _is_audio_document(document):
        file_id = str(document.get("file_id") or document.get("id") or "").strip()
        if not file_id:
            return None
        return IncomingMessage(
            platform=Platform.BALE,
            update_id=int(update["update_id"]),
            chat_id=str(chat_id),
            user_id=str(user_id),
            username=sender.get("username"),
            display_name=display_name,
            message_id=int(message.get("message_id") or 0),
            content_type=ContentType.AUDIO,
            caption=str(message.get("caption") or "").strip() or None,
            source_file_id=file_id,
            source_file_size=int(document.get("file_size") or 0) or None,
            source_file_name=str(document.get("file_name") or document.get("name") or "").strip() or None,
            source_mime_type=str(document.get("mime_type") or document.get("mimeType") or "").strip() or None,
            source_duration_sec=_parse_duration_seconds(document.get("duration")),
            raw=update,
        )

    file_obj = message.get("file")
    if isinstance(file_obj, dict) and _is_audio_document(file_obj):
        file_id = str(file_obj.get("file_id") or file_obj.get("id") or "").strip()
        if not file_id:
            return None
        return IncomingMessage(
            platform=Platform.BALE,
            update_id=int(update["update_id"]),
            chat_id=str(chat_id),
            user_id=str(user_id),
            username=sender.get("username"),
            display_name=display_name,
            message_id=int(message.get("message_id") or 0),
            content_type=ContentType.AUDIO,
            caption=str(message.get("caption") or "").strip() or None,
            source_file_id=file_id,
            source_file_size=int(file_obj.get("file_size") or 0) or None,
            source_file_name=str(file_obj.get("file_name") or file_obj.get("name") or "").strip() or None,
            source_mime_type=str(file_obj.get("mime_type") or file_obj.get("mimeType") or "").strip() or None,
            source_duration_sec=_parse_duration_seconds(file_obj.get("duration")),
            raw=update,
        )

    return IncomingMessage(
        platform=Platform.BALE,
        update_id=int(update["update_id"]),
        chat_id=str(chat_id),
        user_id=str(user_id),
        username=sender.get("username"),
        display_name=display_name,
        message_id=int(message.get("message_id") or 0),
        content_type=ContentType.UNSUPPORTED,
        raw=update,
    )
