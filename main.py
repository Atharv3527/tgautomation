"""
Entry point for the Telegram Sequential Video Automation.
Supports console commands and Telegram Saved Messages (/start, /stop, /status).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
from typing import Optional

# Ensure standard output supports UTF-8 on Windows consoles without charmap crash
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from telethon import TelegramClient, events

from bot_automation import BotAutomation
from config_loader import (
    AppConfig,
    LoopConfig,
    load_app_config,
    prompt_manual_loops,
    save_loops_to_config,
    validate_credentials,
)
from state_manager import StateManager


class FlushingFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()


def setup_logger() -> logging.Logger:
    """Sets up formatted stdout logging and auto-flushing file logging."""
    logger = logging.getLogger("telegram_automation")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    # Console handler
    c_handler = logging.StreamHandler(sys.stdout)
    c_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )
    c_handler.setFormatter(c_format)
    logger.addHandler(c_handler)

    # Auto-flushing file handler
    f_handler = FlushingFileHandler("automation.log", encoding="utf-8")
    f_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s"
    )
    f_handler.setFormatter(f_format)
    logger.addHandler(f_handler)

    return logger


def print_credential_help():
    """Prints instructions if Telegram credentials are missing."""
    print("=" * 65)
    print("[!] MISSING TELEGRAM API CREDENTIALS")
    print("=" * 65)
    print("To automate actions with your personal Telegram account, you need")
    print("your API_ID and API_HASH from Telegram.")
    print()
    print("HOW TO GET THEM:")
    print("1. Open https://my.telegram.org in your web browser.")
    print("2. Log in with your Telegram account phone number and verify the OTP.")
    print("3. Click on 'API development tools'.")
    print("4. Create a new application (you can enter any app title and short name).")
    print("5. Copy your 'api_id' (a number) and 'api_hash' (a string).")
    print()
    print("HOW TO CONFIGURE:")
    print("Open the '.env' file in this folder and fill in the values:")
    print("  TELEGRAM_API_ID=12345678")
    print("  TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789")
    print("  TELEGRAM_PHONE=+1234567890   (optional, Telethon can prompt)")
    print("=" * 65)


async def setup_saved_messages_listener(
    client: TelegramClient, automation: BotAutomation, logger: logging.Logger
):
    """
    Listens for commands (/status, /start, /stop, /pause) sent by the user to 'Saved Messages' ('me').
    Allows controlling the automation from any phone or desktop Telegram app!
    """
    @client.on(events.NewMessage(chats="me"))
    async def on_me_command(event: events.NewMessage.Event):
        text = (event.message.text or "").strip()
        cmd = text.split()[0].lower() if text else ""

        if cmd == "/status":
            summary = automation.get_status_summary()
            await event.reply(summary, parse_mode="markdown")
            logger.info("Executed /status command from Saved Messages.")

        elif cmd in ("/stop", "/pause"):
            automation.pause()
            msg = "⏸ *Automation Paused.*\nSend `/start` or `/resume` in Saved Messages to resume."
            await event.reply(msg, parse_mode="markdown")
            logger.info("Executed /pause command from Saved Messages.")

        elif cmd in ("/start", "/resume"):
            automation.resume()
            msg = "▶️ *Automation Resumed.*"
            await event.reply(msg, parse_mode="markdown")
            logger.info("Executed /resume command from Saved Messages.")


def console_listener(automation: BotAutomation, loop: asyncio.AbstractEventLoop, logger: logging.Logger):
    """Background thread listening for terminal input commands."""
    print("\n[Commands] Type 'status', 'pause', 'resume', or 'exit' into the terminal at any time.\n")
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            cmd = line.strip().lower()
            if not cmd:
                continue

            if cmd == "status":
                summary = automation.get_status_summary()
                print("\n" + summary.replace("*", "").replace("`", "") + "\n")
            elif cmd in ("pause", "stop"):
                automation.pause()
                print("[Console] Automation paused. Type 'start' or 'resume' to continue.")
            elif cmd in ("start", "resume"):
                automation.resume()
                print("[Console] Automation resumed.")
            elif cmd in ("exit", "quit"):
                print("[Console] Stopping automation and exiting...")
                automation.stop()
                break
            else:
                print(f"[Console] Unknown command: '{cmd}'. Available: status, pause, resume, exit")
        except Exception:
            break


async def main():
    parser = argparse.ArgumentParser(description="Telegram Sequential Video Automation")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to configuration JSON (default: config.json)",
    )
    parser.add_argument(
        "--env",
        default=".env",
        help="Path to .env file (default: .env)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run quick test with config.test.json (IDs 25 to 27)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset progress.json and start processing from scratch",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Interactively enter loop base links, start IDs, and end IDs",
    )
    parser.add_argument(
        "--base-link",
        help="Specify loop base link directly (e.g. https://t.me/c/3548255677/15/)",
    )
    parser.add_argument(
        "--start",
        type=int,
        help="Specify start message ID (e.g. 25)",
    )
    parser.add_argument(
        "--end",
        type=int,
        help="Specify end message ID (e.g. 45)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save manual loops to config.json for future runs",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Timeout in seconds to wait for video (default: 600, use 0 for infinite)",
    )
    args = parser.parse_args()

    logger = setup_logger()

    config_path = "config.test.json" if args.test else args.config

    try:
        config: AppConfig = load_app_config(env_file=args.env, config_file=config_path)
    except Exception as e:
        logger.error(f"Configuration load error: {e}")
        sys.exit(1)

    if args.timeout is not None:
        config.timeout_seconds = args.timeout if args.timeout > 0 else None


    # Handle direct CLI arguments for base_link, start, end
    if args.base_link:
        if args.start is None or args.end is None:
            logger.error("--start and --end must be specified when using --base-link.")
            sys.exit(1)
        if args.start <= 0 or args.end < args.start:
            logger.error(f"Invalid range: start={args.start}, end={args.end}")
            sys.exit(1)
        config.loops = [
            LoopConfig(
                name="Manual Loop",
                base_link=args.base_link,
                start_id=args.start,
                end_id=args.end,
            )
        ]
        if args.save:
            save_loops_to_config(config.loops, args.config)

    # Handle interactive manual prompt
    elif args.manual:
        manual_loops = prompt_manual_loops()
        if not manual_loops:
            logger.error("No loops specified.")
            sys.exit(1)
        config.loops = manual_loops
        save_choice = input("\nSave these loops to config.json? (Y/n): ").strip().lower()
        if save_choice not in ("n", "no"):
            save_loops_to_config(config.loops, args.config)

    # Validate Telegram credentials
    cred_errors = validate_credentials(config)
    if cred_errors:
        print_credential_help()
        for err in cred_errors:
            logger.error(f"Validation Error: {err}")
        sys.exit(1)

    state_mgr = StateManager()
    if args.reset:
        state_mgr.reset()
        logger.info("Progress has been reset (--reset flag).")

    state = state_mgr.load(config)
    logger.info(f"Loaded config: {config_path}")
    logger.info(f"Processing Bot: {config.bot_username}")
    logger.info(f"Total Loops configured: {len(config.loops)}")
    for idx, l in enumerate(config.loops):
        logger.info(f"  Loop {idx+1} ({l.name}): {l.base_link} [IDs {l.start_id}..{l.end_id}]")

    # Initialize Telethon Client
    logger.info("Connecting to Telegram...")
    client = TelegramClient(config.session_name, config.api_id, config.api_hash)

    automation = BotAutomation(
        client=client,
        config=config,
        state_mgr=state_mgr,
        logger=logger,
    )

    try:
        await client.start(phone=config.phone)
        me = await client.get_me()
        logger.info(f"Connected as: {me.first_name} (@{me.username or 'No Username'}) [ID: {me.id}]")

        # Initialize bot communication
        await automation.initialize()

        # Set up Saved Messages commands listener (/status, /start, /stop)
        await setup_saved_messages_listener(client, automation, logger)

        # Start terminal console input thread
        loop = asyncio.get_running_loop()
        t = threading.Thread(
            target=console_listener,
            args=(automation, loop, logger),
            daemon=True,
        )
        t.start()

        # Run automation
        await automation.run()

    except KeyboardInterrupt:
        logger.info("Ctrl+C detected. Shutting down gracefully...")
        automation.stop()
    except Exception as e:
        logger.exception(f"Unhandled error in main execution: {e}")
    finally:
        automation.stop()
        if client.is_connected():
            await client.disconnect()
        logger.info("Disconnected from Telegram. Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())
