"""
Core automation engine for Telegram video links batch processing.
Interacts with the processing bot sequentially and waits for video media.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from telethon import TelegramClient, errors, events
from telethon.tl.types import Message

from config_loader import AppConfig, LoopConfig
from media_detector import detect_bot_error, get_media_summary, is_video_message
from state_manager import StateManager


class BotAutomation:
    def __init__(
        self,
        client: TelegramClient,
        config: AppConfig,
        state_mgr: StateManager,
        logger: Optional[logging.Logger] = None,
    ):
        self.client = client
        self.config = config
        self.state_mgr = state_mgr
        self.logger = logger or logging.getLogger("telegram_automation")

        self.bot_entity = None
        self._is_running: bool = False
        self._is_paused: bool = False
        self._resume_event = asyncio.Event()
        self._resume_event.set()

        # Future for awaiting video response
        self._pending_future: Optional[asyncio.Future[Message]] = None
        self._current_task: Optional[Dict[str, Any]] = None
        self._last_sent_msg_id: Optional[int] = None
        self._last_sent_time: Optional[datetime] = None

    async def initialize(self) -> None:
        """Resolve bot entity and register message listeners."""
        self.logger.info(f"Resolving bot entity: {self.config.bot_username}...")
        try:
            self.bot_entity = await self.client.get_entity(self.config.bot_username)
            bot_name = getattr(self.bot_entity, "username", str(self.bot_entity.id))
            self.logger.info(f"Successfully connected to bot: @{bot_name} (ID: {self.bot_entity.id})")
        except Exception as e:
            self.logger.error(
                f"Failed to find bot '{self.config.bot_username}'. "
                f"Ensure you have started a conversation with this bot first in Telegram! Error: {e}"
            )
            raise

        # Register event handlers for the bot (both new messages and edits)
        bot_id = self.bot_entity.id

        @self.client.on(events.NewMessage(chats=bot_id))
        async def on_new_bot_message(event: events.NewMessage.Event):
            await self._handle_bot_message(event.message)

        @self.client.on(events.MessageEdited(chats=bot_id))
        async def on_edited_bot_message(event: events.MessageEdited.Event):
            await self._handle_bot_message(event.message)

    async def _handle_bot_message(self, message: Message) -> None:
        """Processes incoming messages from the processing bot."""
        if not self._is_running or not self._pending_future or self._pending_future.done():
            # Not waiting for any video response right now
            return

        # Check if message is a video or video document
        if is_video_message(message):
            curr_id = self._current_task.get("msg_id") if self._current_task else "?"
            try:
                summary = get_media_summary(message)
            except Exception:
                summary = "Video detected"

            self.logger.info(f"VIDEO RECEIVED: {curr_id} [{summary}] (Msg ID: {message.id})")
            if not self._pending_future.done():
                self._pending_future.set_result(message)
            return


        # If not a video, inspect if it's an explicit error
        error_text = detect_bot_error(message)
        if error_text:
            curr_id = self._current_task.get("msg_id") if self._current_task else "?"
            self.logger.warning(
                f"[Bot Warning for ID {curr_id}] Bot reported: {error_text.strip()}"
            )
            return

        # Log any status or progress text from the bot so the user can see it in terminal
        text = getattr(message, "text", "") or ""
        first_line = text.splitlines()[0] if text else "[Non-video update]"
        self.logger.info(f"[Bot Message]: {first_line[:120]}")

    async def run(self) -> None:
        """Main loop processing links sequentially."""
        self._is_running = True
        self.logger.info("Automation loop started.")

        try:
            while self._is_running:
                # Check for pause
                if self._is_paused:
                    self.logger.info("Automation is currently paused. Waiting to resume...")
                    await self._resume_event.wait()
                    self.logger.info("Automation resumed.")

                if not self._is_running:
                    break

                # Get next task from state manager
                next_task = self.state_mgr.get_next_task(self.config)
                if not next_task:
                    self.logger.info("=========================================")
                    self.logger.info("🎉 ALL CONFIGURED LOOPS COMPLETED!")
                    self.logger.info("=========================================")
                    break

                loop_idx, msg_id = next_task
                loop_cfg = self.config.loops[loop_idx]
                target_url = loop_cfg.get_link_for_id(msg_id)

                success = await self._process_single_id(loop_idx, loop_cfg, msg_id, target_url)
                if not success:
                    # Timeout or failure occurred and was not resolved
                    self.logger.warning(
                        f"Paused due to timeout/failure on Loop '{loop_cfg.name}', ID {msg_id}. "
                        f"ID was NOT skipped. Type 'start' or send /start in Saved Messages to retry."
                    )
                    self.pause()
                    continue

                # Configured cooldown delay between links
                if self.config.delay_between_links > 0 and self._is_running:
                    self.logger.debug(
                        f"Waiting {self.config.delay_between_links}s before next request..."
                    )
                    await asyncio.sleep(self.config.delay_between_links)

        except asyncio.CancelledError:
            self.logger.info("Automation task was cancelled.")
        finally:
            self._is_running = False
            self.logger.info("Automation loop exited.")

    async def _process_single_id(
        self, loop_idx: int, loop_cfg: LoopConfig, msg_id: int, target_url: str
    ) -> bool:
        """Sends one message link and waits for the video response."""
        loop = asyncio.get_running_loop()
        self._pending_future = loop.create_future()
        self._current_task = {
            "loop_idx": loop_idx,
            "loop_name": loop_cfg.name,
            "msg_id": msg_id,
            "url": target_url,
        }

        # Send link with FloodWait handling
        sent_successfully = False
        while not sent_successfully and self._is_running:
            try:
                sent_msg = await self.client.send_message(self.bot_entity, target_url)
                self._last_sent_msg_id = sent_msg.id
                self._last_sent_time = datetime.now()
                self.state_mgr.record_sent(loop_idx, msg_id)
                self.logger.info(f"SENT: {msg_id} ({loop_cfg.name}) -> {target_url}")
                sent_successfully = True
            except errors.FloodWaitError as fwe:
                self.logger.warning(
                    f"Telegram FloodWait encountered: waiting {fwe.seconds}s before retrying..."
                )
                await asyncio.sleep(fwe.seconds + 2)
            except Exception as e:
                self.logger.error(f"Error sending link for ID {msg_id}: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

        if not sent_successfully:
            return False

        # Wait for video response
        try:
            if self.config.timeout_seconds:
                video_msg = await asyncio.wait_for(
                    self._pending_future, timeout=self.config.timeout_seconds
                )
            else:
                video_msg = await self._pending_future

            # Record completion
            self.state_mgr.record_video_received(
                loop_idx=loop_idx, message_id=msg_id, bot_msg_id=video_msg.id
            )
            return True

        except asyncio.TimeoutError:
            self.logger.error(
                f"[TIMEOUT] Timed out waiting for video for ID {msg_id} "
                f"after {self.config.timeout_seconds} seconds! Link: {target_url}"
            )
            return False
        except Exception as e:
            self.logger.error(f"Error while waiting for video on ID {msg_id}: {e}")
            return False
        finally:
            self._pending_future = None

    def pause(self) -> None:
        """Pause processing."""
        self._is_paused = True
        self._resume_event.clear()

    def resume(self) -> None:
        """Resume processing."""
        self._is_paused = False
        self._resume_event.set()

    def stop(self) -> None:
        """Stop processing completely."""
        self._is_running = False
        if self._pending_future and not self._pending_future.done():
            self._pending_future.cancel()
        self._resume_event.set()

    def get_status_summary(self) -> str:
        """Returns a formatted status summary for commands."""
        state = self.state_mgr.state
        status_text = "PAUSED" if self._is_paused else ("RUNNING" if self._is_running else "STOPPED")

        lines = [
            f"🤖 *Telegram Automation Status*",
            f"• State: *{status_text}*",
            f"• Bot: `{self.config.bot_username}`",
        ]

        if self._current_task:
            lines.append(
                f"• Active Task: *{self._current_task['loop_name']}*, ID *{self._current_task['msg_id']}*"
            )
            lines.append(f"• URL: `{self._current_task['url']}`")

        lines.append("\n📁 *Loops Overview:*")
        for idx, loop_cfg in enumerate(self.config.loops):
            key = str(idx)
            loop_st = state.loops.get(key)
            completed_count = len(loop_st.completed_ids) if loop_st else 0
            total = loop_cfg.total_count
            pct = (completed_count / total * 100) if total > 0 else 0
            is_done = "✅ Done" if (loop_st and loop_st.is_finished) else "⏳ In Progress"
            last_id = loop_st.last_completed_id if loop_st else "None"

            lines.append(
                f"[{idx+1}] *{loop_cfg.name}*: {completed_count}/{total} ({pct:.1f}%) | "
                f"Last ID: {last_id} | {is_done}"
            )

        return "\n".join(lines)
