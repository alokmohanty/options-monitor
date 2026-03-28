"""Gemini-powered conversational agent for the options trading bot monitor.

Uses the google-genai SDK with automatic function calling so the agent can
inspect logs and source code on demand.
"""

import logging

from google import genai
from google.genai import types

from options_monitor import config
from options_monitor.tools import TOOLS

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a helpful assistant for monitoring an options trading bot. "
    "You have access to tools to read the bot log file and source code. "
    "Use tools to answer questions about errors, trades, and the trading strategy. "
    "Always prefer tool results over guessing. "
    "Format responses for Discord (use code blocks for logs/code)."
)


def _make_chat_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        temperature=config.GeminiConfig.temperature,
        max_output_tokens=config.GeminiConfig.max_output_tokens,
        tools=TOOLS,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=False,
            maximum_remote_calls=10,
        ),
    )


class Agent:
    """Maintains per-conversation history and handles automatic tool calls."""

    def __init__(self) -> None:
        self._client = genai.Client(api_key=config.GeminiConfig.api_key)
        self._new_chat()

    def _new_chat(self) -> None:
        self._chat = self._client.chats.create(
            model=config.GeminiConfig.model,
            config=_make_chat_config(),
        )

    def ask(self, user_message: str) -> str:
        """Send a user message and return the agent's final text reply."""
        try:
            response = self._chat.send_message(user_message)
            return response.text or "(no response)"
        except Exception as exc:
            logger.exception("Error communicating with Gemini")
            return f"An error occurred while processing your request: {exc}"

    def reset(self) -> None:
        """Clear conversation history by starting a new chat session."""
        self._new_chat()
