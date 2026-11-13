
import os
import time
import shutil
import logging
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import telebot
import yt_dlp
from flask import Flask
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


# ==============================
# Configuration
# ==============================

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "8080"))

# Safe margin below the usual 50 MB Bot API upload limit
UPLOAD_LIMIT = 49 * 1024 * 1024

# Warn users when the estimated download is above this size
LARGE_FILE_LIMIT = 100 * 1024 * 1024

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sinal")
logger.info("yt-dlp version: %s", yt_dlp.version.__version__)

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Per-user state
sessions = {}
sessions_lock = threading.Lock()

# Limit simultaneous downloads to protect server resources
download_slots = threading.BoundedSemaphore(2)


# ==============================
# Health endpoint
# ==============================

@app.route("/")
def home():
    return "Sinal bot is running", 200


def run_server():
    app.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False
    )


# ==============================
# URL validation
# ==============================

def valid_url(url):
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme in ("https", "http")
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
        )
    except (ValueError, AttributeError):
        return False


# ==============================
# yt-dlp helpers
# ==============================

def get_info(url):
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 25,
        "retries": 2,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def estimated_size(info, quality):
    """
    Return a best-effort size estimate in bytes.
    Exact size is not always available before downloading.
    """
    formats = info.get("formats") or []

    if quality == "audio":
        candidates = [
            f for f in formats
            if f.get("vcodec") == "none"
            and f.get("acodec") != "none"
        ]
    else:
        max_height = {
            "1080": 1080,
            "720": 720,
            "480": 480,
        }.get(quality)

        candidates = [
            f for f in formats
            if f.get("vcodec") != "none"
            and (
                max_height is None
                or not f.get("height")
                or f.get("height") <= max_height
            )
        ]

    if not candidates:
        return info.get("filesize") or info.get("filesize_approx")

    # Pick the highest-quality candidate within the selected limit.
    candidates.sort(
        key=lambda f: (
            f.get("height") or 0,
            f.get("tbr") or 0
        )
    )
    chosen = candidates[-1]

    size = chosen.get("filesize") or chosen.get("filesize_approx")

    # Some sites provide separate audio/video formats.
    if quality != "audio" and chosen.get("acodec") == "none":
        audio_formats = [
            f for f in formats
            if f.get("vcodec") == "none"
            and f.get("acodec") != "none"
        ]

        if audio_formats:
            audio_formats.sort(
                key=lambda f: f.get("tbr") or 0
            )
            audio = audio_formats[-1]
            audio_size = (
                audio.get("filesize")
                or audio.get("filesize_approx")
            )

            if size and audio_size:
                return size + audio_size

    return size


def format_size(size):
    if size is None:
        return "غير معروف"

    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)

    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024


def make_options(folder, quality):
    if quality == "audio":
        fmt = "bestaudio/best"

    elif quality == "1080":
        fmt = (
            "bestvideo[height<=1080]+bestaudio/"
            "best[height<=1080]"
        )

    elif quality == "720":
        fmt = (
            "bestvideo[height<=720]+bestaudio/"
            "best[height<=720]"
        )

    elif quality == "480":
        fmt = (
            "bestvideo[height<=480]+bestaudio/"
            "best[height<=480]"
        )

    else:
        # Best available video and audio, without re-encoding.
        fmt = "bestvideo*+bestaudio/best"

    options = {
        "format": fmt,
        "outtmpl": str(Path(folder) / "%(title).100s-%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "continuedl": True,
        "socket_timeout": 30,
    }

    if quality != "audio":
        options["merge_output_format"] = "mp4"

    else:
        options["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    return options


def find_output(folder, audio=False):
    ignored = {
        ".part", ".ytdl", ".json", ".jpg", ".jpeg",
        ".png", ".webp", ".vtt", ".srt", ".description"
    }

    files = [
        p for p in Path(folder).iterdir()
        if p.is_file() and p.suffix.lower() not in ignored
    ]

    if audio:
        files = [
            p for p in files
            if p.suffix.lower() == ".mp3"
        ]

    if not files:
        raise RuntimeError("Downloaded file was not found")

    return max(files, key=lambda p: p.stat().st_size)


# ==============================
# Quality selection
# ==============================

def quality_keyboard(key, info, large=False):
    markup = InlineKeyboardMarkup(row_width=2)

    qualities = [
        ("original", "🌟 أفضل جودة"),
        ("1080", "🎬 1080p"),
        ("720", "📱 720p"),
        ("audio", "🎵 MP3"),
    ]

    available_heights = {
        f.get("height")
        for f in (info.get("formats") or [])
        if f.get("height")
    }

    # Only show 1080p if the source has it.
    if 1080 not in available_heights:
        qualities = [
            item for item in qualities
            if item[0] != "1080"
        ]

    if not any(h <= 720 for h in available_heights):
        qualities = [
            item for item in qualities
            if item[0] != "720"
        ]

    buttons = [
        InlineKeyboardButton(
            label,
            callback_data=f"get:{key}:{quality}"
        )
        for quality, label in qualities
    ]

    markup.add(*buttons)
    markup.add(
        InlineKeyboardButton(
            "❌ إلغاء",
            callback_data=f"cancel:{key}"
        )
    )
    return markup


# ==============================
# Commands
# ==============================

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "أهلًا بك في سِنآل 👋\n\n"
        "أرسل رابط فيديو عام من منصة مدعومة، "
        "وسأفحص الجودات المتاحة قبل التنزيل."
    )


@bot.message_handler(
    func=lambda m: bool(
        m.text and m.text.strip().startswith(("https://", "http://"))
    )
)
def receive_url(message):
    url = message.text.strip()

    if not valid_url(url):
        bot.reply_to(message, "❌ الرابط غير صالح.")
        return

    key = f"{message.chat.id}_{message.from_user.id}"

    status = bot.reply_to(
        message,
        "🔎 أفحص الرابط والجودات والحجم المتاح..."
    )

    try:
        info = get_info(url)

        title = info.get("title") or "بدون عنوان"
        duration = info.get("duration")
        duration_text = (
            f"{int(duration // 60)} دقيقة"
            if duration is not None else "غير معروف"
        )

        with sessions_lock:
            sessions[key] = {
                "url": url,
                "info": info,
                "status_id": status.message_id,
                "chat_id": message.chat.id,
                "user_id": message.from_user.id,
                "created": time.time(),
                "busy": False,
            }

        markup = quality_keyboard(key, info)

        bot.edit_message_text(
            f"📹 {title[:250]}\n"
            f"⏱ المدة: {duration_text}\n\n"
            "اختر الجودة المطلوبة.\n"
            "الحجم المعروض قد يكون تقديريًا، "
            "وقد تختلف خيارات الجودة حسب المنصة.",
            message.chat.id,
            status.message_id,
            reply_markup=markup
        )

    except Exception:
        logger.exception("Failed to inspect URL")

        bot.edit_message_text(
            "❌ تعذر فحص الرابط.\n"
            "تأكد أنه عام ومدعوم، وحدّث yt-dlp "
            "إذا استمرت المشكلة.",
            message.chat.id,
            status.message_id
        )


# ==============================
# Download callbacks
# ==============================

@bot.callback_query_handler(
    func=lambda c: c.data.startswith(("get:", "cancel:"))
)
def callback(call):
    parts = call.data.split(":")

    if parts[0] == "cancel":
        key = parts[1]

        with sessions_lock:
            session = sessions.get(key)

            if session and session["user_id"] == call.from_user.id:
                sessions.pop(key, None)

        bot.answer_callback_query(call.id, "تم الإلغاء.")
        try:
            bot.edit_message_text(
                "تم إلغاء العملية.",
                call.message.chat.id,
                call.message.message_id
            )
        except Exception:
            pass
        return

    _, key, quality = parts

    if quality not in ("original", "1080", "720", "480", "audio"):
        bot.answer_callback_query(call.id, "خيار غير صالح.")
        return

    with sessions_lock:
        session = sessions.get(key)

        if not session or session["user_id"] != call.from_user.id:
            bot.answer_callback_query(
                call.id,
                "انتهت صلاحية الرابط. أرسله مجددًا."
            )
            return

        if session["busy"]:
            bot.answer_callback_query(
                call.id,
                "هناك عملية تنزيل جارية بالفعل."
            )
            return

        info = session["info"]
        size = estimated_size(info, quality)

        if (
            size is not None
            and size > LARGE_FILE_LIMIT
            and not session.get("large_confirmed")
        ):
            session["pending_quality"] = quality

            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton(
                    "⏳ متابعة التنزيل",
                    callback_data=f"confirm:{key}"
                ),
                InlineKeyboardButton(
                    "↩️ اختيار جودة أخرى",
                    callback_data=f"back:{key}"
                )
            )

            bot.answer_callback_query(call.id)

            bot.edit_message_text(
                "⚠️ الملف كبير الحجم\n\n"
                f"الحجم المتوقع: {format_size(size)}\n"
                "قد يستغرق التنزيل وقتًا أطول، "
                "ويحتاج إلى مساحة تخزين كافية.\n\n"
                "تنبيه: إذا تجاوز الملف حد رفع تيليجرام، "
                "فلن يمكن إرساله عبر واجهة البوت المعتادة.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            return

        session["busy"] = True
        session["large_confirmed"] = False

    bot.answer_callback_query(call.id)

    threading.Thread(
        target=download_and_send,
        args=(key, quality),
        daemon=True
    ).start()


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(("confirm:", "back:"))
)
def confirm_callback(call):
    action, key = call.data.split(":", 1)

    with sessions_lock:
        session = sessions.get(key)

        if not session or session["user_id"] != call.from_user.id:
            bot.answer_callback_query(call.id, "انتهت صلاحية العملية.")
            return

        if action == "back":
            session["large_confirmed"] = False
            info = session["info"]
            markup = quality_keyboard(key, info)
            bot.answer_callback_query(call.id)

            bot.edit_message_text(
                "اختر جودة أخرى:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            return

        if session["busy"]:
            bot.answer_callback_query(call.id, "العملية قيد التنفيذ.")
            return

        quality = session.get("pending_quality", "original")
        session["large_confirmed"] = True
        session["busy"] = True

    bot.answer_callback_query(call.id, "بدأ التنزيل.")

    threading.Thread(
        target=download_and_send,
        args=(key, quality),
        daemon=True
    ).start()


# ==============================
# Download worker
# ==============================

def download_and_send(key, quality):
    with sessions_lock:
        session = sessions.get(key)

    if not session:
        return

    chat_id = session["chat_id"]
    status_id = session["status_id"]
    url = session["url"]

    if not download_slots.acquire(blocking=False):
        with sessions_lock:
            if key in sessions:
                sessions[key]["busy"] = False

        bot.send_message(
            chat_id,
            "الخادم مشغول حاليًا. حاول مرة أخرى بعد قليل."
        )
        return

    try:
        bot.edit_message_text(
            "⏳ جارٍ التنزيل بأعلى جودة متاحة ضمن اختيارك...",
            chat_id,
            status_id
        )

        with tempfile.TemporaryDirectory(prefix="sinal_") as folder:
            with yt_dlp.YoutubeDL(
                make_options(folder, quality)
            ) as ydl:
                ydl.download([url])

            output = find_output(folder, quality == "audio")
            size = output.stat().st_size

            if size > UPLOAD_LIMIT:
                bot.edit_message_text(
                    f"✅ اكتمل التنزيل: {format_size(size)}\n\n"
                    "لكن الملف أكبر من حد الإرسال المتاح "
                    "في إعداد البوت الحالي.\n"
                    "لم أضغطه تلقائيًا حتى لا أخفض الجودة.\n\n"
                    "يمكنك اختيار جودة أقل، أو إعداد "
                    "خادم Telegram Bot API محلي أو تخزين خارجي "
                    "لدعم الملفات الكبيرة.",
                    chat_id,
                    status_id
                )
                return

            bot.edit_message_text(
                f"📤 جارٍ إرسال الملف ({format_size(size)})...",
                chat_id,
                status_id
            )

            with output.open("rb") as file:
                if quality == "audio":
                    bot.send_audio(
                        chat_id,
                        file,
                        timeout=180,
                        title=output.stem[:64]
                    )
                elif output.suffix.lower() == ".mp4":
                    bot.send_video(
                        chat_id,
                        file,
                        timeout=180,
                        supports_streaming=True
                    )
                else:
                    bot.send_document(
                        chat_id,
                        file,
                        timeout=180
                    )

        try:
            bot.delete_message(chat_id, status_id)
        except Exception:
            pass

    except Exception:
        logger.exception("Download or upload failed")

        try:
            bot.edit_message_text(
                "❌ فشل التنزيل أو الإرسال.\n"
                "قد تكون المشكلة في الرابط أو FFmpeg "
                "أو قيود المنصة أو حدود تيليجرام. "
                "راجع سجلات Render لمعرفة التفاصيل.",
                chat_id,
                status_id
            )
        except Exception:
            bot.send_message(chat_id, "❌ تعذر إكمال العملية.")

    finally:
        download_slots.release()

        with sessions_lock:
            if key in sessions:
                sessions[key]["busy"] = False
                sessions[key]["large_confirmed"] = False



# ==============================
# Startup
# ==============================

if __name__ == "__main__":
    threading.Thread(
        target=run_server,
        daemon=True
    ).start()

    logger.info("Sinal is starting Telegram polling")

    try:
        bot.infinity_polling(
            skip_pending=True,
            timeout=30,
            long_polling_timeout=30
        )
    except Exception:
        logger.exception("Telegram polling stopped unexpectedly")
        raise
