import asyncio
import io
import os
import logging
import traceback
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")

try:
    from solver import solve_text, solve_image
    log.info("solver.py успешно импортирован")
except Exception as e:
    log.error("ОШИБКА импорта solver.py: %s", e)
    traceback.print_exc()
    raise

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or ""
).strip()

log.info("TELEGRAM_TOKEN найден: %s", bool(TELEGRAM_TOKEN))
log.info("OPENAI_API_KEY найден: %s", bool(os.getenv("OPENAI_API_KEY")))

if not TELEGRAM_TOKEN:
    raise SystemExit("Токен не задан в переменных окружения BotHost")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

user_subject: dict[int, str] = {}

SUBJECTS = [
    ("🧮 Алгебра", "algebra"),
    ("📐 Геометрия", "geometry"),
    ("🇷🇺 Русский", "russian"),
    ("📖 Литература", "literature"),
    ("📜 История", "history"),
    ("🌍 География", "geography"),
    ("🧬 Биология", "biology"),
    ("⚡ Физика", "physics"),
    ("💻 Информатика", "cs"),
    ("🇬🇧 Английский", "english"),
    ("🧪 Химия", "chemistry"),
    ("💬 Общее", "general"),
]

SUBJECT_NAMES = {code: name for name, code in SUBJECTS}
SUBJECT_NAMES["general"] = "💬 Общее"


def subject_kb():
    rows = []
    for i in range(0, len(SUBJECTS), 3):
        row = [
            InlineKeyboardButton(text=name, callback_data=f"subj:{code}")
            for name, code in SUBJECTS[i:i + 3]
        ]
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_subject[message.from_user.id] = "general"
    await message.answer(
        "Привет! Выбери предмет, потом кидай задачу (текстом или фото).\n"
        "Отвечу по делу, с решением и ответом.",
        reply_markup=subject_kb(),
    )


@dp.message(Command("subject"))
async def cmd_subject(message: types.Message):
    await message.answer("Выбери предмет:", reply_markup=subject_kb())


@dp.callback_query(F.data.startswith("subj:"))
async def cb_subject(call: CallbackQuery):
    subj = call.data.split(":", 1)[1]
    user_subject[call.from_user.id] = subj
    name = SUBJECT_NAMES.get(subj, subj)
    await call.message.edit_text(f"Режим: {name}\nКидай задачу.")
    await call.answer()


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    await message.answer("Решаю...")
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
