import asyncio
import io
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from solver import solve_text, solve_image

# --- Читаем переменные окружения, которые заданы в панели BotHost ---
# .strip() убирает случайные пробелы и переносы строк, которые добавляет панель
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

if not TELEGRAM_TOKEN:
    raise SystemExit("Переменная TELEGRAM_TOKEN не задана в панели BotHost!")

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
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, buf)

    subj = user_subject.get(message.from_user.id, "general")
    try:
        answer = await solve_image(buf.getvalue(), message.caption or "", subj)
    except Exception as e:
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
        await message.answer(f"Ошибка: {e}")
        return
    await send_long(message, answer)


async def send_long(message: types.Message, text: str):
    for i in range(0, len(text), 4000):
        await message.answer(text[i:i + 4000])


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
