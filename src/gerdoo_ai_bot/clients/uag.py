from __future__ import annotations

import base64
from dataclasses import dataclass
import logging
import time
from typing import Any

import httpx

from gerdoo_ai_bot.types import AIBackendError, ChatMessage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UAGGenerationConfig:
    temperature: float
    top_p: float
    max_output_tokens: int


@dataclass(slots=True)
class UAGRouterConfig:
    providers: list[str] | None = None
    strategy: str = "fallback_chain"
    mode: str = "limit_safe"
    timeout_sec: float = 28.0
    max_attempts: int = 8


@dataclass(slots=True)
class UAGImageResult:
    image_bytes: bytes | None
    image_url: str | None
    revised_prompt: str


class UnifiedAIGatewayClient:
    def __init__(
        self,
        base_url: str,
        chat_endpoint: str,
        image_endpoint: str,
        audio_transcriptions_endpoint: str,
        timeout_sec: float,
        generation_config: UAGGenerationConfig,
        auth_token: str = "",
        auth_header_name: str = "x-api-token",
        debug_http: bool = True,
        body_preview_chars: int = 500,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_endpoint = chat_endpoint if chat_endpoint.startswith("/") else "/" + chat_endpoint
        self.image_endpoint = image_endpoint if image_endpoint.startswith("/") else "/" + image_endpoint
        self.audio_transcriptions_endpoint = (
            audio_transcriptions_endpoint
            if audio_transcriptions_endpoint.startswith("/")
            else "/" + audio_transcriptions_endpoint
        )

        timeout = httpx.Timeout(connect=10.0, read=timeout_sec, write=40.0, pool=20.0)
        self.client = httpx.AsyncClient(timeout=timeout, trust_env=False)
        self.generation_config = generation_config
        self.auth_token = auth_token.strip()
        self.auth_header_name = (auth_header_name or "x-api-token").strip() or "x-api-token"
        self.debug_http = debug_http
        self.body_preview_chars = max(80, int(body_preview_chars))

    async def aclose(self) -> None:
        await self.client.aclose()

    async def generate_reply(
        self,
        *,
        models: list[str],
        providers: list[str],
        user_message: str,
        history: list[ChatMessage],
        system_prompt: str,
        router: UAGRouterConfig,
        image_inline_data: dict | None = None,
    ) -> str:
        router_payload = UAGRouterConfig(
            providers=(router.providers or [p for p in providers if p.strip()]),
            strategy=router.strategy,
            mode=router.mode,
            timeout_sec=router.timeout_sec,
            max_attempts=router.max_attempts,
        )

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        for item in history:
            role = "assistant" if item.role == "assistant" else "user"
            messages.append({"role": role, "content": item.content})

        if image_inline_data:
            mime_type = str(image_inline_data.get("mimeType") or "image/jpeg")
            encoded = str(image_inline_data.get("data") or "").strip()
            data_url = f"data:{mime_type};base64,{encoded}"
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_message},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": user_message})

        payload: dict[str, Any] = {
            "model": self._build_model_preferences(models),
            "messages": messages,
            "temperature": self.generation_config.temperature,
            "top_p": self.generation_config.top_p,
            "stream": False,
            "x_router": self._build_router_payload(router_payload),
        }
        if int(self.generation_config.max_output_tokens) > 0:
            payload["max_tokens"] = int(self.generation_config.max_output_tokens)

        body = await self._post_json(self.chat_endpoint, payload)
        text = self._extract_text(body)
        if not text:
            raise AIBackendError("UAG returned empty text response")
        return text

    async def generate_image(
        self,
        *,
        models: list[str],
        providers: list[str],
        prompt: str,
        router: UAGRouterConfig,
        size: str,
        quality: str,
        n: int,
        seed: int | None = None,
    ) -> UAGImageResult:
        router_payload = UAGRouterConfig(
            providers=(router.providers or [p for p in providers if p.strip()]),
            strategy=router.strategy,
            mode=router.mode,
            timeout_sec=router.timeout_sec,
            max_attempts=router.max_attempts,
        )

        payload: dict[str, Any] = {
            "model": self._build_model_preferences(models),
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": max(1, int(n)),
            "response_format": "b64_json",
            "x_router": self._build_router_payload(router_payload),
        }
        if seed is not None:
            payload["seed"] = int(seed)
        body = await self._post_json(self.image_endpoint, payload)

        winner = body.get("winner") if isinstance(body, dict) else None
        result_payload: Any = body
        if isinstance(winner, dict) and isinstance(winner.get("payload"), dict):
            result_payload = winner.get("payload")

        if not isinstance(result_payload, dict):
            raise AIBackendError("UAG image response format is invalid")

        data_items = result_payload.get("data")
        if not isinstance(data_items, list) or not data_items:
            raise AIBackendError("UAG image response did not include image data")

        first = data_items[0] if isinstance(data_items[0], dict) else {}
        b64_data = str(first.get("b64_json") or "").strip()
        url = str(first.get("url") or "").strip() or None
        revised_prompt = str(first.get("revised_prompt") or "").strip()

        image_bytes: bytes | None = None
        if b64_data:
            try:
                image_bytes = base64.b64decode(b64_data)
            except Exception as exc:  # noqa: BLE001
                raise AIBackendError(f"Failed to decode generated image: {exc}") from exc

        if image_bytes is None and url and url.startswith("data:image") and "," in url:
            try:
                image_bytes = base64.b64decode(url.split(",", 1)[1])
            except Exception:  # noqa: BLE001
                image_bytes = None

        if image_bytes is None and not url:
            raise AIBackendError("UAG image response did not contain usable image output")

        return UAGImageResult(image_bytes=image_bytes, image_url=url, revised_prompt=revised_prompt)

    async def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        mime_type: str,
        language: str | None,
        provider: str,
        model_preferences: list[str],
    ) -> str:
        files = {
            "file": (filename or "voice.ogg", audio_bytes, mime_type or "audio/ogg"),
        }
        data: dict[str, str] = {}
        if provider:
            data["provider"] = provider.strip().lower()
        if language:
            data["language"] = language
        if model_preferences:
            data["model"] = self._normalize_model_id(model_preferences[0])
            if len(model_preferences) > 1:
                data["fallback_model"] = self._normalize_model_id(model_preferences[1])

        body = await self._post_form(self.audio_transcriptions_endpoint, data=data, files=files)

        payload = body.get("payload") if isinstance(body, dict) else None
        if isinstance(payload, dict):
            text = str(payload.get("text") or "").strip()
            if text:
                return text

        text_direct = str(body.get("text") or "").strip() if isinstance(body, dict) else ""
        if text_direct:
            return text_direct

        raise AIBackendError("UAG transcription response did not include text")

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.auth_token:
            headers[self.auth_header_name] = self.auth_token
        return headers

    async def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", endpoint, json=payload)

    async def _post_form(self, endpoint: str, *, data: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> dict[str, Any]:
        return await self._request("POST", endpoint, data=data, files=files)

    async def _request(self, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = self._build_headers()
        started = time.monotonic()
        if self.debug_http:
            logger.debug("uag_request method=%s url=%s", method, url)
        try:
            response = await self.client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
            logger.error("uag_connect_error elapsed_ms=%s error=%r", elapsed_ms, str(exc))
            raise AIBackendError(f"UAG connection error: {exc}") from exc

        elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
        if self.debug_http:
            logger.debug("uag_response status=%s elapsed_ms=%s", response.status_code, elapsed_ms)

        body: dict[str, Any] | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                body = parsed
            else:
                body = {"result": parsed}
        except Exception:  # noqa: BLE001
            body = None

        if response.status_code != 200:
            detail = response.text
            if body is not None:
                detail = str(body.get("detail") or body.get("error") or body)
            logger.error(
                "uag_error status=%s elapsed_ms=%s detail=%r",
                response.status_code,
                elapsed_ms,
                detail[: self.body_preview_chars],
            )
            raise AIBackendError(f"UAG error ({response.status_code}): {detail}")

        if body is None:
            raise AIBackendError("UAG returned non-JSON response")

        # UAG router-level failure may still arrive with non-200 in most paths, but keep this guard.
        if body.get("ok") is False:
            detail = str(body.get("error") or body.get("error_type") or "unknown routing error")
            raise AIBackendError(f"UAG routing error: {detail}")

        return body

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        # OpenAI-like chat completion response
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message") if isinstance(first, dict) else {}
            content = message.get("content") if isinstance(message, dict) else None

            if isinstance(content, str):
                if content.strip():
                    return content.strip()
            elif isinstance(content, list):
                chunks: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        txt = item.get("text")
                        if isinstance(txt, str) and txt.strip():
                            chunks.append(txt.strip())
                if chunks:
                    return "\n".join(chunks).strip()

        # Router dispatch shape
        winner = body.get("winner")
        if isinstance(winner, dict):
            payload = winner.get("payload")
            if isinstance(payload, dict):
                return UnifiedAIGatewayClient._extract_text(payload)

        # Gemini-like fallback response shape
        candidates = body.get("candidates")
        if isinstance(candidates, list) and candidates:
            first = candidates[0] if isinstance(candidates[0], dict) else {}
            content = first.get("content") if isinstance(first, dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if isinstance(parts, list):
                chunks: list[str] = []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    if part.get("thought") is True:
                        continue
                    txt = part.get("text")
                    if isinstance(txt, str) and txt.strip():
                        chunks.append(txt.strip())
                if chunks:
                    return "\n".join(chunks).strip()

        result_text = body.get("result")
        if isinstance(result_text, str) and result_text.strip():
            return result_text.strip()

        return ""

    @staticmethod
    def _normalize_model_id(model: str) -> str:
        value = model.strip()
        if value.startswith("models/"):
            return value.split("/", 1)[1]
        if "/" in value:
            return value.split("/", 1)[1]
        return value

    @staticmethod
    def _build_model_preferences(models: list[str]) -> list[dict[str, Any]]:
        prefs: list[dict[str, Any]] = []
        for priority, item in enumerate(models):
            raw = (item or "").strip()
            if not raw:
                continue
            if "/" in raw and not raw.startswith("models/"):
                provider, model_id = raw.split("/", 1)
                prefs.append(
                    {
                        "provider": provider.strip().lower(),
                        "model": model_id.strip(),
                        "priority": priority,
                    }
                )
            else:
                prefs.append({"model": raw, "priority": priority})
        return prefs

    @staticmethod
    def _build_router_payload(router: UAGRouterConfig) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "strategy": router.strategy,
            "mode": router.mode,
            "timeout_sec": max(5.0, float(router.timeout_sec)),
            "max_attempts": max(1, int(router.max_attempts)),
            "result_policy": "best_only",
        }
        if router.providers:
            payload["providers"] = [p.strip().lower() for p in router.providers if p.strip()]
        return payload
