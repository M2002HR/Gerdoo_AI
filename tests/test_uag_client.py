from __future__ import annotations

import httpx
import pytest

from gerdoo_ai_bot.clients.uag import UAGGenerationConfig, UnifiedAIGatewayClient
from gerdoo_ai_bot.types import AIBackendError, ChatMessage, ModelCapabilityError


@pytest.mark.asyncio
async def test_generate_reply_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
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
        endpoint="/v1/chat/completions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    reply = await client.generate_reply(
        model="gemini/gemini-2.5-flash",
        user_message="hello",
        history=[ChatMessage(role="user", content="hi")],
        system_prompt="test",
    )
    assert "سلام" in reply
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_extracts_message_list_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "سلام"},
                            {"type": "text", "text": "در خدمتم"},
                        ],
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)

    client = UnifiedAIGatewayClient(
        base_url="http://x",
        endpoint="/v1/chat/completions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    reply = await client.generate_reply(
        model="gemini/gemini-2.5-flash",
        user_message="hello",
        history=[],
        system_prompt="test",
    )
    assert reply == "سلام\nدر خدمتم"
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "upstream failed"})

    client = UnifiedAIGatewayClient(
        base_url="http://x",
        endpoint="/v1/chat/completions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(AIBackendError):
        await client.generate_reply(
            model="gemini/gemini-2.5-flash",
            user_message="hello",
            history=[],
            system_prompt="test",
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_rejects_image_for_non_capable_model() -> None:
    client = UnifiedAIGatewayClient(
        base_url="http://x",
        endpoint="/v1/chat/completions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
        image_capable_models=["gemini-2.5-flash"],
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))

    with pytest.raises(ModelCapabilityError):
        await client.generate_reply(
            model="groq/llama-3.3-70b-versatile",
            user_message="hello",
            history=[],
            system_prompt="test",
            image_inline_data={"mimeType": "image/png", "data": "xxx"},
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_generate_reply_sends_image_as_data_url() -> None:
    seen = {"body": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    client = UnifiedAIGatewayClient(
        base_url="http://x",
        endpoint="/v1/chat/completions",
        timeout_sec=10,
        generation_config=UAGGenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=512),
        image_capable_models=["gemini-2.5-flash"],
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await client.generate_reply(
        model="gemini/gemini-2.5-flash",
        user_message="describe",
        history=[],
        system_prompt="",
        image_inline_data={"mimeType": "image/png", "data": "aGVsbG8="},
    )

    assert seen["body"] is not None
    assert "data:image/png;base64,aGVsbG8=" in str(seen["body"])
    await client.aclose()
