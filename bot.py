import asyncio
import io
import json
import os
import re
import logging
import traceback
from pathlib import Path
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from PIL import Image

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
log.info("NVIDIA_API_KEY найден: %s", bool(os.getenv("NVIDIA_API_KEY")))

if not TELEGRAM_TOKEN:
    raise SystemExit("Токен не задан в переменных окружения BotHost")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# --- Сохранение режимов в /app/data ---
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SUBJECTS_FILE = DATA_DIR / "subjects.json"


def load_subjects() -> dict:
    if SUBJECTS_FILE.exists():
        try:
            data = json.loads(SUBJECTS_FILE.read_text(encoding="utf-8"))
            result = {int(k): v for k, v in data.items()}
            log.info("Загружено режимов из файла: %d", len(result))
            return result
        except Exception as e:
            log.warning("Не смог прочитать subjects.json: %s", e)
    return {}


def save_subjects() -> None:
    try:
        SUBJECTS_FILE.write_text(
            json.dumps({str(k): v for k, v in user_subject.items()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("Не смог сохранить subjects.json: %s", e)


user_subject: dict = load_subjects()

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

# Регулярка для вопросов про режим
MODE_PATTERN = re.compile(
    r"(режим|предмет|урок|класс|работа\w*|сто\w*|выбран|текущ)",
    re.IGNORECASE,
)


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
    log.info("Команда /start от user_id=%s", message.from_user.id)
    if message.from_user.id not in user_subject:
        user_subject[message.from_user.id] = "general"
        save_subjects()
    current = SUBJECT_NAMES.get(user_subject[message.from_user.id], "💬 Общее")
    await message.answer(
        f"Привет! Текущий режим: {current}\n"
        "Выбери предмет кнопкой ниже или сразу кидай задачу (текстом или фото).",
        reply_markup=subject_kb(),
    )


@dp.message(Command("subject"))
async def cmd_subject(message: types.Message):
    log.info("Команда /subject от user_id=%s", message.from_user.id)
    current = user_subject.get(message.from_user.id, "general")
    current_name = SUBJECT_NAMES.get(current, "💬 Общее")
    await message.answer(
        f"Сейчас выбран: {current_name}\nВыбери новый:",
        reply_markup=subject_kb(),
    )


@dp.callback_query(F.data.startswith("subj:"))
async def cb_subject(call: CallbackQuery):
    subj = call.data.split(":", 1)[1]
    user_subject[call.from_user.id] = subj
    save_subjects()
    name = SUBJECT_NAMES.get(subj, subj)
    log.info("Пользователь %s выбрал режим: %s (%s)", call.from_user.id, subj, name)
    await call.message.edit_text(f"Режим: {name}\nКидай задачу.")
    await call.answer()


# --- ВАЖНО: этот обработчик ДО handle_text, чтобы перехватывать вопросы про режим ---
@dp.message(lambda m: m.text and MODE_PATTERN.search(m.text))
async def handle_mode_question(message: types.Message):
    subj = user_subject.get(message.from_user.id, "general")
    mode_name = SUBJECT_NAMES.get(subj, subj)
    log.info("handle_mode_question: user_id=%s, режим=%s", message.from_user.id, subj)
    await message.answer(
        f"Сейчас активен режим: {mode_name}\n"
        "Сменить — /subject",
        reply_markup=subject_kb(),
    )


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    subj = user_subject.get(message.from_user.id, "general")
    mode_name = SUBJECT_NAMES.get(subj, subj)
    log.info("handle_photo: user_id=%s, режим=%s", message.from_user.id, subj)
    await message.answer(f"Решаю... (режим: {mode_name})")
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        original_size = buf.getbuffer().nbytes

        # --- Сжимаем фото перед отправкой ---
        buf.seek(0)
        img = Image.open(buf)
        img = img.convert("RGB")
        max_width = 1024
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=75, optimize=True)
        compressed = out.getvalue()
        log.info("Фото сжато: %d → %d байт", original_size, len(compressed))

        answer = await solve_image(compressed, message.caption or "", subj)
    except Exception as e:
        log.error("Ошибка обработки фото: %s", e)
        traceback.print_exc()
        await message.answer(f"Ошибка: {e}")
        return
    await send_long(message, answer)


@dp.message(F.text)
async def handle_text(message: types.Message):
    subj = user_subject.get(message.from_user.id, "general")
    log.info("handle_text: user_id=%s, режим=%s, текст=%r",
             message.from_user.id, subj, message.text[:80])
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
