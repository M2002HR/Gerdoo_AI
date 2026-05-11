from __future__ import annotations

from gerdoo_ai_bot.clients.bale_api import BaleBotApiClient
from gerdoo_ai_bot.clients.gemini_proxy import GeminiGenerationConfig, GeminiProxyClient
from gerdoo_ai_bot.config import load_settings
from gerdoo_ai_bot.service import BaleAIBotService
from gerdoo_ai_bot.storage import ChatStorage


async def build_service(env_file: str = ".env") -> BaleAIBotService:
    settings = load_settings(env_file)

    bale_client = BaleBotApiClient(
        token=settings.bale_bot_token,
        api_base_url=settings.bale_api_base_url,
        file_base_url=settings.bale_file_base_url,
        timeout_sec=float(settings.bale_poll_timeout_sec + 20),
        debug_http=settings.log_http_enabled,
        body_preview_chars=settings.log_http_body_preview_chars,
    )

    gemini_client = GeminiProxyClient(
        base_url=settings.gemini_proxy_base_url,
        endpoint=settings.gemini_proxy_endpoint,
        timeout_sec=float(settings.gemini_proxy_timeout_sec),
        generation_config=GeminiGenerationConfig(
            temperature=settings.gemini_temperature,
            top_p=settings.gemini_top_p,
            max_output_tokens=settings.gemini_max_output_tokens,
        ),
        image_capable_models=settings.gemini_image_capable_models,
        debug_http=settings.log_http_enabled,
        body_preview_chars=settings.log_http_body_preview_chars,
    )

    storage = ChatStorage(settings.db_url)

    return BaleAIBotService(
        settings=settings,
        bale_client=bale_client,
        gemini_client=gemini_client,
        storage=storage,
    )
