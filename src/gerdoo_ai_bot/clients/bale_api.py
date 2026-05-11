from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from gerdoo_ai_bot.types import PlatformApiError

logger = logging.getLogger(__name__)


class BaleBotApiClient:
    def __init__(
        self,
        token: str,
        api_base_url: str,
        file_base_url: str,
        timeout_sec: float = 45.0,
        debug_http: bool = True,
        body_preview_chars: int = 500,
    ) -> None:
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.file_base_url = file_base_url.rstrip("/")
        self.debug_http = debug_http
        self.body_preview_chars = max(80, int(body_preview_chars))
        timeout = httpx.Timeout(connect=15.0, read=timeout_sec, write=30.0, pool=30.0)
        self.client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self.client.aclose()

    def _method_url(self, method: str) -> str:
        return f"{self.api_base_url}/bot{self.token}/{method}"

    def _safe_method_url(self, method: str) -> str:
        return f"{self.api_base_url}/bot***redacted***{method}"

    async def get_updates(self, offset: int | None, timeout: int, allowed_updates: list[str]) -> list[dict]:
        payload = {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": allowed_updates,
        }
        response = await self._post("getUpdates", json_payload=payload)
        if not isinstance(response, list):
            raise PlatformApiError("bale:getUpdates invalid response")
        return response

    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict:
        payload: dict = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id and reply_to_message_id > 0:
            payload["reply_to_message_id"] = reply_to_message_id

        response = await self._post("sendMessage", json_payload=payload)
        if not isinstance(response, dict):
            raise PlatformApiError("bale:sendMessage invalid response")
        return response

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict:
        payload: dict = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text

        response = await self._post("answerCallbackQuery", json_payload=payload)
        if not isinstance(response, bool):
            return {"ok": bool(response)}
        return {"ok": response}

    async def delete_message(self, chat_id: str, message_id: int) -> dict:
        payload = {"chat_id": chat_id, "message_id": int(message_id)}
        response = await self._post("deleteMessage", json_payload=payload)
        if not isinstance(response, bool):
            return {"ok": bool(response)}
        return {"ok": response}

    async def get_file(self, file_id: str) -> dict:
        response = await self._post("getFile", json_payload={"file_id": file_id})
        if not isinstance(response, dict):
            raise PlatformApiError("bale:getFile invalid response")
        return response

    def file_url(self, file_path: str) -> str:
        return f"{self.file_base_url}/bot{self.token}/{file_path}"

    async def download_file(self, file_path: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        url = self.file_url(file_path)
        async with self.client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise PlatformApiError(f"bale file download failed: {resp.status_code}")
            with output_path.open("wb") as fh:
                async for chunk in resp.aiter_bytes():
                    fh.write(chunk)
        return output_path

    async def _post(
        self,
        method: str,
        *,
        json_payload: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
    ) -> object:
        url = self._method_url(method)
        started = time.monotonic()
        if self.debug_http:
            logger.debug(
                "bale_http_request method=%s url=%s has_json=%s has_data=%s has_files=%s",
                method,
                self._safe_method_url(method),
                bool(json_payload),
                bool(data),
                bool(files),
            )
        response = await self.client.post(url, json=json_payload, data=data, files=files)
        elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
        if self.debug_http:
            logger.debug(
                "bale_http_response method=%s status=%s elapsed_ms=%s",
                method,
                response.status_code,
                elapsed_ms,
            )
        if response.status_code != 200:
            text_preview = response.text[: self.body_preview_chars]
            logger.error(
                "bale_http_error method=%s status=%s elapsed_ms=%s body=%r",
                method,
                response.status_code,
                elapsed_ms,
                text_preview,
            )
            raise PlatformApiError(f"bale:{method} HTTP {response.status_code} {response.text}")

        body = response.json()
        if not body.get("ok"):
            description = body.get("description", "unknown API error")
            logger.error("bale_api_error method=%s description=%r", method, description)
            raise PlatformApiError(f"bale:{method} {description}")

        return body["result"]

    @staticmethod
    def encode_markup(markup: dict) -> str:
        return json.dumps(markup, ensure_ascii=False)
