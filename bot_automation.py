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
from media_detector import detect_bot_error, get_media_summary, is_video_message, detect_login_requirement
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
        self._is_stopped: bool = False
        self._waiting_for_login: bool = False
        import threading
        self._stop_event = threading.Event()
        self._resume_event = asyncio.Event()
        self._resume_event.set()

        # Future for awaiting video response
        self._pending_future: Optional[asyncio.Future[Message]] = None
        self._current_task: Optional[Dict[str, Any]] = None
        self._last_sent_msg_id: Optional[int] = None
        self._last_sent_time: Optional[datetime] = None
        self._asyncio_loop = None
        self._active_processing_message = None

    async def initialize(self) -> None:
        """Resolve bot entity and register message listeners."""
        self._asyncio_loop = asyncio.get_running_loop()
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
        if not self._is_running or not self._pending_future:
            # Not waiting for any video response right now
            return

        # Check if message is a video or explicitly confirms completion
        text = getattr(message, "text", "") or ""
        is_completion_text = "File Delivered Successfully" in text or "✅ File Delivered" in text
        
        if is_video_message(message) or is_completion_text:
            curr_id = self._current_task.get("msg_id") if self._current_task else "?"
            loop_idx = self._current_task.get("loop_idx") if self._current_task else None
            
            try:
                summary = get_media_summary(message) if is_video_message(message) else "Completion text detected"
            except Exception:
                summary = "Completion event detected"

            self.logger.info(f"COMPLETION DETECTED: {curr_id} [{summary}] (Msg ID: {message.id})")
            
            # If we were paused due to manual login, resume automatically
            if self._waiting_for_login:
                self.logger.info("Video received after manual login. Auto-resuming...")
                self._waiting_for_login = False
                self.resume()

            self._active_processing_message = None
            
            if not self._pending_future.done():
                self._pending_future.set_result(message)
            else:
                if curr_id != "?" and loop_idx is not None:
                    self.logger.info(f"[Sequence Debug] Completion event detected for ID: {curr_id} (arrived late)")
                    self.logger.info(f"[Sequence Debug] Persisting next ID...")
                    self.state_mgr.record_video_received(loop_idx, curr_id, message.id)
                    
                    if hasattr(self, "send_whatsapp_notification"):
                        next_next_task = self.state_mgr.get_next_task(self.config)
                        next_id_str = str(next_next_task[1]) if next_next_task else "None (Done)"
                        self.logger.info(f"[Sequence Debug] Advancing ID: {curr_id} -> {next_id_str}")
                        self.send_whatsapp_notification(
                            "✅ VIDEO RECEIVED (LATE)\n\n"
                            f"ID: {curr_id}\n\n"
                            f"➡️ NEXT ID: {next_id_str}\n"
                            "⏳ Waiting for RESUME..." if self._is_stopped else "⏳ Waiting for video..."
                        )
            return

        # Check if bot requires manual login
        if detect_login_requirement(message):
            self._waiting_for_login = True
            self.logger.error("Processing bot requires manual login. Automation paused.")
            if hasattr(self, "send_whatsapp_notification"):
                curr_id = self._current_task.get("msg_id") if self._current_task else "?"
                self.send_whatsapp_notification(
                    "🔐 TELEGRAM LOGIN REQUIRED\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Processing bot requires manual login.\n\n"
                    f"Current ID: {curr_id}\n\n"
                    "Automation is safely paused.\n\n"
                    "Please complete the login in Telegram.\n\n"
                    "Once the video is received, automation can resume.\n\n"
                    "━━━━━━━━━━━━━━━━━━━━"
                )
            self.pause()
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
        self._active_processing_message = message


    async def run(self) -> None:
        """Main loop processing links sequentially."""
        self._is_running = True
        self.logger.info("Automation loop started.")

        try:
            while self._is_running:
                if self._is_paused or self._is_stopped:
                    self.logger.info("Automation is currently paused/stopped. Waiting to resume...")
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
                    if hasattr(self, "send_whatsapp_notification"):
                        total = sum(len(st.completed_ids) for st in self.state_mgr.state.loops.values())
                        self.send_whatsapp_notification(
                            "🎉 AUTOMATION COMPLETED\n"
                            "━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"Bot: {self.config.bot_username}\n\n"
                            "All configured loops have completed successfully.\n\n"
                            f"Total processed: {total}\n\n"
                            "━━━━━━━━━━━━━━━━━━━━"
                        )
                    break

                loop_idx, msg_id = next_task
                loop_cfg = self.config.loops[loop_idx]
                target_url = loop_cfg.get_link_for_id(msg_id)

                success = await self._process_single_id(loop_idx, loop_cfg, msg_id, target_url)
                if not success:
                    if self._is_stopped:
                        self.logger.info(f"Automation stopped at ID {msg_id}. Waiting for RESUME/START.")
                    else:
                        self.logger.warning(
                            f"Paused due to timeout/failure on Loop '{loop_cfg.name}', ID {msg_id}. "
                            f"ID was NOT skipped. Type 'start' or send /start in Saved Messages to retry."
                        )
                        self.pause()
                    continue
                else:
                    if hasattr(self, "send_whatsapp_notification"):
                        next_next_task = self.state_mgr.get_next_task(self.config)
                        if next_next_task:
                            next_id_str = str(next_next_task[1])
                            self.send_whatsapp_notification(
                                "✅ VIDEO RECEIVED\n"
                                "━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"Completed ID: {msg_id}\n"
                                f"Next ID: {next_id_str}\n\n"
                                "▶️ Sending:\n"
                                f"{self.config.loops[next_next_task[0]].get_link_for_id(next_next_task[1])}\n\n"
                                "⏳ Waiting for video...\n\n"
                                "━━━━━━━━━━━━━━━━━━━━"
                            )
                        else:
                            self.send_whatsapp_notification(
                                "✅ VIDEO RECEIVED\n"
                                "━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"ID: {msg_id}\n"
                                "Status: COMPLETED\n\n"
                                "🎉 Loop completed.\n\n"
                                "━━━━━━━━━━━━━━━━━━━━"
                            )

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
        self._active_processing_message = None
        self._current_task = {
            "loop_idx": loop_idx,
            "loop_name": loop_cfg.name,
            "msg_id": msg_id,
            "url": target_url,
        }

        # Send link with FloodWait handling
        sent_successfully = False
        while not sent_successfully and self._is_running:
            if self._is_stopped:
                self.logger.warning(f"STOP requested before sending ID {msg_id}.")
                return False
                
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

            if self._is_stopped:
                self.logger.warning(f"Video received for ID {msg_id}, but STOP was requested. Discarding completion.")
                return False

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
        except asyncio.CancelledError:
            self.logger.warning(f"Wait for video ID {msg_id} was cancelled (STOP requested).")
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

    async def cancel_active_processing_job(self) -> Dict[str, Any]:
        """Attempts to cancel the currently active processing job via Telegram inline button."""
        self.logger.info("[Processing Bot] Checking for active processing job...")
        
        if not self._active_processing_message:
            # Maybe it already completed?
            if self._pending_future and self._pending_future.done():
                self.logger.info("[Processing Bot] Active job already completed")
                self.logger.info("[Processing Bot] No cancellation necessary")
                return {"success": False, "status": "ALREADY_COMPLETED"}
            else:
                self.logger.info("[Processing Bot] No active processing job found")
                return {"success": False, "status": "NO_ACTIVE_JOB"}
            
        curr_id = self._current_task.get("msg_id") if self._current_task else "?"
        msg = self._active_processing_message
        self.logger.info(f"[Processing Bot] Active job detected for ID {curr_id}")
        self.logger.info(f"[Processing Bot] Processing message ID: {msg.id}")
        self.logger.info("[Processing Bot] Inspecting inline keyboard...")
        
        button_to_click = None
        if hasattr(msg, "buttons") and msg.buttons:
            for row in msg.buttons:
                for button in row:
                    if button.text and "cancel" in button.text.lower():
                        button_to_click = button
                        break
                if button_to_click:
                    break

        if not button_to_click:
            self.logger.info("[Processing Bot] Cancel button not found")
            return {"success": False, "status": "BUTTON_NOT_FOUND"}
            
        self.logger.info("[Processing Bot] Cancel button found")
        self.logger.info("[Processing Bot] Sending Cancel callback...")
        
        try:
            await button_to_click.click()
            self.logger.info("[Processing Bot] Cancellation requested successfully")
            self.logger.info("[Processing Bot] Waiting for cancellation confirmation...")
            self.logger.info("[Processing Bot] Current processing job cancelled")
            self._active_processing_message = None
            return {"success": True, "status": "CANCELLED"}
        except Exception as e:
            self.logger.error(f"[Processing Bot] Failed to click Cancel button: {e}")
            return {"success": False, "status": f"ERROR: {str(e)}"}

    def resume(self) -> None:
        """Resume processing."""
        self._is_paused = False
        self._is_stopped = False
        self._stop_event.clear()
        self._resume_event.set()

    def stop(self) -> None:
        """Stop processing completely (interrupts wait)."""
        self._is_stopped = True
        self._stop_event.set()
        self._resume_event.clear()
        
        # Safely cancel pending future from the correct thread
        if self._pending_future and not self._pending_future.done():
            if self._asyncio_loop:
                self._asyncio_loop.call_soon_threadsafe(self._pending_future.cancel)
            else:
                self._pending_future.cancel()

    def get_status_summary(self) -> str:
        """Returns a formatted status summary for commands."""
        state = self.state_mgr.state
        
        if not self._is_running:
            if self.state_mgr.get_next_task(self.config) is None:
                status_text = "COMPLETED"
            else:
                status_text = "IDLE" if not self._is_stopped else "STOPPED"
        elif self._is_stopped:
            status_text = "STOPPED"
        elif self._waiting_for_login:
            status_text = "WAITING_FOR_LOGIN"
        elif self._is_paused:
            status_text = "PAUSED"
        elif self._pending_future and not self._pending_future.done():
            status_text = "WAITING_FOR_VIDEO"
        else:
            status_text = "RUNNING"

        if status_text == "IDLE":
            lines = [
                "🤖 TELEGRAM AUTOMATION STATUS",
                "━━━━━━━━━━━━━━━━━━━━",
                "",
                "State: IDLE",
                "",
                f"Bot: {self.config.bot_username}",
                "",
                "No active automation task.",
                "",
                "▶️ Send:",
                "",
                "START <url> <start> <end>",
                "",
                "━━━━━━━━━━━━━━━━━━━━"
            ]
            return "\n".join(lines)
            
        lines = [
            "🤖 TELEGRAM AUTOMATION STATUS",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"State: {status_text}",
            "",
            f"Bot: {self.config.bot_username}",
        ]
        
        if self._current_task:
            loop_idx = str(self._current_task['loop_idx'])
            loop_st = state.loops.get(loop_idx)
            completed = len(loop_st.completed_ids) if loop_st else 0
            total = self.config.loops[int(loop_idx)].total_count
            pct = (completed / total * 100) if total > 0 else 0
            last_id = loop_st.last_completed_id if loop_st else "None"
            
            lines.extend([
                "",
                "📌 Active Task",
                f"• Loop: {self._current_task['loop_name']}",
                f"• Current ID: {self._current_task['msg_id']}",
                "• URL:",
                f"{self._current_task['url']}",
                "",
                "📊 Progress",
                f"• Completed: {completed}/{total}",
                f"• Progress: {pct:.1f}%",
                f"• Last Completed ID: {last_id}",
            ])
            
        current_status_desc = ""
        if self._active_processing_message and hasattr(self._active_processing_message, "text") and self._active_processing_message.text:
            current_status_desc = self._active_processing_message.text.splitlines()[0]
        else:
            if status_text == "WAITING_FOR_VIDEO":
                current_status_desc = "Waiting for video..."
            elif status_text == "WAITING_FOR_LOGIN":
                current_status_desc = "Waiting for manual login..."
            elif status_text == "STOPPED":
                current_status_desc = "Automation stopped."
            else:
                current_status_desc = "No active processing job"
                
        lines.extend([
            "",
            "⏳ Status:",
            current_status_desc,
            "",
            "📦 Loops Overview",
            ""
        ])

        for idx, loop_cfg in enumerate(self.config.loops):
            key = str(idx)
            loop_st = state.loops.get(key)
            completed_count = len(loop_st.completed_ids) if loop_st else 0
            total = loop_cfg.total_count
            pct = (completed_count / total * 100) if total > 0 else 0
            is_done = "COMPLETED" if (loop_st and loop_st.is_finished) else "RUNNING"
            last_id = loop_st.last_completed_id if loop_st else "None"
            
            lines.extend([
                f"{idx+1}. {loop_cfg.name}",
                f"   Range: {loop_cfg.start_id} → {loop_cfg.end_id}",
                f"   Completed: {completed_count}/{total}",
                f"   Progress: {pct:.1f}%",
                f"   Last ID: {last_id}",
                f"   State: {is_done}",
                ""
            ])

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            f"🕐 Last Update: {now_str}"
        ])
        
        return "\n".join(lines)
