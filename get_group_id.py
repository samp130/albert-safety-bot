import asyncio
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

if not BOT_TOKEN or "your-bot-token" in BOT_TOKEN:
    BOT_TOKEN = input("Enter your Telegram BOT_TOKEN: ").strip()

async def print_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    print("\n" + "="*50)
    print(f"🎉 DETECTED CHAT:")
    print(f"Title: {chat.title or chat.first_name}")
    print(f"Type:  {chat.type}")
    print(f"COUNSELOR_GROUP_ID={chat.id}")
    print("="*50 + "\n")
    if chat.type in ["group", "supergroup"]:
        print(">> Add this to your .env file:")
        print(f"COUNSELOR_GROUP_ID={chat.id}\n")

def main():
    print("\n[+] Starting helper listener...")
    print("[+] Now go to your Counselor Group and send a message mentioning the bot or send any /command (e.g. /id).")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, print_chat_id))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
