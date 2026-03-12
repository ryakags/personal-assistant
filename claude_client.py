import os
import httpx
import logging

logger = logging.getLogger(__name__)

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")


def get_claude_response(system_prompt: str, messages: list) -> str:
    """Send messages to Claude and get a response."""
    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "system": system_prompt,
                "messages": messages
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["content"][0]["text"]
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return "Sorry, I had trouble processing that. Can you say that again?"
