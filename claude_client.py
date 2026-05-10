import os
import json
import httpx
import logging

logger = logging.getLogger(__name__)

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")

DEFAULT_MODEL = "claude-sonnet-4-6"

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}


def get_claude_response(
    system_prompt: str,
    messages: list,
    model: str = DEFAULT_MODEL,
    enable_web_search: bool = False,
    notion_tools: list = None,
) -> str:
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if enable_web_search:
        headers["anthropic-beta"] = "web-search-2025-03-05"

    tools = []
    if enable_web_search:
        tools.append(WEB_SEARCH_TOOL)
    if notion_tools:
        tools.extend(notion_tools)

    payload = {
        "model": model,
        "max_tokens": 2048,
        "system": system_prompt,
        "messages": list(messages),
    }
    if tools:
        payload["tools"] = tools

    try:
        for _ in range(15):  # enough for multi-step Notion operations
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=90,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("content", [])
            stop_reason = data.get("stop_reason")
            logger.info(f"Claude [{model}] stop_reason={stop_reason}, blocks={len(content)}")

            if stop_reason != "tool_use":
                texts = [b["text"] for b in content if b.get("type") == "text"]
                return " ".join(texts) if texts else "Sorry, I couldn't get a response."

            # Execute tool calls and loop back
            payload["messages"].append({"role": "assistant", "content": content})
            tool_results = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                tool_name = block["name"]
                tool_input = block.get("input", {})

                if tool_name == "web_search":
                    # Anthropic executes web search server-side; content is already in the block
                    result = block.get("content", "")
                elif tool_name.startswith("notion_"):
                    from notion_tools import execute_notion_tool
                    result = execute_notion_tool(tool_name, tool_input)
                    logger.info(f"Notion tool [{tool_name}] input={json.dumps(tool_input)[:200]}")
                else:
                    result = f"Unknown tool: {tool_name}"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result if isinstance(result, str) else json.dumps(result),
                })

            payload["messages"].append({"role": "user", "content": tool_results})

        texts = [b["text"] for b in content if b.get("type") == "text"]
        return " ".join(texts) if texts else "Sorry, I had trouble with that."

    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return "Sorry, I had trouble processing that. Can you say that again?"
