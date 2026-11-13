import os
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
    bot.reply_to(message, "أهلاً بك معك سِنآل! 👋\nأرسل لي رابط الفيديو (تيك توك، يوتيوب، إنستغرام، X) وسأقوم بتحميله لك بدون علامة مائية.")

@bot.message_handler(func=lambda message: message.text and message.text.startswith("http"))
def handle_url(message):
    chat_id = message.chat.id
    user_links[chat_id] = message.text.strip()
    
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("🎬 فيديو 1080p", callback_data="vid_1080"),
        InlineKeyboardButton("🎬 فيديو 720p", callback_data="vid_720"),
        InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data="audio")
    )
    
    bot.reply_to(message, "اختر صيغة ودقة التحميل المطلوبة:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    url = user_links.get(chat_id)
    
    if not url:
        bot.answer_callback_query(call.id, "انتهت صلاحية الرابط، أرسله مجدداً.")
        return

    bot.edit_message_text("⏳ جاري التحميل والمعالجة... يرجى الانتظار.", chat_id, call.message.message_id)
    
    file_prefix = f"download_{chat_id}"
    ydl_opts = {
        'outtmpl': f'{file_prefix}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }

    if call.data == "vid_1080":
        ydl_opts['format'] = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best'
    elif call.data == "vid_720":
        ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best'
    elif call.data == "audio":
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

        bot.edit_message_text("📤 جاري إرسال الملف إليك...", chat_id, call.message.message_id)

        with open(filename, 'rb') as file:
            if call.data == "audio":
                bot.send_audio(chat_id, file)
            else:
                bot.send_video(chat_id, file)

        os.remove(filename)
        bot.delete_message(chat_id, call.message.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ حدث خطأ أثناء التحميل. تأكد أن الرابط عام وليس خاصاً.\nالتفاصيل: {str(e)[:100]}", chat_id, call.message.message_id)
        for f in os.listdir():
            if f.startswith(file_prefix):
                os.remove(f)

# --- تشغيل السيرفر الوهمي والبوت معاً ---
if __name__ == "__main__":
    keep_alive()
    print("البوت يعمل الآن...")
    bot.infinity_polling()
