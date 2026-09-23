import asyncio
import io
import json
import os
import re
import logging
import secrets
import string
import traceback
from datetime import datetime
from pathlib import Path
from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
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

ADMIN_ID = int(os.getenv("ADMIN_ID", "6112132988"))

log.info("TELEGRAM_TOKEN найден: %s", bool(TELEGRAM_TOKEN))
log.info("OPENAI_API_KEY найден: %s", bool(os.getenv("OPENAI_API_KEY")))
log.info("ADMIN_ID=%s", ADMIN_ID)

if not TELEGRAM_TOKEN:
    raise SystemExit("Токен не задан в переменных окружения BotHost")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ============ ФАЙЛЫ ДАННЫХ ============
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
AUTHORIZED_FILE = DATA_DIR / "authorized.json"
STATS_FILE = DATA_DIR / "stats.json"
SUBJECTS_FILE = DATA_DIR / "subjects.json"
PASSWORDS_FILE = DATA_DIR / "passwords.json"
BLOCKED_FILE = DATA_DIR / "blocked.json"


def _load(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Не смог прочитать %s: %s", path, e)
    return default


def _save(path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Не смог сохранить %s: %s", path, e)


authorized: set = set(_load(AUTHORIZED_FILE, []))
stats: dict = _load(STATS_FILE, {})
user_subject: dict = {int(k): v for k, v in _load(SUBJECTS_FILE, {}).items()}
passwords: dict = _load(PASSWORDS_FILE, {})
blocked: set = set(_load(BLOCKED_FILE, []))

log.info("Загружено: авторизовано=%d, в статистике=%d, режимов=%d, паролей=%d, блок=%d",
         len(authorized), len(stats), len(user_subject), len(passwords), len(blocked))


def save_authorized():
    _save(AUTHORIZED_FILE, list(authorized))


def save_stats():
    _save(STATS_FILE, stats)


def save_subjects():
    _save(SUBJECTS_FILE, {str(k): v for k, v in user_subject.items()})


def save_passwords():
    _save(PASSWORDS_FILE, passwords)


def save_blocked():
    _save(BLOCKED_FILE, list(blocked))


def touch_user(user, task_type=None):
    uid = str(user.id)
    now = datetime.now().isoformat(timespec="seconds")
    if uid not in stats:
        stats[uid] = {
            "username": user.username or user.full_name,
            "full_name": user.full_name,
            "first_seen": now,
            "last_seen": now,
            "tasks": 0,
            "texts": 0,
            "photos": 0,
        }
    stats[uid]["last_seen"] = now
    stats[uid]["username"] = user.username or user.full_name
    if task_type == "text":
        stats[uid]["texts"] = stats[uid].get("texts", 0) + 1
        stats[uid]["tasks"] = stats[uid].get("tasks", 0) + 1
    elif task_type == "photo":
        stats[uid]["photos"] = stats[uid].get("photos", 0) + 1
        stats[uid]["tasks"] = stats[uid].get("tasks", 0) + 1
    save_stats()


def generate_password(prefix: str = "") -> str:
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
    if prefix:
        prefix = re.sub(r"[^A-Z0-9]", "", prefix.upper())[:8]
        part = "".join(secrets.choice(alphabet) for _ in range(4))
        return f"{prefix}-{part}"
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "-".join(parts)


def find_password_by_value(value: str):
    value = value.strip().upper()
    for pwd, info in passwords.items():
        if pwd.upper() == value:
            return pwd, info
    return None, None


def user_has_password_activated(user_id: int):
    for pwd, info in passwords.items():
        if info.get("used_by") == user_id:
            return pwd
    return None


# ============ СОСТОЯНИЯ АДМИНА ============
admin_states: dict = {}   # {admin_id: "awaiting_password_to_delete"}


# ============ АУТЕНТИФИКАЦИЯ ============
class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, types.Message):
            return await handler(event, data)
        user_id = event.from_user.id

        if user_id == ADMIN_ID:
            return await handler(event, data)

        # Заблокированные — блокируем сразу, даже если в authorized
        if user_id in blocked:
            await event.answer(
                "🚫 Ваш доступ заблокирован.\n"
                "Если хотите продлить — свяжитесь с продавцом."
            )
            return

        if user_id in authorized:
            return await handler(event, data)

        # Проверяем, не пароль ли ввёл
        if event.text:
            pwd_key, pwd_info = find_password_by_value(event.text)
            if pwd_key:
                if pwd_info.get("used_by") is None:
                    pwd_info["used_by"] = user_id
                    pwd_info["used_at"] = datetime.now().isoformat(timespec="seconds")
                    pwd_info["used_by_name"] = event.from_user.full_name
                    pwd_info["used_by_username"] = event.from_user.username or ""
                    save_passwords()
                    authorized.add(user_id)
                    save_authorized()
                    touch_user(event.from_user)
                    log.info("Пароль %s активирован юзером %s", pwd_key, user_id)
                    await event.answer(
                        "✅ Пароль активирован! Добро пожаловать.\n"
                        "Выбери предмет и кинь задачу.",
                        reply_markup=subject_kb(),
                    )
                    return
                elif pwd_info.get("used_by") == user_id:
                    authorized.add(user_id)
                    save_authorized()
                    await event.answer("✅ Доступ восстановлен.")
                    return
                else:
                    await event.answer(
                        "❌ Этот пароль уже активирован другим пользователем.\n"
                        "Если вы купили пароль — напишите продавцу."
                    )
                    return

        await event.answer(
            "🔒 Введи пароль для доступа к боту.\n"
            "Если у тебя нет пароля — напиши продавцу."
        )


# ============ МЕНЮ ============
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


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔑 Сгенерировать 1", callback_data="admin:gen1"),
            InlineKeyboardButton(text="🔑🔑 5 паролей", callback_data="admin:gen5"),
        ],
        [
            InlineKeyboardButton(text="📋 Список паролей", callback_data="admin:list"),
            InlineKeyboardButton(text="🗑 Удалить пароли", callback_data="admin:del_menu"),
        ],
        [
            InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:refresh"),
        ],
    ])


def admin_del_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить конкретный пароль", callback_data="admin:del_one")],
        [InlineKeyboardButton(text="🗑 Удалить все СВОБОДНЫЕ", callback_data="admin:del_free")],
        [InlineKeyboardButton(text="🗑 Удалить все ИСПОЛЬЗОВАННЫЕ", callback_data="admin:del_used")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:refresh")],
    ])


def admin_panel_text() -> str:
    total_pwd = len(passwords)
    used_pwd = sum(1 for v in passwords.values() if v.get("used_by"))
    free_pwd = total_pwd - used_pwd
    return (
        f"🎛 Админ-панель\n\n"
        f"🔑 Паролей: {total_pwd}\n"
        f"   🆓 свободных: {free_pwd}\n"
        f"   ✅ использованных: {used_pwd}\n"
        f"👥 Авторизованных: {len(authorized)}\n"
        f"🚫 Заблокированных: {len(blocked)}"
    )


dp.message.middleware(AuthMiddleware())


# ============ КОМАНДЫ ============
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id not in user_subject:
        user_subject[message.from_user.id] = "general"
        save_subjects()
    touch_user(message.from_user)
    current = SUBJECT_NAMES.get(user_subject[message.from_user.id], "💬 Общее")
    await message.answer(
        f"Привет! Текущий режим: {current}\n"
        "Выбери предмет или сразу кидай задачу (текстом или фото).",
        reply_markup=subject_kb(),
    )


@dp.message(Command("subject"))
async def cmd_subject(message: types.Message):
    current = user_subject.get(message.from_user.id, "general")
    current_name = SUBJECT_NAMES.get(current, "💬 Общее")
    await message.answer(
        f"Сейчас выбран: {current_name}\nВыбери новый:",
        reply_markup=subject_kb(),
    )


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    await message.answer(admin_panel_text(), reply_markup=admin_kb())


# ============ АДМИН-СОСТОЯНИЕ (удаление пароля по тексту) ============
@dp.message(lambda m: m.from_user.id == ADMIN_ID
            and admin_states.get(ADMIN_ID) == "awaiting_password_to_delete"
            and m.text)
async def handle_admin_delete_password(message: types.Message):
    admin_states.pop(ADMIN_ID, None)
    value = (message.text or "").strip().upper()
    pwd_key, info = find_password_by_value(value)
    if not pwd_key:
        await message.answer(f"❌ Пароль `{value}` не найден.")
        return
    used_by = info.get("used_by")
    del passwords[pwd_key]
    save_passwords()
    if used_by:
        authorized.discard(used_by)
        save_authorized()
        await message.answer(
            f"✅ Пароль `{pwd_key}` удалён.\n"
            f"🚫 Доступ пользователя {used_by} отозван."
        )
    else:
        await message.answer(f"✅ Свободный пароль `{pwd_key}` удалён.")


# ============ CALLBACK: АДМИН-ПАНЕЛЬ ============
@dp.callback_query(F.data.startswith("admin:"))
async def cb_admin(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("⛔ Только для админа", show_alert=True)
        return

    action = call.data.split(":", 1)[1]

    if action == "gen1":
        pwd = generate_password()
        while pwd in passwords:
            pwd = generate_password()
        passwords[pwd] = {
            "created": datetime.now().isoformat(timespec="seconds"),
            "used_by": None, "used_at": None, "note": "",
        }
        save_passwords()
        await call.message.answer(
            f"🔑 Новый пароль:\n\n`{pwd}`\n\n"
            f"Скопируй и продай покупателю. Активируется один раз.",
            parse_mode="Markdown",
        )
        await call.answer("Создан")

    elif action == "gen5":
        new_pwds = []
        for _ in range(5):
            pwd = generate_password()
            while pwd in passwords:
                pwd = generate_password()
            passwords[pwd] = {
                "created": datetime.now().isoformat(timespec="seconds"),
                "used_by": None, "used_at": None, "note": "",
            }
            new_pwds.append(pwd)
        save_passwords()
        await call.message.answer(
            "🔑 5 новых паролей:\n\n" + "\n".join(f"`{p}`" for p in new_pwds),
            parse_mode="Markdown",
        )
        await call.answer("Создано 5")

    elif action == "list":
        if not passwords:
            await call.message.answer("Паролей пока нет.")
            await call.answer()
            return
        lines = ["📋 Список паролей:\n"]
        free = [p for p, v in passwords.items() if not v.get("used_by")]
        used = [(p, v) for p, v in passwords.items() if v.get("used_by")]
        if free:
            lines.append(f"🆓 Свободные ({len(free)}):")
            for p in free:
                lines.append(f"   `{p}`")
            lines.append("")
        if used:
            lines.append(f"✅ Использованные ({len(used)}):")
            for p, v in used:
                name = v.get("used_by_name", "?")
                uname = v.get("used_by_username", "")
                uname_str = f" @{uname}" if uname else ""
                lines.append(f"   `{p}` → {name}{uname_str}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n... (обрезано)"
        await call.message.answer(text, parse_mode="Markdown")
        await call.answer()

    elif action == "del_menu":
        await call.message.answer(
            "🗑 Управление паролями:",
            reply_markup=admin_del_kb(),
        )
        await call.answer()

    elif action == "del_one":
        admin_states[ADMIN_ID] = "awaiting_password_to_delete"
        await call.message.answer(
            "Отправь пароль, который нужно удалить (одним сообщением):"
        )
        await call.answer()

    elif action == "del_free":
        free = [p for p, v in passwords.items() if not v.get("used_by")]
        for p in free:
            del passwords[p]
        save_passwords()
        await call.message.answer(f"🗑 Удалено свободных паролей: {len(free)}")
        await call.answer()

    elif action == "del_used":
        used = [(p, v) for p, v in passwords.items() if v.get("used_by")]
        count = len(used)
        revoked = 0
        for p, v in used:
            uid = v.get("used_by")
            if uid:
                authorized.discard(uid)
                revoked += 1
            del passwords[p]
        save_passwords()
        save_authorized()
        await call.message.answer(
            f"🗑 Удалено использованных паролей: {count}\n"
            f"🚫 Отозван доступ у {revoked} пользователей."
        )
        await call.answer()

    elif action == "stats":
        total_tasks = sum(s.get("tasks", 0) for s in stats.values())
        total_texts = sum(s.get("texts", 0) for s in stats.values())
        total_photos = sum(s.get("photos", 0) for s in stats.values())
        used_pwd = sum(1 for v in passwords.values() if v.get("used_by"))
        lines = [
            "📊 Статистика бота",
            "",
            f"🔑 Паролей: {len(passwords)} (использовано {used_pwd})",
            f"👥 Авторизованных: {len(authorized)}",
            f"🚫 Заблокированных: {len(blocked)}",
            f"✅ Всего задач: {total_tasks}",
            f"   📝 текстом: {total_texts}",
            f"   📷 фото: {total_photos}",
            "",
            "📋 Топ-10 по задачам:",
        ]
        sorted_users = sorted(stats.items(), key=lambda x: x[1].get("tasks", 0), reverse=True)[:10]
        for i, (uid, s) in enumerate(sorted_users, 1):
            uname = s.get("username", "?")
            lines.append(f"{i}. @{uname} (id {uid}): {s.get('tasks', 0)} задач")
        await call.message.answer("\n".join(lines))
        await call.answer()

    elif action == "users":
        if not stats:
            await call.message.answer("Пока никого нет.")
            await call.answer()
            return
        # Сортируем по дате последнего визита
        items = sorted(stats.items(),
                       key=lambda x: x[1].get("last_seen", ""),
                       reverse=True)[:30]
        rows = []
        for uid, s in items:
            uid_int = int(uid)
            uname = s.get("username", "?")
            tasks = s.get("tasks", 0)
            if uid_int in blocked:
                status = "🚫"
            elif uid_int in authorized or uid_int == ADMIN_ID:
                status = "✅"
            else:
                status = "❌"
            label = f"{status} @{uname} · {tasks} задач"
            rows.append([InlineKeyboardButton(
                text=label[:60],
                callback_data=f"usr:info:{uid_int}",
            )])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:refresh")])
        await call.message.answer(
            "👥 Пользователи (кликни для действий):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await call.answer()

    elif action == "refresh":
        try:
            await call.message.edit_text(admin_panel_text(), reply_markup=admin_kb())
        except Exception:
            await call.message.answer(admin_panel_text(), reply_markup=admin_kb())
        await call.answer("Обновлено")


# ============ CALLBACK: ИНФО О ПОЛЬЗОВАТЕЛЕ ============
@dp.callback_query(F.data.startswith("usr:"))
async def cb_user_action(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("⛔", show_alert=True)
        return

    parts = call.data.split(":")
    action = parts[1]
    uid = int(parts[2])

    if action == "info":
        s = stats.get(str(uid), {})
        uname = s.get("username", "?")
        full_name = s.get("full_name", "?")
        tasks = s.get("tasks", 0)
        texts = s.get("texts", 0)
        photos = s.get("photos", 0)
        first_seen = s.get("first_seen", "?")[:10]
        last_seen = s.get("last_seen", "?")[:10]
        pwd = user_has_password_activated(uid) or "нет"
        is_blocked = uid in blocked
        is_auth = uid in authorized

        if is_blocked:
            status_txt = "🚫 ЗАБЛОКИРОВАН"
        elif is_auth:
            status_txt = "✅ Авторизован"
        else:
            status_txt = "❌ Не авторизован"

        text = (
            f"👤 Пользователь\n\n"
            f"ID: {uid}\n"
            f"Имя: {full_name}\n"
            f"Username: @{uname}\n"
            f"Статус: {status_txt}\n"
            f"Пароль: {pwd}\n\n"
            f"📊 Задач: {tasks} (текст {texts}, фото {photos})\n"
            f"Первый раз: {first_seen}\n"
            f"Последний: {last_seen}"
        )

        buttons = []
        if is_blocked:
            buttons.append(InlineKeyboardButton(
                text="✅ Разблокировать", callback_data=f"usr:unblock:{uid}"))
        else:
            buttons.append(InlineKeyboardButton(
                text="🚫 Заблокировать", callback_data=f"usr:block:{uid}"))
        buttons.append(InlineKeyboardButton(
            text="🗑 Удалить из базы", callback_data=f"usr:delete:{uid}"))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            buttons,
            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="admin:users")],
        ])
        await call.message.answer(text, reply_markup=kb)
        await call.answer()

    elif action == "block":
        blocked.add(uid)
        save_blocked()
        authorized.discard(uid)
        save_authorized()
        log.info("Юзер %s заблокирован", uid)
        await call.message.answer(f"🚫 Пользователь {uid} заблокирован. Доступ отозван.")
        await call.answer("Заблокирован")

    elif action == "unblock":
        blocked.discard(uid)
        save_blocked()
        pwd = user_has_password_activated(uid)
        if pwd:
            authorized.add(uid)
            save_authorized()
        log.info("Юзер %s разблокирован", uid)
        await call.message.answer(
            f"✅ Пользователь {uid} разблокирован.\n"
            f"{'Доступ восстановлен.' if pwd else 'Пароль не найден — доступ не выдан.'}"
        )
        await call.answer("Разблокирован")

    elif action == "delete":
        blocked.discard(uid)
        authorized.discard(uid)
        stats.pop(str(uid), None)
        for pwd, info in list(passwords.items()):
            if info.get("used_by") == uid:
                del passwords[pwd]
        save_blocked()
        save_authorized()
        save_stats()
        save_passwords()
        log.info("Юзер %s удалён из базы", uid)
        await call.message.answer(f"🗑 Пользователь {uid} удалён из базы.")
        await call.answer("Удалён")


# ============ CALLBACK: ВЫБОР ПРЕДМЕТА ============
@dp.callback_query(F.data.startswith("subj:"))
async def cb_subject(call: CallbackQuery):
    subj = call.data.split(":", 1)[1]
    user_subject[call.from_user.id] = subj
    save_subjects()
    name = SUBJECT_NAMES.get(subj, subj)
    log.info("Пользователь %s выбрал режим: %s", call.from_user.id, subj)
    await call.message.edit_text(f"Режим: {name}\nКидай задачу.")
    await call.answer()


# ============ ВОПРОС ПРО РЕЖИМ ============
@dp.message(lambda m: m.text and MODE_PATTERN.search(m.text))
async def handle_mode_question(message: types.Message):
    subj = user_subject.get(message.from_user.id, "general")
    mode_name = SUBJECT_NAMES.get(subj, subj)
    await message.answer(
        f"Сейчас активен режим: {mode_name}\nСменить — /subject",
        reply_markup=subject_kb(),
    )


# ============ ФОТО ============
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
        touch_user(message.from_user, task_type="photo")
    except Exception as e:
        log.error("Ошибка обработки фото: %s", e)
        traceback.print_exc()
        await message.answer(f"Ошибка: {e}")
        return
    await send_long(message, answer)


# ============ ТЕКСТ ============
@dp.message(F.text)
async def handle_text(message: types.Message):
    subj = user_subject.get(message.from_user.id, "general")
    log.info("handle_text: user_id=%s, режим=%s, текст=%r",
             message.from_user.id, subj, message.text[:80])
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        answer = await solve_text(message.text, subj)
        touch_user(message.from_user, task_type="text")
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
