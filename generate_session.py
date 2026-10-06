import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")

if not api_id or not api_hash:
    print("Please set TELEGRAM_API_ID and TELEGRAM_API_HASH in your .env file.")
    exit(1)

async def main():
    print("Generating Telethon String Session...")
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start()
    
    print("\n" + "="*50)
    print("YOUR STRING SESSION IS BELOW:")
    print("Copy it and set it as the 'TELEGRAM_STRING_SESSION' environment variable in Render.")
    print("="*50 + "\n")
    print(client.session.save())
    print("\n" + "="*50)
    
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
