from __future__ import annotations

import base64
import json

import httpx
import pytest

from gerdoo_ai_bot.clients.uag import UAGGenerationConfig, UAGRouterConfig, UnifiedAIGatewayClient
from gerdoo_ai_bot.types import AIBackendError, ChatMessage


@pytest.mark.asyncio
async def test_generate_reply_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["x_router"]["providers"] == ["gemini", "groq"]
        body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "سلام\nچطور می‌تونم کمک کنم؟",
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)

    client = UnifiedAIGatewayClient(
        base_url="http://x",
        chat_endpoint="/v1/chat/completions",
        image_endpoint="/v1/images/generations",
        audio_transcriptions_endpoint="/v1/audio/transcriptions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    reply = await client.generate_reply(
        models=["gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"],
        providers=["gemini", "groq"],
        user_message="hello",
        history=[ChatMessage(role="user", content="hi")],
        system_prompt="test",
        router=UAGRouterConfig(),
    )
    assert "سلام" in reply
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_extracts_router_winner_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "ok": True,
            "winner": {
                "provider": "gemini",
                "payload": {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "در خدمتم",
                            }
                        }
                    ]
                },
            },
        }
        return httpx.Response(200, json=body)

    client = UnifiedAIGatewayClient(
        base_url="http://x",
        chat_endpoint="/v1/chat/completions",
        image_endpoint="/v1/images/generations",
        audio_transcriptions_endpoint="/v1/audio/transcriptions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    reply = await client.generate_reply(
        models=["gemini/gemini-2.5-flash"],
        providers=["gemini"],
        user_message="hello",
        history=[],
        system_prompt="test",
        router=UAGRouterConfig(),
    )
    assert reply == "در خدمتم"
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "upstream failed"})

    client = UnifiedAIGatewayClient(
        base_url="http://x",
        chat_endpoint="/v1/chat/completions",
        image_endpoint="/v1/images/generations",
        audio_transcriptions_endpoint="/v1/audio/transcriptions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(AIBackendError):
        await client.generate_reply(
            models=["gemini/gemini-2.5-flash"],
            providers=["gemini"],
            user_message="hello",
            history=[],
            system_prompt="test",
            router=UAGRouterConfig(),
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_sends_image_as_data_url() -> None:
    seen = {"body": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    client = UnifiedAIGatewayClient(
        base_url="http://x",
        chat_endpoint="/v1/chat/completions",
        image_endpoint="/v1/images/generations",
        audio_transcriptions_endpoint="/v1/audio/transcriptions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await client.generate_reply(
        models=["gemini/gemini-2.5-flash"],
        providers=["gemini"],
        user_message="describe",
        history=[],
        system_prompt="",
        router=UAGRouterConfig(),
        image_inline_data={"mimeType": "image/png", "data": "aGVsbG8="},
    )

    assert seen["body"] is not None
    content_items = seen["body"]["messages"][-1]["content"]
    assert content_items[1]["image_url"]["url"] == "data:image/png;base64,aGVsbG8="
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_omits_max_tokens_when_zero() -> None:
    seen = {"body": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    client = UnifiedAIGatewayClient(
        base_url="http://x",
        chat_endpoint="/v1/chat/completions",
        image_endpoint="/v1/images/generations",
        audio_transcriptions_endpoint="/v1/audio/transcriptions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=0),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await client.generate_reply(
        models=["groq/openai/gpt-oss-120b"],
        providers=["groq"],
        user_message="hello",
        history=[],
        system_prompt="",
        router=UAGRouterConfig(),
    )
    assert isinstance(seen["body"], dict)
    assert "max_tokens" not in seen["body"]
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_image_success() -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n\x00"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/images/generations"
        body = {
            "ok": True,
            "winner": {
                "payload": {
                    "data": [
                        {
                            "b64_json": base64.b64encode(png_bytes).decode("ascii"),
                            "revised_prompt": "updated",
                        }
                    ]
                }
            },
        }
        return httpx.Response(200, json=body)

    client = UnifiedAIGatewayClient(
        base_url="http://x",
        chat_endpoint="/v1/chat/completions",
        image_endpoint="/v1/images/generations",
        audio_transcriptions_endpoint="/v1/audio/transcriptions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    out = await client.generate_image(
        models=["pollinations/flux"],
        providers=["pollinations"],
        prompt="cat",
        router=UAGRouterConfig(),
        size="1024x1024",
        quality="medium",
        n=1,
    )
    assert out.image_bytes == png_bytes
    assert out.revised_prompt == "updated"
    await client.aclose()


@pytest.mark.asyncio
async def test_transcribe_audio_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        return httpx.Response(
            200,
            json={
                "provider": "groq",
                "ok": True,
                "payload": {"text": "سلام دنیا"},
            },
        )

    client = UnifiedAIGatewayClient(
        base_url="http://x",
        chat_endpoint="/v1/chat/completions",
        image_endpoint="/v1/images/generations",
        audio_transcriptions_endpoint="/v1/audio/transcriptions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    text = await client.transcribe_audio(
        audio_bytes=b"abc",
        filename="voice.ogg",
        mime_type="audio/ogg",
        provider="groq",
        language="fa",
        model_preferences=["groq/whisper-large-v3-turbo"],
    )
    assert text == "سلام دنیا"
    await client.aclose()
