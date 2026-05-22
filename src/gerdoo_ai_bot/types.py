from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Platform(str, Enum):
    BALE = "bale"


class ContentType(str, Enum):
    TEXT = "TEXT"
    PHOTO = "PHOTO"
    AUDIO = "AUDIO"
    CALLBACK = "CALLBACK"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(slots=True)
class IncomingMessage:
    platform: Platform
    update_id: int
    chat_id: str
    user_id: str
    username: str | None
    display_name: str
    message_id: int
    content_type: ContentType
    text: str | None = None
    caption: str | None = None
    source_file_id: str | None = None
    source_file_size: int | None = None
    source_file_name: str | None = None
    source_mime_type: str | None = None
    source_duration_sec: int | None = None
    callback_data: str | None = None
    callback_query_id: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str


class BotError(Exception):
    pass


class PlatformApiError(BotError):
    pass


class AIBackendError(BotError):
    pass


class ModelCapabilityError(AIBackendError):
    pass
