# Telegram Sequential Video Automation

A reliable, sequential Telegram automation built with **Telethon** and **Python 3**. 

It automates sending Telegram message links to a processing bot (`@save_restricted_contentpro_bot`) and strictly **waits for the actual video media response** before sending the next link in the sequence.

---

## 🎯 Key Features

- **No Fixed Waiting Times:** Monitors incoming MTProto messages and advances **only** when the actual video / video document is confirmed received.
- **Progress Tracking & Resumption (`progress.json`):** If interrupted or restarted, it picks up right where it left off. Never sends duplicates, never skips IDs.
- **Multi-Loop Support:** Automatically runs Loop 1 (e.g. IDs 25 to 45), and upon completion transitions seamlessly to Loop 2, Loop 3, etc.
- **Remote Telegram Commands:** Control the script from your phone or desktop via **Telegram Saved Messages** (`/status`, `/stop`, `/start`).
- **Terminal Console Commands:** Type `status`, `pause`, `resume`, or `exit` into the terminal at any time.
- **Safety & Error Handling:** Handles Telegram `FloodWaitError` with automatic cooldown, connection dropouts, bot errors, and configurable timeouts.
- **Zero Video Downloads:** Detects receipt by inspecting message media metadata without downloading heavy video files to your disk.

---

## 📁 Project Structure

```
telegram-automation/
├── .venv/                   # Python virtual environment
├── .env                     # Your Telegram credentials (API_ID, API_HASH, Phone)
├── .env.example             # Template for credentials
├── config.json              # Full loops configuration (IDs 25–45)
├── config.test.json         # Quick test configuration (IDs 25–27)
├── config_loader.py         # Configuration parser and validator
├── state_manager.py         # Progress saving and resumption logic
├── media_detector.py        # Video detection without downloading
├── bot_automation.py        # Core sequential loop & event listeners
├── main.py                  # CLI entry point, command listener & runner
├── test_automation.py       # Automated unit tests
├── requirements.txt         # Dependencies (telethon, python-dotenv)
└── README.md                # Documentation & instructions
```

---

## 🔑 Step 1: Telegram Credentials Setup

To use Telethon with your personal account, Telegram requires an **API ID** and **API Hash**:

1. Go to **[https://my.telegram.org](https://my.telegram.org)** in your web browser.
2. Enter your phone number (with country code, e.g. `+1...` or `+91...`) and log in using the code sent to your Telegram app.
3. Click on **API development tools**.
4. Fill in an app title and short name (e.g., `Automation` and `app`).
5. You will see your **`api_id`** (numbers) and **`api_hash`** (alphanumeric string).
6. Open the file [`.env`](file:///d:/telegram-automation/.env) and insert your values:
   ```env
   TELEGRAM_API_ID=12345678
   TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
   TELEGRAM_PHONE=+1234567890
   TELEGRAM_SESSION=telegram_automation
   BOT_USERNAME=@save_restricted_contentpro_bot
   ```

> ⚠️ **Important:** Ensure you have opened a chat with **`@save_restricted_contentpro_bot`** in your Telegram app and clicked **Start** at least once so your account can message it.

---

## 🧪 Step 2: Run the Initial Test (IDs 25–27)

Before running the full range (25 to 45), test with a small batch (IDs 25, 26, 27) using the included test config:

1. Open PowerShell or Command Prompt in `d:\telegram-automation`.
2. Run the test command:
   ```powershell
   .venv\Scripts\python main.py --test
   ```
3. **First-time login:**
   - Telethon will ask for your phone number (if not set in `.env`) and the confirmation code sent to your Telegram app.
   - If you have Two-Step Verification (cloud password) enabled, enter your password when prompted.
   - A session file (`telegram_automation.session`) will be saved locally. Subsequent runs will not ask for login codes again.
4. **Observe the test output:**
   ```
   10:00:01 [INFO] SENT: 25 (Test Loop) -> https://t.me/c/3548255677/15/25
   10:00:15 [INFO] VIDEO RECEIVED: 25 [Size: 45.20 MB | Duration: 02:15] (Msg ID: 5120)
   10:00:18 [INFO] SENT: 26 (Test Loop) -> https://t.me/c/3548255677/15/26
   10:00:32 [INFO] VIDEO RECEIVED: 26 [Size: 62.10 MB | Duration: 03:40] (Msg ID: 5124)
   10:00:35 [INFO] SENT: 27 (Test Loop) -> https://t.me/c/3548255677/15/27
   10:00:48 [INFO] VIDEO RECEIVED: 27 [Size: 38.50 MB | Duration: 01:50] (Msg ID: 5128)
   10:00:49 [INFO] 🎉 ALL CONFIGURED LOOPS COMPLETED!
   ```

---

## 🚀 Step 3: Run the Full Batch (IDs 25–45 & Multiple Loops)

Once the test is confirmed working, you can run the full automation:

1. Review or customize [`config.json`](file:///d:/telegram-automation/config.json):
   ```json
   {
     "bot_username": "@save_restricted_contentpro_bot",
     "timeout_seconds": 600,
     "delay_between_links": 3,
     "loops": [
       {
         "name": "Loop 1",
         "base_link": "https://t.me/c/3548255677/15/",
         "start_id": 25,
         "end_id": 45
       },
       {
         "name": "Loop 2",
         "base_link": "https://t.me/c/3548255678/15/",
         "start_id": 25,
         "end_id": 45
       }
     ]
   }
   ```
2. **Choose How to Run Real Working Mode:**

   **Option A: Interactive Manual Mode (Prompts for base link & IDs)**
   ```powershell
   .venv\Scripts\python main.py --manual --reset
   ```
   *The script will ask you for base link, start ID, and end ID right in the terminal, and can save it to config.json.*

   **Option B: Direct Command-Line Arguments**
   ```powershell
   .venv\Scripts\python main.py --base-link "https://t.me/c/3548255677/15/" --start 25 --end 45 --reset
   ```

   **Option C: Using [`config.json`](file:///d:/telegram-automation/config.json)**
   ```powershell
   .venv\Scripts\python main.py --reset
   ```

---

## 🎮 Controlling the Automation

### 1. Remote Control via Telegram ("Saved Messages")
From your phone or Telegram app anywhere:
- Open your **"Saved Messages"** chat.
- Send:
  - `/status` — View current loop, active link, IDs completed, and percentage.
  - `/stop` or `/pause` — Pauses sending next link.
  - `/start` or `/resume` — Resumes automation.

### 2. Terminal Console
In the running terminal window, you can type anytime:
- `status` — Prints current status and progress.
- `pause` — Pauses the automation.
- `resume` — Resumes the automation.
- `exit` — Gracefully stops and exits.

---

## 🛠 Advanced Options

- **Resume After Interruption (Laptop Restart, Crash, etc.):** 
  Simply re-run `.venv\Scripts\python main.py`. It reads `progress.json` and resumes automatically from the exact ID it left off at without skipping or resending confirmed videos. By default, the script starts **IDLE** waiting for a `START` or `RESUME` command. 
- **Auto-Start:** 
  Use the `--auto-start` flag to start sending links immediately instead of starting paused:
  `.venv\Scripts\python main.py --auto-start`
- **Infinite Waiting:** In `config.json`, set `"timeout_seconds": null` to wait indefinitely for the bot's video response.
- **Change Delay:** Adjust `"delay_between_links": 3` (in seconds) to comply with any bot rate limits.

---

## 📱 WhatsApp Webhook Server

A modular Flask webhook server is available in [`whatsapp_webhook.py`](file:///d:/telegram-automation/whatsapp_webhook.py) to receive incoming WhatsApp messages and Meta webhook verification.

### 1. Configure Verification Token
In [`.env`](file:///d:/telegram-automation/.env):
```env
HUB_VERIFY_TOKEN=your_verify_token_here
```

### 2. Run Options

**Option A: Integrated with Telegram Automation (Single Command)**
```powershell
.venv\Scripts\python main.py --webhook
```
*Runs the Telegram video automation and the WhatsApp webhook server concurrently. Incoming WhatsApp messages will directly control the automation!*

**Option B: Standalone Webhook Server**
```powershell
.venv\Scripts\python whatsapp_webhook.py
```
*Runs on port 5000 (`http://localhost:5000/webhook`) and displays incoming messages in the terminal.*

### 3. WhatsApp Commands
Send any of the following to your connected WhatsApp business number:
- `STATUS` — Displays active loop, processed IDs, current URL, and progress percentage.
- `STOP` or `PAUSE` — Safely pauses the Telegram video loop without losing progress.
- `RESUME` — Resumes processing the currently saved loop from where it left off.
- `START <telegram_base_url> <start_id> <end_id>` — Creates a fresh active loop, resets progress for that loop, and begins processing.
  *(Example: `START https://t.me/c/3548255677/157/ 25 45`)*

*(Optional)*: Set `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` in `.env` to automatically send replies and status summaries back to the sender on WhatsApp.

### 4. Security Notes
- **Environment Variables**: Never commit `.env` or your `.session` files to version control. They are protected by `.gitignore`.
- **Allowed Senders**: To prevent unauthorized users from controlling your automation, set `ALLOWED_WHATSAPP_NUMBER` in your `.env`. Example: `ALLOWED_WHATSAPP_NUMBER=919356711936`. Any commands from other numbers will be safely ignored.
- **Webhooks**: Your webhook token (`HUB_VERIFY_TOKEN`) is stored securely in your `.env`.

### 4. Endpoints
- `GET /` — Health check endpoint.
- `GET /webhook` — Meta Webhook verification (`hub.mode`, `hub.verify_token`, `hub.challenge`).
- `POST /webhook` — Receives incoming WhatsApp messages, extracts text, prints clearly in terminal, and dispatches automation commands.
