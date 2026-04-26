import os
import httpx
import logging

logger = logging.getLogger(__name__)

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")

DEFAULT_MODEL = "claude-sonnet-4-6"

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}


def get_claude_response(system_prompt: str, messages: list, model: str = DEFAULT_MODEL, enable_web_search: bool = False) -> str:
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    if enable_web_search:
        headers["anthropic-beta"] = "web-search-2025-03-05"

    payload = {
        "model": model,
        "max_tokens": 1000,
        "system": system_prompt,
        "messages": list(messages)
    }
    if enable_web_search:
        payload["tools"] = [WEB_SEARCH_TOOL]

    try:
        for _ in range(5):
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("content", [])
            stop_reason = data.get("stop_reason")
            logger.info(f"Claude response using model: {model}, stop_reason: {stop_reason}")

            if stop_reason != "tool_use":
                texts = [b["text"] for b in content if b.get("type") == "text"]
                return " ".join(texts) if texts else "Sorry, I couldn't get a response."

            # Agentic loop: pass tool results back so Claude can continue
            payload["messages"].append({"role": "assistant", "content": content})
            tool_results = [
                {"type": "tool_result", "tool_use_id": b["id"], "content": b.get("content", "")}
                for b in content if b.get("type") == "tool_use"
            ]
            payload["messages"].append({"role": "user", "content": tool_results})

        texts = [b["text"] for b in content if b.get("type") == "text"]
        return " ".join(texts) if texts else "Sorry, I had trouble with that."
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return "Sorry, I had trouble processing that. Can you say that again?"
