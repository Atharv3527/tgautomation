"""
Unit tests for WhatsApp Webhook Server, Message Extraction, and Telegram Automation Bridge.
"""

import time
import unittest
from unittest.mock import MagicMock

from whatsapp_webhook import (
    WhatsAppClient,
    WhatsAppCommandHandler,
    WhatsAppMessage,
    connect_automation_to_whatsapp,
    create_app,
    extract_messages,
    get_progress_status_summary,
    start_webhook_background,
)


class TestWhatsAppWebhook(unittest.TestCase):
    def setUp(self):
        self.verify_token = "test_secret_token_123"
        self.command_handler = WhatsAppCommandHandler()
        self.app = create_app(
            verify_token=self.verify_token,
            command_handler=self.command_handler,
        )
        self.client = self.app.test_client()

    def test_health_check_endpoint(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data.get("status"), "online")
        self.assertEqual(data.get("webhook_path"), "/webhook")

    def test_webhook_verification_success(self):
        challenge = "random_challenge_code_98765"
        params = {
            "hub.mode": "subscribe",
            "hub.verify_token": self.verify_token,
            "hub.challenge": challenge,
        }
        response = self.client.get("/webhook", query_string=params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.decode("utf-8"), challenge)

    def test_webhook_verification_failure_wrong_token(self):
        params = {
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "12345",
        }
        response = self.client.get("/webhook", query_string=params)
        self.assertEqual(response.status_code, 403)

    def test_webhook_verification_failure_wrong_mode(self):
        params = {
            "hub.mode": "unsubscribe",
            "hub.verify_token": self.verify_token,
            "hub.challenge": "12345",
        }
        response = self.client.get("/webhook", query_string=params)
        self.assertEqual(response.status_code, 403)

    def test_post_webhook_message_extraction(self):
        sample_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "100200300",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15551234567",
                                    "phone_number_id": "100200300",
                                },
                                "contacts": [
                                    {
                                        "profile": {"name": "Alice Developer"},
                                        "wa_id": "15559876543",
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": "15559876543",
                                        "id": "wamid.HBgL...",
                                        "timestamp": "1672531199",
                                        "text": {"body": "Hello Telegram Bot!"},
                                        "type": "text",
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        msgs = extract_messages(sample_payload)
        self.assertEqual(len(msgs), 1)
        msg = msgs[0]
        self.assertEqual(msg.sender, "15559876543")
        self.assertEqual(msg.sender_name, "Alice Developer")
        self.assertEqual(msg.text, "Hello Telegram Bot!")
        self.assertEqual(msg.message_id, "wamid.HBgL...")
        self.assertEqual(msg.msg_type, "text")

        response = self.client.post("/webhook", json=sample_payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "EVENT_RECEIVED"})

    def test_post_webhook_status_update_ignored_cleanly(self):
        status_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "100200300",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15551234567",
                                    "phone_number_id": "100200300",
                                },
                                "statuses": [
                                    {
                                        "id": "wamid.HBgL...",
                                        "status": "delivered",
                                        "timestamp": "1672531200",
                                        "recipient_id": "15559876543",
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
        msgs = extract_messages(status_payload)
        self.assertEqual(len(msgs), 0)

        response = self.client.post("/webhook", json=status_payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "EVENT_RECEIVED"})

    def test_command_handler_callback(self):
        called = {}

        def on_start(msg: WhatsAppMessage):
            called["start"] = msg.text

        self.command_handler.register_command("START", on_start)

        msg_start = WhatsAppMessage(
            sender="12345",
            sender_name="Tester",
            message_id="msg-1",
            timestamp="1672531199",
            text="/start",
        )
        self.command_handler.process_message(msg_start)
        self.assertEqual(called.get("start"), "/start")

    def test_interactive_message_extraction(self):
        interactive_payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "12345",
                                        "id": "btn-1",
                                        "timestamp": "1672531199",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {
                                                "id": "btn_start",
                                                "title": "START",
                                            },
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        msgs = extract_messages(interactive_payload)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].text, "START")
        self.assertEqual(msgs[0].msg_type, "interactive")

    def test_connect_automation_to_whatsapp(self):
        # Mock automation object
        mock_automation = MagicMock()
        mock_automation.get_status_summary.return_value = "Status: Running 10/20"
        mock_automation.config.bot_username = "@save_restricted_contentpro_bot"
        mock_automation._current_task = {
            "loop_idx": 0,
            "loop_name": "Test",
            "msg_id": 25,
            "url": "https://test",
        }

        # Mock WhatsApp client for outbound replies
        mock_client = MagicMock(spec=WhatsAppClient)
        mock_client.is_configured = True

        handler = WhatsAppCommandHandler(whatsapp_client=mock_client)
        connect_automation_to_whatsapp(handler, mock_automation, whatsapp_client=mock_client)

        # 1. Send START command (without args)
        msg_start = WhatsAppMessage(
            sender="+123456789",
            sender_name="Alice",
            message_id="id-1",
            timestamp="1672531199",
            text="START",
        )
        handler.process_message(msg_start)
        mock_automation.resume.assert_called_once()
        args, _ = mock_client.send_text_message.call_args
        self.assertIn("▶️ AUTOMATION RESUMED", args[1])

        # 1.5 Send START command with URL args
        mock_automation.resume.reset_mock()
        mock_client.send_text_message.reset_mock()
        msg_start_args = WhatsAppMessage(
            sender="+123456789",
            sender_name="Alice",
            message_id="id-1.5",
            timestamp="1672531200",
            text="START https://t.me/c/3548255677/157/ 25 27",
        )
        handler.process_message(msg_start_args)
        
        # Verify the config was overridden
        self.assertEqual(len(mock_automation.config.loops), 1)
        self.assertEqual(mock_automation.config.loops[0].base_link, "https://t.me/c/3548255677/157/")
        self.assertEqual(mock_automation.config.loops[0].start_id, 25)
        self.assertEqual(mock_automation.config.loops[0].end_id, 27)
        mock_automation.state_mgr.reset.assert_called_once()
        mock_automation.resume.assert_called_once()
        args, _ = mock_client.send_text_message.call_args
        self.assertIn("🚀 AUTOMATION STARTED", args[1])

        # 2. Send STOP command
        mock_automation._is_running = True
        mock_automation._is_stopped = False
        mock_automation._current_task = {'msg_id': 25}
        
        msg_stop = WhatsAppMessage(
            sender="+123456789",
            sender_name="Alice",
            message_id="id-2",
            timestamp="1672531199",
            text="STOP",
        )
        handler.process_message(msg_stop)
        mock_automation.stop.assert_called_once()
        args, _ = mock_client.send_text_message.call_args
        self.assertIn("🛑 AUTOMATION STOPPED", args[1])
        
        # 2.5 Send /stop, stop, STOP with spaces
        for idx, text_variation in enumerate(["/stop", "stop", " STOP "]):
            msg_var = WhatsAppMessage(
                sender="+123456789",
                sender_name="Alice",
                message_id=f"id-2.5.{idx}",
                timestamp="1672531199",
                text=text_variation,
            )
            # Reset mock to verify it gets called again
            mock_automation.stop.reset_mock()
            mock_automation._is_running = True
            mock_automation._is_stopped = False
            handler.process_message(msg_var)
            mock_automation.stop.assert_called_once()

        # 3. Send STATUS command
        msg_status = WhatsAppMessage(
            sender="+123456789",
            sender_name="Alice",
            message_id="id-3",
            timestamp="1672531199",
            text="STATUS",
        )
        handler.process_message(msg_status)
        mock_automation.get_status_summary.assert_called_once()
        mock_client.send_text_message.assert_called_with(
            "+123456789", "Status: Running 10/20"
        )

    def test_progress_status_summary_fallback(self):
        summary = get_progress_status_summary("progress.json")
        self.assertIn("Telegram Automation Status", summary)

    def test_start_invalid_url(self):
        mock_automation = MagicMock()
        mock_client = MagicMock(spec=WhatsAppClient)
        mock_client.is_configured = True
        handler = WhatsAppCommandHandler(whatsapp_client=mock_client)
        connect_automation_to_whatsapp(handler, mock_automation, whatsapp_client=mock_client)

        msg = WhatsAppMessage(sender="+1", sender_name="A", message_id="1", timestamp="1", text="START not_a_url 25 30")
        handler.process_message(msg)
        
        mock_client.send_text_message.assert_called_with("+1", "⚠️ Invalid START command. Error: URL must start with http\nUse: START <base_url> <start_id> <end_id>")
        mock_automation.resume.assert_not_called()

    def test_start_invalid_range(self):
        mock_automation = MagicMock()
        mock_client = MagicMock(spec=WhatsAppClient)
        mock_client.is_configured = True
        handler = WhatsAppCommandHandler(whatsapp_client=mock_client)
        connect_automation_to_whatsapp(handler, mock_automation, whatsapp_client=mock_client)

        msg = WhatsAppMessage(sender="+1", sender_name="A", message_id="1", timestamp="1", text="START https://url 40 30")
        handler.process_message(msg)
        
        mock_client.send_text_message.assert_called_with("+1", "⚠️ Invalid START command. Error: Invalid ID range\nUse: START <base_url> <start_id> <end_id>")

    def test_resume_command(self):
        mock_automation = MagicMock()
        mock_client = MagicMock(spec=WhatsAppClient)
        mock_client.is_configured = True
        handler = WhatsAppCommandHandler(whatsapp_client=mock_client)
        connect_automation_to_whatsapp(handler, mock_automation, whatsapp_client=mock_client)

        msg = WhatsAppMessage(sender="+1", sender_name="A", message_id="1", timestamp="1", text="RESUME")
        handler.process_message(msg)
        
        mock_automation.resume.assert_called_once()
        args, _ = mock_client.send_text_message.call_args
        self.assertIn("▶️ AUTOMATION RESUMED", args[1])

if __name__ == "__main__":
    unittest.main()
