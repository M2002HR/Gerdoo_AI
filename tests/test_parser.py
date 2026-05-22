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


def test_parse_non_private_callback_ignored() -> None:
    update = {
        "update_id": 5,
        "callback_query": {
            "id": "cb-x",
            "data": "mdl:set:0",
            "from": {"id": 200, "first_name": "Ali"},
            "message": {"message_id": 11, "chat": {"id": -100, "type": "group"}},
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


def test_parse_voice_message() -> None:
    update = {
        "update_id": 6,
        "message": {
            "message_id": 13,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "first_name": "Ali", "username": "ali"},
            "voice": {
                "file_id": "voice-file-id",
                "duration": 42,
                "mime_type": "audio/ogg",
                "file_size": 1234,
            },
        },
    }

    msg = parse_update(update)
    assert msg is not None
    assert msg.content_type == ContentType.AUDIO
    assert msg.source_file_id == "voice-file-id"
    assert msg.source_duration_sec == 42
    assert msg.source_mime_type == "audio/ogg"


def test_parse_audio_document_message() -> None:
    update = {
        "update_id": 7,
        "message": {
            "message_id": 14,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "first_name": "Ali", "username": "ali"},
            "document": {
                "file_id": "doc-audio-file-id",
                "file_name": "06-01_Audio_out.mp3",
                "mime_type": "audio/mpeg",
                "file_size": 556677,
            },
        },
    }

    msg = parse_update(update)
    assert msg is not None
    assert msg.content_type == ContentType.AUDIO
    assert msg.source_file_id == "doc-audio-file-id"
    assert msg.source_file_name == "06-01_Audio_out.mp3"
    assert msg.source_mime_type == "audio/mpeg"


def test_parse_audio_file_message_variant() -> None:
    update = {
        "update_id": 8,
        "message": {
            "message_id": 15,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "first_name": "Ali", "username": "ali"},
            "file": {
                "id": "file-audio-id",
                "name": "sample_voice.m4a",
                "mimeType": "audio/mp4",
                "file_size": 778899,
            },
        },
    }

    msg = parse_update(update)
    assert msg is not None
    assert msg.content_type == ContentType.AUDIO
    assert msg.source_file_id == "file-audio-id"
    assert msg.source_file_name == "sample_voice.m4a"
    assert msg.source_mime_type == "audio/mp4"


def test_parse_audio_duration_mm_ss_string() -> None:
    update = {
        "update_id": 9,
        "message": {
            "message_id": 16,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "first_name": "Ali", "username": "ali"},
            "audio": {
                "file_id": "audio-file-id",
                "duration": "03:10",
                "mime_type": "audio/mpeg",
                "file_size": 1000,
            },
        },
    }

    msg = parse_update(update)
    assert msg is not None
    assert msg.content_type == ContentType.AUDIO
    assert msg.source_duration_sec == 190


def test_parse_audio_document_duration_is_supported() -> None:
    update = {
        "update_id": 10,
        "message": {
            "message_id": 17,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "first_name": "Ali", "username": "ali"},
            "document": {
                "file_id": "doc-audio-file-id",
                "file_name": "short_voice.mp3",
                "mime_type": "audio/mpeg",
                "duration": "00:02",
                "file_size": 556677,
            },
        },
    }

    msg = parse_update(update)
    assert msg is not None
    assert msg.content_type == ContentType.AUDIO
    assert msg.source_duration_sec == 2
