from __future__ import annotations

from dataclasses import dataclass
import logging
import time

import httpx

from gerdoo_ai_bot.types import AIBackendError, ChatMessage, ModelCapabilityError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UAGGenerationConfig:
    temperature: float
    top_p: float
    max_output_tokens: int


class UnifiedAIGatewayClient:
    def __init__(
        self,
        base_url: str,
        endpoint: str,
        timeout_sec: float,
        generation_config: UAGGenerationConfig,
        image_capable_models: list[str] | None = None,
        auth_token: str = "",
        auth_header_name: str = "x-api-token",
        debug_http: bool = True,
        body_preview_chars: int = 500,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint if endpoint.startswith("/") else "/" + endpoint
        timeout = httpx.Timeout(connect=10.0, read=timeout_sec, write=20.0, pool=20.0)
        self.client = httpx.AsyncClient(timeout=timeout, trust_env=False)
        self.generation_config = generation_config
        self.image_capable_models = {self._normalize_model_id(m) for m in (image_capable_models or [])}
        self.auth_token = auth_token.strip()
        self.auth_header_name = (auth_header_name or "x-api-token").strip() or "x-api-token"
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

        messages: list[dict] = []
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

        payload: dict = {
            "model": self._build_model_payload(model),
            "messages": messages,
            "temperature": self.generation_config.temperature,
            "top_p": self.generation_config.top_p,
            "max_tokens": self.generation_config.max_output_tokens,
            "stream": False,
        }

        headers: dict[str, str] = {}
        if self.auth_token:
            headers[self.auth_header_name] = self.auth_token

        url = f"{self.base_url}{self.endpoint}"
        started = time.monotonic()
        if self.debug_http:
            logger.debug(
                "uag_request url=%s model=%s history_len=%s use_image=%s",
                url,
                model_id,
                len(history),
                bool(image_inline_data),
            )
        try:
            response = await self.client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
            logger.error(
                "uag_connect_error elapsed_ms=%s model=%s error=%r",
                elapsed_ms,
                model_id,
                str(exc),
            )
            raise AIBackendError(f"UAG connection error: {exc}") from exc

        elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
        if self.debug_http:
            logger.debug(
                "uag_response status=%s elapsed_ms=%s model=%s",
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
                "uag_error status=%s elapsed_ms=%s model=%s detail=%r",
                response.status_code,
                elapsed_ms,
                model_id,
                detail[: self.body_preview_chars],
            )
            raise AIBackendError(f"UAG error ({response.status_code}): {detail}")

        body = response.json()
        text = self._extract_text(body)
        if not text:
            raise AIBackendError("UAG returned empty text response")
        return text

    @staticmethod
    def _extract_text(body: dict) -> str:
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
    def _build_model_payload(model: str) -> str | list[dict]:
        value = model.strip()
        if "/" not in value or value.startswith("models/"):
            return value

        provider, model_id = value.split("/", 1)
        provider = provider.strip().lower()
        model_id = model_id.strip()
        if not provider or not model_id:
            return value

        return [{"provider": provider, "model": model_id, "priority": 0}]
