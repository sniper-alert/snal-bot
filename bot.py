import os

# تحديث أداة التحميل لأحدث إصدار تلقائياً
os.system('pip install --upgrade yt-dlp')

from flask import Flask
import threading
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# --- بداية السيرفر الوهمي (لحل مشكلة Render) ---
app = Flask('')

@app.route('/')
def home():
    return "I am alive!"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    t = threading.Thread(target=run)
    t.start()
# ---------------------------------------------

BOT_TOKEN = "9239041330:AAEk8woAvKhdslKCpPZUs9V4n2vs8Br3jB4"
bot = telebot.TeleBot(BOT_TOKEN)

user_links = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "أهلاً بك معك سِنآل! 👋\nأرسل لي رابط الفيديو (تيك توك، يوتيوب، إنستغرام، X) وسأقوم بتحميله لك.")

@bot.message_handler(func=lambda message: message.text and message.text.startswith("http"))
def handle_url(message):
    chat_id = message.chat.id
    user_links[chat_id] = message.text.strip()
    
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("🎬 تحميل الفيديو (مناسب لتيليجرام)", callback_data="vid_best"),
        InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data="audio")
    )
    
    bot.reply_to(message, "اختر صيغة التحميل المطلوبة:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    url = user_links.get(chat_id)
    
    if not url:
        bot.answer_callback_query(call.id, "انتهت صلاحية الرابط، أرسله مجدداً.")
        return

    bot.edit_message_text("⏳ جاري التحميل والمعالجة... يرجى الانتظار .. سِنآل يرحب بك.", chat_id, call.message.message_id)
    
    file_prefix = f"download_{chat_id}"
    ydl_opts = {
        'outtmpl': f'{file_prefix}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        # تقييد الدقة والحجم لضمان عدم تجاوز حدود تيليجرام (أقل من 45MB)
        'format': 'best[height<=720][filesize<45M]/best[height<=480]/best[ext=mp4]/best',
    }

    if call.data == "audio":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if call.data == "audio":
                filename = os.path.splitext(filename)[0] + ".mp3"

        if not os.path.exists(filename):
            raise Exception("فشل حفظ الملف على الخادم.")

        file_size_mb = os.path.getsize(filename) / (1024 * 1024)
        if file_size_mb > 50:
            bot.edit_message_text(f"❌ حجم الفيديو ({file_size_mb:.1f}MB) أكبر من الحد المسموح به في تيليجرام (50MB).", chat_id, call.message.message_id)
            os.remove(filename)
            return

        bot.edit_message_text("📤 جاري إرسال الملف إليك...", chat_id, call.message.message_id)

        with open(filename, 'rb') as file:
            if call.data == "audio":
                bot.send_audio(chat_id, file)
            else:
                bot.send_video(chat_id, file)

        os.remove(filename)
        bot.delete_message(chat_id, call.message.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ حدث خطأ أثناء التحميل.\nالتفاصيل: {str(e)[:100]}", chat_id, call.message.message_id)
        for f in os.listdir():
            if f.startswith(file_prefix):
                try:
                    os.remove(f)
                except:
                    pass

# --- تشغيل السيرفر الوهمي والبوت معاً ---
if __name__ == "__main__":
    keep_alive()
    print("البوت يعمل الآن...")
    bot.infinity_polling()
