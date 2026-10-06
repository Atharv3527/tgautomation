"""
Unit tests for Telegram Automation modules:
- Media detector logic
- Config loading and URL generation
- State manager and loop progression
"""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from config_loader import AppConfig, LoopConfig, load_app_config, validate_credentials
from media_detector import detect_bot_error, get_media_summary, is_video_message
from state_manager import StateManager


class TestConfigLoader(unittest.TestCase):
    def test_loop_url_generation(self):
        # Case 1: Trailing slash in base_link
        loop1 = LoopConfig(
            name="Loop 1",
            base_link="https://t.me/c/3548255677/15/",
            start_id=25,
            end_id=45,
        )
        self.assertEqual(
            loop1.get_link_for_id(25), "https://t.me/c/3548255677/15/25"
        )
        self.assertEqual(
            loop1.get_link_for_id(26), "https://t.me/c/3548255677/15/26"
        )
        self.assertEqual(loop1.total_count, 21)

        # Case 2: No trailing slash in base_link
        loop2 = LoopConfig(
            name="Loop 2",
            base_link="https://t.me/c/3548255678/15",
            start_id=1,
            end_id=10,
        )
        self.assertEqual(
            loop2.get_link_for_id(5), "https://t.me/c/3548255678/15/5"
        )
        self.assertEqual(loop2.total_count, 10)

    def test_validation_errors(self):
        # Missing credentials
        cfg = AppConfig(
            api_id=None,
            api_hash=None,
            phone=None,
            session_name="test",
            bot_username="@bot",
            timeout_seconds=600,
            delay_between_links=3,
            loops=[],
        )
        errors = validate_credentials(cfg)
        self.assertTrue(len(errors) >= 2)


class TestMediaDetector(unittest.TestCase):
    def test_is_video_message_with_video_prop(self):
        # Telethon message.video property returns a truthy Document
        mock_msg = SimpleNamespace(video=object(), document=None, photo=None, text="Here is your video")
        self.assertTrue(is_video_message(mock_msg))

    def test_is_video_message_with_document_mime(self):
        mock_doc = SimpleNamespace(mime_type="video/mp4", attributes=[])
        mock_msg = SimpleNamespace(video=None, document=mock_doc, photo=None, text="")
        self.assertTrue(is_video_message(mock_msg))

    def test_is_video_message_with_document_video_attribute(self):
        class DocumentAttributeVideo:
            pass

        mock_doc = SimpleNamespace(mime_type="application/octet-stream", attributes=[DocumentAttributeVideo()])
        mock_msg = SimpleNamespace(video=None, document=mock_doc, photo=None, text="")
        self.assertTrue(is_video_message(mock_msg))

    def test_is_video_message_with_file_extension(self):
        mock_doc = SimpleNamespace(mime_type="application/octet-stream", attributes=[])
        mock_file = SimpleNamespace(ext=".mkv", name="movie.mkv", size=1048576, duration=120)
        mock_msg = SimpleNamespace(video=None, document=mock_doc, file=mock_file, photo=None, text="")
        self.assertTrue(is_video_message(mock_msg))

    def test_media_summary_float_duration_and_hours(self):
        # 1 hour 48 mins 36 secs as a float duration (6516.0)
        mock_file = SimpleNamespace(
            ext=".mp4", name="10. Portfolio Project Day-04.mp4", size=392167424, duration=6516.0
        )
        mock_doc = SimpleNamespace(mime_type="video/mp4", attributes=[])
        mock_msg = SimpleNamespace(video=object(), document=mock_doc, file=mock_file, photo=None, text="")
        summary = get_media_summary(mock_msg)
        self.assertIn("01:48:36", summary)
        self.assertIn("374.00 MB", summary)


    def test_is_not_video_message_for_text_and_photo(self):
        # Status text message
        mock_text_msg = SimpleNamespace(
            video=None,
            document=None,
            photo=None,
            text="Downloading video... 45%",
        )
        self.assertFalse(is_video_message(mock_text_msg))

        # Photo message
        mock_photo_msg = SimpleNamespace(
            video=None,
            document=None,
            photo=object(),
            text="Here is thumbnail",
        )
        self.assertFalse(is_video_message(mock_photo_msg))

        # Non-video document (e.g. PDF)
        mock_pdf_doc = SimpleNamespace(mime_type="application/pdf", attributes=[])
        mock_pdf_file = SimpleNamespace(ext=".pdf", name="document.pdf", size=5000, duration=None)
        mock_pdf_msg = SimpleNamespace(
            video=None,
            document=mock_pdf_doc,
            file=mock_pdf_file,
            photo=None,
            text="file.pdf",
        )
        self.assertFalse(is_video_message(mock_pdf_msg))

    def test_detect_bot_error(self):
        mock_msg_error = SimpleNamespace(text="Error: This link is invalid or message not found in channel.")
        self.assertIsNotNone(detect_bot_error(mock_msg_error))

        mock_msg_status = SimpleNamespace(text="Task added to queue. Please wait...")
        self.assertIsNone(detect_bot_error(mock_msg_status))

    def test_completion_text(self):
        # We don't have is_completion_text in media_detector anymore, but we can test
        # that get_media_summary gracefully handles non-video text messages.
        mock_msg = SimpleNamespace(file=None, text="✅ File Delivered Successfully", video=None, document=None)
        self.assertEqual(get_media_summary(mock_msg), "Video detected")


class TestStateManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.temp_dir.name, "progress.json")
        self.mgr = StateManager(state_file=self.state_file)

        self.config = AppConfig(
            api_id=12345,
            api_hash="abcdef",
            phone=None,
            session_name="test",
            bot_username="@save_restricted_contentpro_bot",
            timeout_seconds=600,
            delay_between_links=3,
            loops=[
                LoopConfig(
                    name="Loop 1",
                    base_link="https://t.me/c/3548255677/15/",
                    start_id=25,
                    end_id=27,
                ),
                LoopConfig(
                    name="Loop 2",
                    base_link="https://t.me/c/3548255678/15/",
                    start_id=10,
                    end_id=11,
                ),
            ],
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_lifecycle_and_loop_progression(self):
        state = self.mgr.load(self.config)
        self.assertEqual(len(state.loops), 2)

        # First task should be Loop 0, ID 25
        task = self.mgr.get_next_task(self.config)
        self.assertEqual(task, (0, 25))

        # Record sent 25
        self.mgr.record_sent(0, 25)
        # Record video received 25
        self.mgr.record_video_received(0, 25, bot_msg_id=1001)

        # Next task should be Loop 0, ID 26
        task = self.mgr.get_next_task(self.config)
        self.assertEqual(task, (0, 26))

        # Complete 26
        self.mgr.record_sent(0, 26)
        self.mgr.record_video_received(0, 26, bot_msg_id=1002)

        # Next task should be Loop 0, ID 27
        task = self.mgr.get_next_task(self.config)
        self.assertEqual(task, (0, 27))

        # Complete 27 (end of Loop 0)
        self.mgr.record_sent(0, 27)
        self.mgr.record_video_received(0, 27, bot_msg_id=1003)

        # Now Loop 0 should be marked completed, and next task should transition to Loop 1, ID 10!
        task = self.mgr.get_next_task(self.config)
        self.assertEqual(task, (1, 10))

        # Simulate restart: reload from state file
        new_mgr = StateManager(state_file=self.state_file)
        new_mgr.load(self.config)
        task_after_restart = new_mgr.get_next_task(self.config)
        self.assertEqual(task_after_restart, (1, 10))

        # Complete Loop 1
        new_mgr.record_sent(1, 10)
        new_mgr.record_video_received(1, 10, bot_msg_id=2001)
        new_mgr.record_sent(1, 11)
        new_mgr.record_video_received(1, 11, bot_msg_id=2002)

        # All finished!
        final_task = new_mgr.get_next_task(self.config)
        self.assertIsNone(final_task)


if __name__ == "__main__":
    unittest.main()
