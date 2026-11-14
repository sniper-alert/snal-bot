import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    FSInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_TOKEN
from downloader import (
    download, cleanup, extract_url, is_supported,
    fmt_duration, fmt_size, DownloadError, MAX_HEIGHT,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger('bot')

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

pending = {}


WELCOME = (
    '<b>Video Downloader</b>\n\n'
    'Send me a link — I will download the video and send it back.\n\n'
    'Supported:\n'
    '• YouTube\n'
    '• TikTok\n'
    '• Instagram\n'
    '• Twitter / X\n\n'
    'Max quality: 720p\n'
    'Max file size: 49 MB\n\n'
    'Commands:\n'
    '/start — this message\n'
    '/help — same as /start\n'
    '/cancel — clear pending link'
)


def quality_kb():
    b = InlineKeyboardBuilder()
    b.button(text='360p', callback_data='q:360')
    b.button(text='480p', callback_data='q:480')
    b.button(text='720p', callback_data='q:720')
    b.button(text='Cancel', callback_data='q:cancel')
    b.adjust(3, 1)
    return b.as_markup()


@dp.message(Command('start'))
@dp.message(Command('help'))
async def cmd_start(message: Message):
    await message.answer(WELCOME)


@dp.message(Command('cancel'))
async def cmd_cancel(message: Message):
    pending.pop(message.from_user.id, None)
    await message.answer('Pending link cleared.')


@dp.message(F.text)
async def on_text(message: Message):
    url = extract_url(message.text)

    if not url:
        await message.answer('Send me a video link or /help.')
        return

    if not is_supported(url):
        await message.answer(
            'This site is not in the supported list.\n'
            'Send /help to see what works.'
        )
        return

    pending[message.from_user.id] = url
    await message.answer(
        f'Link received.\nChoose quality (max {MAX_HEIGHT}p):',
        reply_markup=quality_kb(),
    )


@dp.callback_query(F.data.startswith('q:'))
async def on_quality(cb: CallbackQuery):
    user_id = cb.from_user.id
    choice = cb.data.split(':', 1)[1]

    if choice == 'cancel':
        pending.pop(user_id, None)
        await cb.message.edit_text('Cancelled.')
        await cb.answer()
        return

    url = pending.pop(user_id, None)
    if not url:
        await cb.message.edit_text('Link expired. Send it again.')
        await cb.answer()
        return

    try:
        height = int(choice)
    except ValueError:
        height = 720

    await cb.answer(f'Downloading {height}p...')
    try:
        await cb.message.edit_text(f'⏳ Downloading ({height}p)...')
    except Exception:
        pass
    await _run_download(cb.message, url, user_id, height)


async def _run_download(message: Message, url: str, user_id: int, height: int):
    chat_id = message.chat.id

    try:
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
    except Exception:
        pass

    try:
        result = await download(url, user_id, message.message_id, max_height=height)
    except DownloadError as e:
        log.error('download error: %s', e)
        try:
            await bot.send_message(chat_id, f'❌ {e}')
        except Exception:
            log.exception('failed to send error')
        return
    except Exception as e:
        log.exception('download crashed')
        try:
            await bot.send_message(chat_id, f'❌ unexpected: {str(e)[:300]}')
        except Exception:
            log.exception('failed to send error')
        return

    path = result['path']
    caption = (
        f"<b>{result['title'][:180]}</b>\n"
        f"Duration: {fmt_duration(result['duration'])}\n"
        f"Size: {fmt_size(result['size'])}"
    )

    try:
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
        video = FSInputFile(path)
        await bot.send_video(
            chat_id=chat_id,
            video=video,
            caption=caption,
            supports_streaming=True,
            request_timeout=120,
        )
    except Exception as e:
        log.exception('upload failed')
        try:
            await bot.send_message(chat_id, f'❌ upload failed: {str(e)[:300]}')
        except Exception:
            log.exception('failed to send upload error')
    finally:
        cleanup(path)


async def main():
    log.info('bot starting')
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info('bot stopped')
