from __future__ import annotations

from dataclasses import dataclass
import logging
import time

import httpx

from gerdoo_ai_bot.types import ChatMessage, GeminiProxyError, ModelCapabilityError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GeminiGenerationConfig:
    temperature: float
    top_p: float
    max_output_tokens: int


class GeminiProxyClient:
    def __init__(
        self,
        base_url: str,
        endpoint: str,
        timeout_sec: float,
        generation_config: GeminiGenerationConfig,
        image_capable_models: list[str] | None = None,
        debug_http: bool = True,
        body_preview_chars: int = 500,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint if endpoint.startswith("/") else "/" + endpoint
        timeout = httpx.Timeout(connect=10.0, read=timeout_sec, write=20.0, pool=20.0)
        self.client = httpx.AsyncClient(timeout=timeout)
        self.generation_config = generation_config
        self.image_capable_models = {self._normalize_model_id(m) for m in (image_capable_models or [])}
        self.debug_http = debug_http
        self.body_preview_chars = max(80, int(body_preview_chars))

    async def aclose(self) -> None:
        await self.client.aclose()

    async def generate_reply(
        self,
        *,
        model: str,
        user_message: str,
        history: list[ChatMessage],
        system_prompt: str,
        image_inline_data: dict | None = None,
    ) -> str:
        model_id = self._normalize_model_id(model)
        if image_inline_data and self.image_capable_models and model_id not in self.image_capable_models:
            raise ModelCapabilityError(f"Model '{model_id}' does not support image input in current bot configuration.")

        contents: list[dict] = []
        for item in history:
            role = "model" if item.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": item.content}]})

        user_parts: list[dict] = [{"text": user_message}]
        if image_inline_data:
            user_parts.append({"inlineData": image_inline_data})
        contents.append({"role": "user", "parts": user_parts})

        payload: dict = {
            "model": model,
            "contents": contents,
            "generationConfig": {
                "temperature": self.generation_config.temperature,
                "topP": self.generation_config.top_p,
                "maxOutputTokens": self.generation_config.max_output_tokens,
            },
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        url = f"{self.base_url}{self.endpoint}"
        started = time.monotonic()
        if self.debug_http:
            logger.debug(
                "gemini_proxy_request url=%s model=%s history_len=%s use_image=%s",
                url,
                model_id,
                len(history),
                bool(image_inline_data),
            )
        try:
            response = await self.client.post(url, json=payload)
        except httpx.HTTPError as exc:
            elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
            logger.error(
                "gemini_proxy_connect_error elapsed_ms=%s model=%s error=%r",
                elapsed_ms,
                model_id,
                str(exc),
            )
            raise GeminiProxyError(f"Gemini proxy connection error: {exc}") from exc
        elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
        if self.debug_http:
            logger.debug(
                "gemini_proxy_response status=%s elapsed_ms=%s model=%s",
                response.status_code,
                elapsed_ms,
                model_id,
            )

        if response.status_code != 200:
            detail = response.text
            try:
                body = response.json()
                detail = str(body.get("detail") or body.get("error") or detail)
            except Exception:
                pass
            logger.error(
                "gemini_proxy_error status=%s elapsed_ms=%s model=%s detail=%r",
                response.status_code,
                elapsed_ms,
                model_id,
                detail[: self.body_preview_chars],
            )
            raise GeminiProxyError(f"Gemini proxy error ({response.status_code}): {detail}")

        body = response.json()
        text = self._extract_text(body)
        if not text:
            raise GeminiProxyError("Gemini proxy returned empty text response")
        return text

    @staticmethod
    def _extract_text(body: dict) -> str:
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return ""

        first = candidates[0]
        content = first.get("content") if isinstance(first, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            return ""

        chunks: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            # Gemini responses can include internal reasoning fragments marked as thought=true.
            # Never expose those fragments to end-users.
            if part.get("thought") is True:
                continue
            if isinstance(part.get("text"), str):
                chunks.append(part["text"].strip())
        return "\n".join(chunk for chunk in chunks if chunk).strip()

    @staticmethod
    def _normalize_model_id(model: str) -> str:
        value = model.strip()
        if value.startswith("models/"):
            return value.split("/", 1)[1]
        return value
