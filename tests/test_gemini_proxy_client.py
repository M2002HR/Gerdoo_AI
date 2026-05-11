from __future__ import annotations

import httpx
import pytest

from gerdoo_ai_bot.clients.gemini_proxy import GeminiGenerationConfig, GeminiProxyClient
from gerdoo_ai_bot.types import ChatMessage, GeminiProxyError, ModelCapabilityError


@pytest.mark.asyncio
async def test_generate_reply_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "سلام"},
                            {"text": "چطور می‌تونم کمک کنم؟"},
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)

    client = GeminiProxyClient(
        base_url="http://x",
        endpoint="/proxy/gemini",
        timeout_sec=10,
        generation_config=GeminiGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    reply = await client.generate_reply(
        model="gemini-2.5-flash",
        user_message="hello",
        history=[ChatMessage(role="user", content="hi")],
        system_prompt="test",
    )
    assert "سلام" in reply
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_ignores_thought_parts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "analysis text", "thought": True},
                            {"text": "سلام! چطور می‌توانم کمک کنم؟"},
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)

    client = GeminiProxyClient(
        base_url="http://x",
        endpoint="/proxy/gemini",
        timeout_sec=10,
        generation_config=GeminiGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    reply = await client.generate_reply(
        model="gemini-2.5-flash",
        user_message="hello",
        history=[],
        system_prompt="test",
    )
    assert "analysis text" not in reply
    assert reply == "سلام! چطور می‌توانم کمک کنم؟"
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "upstream failed"})

    client = GeminiProxyClient(
        base_url="http://x",
        endpoint="/proxy/gemini",
        timeout_sec=10,
        generation_config=GeminiGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(GeminiProxyError):
        await client.generate_reply(
            model="gemini-2.5-flash",
            user_message="hello",
            history=[],
            system_prompt="test",
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_rejects_image_for_non_capable_model() -> None:
    client = GeminiProxyClient(
        base_url="http://x",
        endpoint="/proxy/gemini",
        timeout_sec=10,
        generation_config=GeminiGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
        image_capable_models=["gemini-2.5-flash"],
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))

    with pytest.raises(ModelCapabilityError):
        await client.generate_reply(
            model="gemini-3.1-flash-lite",
            user_message="hello",
            history=[],
            system_prompt="test",
            image_inline_data={"mimeType": "image/png", "data": "xxx"},
        )
    await client.aclose()
