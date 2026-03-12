import os
import httpx
import logging

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_message(chat_id: str, text: str) -> bool:
    """Send a message via Telegram."""
    try:
        response = httpx.post(
            f"{BASE_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown"
            },
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def set_webhook(webhook_url: str) -> bool:
    """Register the webhook URL with Telegram."""
    try:
        response = httpx.post(
            f"{BASE_URL}/setWebhook",
            json={"url": webhook_url},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        logger.info(f"Webhook set: {result}")
        return result.get("ok", False)
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
        return False
