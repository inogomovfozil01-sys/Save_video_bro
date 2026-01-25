import os
import json
import time
import asyncio
import yt_dlp
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.request import HTTPXRequest

# Настройка логирования (важно для хостинга, чтобы видеть ошибки)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG_FILE = "config.json"
USERS_FILE = "users.json"
COOKIES_FILE = "cookies.txt"

# Инициализация конфига
if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "bot_token": "ВАШ_ТОКЕН",
            "mandatory_channels": ["@DevZone_IT"],
            "max_file_size_gb": 2
        }, f, ensure_ascii=False, indent=2)

config = json.load(open(CONFIG_FILE, "r", encoding="utf-8"))
BOT_TOKEN = config["bot_token"]
MANDATORY_CHANNELS = config["mandatory_channels"]
MAX_FILE_SIZE = config["max_file_size_gb"] * 1024 * 1024 * 1024

def load_users():
    if not os.path.exists(USERS_FILE): return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(data):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def ensure_user(user):
    data = load_users()
    uid = str(user.id)
    if uid not in data:
        data[uid] = {"username": user.username, "registered": time.strftime("%Y-%m-%d %H:%M:%S"), "downloads": 0}
        save_users(data)

async def check_subscriptions(user_id, bot):
    for channel in MANDATORY_CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except Exception as e:
            logger.error(f"Ошибка проверки подписки: {e}")
            return False
    return True

def subscribe_keyboard():
    keyboard = [[InlineKeyboardButton(f"📢 Подписаться {ch}", url=f"https://t.me/{ch.replace('@','')}")] for ch in MANDATORY_CHANNELS]
    keyboard.append([InlineKeyboardButton("✅ Проверить", callback_data="check_subscribe")])
    return InlineKeyboardMarkup(keyboard)

def download_media(url, filename_tmpl):
    # ИСПРАВЛЕННЫЕ НАСТРОЙКИ: качаем лучшее видео + лучшее аудио и склеиваем в mp4
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": filename_tmpl,
        "merge_output_format": "mp4", # Принудительно делаем mp4 для Telegram
        "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_FILE_SIZE,
        "concurrent_fragment_downloads": 5,
        "socket_timeout": 30,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)
    except Exception as e:
        logger.error(f"Ошибка yt-dlp: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    if not await check_subscriptions(update.effective_user.id, context.bot):
        await update.message.reply_text("Подпишись на все каналы, чтобы пользоваться ботом.", reply_markup=subscribe_keyboard())
        return
    await update.message.reply_text("Кидай ссылку с TikTok, YouTube, Instagram или Pinterest.")

async def check_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if await check_subscriptions(q.from_user.id, context.bot):
        await q.edit_message_text("✅ Подписка подтверждена. Можешь отправлять ссылки.")
    else:
        await q.edit_message_reply_markup(reply_markup=subscribe_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"): return

    if not await check_subscriptions(update.effective_user.id, context.bot):
        await update.message.reply_text("Сначала подпишись на каналы.", reply_markup=subscribe_keyboard())
        return

    status_msg = await update.message.reply_text("⏳ Обработка видео... (это может занять время)")

    # Создаем уникальное имя файла
    file_id = f"{update.effective_user.id}_{int(time.time())}"
    filename_tmpl = f"media_{file_id}.%(ext)s"

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, lambda: download_media(url, filename_tmpl))

    if not info:
        await status_msg.edit_text("❌ Не удалось скачать. Возможно, ссылка не поддерживается или файл слишком большой.")
        return

    # yt-dlp может изменить расширение при склейке (например, в mp4)
    # Поэтому ищем файл, который начинается на media_file_id
    downloaded_file = None
    for f in os.listdir('.'):
        if f.startswith(f"media_{file_id}"):
            downloaded_file = f
            break

    if not downloaded_file or not os.path.exists(downloaded_file):
        await status_msg.edit_text("❌ Файл не найден после загрузки.")
        return

    await status_msg.edit_text("🚀 Отправляю в Telegram...")

    try:
        title = info.get("title", "Медиа")
        with open(downloaded_file, "rb") as f:
            if downloaded_file.lower().endswith(('jpg', 'jpeg', 'png', 'webp')):
                await update.message.reply_photo(photo=f, caption=title)
            else:
                # Отправляем именно как видео
                await update.message.reply_video(video=f, caption=title, supports_streaming=True)
        
        # Обновляем статистику
        users = load_users()
        uid = str(update.effective_user.id)
        users[uid]["downloads"] += 1
        save_users(users)

    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        await update.message.reply_text("❌ Ошибка при отправке файла.")
    finally:
        if downloaded_file and os.path.exists(downloaded_file):
            os.remove(downloaded_file)
        await status_msg.delete()

def main():
    request = HTTPXRequest(connect_timeout=60, read_timeout=120)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_subscribe, pattern="check_subscribe"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
