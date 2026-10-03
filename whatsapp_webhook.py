"""
WhatsApp Webhook Server for Telegram Automation.

Receives incoming messages and verification requests from Meta WhatsApp Cloud API.
Provides modular command dispatching for START, STOP, STATUS, etc.
Supports outbound WhatsApp messaging and seamless integration with Telegram BotAutomation.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Ensure standard output supports UTF-8 on Windows consoles without charmap crash
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request

# Load environment variables from .env
load_dotenv()

# Setup logger
logger = logging.getLogger("whatsapp_webhook")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    c_handler = logging.StreamHandler(sys.stdout)
    c_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )
    c_handler.setFormatter(c_format)
    logger.addHandler(c_handler)


@dataclass
class WhatsAppMessage:
    """Represents a normalized incoming WhatsApp message."""

    sender: str
    sender_name: Optional[str]
    message_id: str
    timestamp: str
    text: str
    msg_type: str = "text"
    raw: Dict[str, Any] = field(default_factory=dict)


class WhatsAppClient:
    """
    Sends outbound WhatsApp messages via Meta Graph API using standard library urllib.
    Requires WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_TOKEN in .env.
    """

    def __init__(
        self,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
        api_version: str = "v18.0",
    ):
        self.phone_number_id = phone_number_id or os.getenv(
            "WHATSAPP_PHONE_NUMBER_ID", ""
        )
        self.access_token = access_token or os.getenv("WHATSAPP_TOKEN", "")
        self.api_version = api_version

    @property
    def is_configured(self) -> bool:
        """Returns True if Meta API credentials are set."""
        return bool(self.phone_number_id and self.access_token)

    def send_text_message(self, recipient_id: str, text: str) -> bool:
        """
        Sends a plain text message to a WhatsApp user phone number.
        Returns True on HTTP 200/201 success, False otherwise.
        """
        if not self.is_configured:
            logger.debug(
                "WhatsAppClient credentials not configured (WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID missing)."
            )
            return False

        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_id,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=req_data,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status_code = resp.getcode()
                if status_code in (200, 201):
                    logger.info(f"Outbound WhatsApp message sent to {recipient_id}.")
                    return True
                return False
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.error(
                f"Meta API HTTP Error {e.code} sending message to {recipient_id}: {err_body}"
            )
            return False
        except Exception as e:
            logger.error(f"Error sending WhatsApp message to {recipient_id}: {e}")
            return False


def get_progress_status_summary(progress_path: str = "progress.json") -> str:
    """Reads progress.json to return a summary when BotAutomation is not in memory."""
    path = Path(progress_path)
    if not path.exists():
        return "🤖 Telegram Automation: No active progress file (progress.json not found)."

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        loops = data.get("loops", {})
        curr_msg_id = data.get("current_message_id", "None")
        last_action = data.get("last_action", "UNKNOWN")
        updated_at = data.get("updated_at", "")

        lines = [
            "🤖 *Telegram Automation Status*",
            f"• Current Msg ID: {curr_msg_id}",
            f"• Last Action: {last_action}",
            f"• Updated: {updated_at}",
            "\n📁 *Configured Loops:*",
        ]

        for k, l in loops.items():
            name = l.get("name", f"Loop {k}")
            start_id = l.get("start_id", 0)
            end_id = l.get("end_id", 0)
            completed = len(l.get("completed_ids", []))
            total = (end_id - start_id + 1) if (end_id >= start_id) else 0
            pct = (completed / total * 100) if total > 0 else 0
            is_done = "✅ Done" if l.get("is_finished") else "⏳ In Progress"
            lines.append(
                f"• *{name}*: {completed}/{total} ({pct:.1f}%) | {is_done}"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"🤖 Telegram Automation Status: Error reading progress file: {e}"


def extract_messages(payload: Dict[str, Any]) -> List[WhatsAppMessage]:
    """
    Extracts incoming WhatsApp messages from Meta's nested webhook JSON payload.
    Supports standard text messages, interactive button replies, and quick replies.
    """
    messages: List[WhatsAppMessage] = []

    if not isinstance(payload, dict):
        return messages

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue

            # Build map of contacts (wa_id -> profile name)
            contacts_map: Dict[str, str] = {}
            for contact in value.get("contacts", []):
                wa_id = contact.get("wa_id")
                profile = contact.get("profile", {})
                name = profile.get("name")
                if wa_id and name:
                    contacts_map[wa_id] = name

            # Parse each message
            for raw_msg in value.get("messages", []):
                sender = raw_msg.get("from", "")
                msg_id = raw_msg.get("id", "")
                timestamp = raw_msg.get("timestamp", "")
                msg_type = raw_msg.get("type", "unknown")
                sender_name = contacts_map.get(sender)

                extracted_text = ""

                if msg_type == "text":
                    extracted_text = raw_msg.get("text", {}).get("body", "")
                elif msg_type == "interactive":
                    interactive = raw_msg.get("interactive", {})
                    i_type = interactive.get("type")
                    if i_type == "button_reply":
                        extracted_text = interactive.get("button_reply", {}).get(
                            "title", ""
                        )
                    elif i_type == "list_reply":
                        extracted_text = interactive.get("list_reply", {}).get(
                            "title", ""
                        )
                elif msg_type == "button":
                    extracted_text = raw_msg.get("button", {}).get("text", "")
                else:
                    # Non-text media (e.g., image, document, audio, video)
                    extracted_text = f"[{msg_type.upper()} MESSAGE]"

                messages.append(
                    WhatsAppMessage(
                        sender=sender,
                        sender_name=sender_name,
                        message_id=msg_id,
                        timestamp=timestamp,
                        text=extracted_text,
                        msg_type=msg_type,
                        raw=raw_msg,
                    )
                )

    return messages


class WhatsAppCommandHandler:
    """
    Modular command handler for WhatsApp messages.
    Supports START, STOP, STATUS, and custom action hooks for Telegram automation.
    """

    def __init__(self, whatsapp_client: Optional[WhatsAppClient] = None):
        self._handlers: Dict[str, Callable[[WhatsAppMessage], Any]] = {}
        self._default_handler: Optional[Callable[[WhatsAppMessage], Any]] = None
        self.whatsapp_client = whatsapp_client or WhatsAppClient()

        # Register default fallback handler for STATUS if no custom automation is attached
        self.register_command("STATUS", self._default_status_handler)

    def _default_status_handler(self, msg: WhatsAppMessage) -> None:
        """Fallback status handler when running standalone without live BotAutomation."""
        summary = get_progress_status_summary()
        print("\n" + summary.replace("*", "").replace("`", "") + "\n")
        if self.whatsapp_client.is_configured:
            self.whatsapp_client.send_text_message(msg.sender, summary)

    def register_command(
        self, command: str, handler: Callable[[WhatsAppMessage], Any]
    ) -> None:
        """Register a callback for a specific command (e.g. 'START', 'STOP', 'STATUS')."""
        self._handlers[command.strip().upper()] = handler

    def set_default_handler(
        self, handler: Callable[[WhatsAppMessage], Any]
    ) -> None:
        """Set a fallback handler for non-command messages."""
        self._default_handler = handler

    @staticmethod
    def normalize_command(text: str) -> str:
        """Extracts and normalizes the command keyword (e.g., '/start' or 'START' -> 'START')."""
        stripped = text.strip()
        if not stripped:
            return ""
        first_token = stripped.split()[0]
        if first_token.startswith("/"):
            first_token = first_token[1:]
        return first_token.upper()

    def process_message(self, msg: WhatsAppMessage) -> None:
        """Prints the received message clearly in the terminal and dispatches commands."""
        # Convert timestamp to human-readable format if available
        time_str = msg.timestamp
        try:
            if msg.timestamp and msg.timestamp.isdigit():
                time_str = datetime.fromtimestamp(int(msg.timestamp)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
        except Exception:
            pass

        sender_display = (
            f"{msg.sender} ({msg.sender_name})"
            if msg.sender_name
            else msg.sender
        )

        # Print message clearly in terminal
        print("\n" + "=" * 60)
        print("📨 [WhatsApp] Incoming Message Received")
        print("-" * 60)
        print(f"From   : {sender_display}")
        print(f"Time   : {time_str}")
        print(f"Type   : {msg.msg_type}")
        print(f"ID     : {msg.message_id}")
        print(f"Text   : {msg.text}")
        print("=" * 60)

        # Check for recognized command keyword
        cmd = self.normalize_command(msg.text)
        if cmd in ("START", "RESUME", "STOP", "PAUSE", "STATUS"):
            print(f"⚡ [WhatsApp Command] Detected action: '{cmd}' from {msg.sender}")

            if cmd in self._handlers:
                try:
                    self._handlers[cmd](msg)
                except Exception as e:
                    logger.error(f"Error executing handler for command {cmd}: {e}")
            else:
                print(
                    f"ℹ️ [WhatsApp Command] '{cmd}' recognized. Ready to connect to Telegram automation."
                )
        elif self._default_handler:
            try:
                self._default_handler(msg)
            except Exception as e:
                logger.error(f"Error executing default message handler: {e}")


def connect_automation_to_whatsapp(
    command_handler: WhatsAppCommandHandler,
    automation: Any,
    whatsapp_client: Optional[WhatsAppClient] = None,
) -> None:
    """
    Connects Telegram BotAutomation controls to WhatsApp command handler.
    Binds START, RESUME, STOP, PAUSE, and STATUS commands to BotAutomation methods.
    """
    client = whatsapp_client or command_handler.whatsapp_client

    def on_start(msg: WhatsAppMessage):
        parts = msg.text.strip().split()
        cmd = parts[0].upper()

        if cmd == "RESUME" or len(parts) < 4:
            # It's a RESUME command or a START without args (which acts as resume)
            automation.resume()
            reply = "▶️ Telegram Automation has been RESUMED."
            print(f"⚡ [Automation Bridge] {reply}")
            if client.is_configured:
                client.send_text_message(msg.sender, reply)
            return

        # It's a START command with args: START <base_url> <start_id> <end_id>
        try:
            base_url = parts[1]
            if not base_url.startswith("http"):
                raise ValueError("URL must start with http")
            start_id = int(parts[2])
            end_id = int(parts[3])
            
            if start_id < 0 or start_id > end_id:
                raise ValueError("Invalid ID range")

            from config_loader import LoopConfig, save_loops_to_config
            
            new_loop = LoopConfig(
                name="WhatsApp Loop",
                base_link=base_url,
                start_id=start_id,
                end_id=end_id,
            )
            
            automation.config.loops = [new_loop]
            
            # Save the new loops to config.json so it persists
            save_loops_to_config(automation.config.loops, "config.json")
            
            # Reset only the state, then initialize it fresh
            automation.state_mgr.reset()
            automation.state_mgr.state = automation.state_mgr.load(automation.config)
            
            reply = f"▶️ Created new loop: {base_url} [{start_id}..{end_id}]. Resuming..."
        except ValueError as e:
            reply = f"⚠️ Invalid START command. Error: {e}\nUse: START <base_url> <start_id> <end_id>"
            print(f"⚡ [Automation Bridge] {reply}")
            if client.is_configured:
                client.send_text_message(msg.sender, reply)
            return
        except Exception as e:
            reply = f"⚠️ Error processing START command: {e}"
            print(f"⚡ [Automation Bridge] {reply}")
            if client.is_configured:
                client.send_text_message(msg.sender, reply)
            return

        automation.resume()
        print(f"⚡ [Automation Bridge] {reply}")
        if client.is_configured:
            client.send_text_message(msg.sender, reply)

    def on_stop(msg: WhatsAppMessage):
        automation.pause()
        reply = "⏸ Telegram Automation has been PAUSED."
        print(f"⚡ [Automation Bridge] {reply}")
        if client.is_configured:
            client.send_text_message(msg.sender, reply)

    def on_status(msg: WhatsAppMessage):
        summary = automation.get_status_summary()
        print(f"\n⚡ [Automation Bridge] Sending Status to {msg.sender}:\n" + summary.replace("*", "").replace("`", "") + "\n")
        if client.is_configured:
            client.send_text_message(msg.sender, summary)

    command_handler.register_command("START", on_start)
    command_handler.register_command("RESUME", on_start)
    command_handler.register_command("STOP", on_stop)
    command_handler.register_command("PAUSE", on_stop)
    command_handler.register_command("STATUS", on_status)
    logger.info("WhatsApp commands (START, STOP, STATUS) successfully connected to Telegram BotAutomation.")


# Basic idempotency cache
PROCESSED_MESSAGES = set()

def create_app(
    verify_token: Optional[str] = None,
    command_handler: Optional[WhatsAppCommandHandler] = None,
) -> Flask:
    """
    Flask application factory for WhatsApp Webhook Server.
    """
    app = Flask(__name__)
    token = verify_token if verify_token is not None else os.getenv("HUB_VERIFY_TOKEN", "")
    handler = command_handler or WhatsAppCommandHandler()

    # Store references on app config for easy retrieval or testing
    app.config["HUB_VERIFY_TOKEN"] = token
    app.config["COMMAND_HANDLER"] = handler

    @app.route("/", methods=["GET"])
    def index():
        """Health check endpoint."""
        return jsonify(
            {
                "status": "online",
                "service": "WhatsApp Webhook Server",
                "webhook_path": "/webhook",
            }
        ), 200

    @app.route("/webhook", methods=["GET"])
    def verify_webhook():
        """
        Meta Webhook verification endpoint.
        Validates hub.mode, hub.verify_token and returns hub.challenge.
        """
        mode = request.args.get("hub.mode")
        req_token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        expected_token = app.config.get("HUB_VERIFY_TOKEN", "")

        # If token doesn't match or is empty, re-check .env dynamically in case it was updated
        if (not expected_token or req_token != expected_token) and Path(".env").exists():
            from dotenv import dotenv_values

            current_env = dotenv_values(".env")
            reloaded_token = current_env.get("HUB_VERIFY_TOKEN")
            if reloaded_token:
                expected_token = reloaded_token
                app.config["HUB_VERIFY_TOKEN"] = reloaded_token

        if not expected_token:
            logger.warning(
                "HUB_VERIFY_TOKEN is not configured in .env! Verification will fail."
            )

        if mode == "subscribe" and req_token and req_token == expected_token:
            logger.info("WhatsApp webhook verified successfully by Meta.")
            return Response(challenge, status=200, mimetype="text/plain")
        else:
            logger.warning(
                f"Failed webhook verification: mode='{mode}', received token='{req_token}'"
            )
            return Response("Verification failed: Token mismatch or invalid mode", status=403)

    @app.route("/webhook", methods=["POST"])
    def receive_webhook():
        """
        Receives WhatsApp Cloud API notifications (messages, status updates).
        """
        data = request.get_json(silent=True) or {}

        try:
            # Extract messages from payload
            incoming_messages = extract_messages(data)

            # Process valid messages in background
            def process_msgs(msgs):
                for msg in msgs:
                    # Idempotency check
                    if msg.message_id in PROCESSED_MESSAGES:
                        continue
                    PROCESSED_MESSAGES.add(msg.message_id)
                    if len(PROCESSED_MESSAGES) > 1000:
                        # naive cleanup
                        # remove arbitrary elements (not strictly LRU, but prevents memory leak)
                        for _ in range(500):
                            if PROCESSED_MESSAGES:
                                PROCESSED_MESSAGES.pop()
                    
                    # Security check: validate sender
                    allowed_number = os.getenv("ALLOWED_WHATSAPP_NUMBER", "").strip()
                    if allowed_number and msg.sender != allowed_number:
                        logger.warning(f"Unauthorized sender attempted command: {msg.sender}")
                        continue
                    
                    handler.process_message(msg)

            if incoming_messages:
                threading.Thread(target=process_msgs, args=(incoming_messages,), daemon=True).start()

        except Exception as e:
            logger.error(f"Error processing incoming WhatsApp webhook: {e}", exc_info=True)

        # Meta requires 200 OK fast response to acknowledge receipt
        return jsonify({"status": "EVENT_RECEIVED"}), 200

    return app


def start_webhook_background(
    automation: Optional[Any] = None,
    port: int = 5000,
    host: str = "0.0.0.0",
) -> threading.Thread:
    """
    Starts the WhatsApp Webhook server in a background daemon thread.
    Automatically connects commands to the provided Telegram BotAutomation instance.
    """
    handler = WhatsAppCommandHandler()
    if automation is not None:
        connect_automation_to_whatsapp(handler, automation)

    app = create_app(command_handler=handler)

    # Disable Flask banner logs when running in background thread
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.WARNING)

    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    logger.info(f"WhatsApp webhook background server running on http://{host}:{port}/webhook")
    return thread


def main():
    """Runs the Flask webhook server."""
    port = int(os.getenv("PORT", 5000))
    host = os.getenv("HOST", "0.0.0.0")
    debug = os.getenv("FLASK_DEBUG", "false").lower() in ("true", "1")

    verify_token = os.getenv("HUB_VERIFY_TOKEN", "")
    token_status = (
        "Configured [OK]"
        if verify_token and verify_token != "your_verify_token_here"
        else "NOT CONFIGURED [!] (Please set HUB_VERIFY_TOKEN in .env)"
    )

    client = WhatsAppClient()
    outbound_status = (
        "Configured [OK]"
        if client.is_configured
        else "Optional (Set WHATSAPP_TOKEN & WHATSAPP_PHONE_NUMBER_ID to enable replies)"
    )

    print("=" * 65)
    print("🚀 WhatsApp Webhook Server for Telegram Automation")
    print("=" * 65)
    print(f" Server URL       : http://localhost:{port}")
    print(f" Webhook Endpoint : http://localhost:{port}/webhook")
    print(f" Verify Token     : {token_status}")
    print(f" Outbound Replies : {outbound_status}")
    print(f" Port             : {port}")
    print("=" * 65)
    print("Waiting for incoming WhatsApp messages and webhook events...\n")

    app = create_app(verify_token=verify_token)
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
