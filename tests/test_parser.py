from __future__ import annotations

from gerdoo_ai_bot.parser import parse_update
from gerdoo_ai_bot.types import ContentType


def test_parse_text_message() -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "first_name": "Ali", "username": "ali"},
            "text": "hello",
        },
    }

    msg = parse_update(update)
    assert msg is not None
    assert msg.content_type == ContentType.TEXT
    assert msg.text == "hello"


def test_parse_callback() -> None:
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "data": "mdl:set:0",
            "from": {"id": 200, "first_name": "Ali"},
            "message": {"message_id": 11, "chat": {"id": 100, "type": "private"}},
        },
    }

    msg = parse_update(update)
    assert msg is not None
    assert msg.content_type == ContentType.CALLBACK
    assert msg.callback_data == "mdl:set:0"


def test_parse_non_private_ignored() -> None:
    update = {
        "update_id": 3,
        "message": {
            "message_id": 99,
            "chat": {"id": -100, "type": "group"},
            "from": {"id": 201},
            "text": "group",
        },
    }

    assert parse_update(update) is None


def test_parse_photo_message() -> None:
    update = {
        "update_id": 4,
        "message": {
            "message_id": 12,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "first_name": "Ali", "username": "ali"},
            "caption": "an image",
            "photo": [
                {"file_id": "small", "file_size": 10},
                {"file_id": "big", "file_size": 20},
            ],
        },
    }

    msg = parse_update(update)
    assert msg is not None
    assert msg.content_type == ContentType.PHOTO
    assert msg.source_file_id == "big"
    assert msg.caption == "an image"
