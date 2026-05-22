from __future__ import annotations

from gerdoo_ai_bot.types import ContentType, IncomingMessage, Platform


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
