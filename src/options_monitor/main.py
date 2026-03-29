"""Entry point for the options-monitor Discord agent."""

import logging
import sys

from options_monitor import config
from options_monitor.counter import install_http_hook
from options_monitor.discord_bot import MonitorBot


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)

    try:
        config.validate()
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

    logger.info("Starting options-monitor bot with model: %s", config.GeminiConfig.model)
    install_http_hook()  # must be before any genai.Client is created
    bot = MonitorBot()
    bot.run(config.DiscordConfig.bot_token, log_handler=None)


if __name__ == "__main__":
    main()
