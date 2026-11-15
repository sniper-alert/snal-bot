import os
import threading
import time
from flask import Flask
import telebot
import yt_dlp

# جلب توكن البوت من متغيرات البيئة بأمان
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
  raise ValueError("BOT_TOKEN is missing in environment variables!")

bot = telebot.TeleBot(TOKEN)

# حذف الويب هوك القديم لمنع خطأ التعارض (Conflict 409)
try:
  bot.delete_webhook(drop_pending_updates=True)
  print("Old webhook deleted successfully.")
except Exception as e:
  print(f"Webhook delete error: {e}")

# إعداد سيرفر Flask مصغر للحفاظ على نشاط البوت على Render (منع النوم)
app = Flask("")


@app.route("/")
def home():
  return "Snal Bot is running 24/7!"


def run_flask():
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


# رسالة البدء
@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
  bot.reply_to(
      message,
      "أهلاً بك يا سِنآل! 🤖\n\nأرسل لي أي رابط من (TikTok, YouTube, Instagram,"
      " X/Twitter) وسأقوم بتحميله وإرساله لك فوراً بجودة عالية.",
  )


# استقبال الروابط ومعالجتها
@bot.message_handler(func=lambda message: True)
def handle_link(message):
  url = message.text.strip()

  # التحقق من أن الرسالة رابط
  if not url.startswith("http"):
    bot.reply_to(message, "الرجاء إرسال رابط صحيح يبدأ بـ http أو https.")
    return

  msg = bot.reply_to(message, "⏳ جاري تحليل الرابط والتحميل، انتظر قليلاً...")

  # تشغيل التحميل في خيط منفصل (Background Thread) لعدم تجميد البوت
  threading.Thread(
      target=download_and_send, args=(message.chat.id, url, msg.message_id)
  ).start()


def download_and_send(chat_id, url, processing_msg_id):
  output_template = f"downloads/file_{chat_id}_%(id)s.%(ext)s"
  os.makedirs("downloads", exist_ok=True)
  downloaded_file = None

  # إعدادات yt-dlp المحدثة خصيصاً لمحاكاة متصفح هاتف ذكي لتجاوز حظر تيك توك
  ydl_opts = {
      "format": "best",
      "outtmpl": output_template,
      "max_filesize": 50 * 1024 * 1024,
      "nocheckcertificate": True,
      "ignoreerrors": False,
      "quiet": True,
      "geo_bypass": True,
      "extractor_args": {
          "tiktok": {
              "app_name": ["musically"],  # محاكاة تطبيق تيك توك الرسمي
              "api_hostname": ["api16-normal-c-useast1a.tiktokv.com"],
          }
      },
      "http_headers": {
          "User-Agent": (
              "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 "
              "Mobile/15E148 Safari/604.1"
          ),
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "en-US,en;q=0.5",
          "Sec-Fetch-Mode": "navigate",
      },
  }

  try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
      info = ydl.extract_info(url, download=True)
      downloaded_file = ydl.prepare_filename(info)

    if downloaded_file and os.path.exists(downloaded_file):
      bot.edit_message_text(
          "📤 جاري رفع الملف إليك...",
          chat_id=chat_id,
          message_id=processing_msg_id,
      )

      with open(downloaded_file, "rb") as f:
        if downloaded_file.endswith((".mp3", ".m4a", ".wav")):
          bot.send_audio(chat_id, f)
        else:
          bot.send_video(chat_id, f, supports_streaming=True)

      bot.delete_message(chat_id, processing_msg_id)
    else:
      bot.edit_message_text(
          "❌ عذراً، لم أتمكن من العثور على ملف قابل للتحميل.",
          chat_id=chat_id,
          message_id=processing_msg_id,
      )

  except Exception as e:
    print(f"Error details: {e}")
    error_msg = f"❌ تعذر تحميل تيك توك: {str(e)[:80]}"
    try:
      bot.edit_message_text(
          error_msg, chat_id=chat_id, message_id=processing_msg_id
      )
    except:
      bot.send_message(chat_id, error_msg)

  finally:
    if downloaded_file and os.path.exists(downloaded_file):
      try:
        os.remove(downloaded_file)
      except Exception as e:
        print(f"Error removing file: {e}")


# تشغيل سيرفر الـ Flask والبوت معاً بحلقة تشغيل آمنة
if __name__ == "__main__":
  flask_thread = threading.Thread(target=run_flask)
  flask_thread.daemon = True
  flask_thread.start()

  print("Bot is starting safely...")
  while True:
    try:
      bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
    except Exception as e:
      print(f"Polling error: {e}")
      time.sleep(5)
