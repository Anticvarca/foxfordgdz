import asyncio
import io
import os
import logging
import traceback
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# --- Логирование в stdout, чтобы BotHost показывал ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")

# --- Импорт solver с диагностикой ---
try:
    from solver import solve_text, solve_image
    log.info("solver.py успешно импортирован")
except Exception as e:
    log.error("ОШИБКА импорта solver.py: %s", e)
    traceback.print_exc()
    raise

# --- Токен из переменных окружения BotHost ---
TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or ""
).strip()

log.info("TELEGRAM_TOKEN найден: %s", bool(TELEGRAM_TOKEN))
log.info("GIGACHAT_CREDENTIALS найден: %s", bool(os.getenv("GIGACHAT_CREDENTIALS")))

if not TELEGRAM_TOKEN:
    raise SystemExit("Токен не задан в переменных окружения BotHost")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
user_subject: dict[int, str] = {}


def subject_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🧮 Математика", callback_data="subj:math"),
        InlineKeyboardButton(text="📜 История", callback_data="subj:history"),
        InlineKeyboardButton(text="💬 Общее", callback_data="subj:general"),
    ]])


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_subject[message.from_user.id] = "general"
    await message.answer(
        "Привет! Кинь текст или фото задачи — я дам ответ.\nВыбери предмет:",
        reply_markup=subject_kb(),
    )


@dp.message(Command("subject"))
async def cmd_subject(message: types.Message):
    await message.answer("Выбери предмет:", reply_markup=subject_kb())


@dp.callback_query(F.data.startswith("subj:"))
async def cb_subject(call: CallbackQuery):
    subj = call.data.split(":")[1]
    user_subject[call.from_user.id] = subj
    names = {"math": "Математика", "history": "История", "general": "Общее"}
    await call.message.edit_text(f"Режим: {names[subj]}")
    await call.answer()


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    await message.answer("Обрабатываю фото...")
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)

        subj = user_subject.get(message.from_user.id, "general")
        answer = await solve_image(buf.getvalue(), message.caption or "", subj)
    except Exception as e:
        log.error("Ошибка обработки фото: %s", e)
        traceback.print_exc()
        await message.answer(f"Ошибка: {e}")
        return
    await send_long(message, answer)


@dp.message(F.text)
async def handle_text(message: types.Message):
    subj = user_subject.get(message.from_user.id, "general")
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        answer = await solve_text(message.text, subj)
    except Exception as e:
        log.error("Ошибка обработки текста: %s", e)
        traceback.print_exc()
        await message.answer(f"Ошибка: {e}")
        return
    await send_long(message, answer)


async def send_long(message: types.Message, text: str):
    for i in range(0, len(text), 4000):
        await message.answer(text[i:i + 4000])


async def main():
    log.info("Запускаю polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        log.error("Бот упал: %s", e)
        traceback.print_exc()
        raise
