import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from bot_automation import BotAutomation
from config_loader import AppConfig

class TestBotAutomationCancel(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MagicMock(spec=AppConfig)
        self.config.bot_username = "@save_restricted_contentpro_bot"
        self.state_mgr = MagicMock()
        self.client = MagicMock()
        self.bot = BotAutomation(self.client, self.config, self.state_mgr)
        self.bot._current_task = {"msg_id": 25, "loop_name": "Test", "loop_idx": 0, "url": "http"}

    async def test_cancel_button_found_and_clicked(self):
        # 1. STOP with active processing job and Cancel button
        mock_msg = MagicMock()
        mock_msg.id = 100
        mock_button = AsyncMock()
        mock_button.text = "🚫 Cancel"
        mock_msg.buttons = [[mock_button]]
        
        self.bot._active_processing_message = mock_msg
        
        res = await self.bot.cancel_active_processing_job()
        
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "CANCELLED")
        mock_button.click.assert_awaited_once()
        self.assertIsNone(self.bot._active_processing_message)

    async def test_cancel_button_not_found(self):
        # 4. Cancel button not found
        mock_msg = MagicMock()
        mock_msg.id = 100
        mock_button = AsyncMock()
        mock_button.text = "Other"
        mock_msg.buttons = [[mock_button]]
        
        self.bot._active_processing_message = mock_msg
        
        res = await self.bot.cancel_active_processing_job()
        
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "BUTTON_NOT_FOUND")
        mock_button.click.assert_not_awaited()

    async def test_no_active_job(self):
        # 2. STOP with no active processing job
        self.bot._active_processing_message = None
        
        res = await self.bot.cancel_active_processing_job()
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "NO_ACTIVE_JOB")

    async def test_already_completed(self):
        # 3. STOP after processing already completed (future done)
        self.bot._active_processing_message = None
        future = asyncio.Future()
        future.set_result(MagicMock())
        self.bot._pending_future = future
        
        res = await self.bot.cancel_active_processing_job()
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "ALREADY_COMPLETED")

if __name__ == "__main__":
    unittest.main()
