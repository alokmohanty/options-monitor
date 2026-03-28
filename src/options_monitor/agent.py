"""Gemini-powered conversational agent for the options trading bot monitor.

Uses the google-genai SDK with automatic function calling so the agent can
inspect logs and source code on demand.
"""

import logging
import re
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from options_monitor import config
from options_monitor.tools import TOOLS

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a helpful assistant for monitoring an options trading bot. "
    "You have access to tools to read the bot log file and source code. "
    "Use tools only when necessary to answer the question — do not chain multiple tool calls "
    "unless the question clearly requires it. Prefer a single focused tool call over several. "
    "For general questions that don't need live data, answer directly without using tools. "
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
            maximum_remote_calls=5,
        ),
    )


def _parse_retry_delay(error_message: str) -> float | None:
    """Extract the suggested retry delay (in seconds) from a 429 error message."""
    match = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", error_message, re.IGNORECASE)
    return float(match.group(1)) if match else None


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
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self._chat.send_message(user_message)
                text = response.text
                # If the model hit the tool-call limit mid-chain, response.text is
                # empty. Send a follow-up so it summarises with what it gathered.
                if not text or not text.strip():
                    logger.warning("Empty response (likely AFC limit hit), sending follow-up")
                    follow_up = self._chat.send_message(
                        "Based on the information you have gathered so far, "
                        "please answer the original question."
                    )
                    text = follow_up.text
                return text or "(no response)"
            except genai_errors.ClientError as exc:
                is_rate_limited = (
                    "429" in str(exc)
                    or getattr(exc, 'code', None) == 429
                    or getattr(exc, 'status_code', None) == 429
                )
                if is_rate_limited:
                    delay = _parse_retry_delay(str(exc)) or (30 * (attempt + 1))
                    if attempt < max_retries - 1:
                        logger.warning("Rate limited (429), retrying in %.0fs...", delay)
                        time.sleep(delay)
                        self._new_chat()
                        continue
                    return (
                        f"⚠️ Rate limit exceeded (free tier: 5 requests/min). "
                        f"Please wait ~{int(delay)}s and try again. "
                        f"Consider enabling billing at https://ai.dev/rate-limit for higher limits."
                    )
                logger.exception("Gemini API error")
                return f"Gemini API error: {exc}"
            except Exception as exc:
                logger.exception("Error communicating with Gemini")
                return f"An error occurred while processing your request: {exc}"
        return "Request failed after retries. Please try again in a minute."

    def reset(self) -> None:
        """Clear conversation history by starting a new chat session."""
        self._new_chat()
