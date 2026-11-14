import os
import threading
from flask import Flask
import telebot
import yt_dlp

# جلب توكن البوت من متغيرات البيئة بأمان
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
  raise ValueError("BOT_TOKEN is missing in environment variables!")

bot = telebot.TeleBot(TOKEN)

# إعداد سيرفر Flask مصغر للحفاظ على نشاط البوت على Render (منع النوم)
app = Flask("")


@app.route("/")
def home():
  return "Snal Bot is running 24/7!"


def run_flask():
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


# رسالة البدء
@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
  bot.reply_to(
      message,
      "أهلاً بك يا سِنآل! 🤖\n\nأرسل لي أي رابط من (TikTok, YouTube, Instagram, X/Twitter)"
      " وسأقوم بتحميله وإرساله لك فوراً بجودة عالية.",
  )


# استقبال الروابط ومعالجتها
@bot.message_handler(func=lambda message: True)
def handle_link(message):
  url = message.text.strip()

  # التحقق البسيط من أن الرسالة رابط
  if not url.startswith("http"):
    bot.reply_to(message, "الرجاء إرسال رابط صحيح يبدأ بـ http أو https.")
    return

  msg = bot.reply_to(message, "⏳ جاري تحليل الرابط والتحميل، انتظر قليلاً...")

  # تشغيل التحميل في خيط منفصل (Background Thread) لعدم تجميد البوت
  threading.Thread(target=download_and_send, args=(message.chat.id, url, msg.message_id)).start()


def download_and_send(chat_id, url, processing_msg_id):
  output_template = f"downloads/file_{chat_id}_%(id)s.%(ext)s"
  os.makedirs("downloads", exist_ok=True)

  # إعدادات yt-dlp الذكية والمستقرة لتجنب المشاكل قدر الإمكان
  ydl_opts = {
      "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
      "outtmpl": output_template,
      "max_filesize": 50 * 1024 * 1024,  # تحديد الحد الأقصى 50 ميجابايت لتوافق تيليجرام
      "nocheckcertificate": True,
      "ignoreerrors": False,
      "quiet": True,
  }

  downloaded_file = None
  try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
      # استخراج معلومات الفيديو أولاً للتحقق
      info = ydl.extract_info(url, download=True)
      downloaded_file = ydl.prepare_filename(info)

    # التأكد من وجود الملف وجاهزيته للإرسال
    if downloaded_file and os.path.exists(downloaded_file):
      bot.edit_message_text(
          "📤 جاري رفع الملف إليك...", chat_id=chat_id, message_id=processing_msg_id
      )

      with open(downloaded_file, "rb") as f:
        # فحص إذا كان الملف صوتاً أو فيديو للإرسال بالطريقة المناسبة
        if downloaded_file.endswith((".mp3", ".m4a", ".wav")):
          bot.send_audio(chat_id, f)
        else:
          bot.send_video(chat_id, f, supports_streaming=True)

      # حذف رسالة "جاري التحميل" لتنظيف المحادثة
      bot.delete_message(chat_id, processing_msg_id)
    else:
      bot.edit_message_text(
          "❌ عذراً، لم أتمكن من العثور على ملف قابل للتحميل من هذا الرابط.",
          chat_id=chat_id,
          message_id=processing_msg_id,
      )

  except Exception as e:
    print(f"Error details: {e}")
    error_msg = "❌ حدث خطأ أثناء التحميل. قد يكون الرابط محظوراً، أو الفيديو كبيراً جداً، أو المنصة غير متاح فيها التحميل مؤقتاً."
    try:
      bot.edit_message_text(
          error_msg, chat_id=chat_id, message_id=processing_msg_id
      )
    except:
      bot.send_message(chat_id, error_msg)

  finally:
    # تنظيف وحذف الملف من مساحة التخزين المؤقتة حتماً لمنع امتلاء السيرفر
    if downloaded_file and os.path.exists(downloaded_file):
      try:
        os.remove(downloaded_file)
      except Exception as e:
        print(f"Error removing file: {e}")


# تشغيل سيرفر الـ Flask والبوت معاً
if __name__ == "__main__":
  flask_thread = threading.Thread(target=run_flask)
  flask_thread.daemon = True
  flask_thread.start()

  print("Bot is starting...")
  bot.infinity_polling()
