"""
Discord bot interface for the options-monitor agent.

Each Discord channel gets its own Agent instance so conversation history
is isolated per channel.
"""

import asyncio
import logging
from collections import defaultdict

import discord
from discord.ext import commands

from options_monitor import config
from options_monitor.agent import Agent

logger = logging.getLogger(__name__)

# Discord messages have a 2000-character limit per message
_DISCORD_MAX_LEN = 1990


def _split_message(text: str) -> list[str]:
    """Split a long message into Discord-safe chunks."""
    chunks: list[str] = []
    while len(text) > _DISCORD_MAX_LEN:
        split_at = text.rfind("\n", 0, _DISCORD_MAX_LEN)
        if split_at == -1:
            split_at = _DISCORD_MAX_LEN
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


class MonitorBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # Required for reading message text
        super().__init__(
            command_prefix=config.DiscordConfig.command_prefix,
            intents=intents,
            help_command=None,  # We provide our own
        )
        # Per-channel agent instances
        self._agents: dict[int, Agent] = defaultdict(Agent)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        await self.tree.sync()

    async def on_message(self, message: discord.Message) -> None:
        # Ignore messages from bots (including self)
        if message.author.bot:
            return

        # Optionally restrict to allowed channels
        allowed = config.DiscordConfig.allowed_channel_ids
        if allowed and message.channel.id not in allowed:
            return

        # Let command handlers run first
        await self.process_commands(message)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def setup_hook(self) -> None:
        """Register all slash + prefix commands."""
        self._register_prefix_commands()

    def _register_prefix_commands(self) -> None:

        @self.command(name="ask")
        async def ask_cmd(ctx: commands.Context, *, question: str) -> None:
            """Ask the agent a question about the trading bot.
            Usage: !ask <your question>
            """
            await self._handle_question(ctx, question)

        @self.command(name="reset")
        async def reset_cmd(ctx: commands.Context) -> None:
            """Reset the conversation history for this channel.
            Usage: !reset
            """
            agent = self._agents[ctx.channel.id]
            agent.reset()
            await ctx.send("Conversation history cleared for this channel.")

        @self.command(name="help")
        async def help_cmd(ctx: commands.Context) -> None:
            """Show available commands."""
            prefix = config.DiscordConfig.command_prefix
            embed = discord.Embed(
                title="Options Monitor Bot",
                description="I can answer questions about your options trading bot.",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name=f"`{prefix}ask <question>`",
                value="Ask anything about the bot: errors, trades, strategy, code.",
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}reset`",
                value="Clear conversation history for this channel.",
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}help`",
                value="Show this help message.",
                inline=False,
            )
            embed.set_footer(text="Powered by Gemini AI")
            await ctx.send(embed=embed)

        @self.command(name="logs")
        async def logs_cmd(ctx: commands.Context, lines: int = 50) -> None:
            """Fetch the last N lines from the trading bot log.
            Usage: !logs [n_lines]  (default 50)
            """
            question = f"Show me the last {lines} lines of the trading bot log."
            await self._handle_question(ctx, question)

        @self.command(name="errors")
        async def errors_cmd(ctx: commands.Context) -> None:
            """Check the log for recent errors.
            Usage: !errors
            """
            await self._handle_question(ctx, "Check the log file for errors and summarize them.")

        @self.command(name="trades")
        async def trades_cmd(ctx: commands.Context) -> None:
            """Show recent trade activity from the log.
            Usage: !trades
            """
            await self._handle_question(
                ctx,
                "Search the log for recent trade entries and summarize what trades were executed.",
            )

        @self.command(name="strategy")
        async def strategy_cmd(ctx: commands.Context) -> None:
            """Explain the trading strategy by reading the bot code.
            Usage: !strategy
            """
            await self._handle_question(
                ctx,
                "Read the trading bot source code and explain the trading strategy in detail.",
            )

    # ------------------------------------------------------------------
    # Core dispatcher
    # ------------------------------------------------------------------

    async def _handle_question(
        self, ctx: commands.Context, question: str
    ) -> None:
        async with ctx.typing():
            agent = self._agents[ctx.channel.id]
            # Run synchronous agent.ask() in a thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(None, agent.ask, question)

        chunks = _split_message(answer)
        for chunk in chunks:
            await ctx.send(chunk)
