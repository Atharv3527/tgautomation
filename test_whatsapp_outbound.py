import unittest
from unittest.mock import patch, MagicMock
import urllib.error
import io
import json

from whatsapp_webhook import WhatsAppClient

class TestWhatsAppOutbound(unittest.TestCase):
    def setUp(self):
        self.client = WhatsAppClient(phone_number_id="123", access_token="TOKEN", api_version="v18.0")
        self.recipient = "919356711936"
        self.text = "Test Message"

    def test_missing_credentials(self):
        client = WhatsAppClient(phone_number_id="", access_token="")
        with self.assertLogs("whatsapp_webhook", level="ERROR") as cm:
            res = client.send_text_message(self.recipient, self.text)
        self.assertFalse(res)
        self.assertTrue(any("missing/invalid/expired" in log for log in cm.output))
        # Ensure token is not logged anywhere
        self.assertNotIn("TOKEN", str(cm.output))

    @patch("urllib.request.urlopen")
    def test_meta_api_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = json.dumps({"messages": [{"id": "wamid.123"}]}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        with self.assertLogs("whatsapp_webhook", level="INFO") as cm:
            res = self.client.send_text_message(self.recipient, self.text)

        self.assertTrue(res)
        output = "\n".join(cm.output)
        self.assertIn("Message accepted by Meta", output)
        self.assertIn("Message ID: wamid.123", output)
        self.assertIn("Message type: text", output)
        self.assertNotIn("TOKEN", output)

    @patch("urllib.request.urlopen")
    def test_meta_api_http_error(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 400
        mock_resp.read.return_value = b'{"error": {"message": "Invalid parameter"}}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        with self.assertLogs("whatsapp_webhook", level="INFO") as cm:
            res = self.client.send_text_message(self.recipient, self.text)

        self.assertFalse(res)
        output = "\n".join(cm.output)
        self.assertIn("FAILED", output)
        self.assertIn("Invalid parameter", output)
        self.assertNotIn("TOKEN", output)

    @patch("urllib.request.urlopen")
    def test_meta_api_urllib_http_error_exception(self, mock_urlopen):
        # urllib raises HTTPError on 4xx/5xx by default sometimes depending on usage
        fp = io.BytesIO(b'{"error": {"message": "Outside window"}}')
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://test", 403, "Forbidden", {}, fp
        )

        with self.assertLogs("whatsapp_webhook", level="INFO") as cm:
            res = self.client.send_text_message(self.recipient, self.text)

        self.assertFalse(res)
        output = "\n".join(cm.output)
        self.assertIn("FAILED", output)
        self.assertIn("Outside window", output)
        self.assertNotIn("TOKEN", output)

    @patch("urllib.request.urlopen")
    def test_meta_api_timeout(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("timed out")

        with self.assertLogs("whatsapp_webhook", level="INFO") as cm:
            res = self.client.send_text_message(self.recipient, self.text)

        self.assertFalse(res)
        output = "\n".join(cm.output)
        self.assertIn("ERROR: Connection or Timeout error", output)
        self.assertNotIn("TOKEN", output)

if __name__ == "__main__":
    unittest.main()
