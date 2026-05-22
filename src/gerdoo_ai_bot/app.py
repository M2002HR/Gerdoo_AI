from __future__ import annotations

from gerdoo_ai_bot.clients.bale_api import BaleBotApiClient
from gerdoo_ai_bot.clients.uag import UAGGenerationConfig, UnifiedAIGatewayClient
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

    ai_client = UnifiedAIGatewayClient(
        base_url=settings.uag_base_url,
        chat_endpoint=settings.uag_chat_endpoint,
        image_endpoint=settings.uag_image_endpoint,
        audio_transcriptions_endpoint=settings.uag_audio_transcriptions_endpoint,
        timeout_sec=float(settings.uag_timeout_sec),
        generation_config=UAGGenerationConfig(
            temperature=settings.ai_temperature,
            top_p=settings.ai_top_p,
            max_output_tokens=settings.ai_max_output_tokens,
        ),
        auth_token=settings.uag_auth_token if settings.uag_auth_enabled else "",
        auth_header_name=settings.uag_auth_header_name,
        debug_http=settings.log_http_enabled,
        body_preview_chars=settings.log_http_body_preview_chars,
    )

    storage = ChatStorage(settings.db_url)

    return BaleAIBotService(
        settings=settings,
        bale_client=bale_client,
        ai_client=ai_client,
        storage=storage,
    )
