"""The insight narrative: one forced tool call. Goes through reasoning/bedrock_client like every model call in this
service, so it gets the PII sentinel, throttling retries, token usage and traces."""
import asyncio
import logging

from gemini_brain.insights.bedrock.prompts import SYSTEM_PROMPT, TOOL_NAME, TOOL_SPEC, build_user_message
from gemini_brain.insights.config import get_settings

logger = logging.getLogger("gemini_brain.insights.bedrock")


class BedrockGenerationError(Exception):
    pass


class BedrockInsightClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._model_id = settings.bedrock_model_id
        self._max_tokens = settings.bedrock_max_tokens

    def _invoke_sync(self, widget_display_name: str, facts_text: str) -> dict:
        from gemini_brain.reasoning.bedrock_client import converse_tools

        try:
            response = converse_tools(
                SYSTEM_PROMPT,
                [{"role": "user", "content": [{"text": build_user_message(widget_display_name, facts_text)}]}],
                tools=[TOOL_SPEC],
                temperature=0.3,
                max_tokens=self._max_tokens,
                model_id=self._model_id,
                purpose="insight",
                tool_choice=TOOL_NAME,
            )
        except Exception as exc:
            logger.error("Bedrock invocation failed: %s", exc)
            raise BedrockGenerationError(str(exc)) from exc

        content_blocks = response.get("output", {}).get("message", {}).get("content", [])
        for block in content_blocks:
            tool_use = block.get("toolUse")
            if tool_use and tool_use.get("name") == TOOL_NAME:
                return tool_use.get("input", {})

        raise BedrockGenerationError("Model response did not include the expected tool call")

    async def generate_narrative(self, widget_display_name: str, facts_text: str) -> dict:
        return await asyncio.to_thread(self._invoke_sync, widget_display_name, facts_text)


_client_singleton: BedrockInsightClient | None = None


def get_bedrock_client() -> BedrockInsightClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = BedrockInsightClient()
    return _client_singleton
