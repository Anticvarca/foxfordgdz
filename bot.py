import asyncio
import io
import json
import os
import re
import logging
import secrets
import string
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    LabeledPrice, PreCheckoutQuery
)
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
PRICE_STARS = int(os.getenv("PRICE_STARS", "60"))
SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))

log.info("TELEGRAM_TOKEN найден: %s", bool(TELEGRAM_TOKEN))
log.info("OPENAI_API_KEY найден: %s", bool(os.getenv("OPENAI_API_KEY")))
log.info("ADMIN_ID=%s", ADMIN_ID)
log.info("PRICE_STARS=%s", PRICE_STARS)
log.info("SUBSCRIPTION_DAYS=%s", SUBSCRIPTION_DAYS)

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
TICKETS_FILE = DATA_DIR / "tickets.json"
PAYMENTS_FILE = DATA_DIR / "payments.json"

INFINITE = "infinite"


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


raw_authorized = _load(AUTHORIZED_FILE, {})
if isinstance(raw_authorized, list):
    now_iso = (datetime.now() + timedelta(days=SUBSCRIPTION_DAYS)).isoformat(timespec="seconds")
    authorized: dict = {int(uid): now_iso for uid in raw_authorized}
    _save(AUTHORIZED_FILE, {str(k): v for k, v in authorized.items()})
else:
    authorized: dict = {int(k): v for k, v in raw_authorized.items()}

stats: dict = _load(STATS_FILE, {})
user_subject: dict = {int(k): v for k, v in _load(SUBJECTS_FILE, {}).items()}
passwords: dict = _load(PASSWORDS_FILE, {})
blocked: set = set(_load(BLOCKED_FILE, []))
tickets: dict = _load(TICKETS_FILE, {})
payments: dict = _load(PAYMENTS_FILE, {})

log.info("Загружено: подписок=%d, в статистике=%d, режимов=%d, паролей=%d, блок=%d, тикетов=%d, платежей=%d",
         len(authorized), len(stats), len(user_subject), len(passwords),
         len(blocked), len(tickets), len(payments))


def save_authorized():
    _save(AUTHORIZED_FILE, {str(k): v for k, v in authorized.items()})


def touch_user(user, task_type=None):
    uid = str(user.id)
    now = datetime.now().isoformat(timespec="seconds")
    if uid not in stats:
        stats[uid] = {
            "username": user.username or user.full_name,
            "full_name": user.full_name,
            "first_seen": now,
            "last_seen": now,
            "tasks": 0, "texts": 0, "photos": 0,
        }
    stats[uid]["last_seen"] = now
    stats[uid]["username"] = user.username or user.full_name
    if task_type == "text":
        stats[uid]["texts"] = stats[uid].get("texts", 0) + 1
        stats[uid]["tasks"] = stats[uid].get("tasks", 0) + 1
    elif task_type == "photo":
        stats[uid]["photos"] = stats[uid].get("photos", 0) + 1
        stats[uid]["tasks"] = stats[uid].get("tasks", 0) + 1
    _save(STATS_FILE, stats)


def generate_password() -> str:
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
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


def get_open_ticket(user_id: int):
    for tid, t in tickets.items():
        if t["user_id"] == user_id and t["status"] == "open":
            return tid
    return None


def create_ticket(user_id: int, username: str, text: str) -> str:
    ticket_id = f"T{datetime.now().strftime('%Y%m%d%H%M%S')}{secrets.randbelow(1000):03d}"
    tickets[ticket_id] = {
        "user_id": user_id,
        "username": username,
        "created": datetime.now().isoformat(timespec="seconds"),
        "status": "open",
        "messages": [{
            "from": "user",
            "text": text,
            "time": datetime.now().isoformat(timespec="seconds"),
        }],
    }
    _save(TICKETS_FILE, tickets)
    return ticket_id


def extend_subscription(user_id: int, days=None) -> str:
    """
    Продлевает подписку.
    days=None → бессрочная подписка.
    Возвращает либо ISO-дату, либо "infinite".
    """
    if days is None:
        authorized[user_id] = INFINITE
        save_authorized()
        log.info("extend_subscription: user=%s INFINITE", user_id)
        return INFINITE

    now = datetime.now()
    current_expire_iso = authorized.get(user_id)
    # Если текущая подписка бессрочная — оставляем её
    if current_expire_iso == INFINITE:
        return INFINITE
    if current_expire_iso:
        try:
            current_expire = datetime.fromisoformat(current_expire_iso)
            new_expire = (current_expire if current_expire > now else now) + timedelta(days=days)
        except Exception:
            new_expire = now + timedelta(days=days)
    else:
        new_expire = now + timedelta(days=days)
    authorized[user_id] = new_expire.isoformat(timespec="seconds")
    save_authorized()
    log.info("extend_subscription: user=%s new_expire=%s", user_id, new_expire)
    return authorized[user_id]


def get_subscription_status(user_id: int):
    """
    Возвращает (status, expire_dt_or_None, days_left)
    status: "active" | "expired" | "none" | "infinite"
    """
    if user_id not in authorized:
        return ("none", None, 0)
    value = authorized[user_id]
    if value == INFINITE:
        return ("infinite", None, -1)
    try:
        expire_dt = datetime.fromisoformat(value)
    except Exception:
        return ("none", None, 0)
    now = datetime.now()
    if expire_dt <= now:
        return ("expired", expire_dt, 0)
    return ("active", expire_dt, (expire_dt - now).days)


def format_subscription(uid: int) -> str:
    """Человекочитаемая строка про подписку."""
    status, expire_dt, days_left = get_subscription_status(uid)
    if status == "infinite":
        return "♾ Бессрочная"
    if status == "active":
        return f"до {expire_dt.strftime('%d.%m.%Y')} ({days_left} дн.)"
    if status == "expired":
        return f"истекла {expire_dt.strftime('%d.%m.%Y')}"
    return "нет"


def get_user_stars_paid(user_id: int) -> int:
    total = 0
    for p in payments.values():
        if p.get("user_id") == user_id and p.get("status") == "paid":
            total += p.get("amount", 0)
    return total


# ============ ПРОВЕРКА: ЭТО ЗАДАНИЕ ИЛИ БРЕД? ============
TASK_VERBS = re.compile(
    r"(реши|решить|реша|найди|найти|определ|вычисли|перевед|перевод|вставь|встав|"
    r"выбери|выбер|ответь|ответ|что такое|почему|назови|выпиши|объясни|"
    r"докажи|сравни|составь|запиши|подчеркни|раскрой|укажи|посчитай|обозначь|"
    r"задание|упражнени|задач|тест|формул|уравнени|пример|разбор|анализ|"
    r"напиши|приведи|опиши|построй|постро|изобрази|соотнеси|распредел|"
    r"прочитай|прочти|проверь|исправь|дополни|заполни|начерти)",
    re.IGNORECASE,
)


def looks_like_task(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    if "?" in t:
        return True
    if re.search(r"\d", t):
        return True
    if "_" in t:
        return True
    if any(s in t for s in "=+-*/:;()[]"):
        return True
    if TASK_VERBS.search(t):
        return True
    if len(t) >= 15:
        return True
    return False


# ============ СОСТОЯНИЯ АДМИНА ============
admin_states: dict = {}


# ============ АУТЕНТИФИКАЦИЯ ============
class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, types.Message):
            return await handler(event, data)
        user_id = event.from_user.id

        log.info("MW: user=%s text=%r payment=%s",
                 user_id,
                 (event.text or event.caption or "")[:40],
                 bool(event.successful_payment))

        if user_id == ADMIN_ID:
            return await handler(event, data)

        if event.successful_payment is not None:
            log.info("MW: пропускаю successful_payment")
            return await handler(event, data)

        if event.text and event.text.startswith(("/start", "/support", "/mysub", "/buy", "/help")):
            return await handler(event, data)

        if event.text and get_open_ticket(user_id):
            return await handler(event, data)

        if user_id in blocked:
            await event.answer("🚫 Ваш доступ заблокирован. Свяжитесь с продавцом.")
            return

        status, expire_dt, days_left = get_subscription_status(user_id)

        # Бессрочная подписка — пропускаем всё
        if status == "infinite":
            return await handler(event, data)

        if status == "active":
            if days_left <= 3 and event.text and not event.text.startswith("/"):
                try:
                    await event.answer(
                        f"⏰ Ваша подписка истекает через {days_left} дн. Продлить — /buy"
                    )
                except Exception:
                    pass
            return await handler(event, data)

        if status == "expired":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"💳 Продлить за {PRICE_STARS} ⭐", callback_data="buy")],
                [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support_start")],
            ])
            await event.answer(
                f"⌛ Ваша подписка истекла.\n"
                f"Продлить доступ на {SUBSCRIPTION_DAYS} дней — {PRICE_STARS} ⭐.",
                reply_markup=kb,
            )
            return

        if event.text:
            pwd_key, pwd_info = find_password_by_value(event.text)
            if pwd_key:
                if pwd_info.get("used_by") is None:
                    pwd_info["used_by"] = user_id
                    pwd_info["used_at"] = datetime.now().isoformat(timespec="seconds")
                    pwd_info["used_by_name"] = event.from_user.full_name
                    pwd_info["used_by_username"] = event.from_user.username or ""
                    _save(PASSWORDS_FILE, passwords)

                    # Дни берём из пароля; если нет — стандарт
                    days = pwd_info.get("days", SUBSCRIPTION_DAYS)
                    new_val = extend_subscription(user_id, days)
                    touch_user(event.from_user)

                    if new_val == INFINITE:
                        await event.answer(
                            "✅ Пароль активирован! Доступ ♾ БЕССРОЧНЫЙ.",
                            reply_markup=subject_kb(),
                        )
                    else:
                        exp_dt = datetime.fromisoformat(new_val)
                        await event.answer(
                            f"✅ Пароль активирован! Доступ на {days} дней "
                            f"до {exp_dt.strftime('%d.%m.%Y')}.",
                            reply_markup=subject_kb(),
                        )
                    return
                elif pwd_info.get("used_by") == user_id:
                    days = pwd_info.get("days", SUBSCRIPTION_DAYS)
                    new_val = extend_subscription(user_id, days)
                    if new_val == INFINITE:
                        await event.answer("✅ Доступ продлён ♾ БЕССРОЧНО.")
                    else:
                        exp_dt = datetime.fromisoformat(new_val)
                        await event.answer(f"✅ Доступ продлён до {exp_dt.strftime('%d.%m.%Y')}.")
                    return
                else:
                    await event.answer("❌ Этот пароль уже активирован другим пользователем.")
                    return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Купить доступ за {PRICE_STARS} ⭐", callback_data="buy")],
            [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support_start")],
        ])
        await event.answer(
            f"🔒 Доступ закрыт.\n\n"
            f"Подписка на {SUBSCRIPTION_DAYS} дней — {PRICE_STARS} ⭐.",
            reply_markup=kb,
        )


# ============ МЕНЮ ============
SUBJECTS = [
    ("🧮 Алгебра", "algebra"), ("📐 Геометрия", "geometry"),
    ("🇷🇺 Русский", "russian"), ("📖 Литература", "literature"),
    ("📜 История", "history"), ("🌍 География", "geography"),
    ("🧬 Биология", "biology"), ("⚡ Физика", "physics"),
    ("💻 Информатика", "cs"), ("🇬🇧 Английский", "english"),
    ("🧪 Химия", "chemistry"), ("💬 Общее", "general"),
]
SUBJECT_NAMES = {code: name for name, code in SUBJECTS}
SUBJECT_NAMES["general"] = "💬 Общее"

MODE_PATTERN = re.compile(r"(режим|предмет|урок|класс|работа\w*|сто\w*|выбран|текущ)", re.IGNORECASE)


def subject_kb():
    rows = []
    for i in range(0, len(SUBJECTS), 3):
        row = [InlineKeyboardButton(text=name, callback_data=f"subj:{code}")
               for name, code in SUBJECTS[i:i + 3]]
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Купить доступ за {PRICE_STARS} ⭐", callback_data="buy")],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support_start")],
    ])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔑 1 пароль ({SUBSCRIPTION_DAYS}д)", callback_data="admin:gen1"),
         InlineKeyboardButton(text="🔑🔑 5 паролей", callback_data="admin:gen5")],
        [InlineKeyboardButton(text="♾ 1 бесконечный", callback_data="admin:gen1_inf"),
         InlineKeyboardButton(text="♾♾ 5 бесконечных", callback_data="admin:gen5_inf")],
        [InlineKeyboardButton(text="📋 Список паролей", callback_data="admin:list"),
         InlineKeyboardButton(text="🗑 Удалить пароли", callback_data="admin:del_menu")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users"),
         InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
        [InlineKeyboardButton(text="💳 Подписки", callback_data="admin:subs"),
         InlineKeyboardButton(text="🎫 Тикеты", callback_data="admin:tickets")],
        [InlineKeyboardButton(text="➕ Добавить юзера", callback_data="admin:add_user"),
         InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:refresh")],
    ])


def admin_del_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить конкретный", callback_data="admin:del_one")],
        [InlineKeyboardButton(text="🗑 Удалить все СВОБОДНЫЕ", callback_data="admin:del_free")],
        [InlineKeyboardButton(text="🗑 Удалить все ИСПОЛЬЗОВАННЫЕ", callback_data="admin:del_used")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:refresh")],
    ])


def admin_panel_text() -> str:
    total_pwd = len(passwords)
    used_pwd = sum(1 for v in passwords.values() if v.get("used_by"))
    free_pwd = total_pwd - used_pwd
    open_tickets = sum(1 for t in tickets.values() if t["status"] == "open")

    active_subs = 0
    expired_subs = 0
    infinite_subs = 0
    for uid, val in authorized.items():
        if val == INFINITE:
            infinite_subs += 1
            continue
        try:
            exp_dt = datetime.fromisoformat(val)
            if exp_dt > datetime.now():
                active_subs += 1
            else:
                expired_subs += 1
        except Exception:
            expired_subs += 1

    paid = [p for p in payments.values() if p.get("status") == "paid"]
    total_stars = sum(p.get("amount", 0) for p in paid)

    return (
        f"🎛 Админ-панель\n\n"
        f"🔑 Паролей: {total_pwd} (🆓 {free_pwd} / ✅ {used_pwd})\n"
        f"💳 Подписок активных: {active_subs}\n"
        f"♾ Бессрочных: {infinite_subs}\n"
        f"⌛ Подписок истекших: {expired_subs}\n"
        f"🚫 Заблокированных: {len(blocked)}\n"
        f"🎫 Открытых тикетов: {open_tickets}\n"
        f"💰 Оплат всего: {len(paid)} на {total_stars} ⭐\n"
        f"⭐ Цена: {PRICE_STARS} звёзд / {SUBSCRIPTION_DAYS} дней"
    )


dp.message.outer_middleware(AuthMiddleware())


# ============ КОМАНДЫ ============
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    log.info("cmd_start: user=%s (%s)", message.from_user.id, message.from_user.full_name)

    if message.from_user.id not in user_subject:
        user_subject[message.from_user.id] = "general"
        _save(SUBJECTS_FILE, {str(k): v for k, v in user_subject.items()})
    touch_user(message.from_user)

    if message.from_user.id == ADMIN_ID:
        current = SUBJECT_NAMES.get(user_subject[message.from_user.id], "💬 Общее")
        await message.answer(
            f"👑 Вы админ. Панель: /admin\n"
            f"Текущий режим: {current}",
            reply_markup=subject_kb(),
        )
        return

    status, expire_dt, days_left = get_subscription_status(message.from_user.id)
    log.info("cmd_start: user=%s status=%s", message.from_user.id, status)

    if status == "infinite":
        current = SUBJECT_NAMES.get(user_subject[message.from_user.id], "💬 Общее")
        await message.answer(
            f"Привет! Текущий режим: {current}\n"
            f"💳 Подписка: ♾ Бессрочная\n"
            f"Выбери предмет или сразу кидай задачу.",
            reply_markup=subject_kb(),
        )
        return

    if status == "active":
        current = SUBJECT_NAMES.get(user_subject[message.from_user.id], "💬 Общее")
        await message.answer(
            f"Привет! Текущий режим: {current}\n"
            f"💳 Подписка до {expire_dt.strftime('%d.%m.%Y')} ({days_left} дн.)\n"
            f"Выбери предмет или сразу кидай задачу.",
            reply_markup=subject_kb(),
        )
        return

    if status == "expired":
        await message.answer(
            f"⌛ Ваша подписка истекла {expire_dt.strftime('%d.%m.%Y')}.\n\n"
            f"Продлить доступ на {SUBSCRIPTION_DAYS} дней — {PRICE_STARS} ⭐.",
            reply_markup=buy_kb(),
        )
        return

    await message.answer(
        f"👋 Привет!\n\n"
        f"Это бот для решения домашних заданий по всем школьным предметам.\n\n"
        f"📚 Что умеет:\n"
        f"• Решает задачи по фото и тексту\n"
        f"• Алгебра, геометрия, русский, история, физика и другие\n"
        f"• Отвечает за 3-10 секунд\n\n"
        f"💳 Подписка на {SUBSCRIPTION_DAYS} дней — {PRICE_STARS} ⭐\n\n"
        f"Оформи подписку, чтобы начать:",
        reply_markup=buy_kb(),
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📚 Доступные команды:\n\n"
        "/start — главное меню\n"
        "/subject — сменить предмет\n"
        "/mysub — статус подписки\n"
        "/buy — купить/продлить доступ\n"
        "/support — написать в поддержку"
    )


@dp.message(Command("subject"))
async def cmd_subject(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        status, _, _ = get_subscription_status(message.from_user.id)
        if status not in ("active", "infinite"):
            await message.answer(
                "🔒 Смена предмета доступна только с активной подпиской.",
                reply_markup=buy_kb(),
            )
            return
    current = user_subject.get(message.from_user.id, "general")
    current_name = SUBJECT_NAMES.get(current, "💬 Общее")
    await message.answer(f"Сейчас выбран: {current_name}\nВыбери новый:", reply_markup=subject_kb())


@dp.message(Command("mysub"))
async def cmd_mysub(message: types.Message):
    status, expire_dt, days_left = get_subscription_status(message.from_user.id)
    stars_paid = get_user_stars_paid(message.from_user.id)

    if status == "infinite":
        await message.answer(
            f"💳 Ваша подписка: ♾ Бессрочная\n"
            f"💰 Всего оплачено: {stars_paid} ⭐"
        )
    elif status == "active":
        await message.answer(
            f"💳 Ваша подписка активна.\n"
            f"📅 Действует до: {expire_dt.strftime('%d.%m.%Y %H:%M')}\n"
            f"⏰ Осталось: {days_left} дн.\n"
            f"💰 Всего оплачено: {stars_paid} ⭐"
        )
    elif status == "expired":
        await message.answer(
            f"⌛ Ваша подписка истекла {expire_dt.strftime('%d.%m.%Y')}.\n"
            f"💰 Всего оплачено: {stars_paid} ⭐\n\n"
            f"Продлить — {PRICE_STARS} ⭐.",
            reply_markup=buy_kb(),
        )
    else:
        await message.answer("У вас нет активной подписки.", reply_markup=buy_kb())


@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    log.info("cmd_buy: user=%s", message.from_user.id)
    # Если бессрочная — отказываем
    status, _, _ = get_subscription_status(message.from_user.id)
    if status == "infinite":
        await message.answer("✅ У вас ♾ бессрочный доступ. Оплата не нужна.")
        return
    await send_stars_invoice(message)


@dp.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery):
    log.info("cb_buy: user=%s", call.from_user.id)
    status, _, _ = get_subscription_status(call.from_user.id)
    if status == "infinite":
        await call.answer("✅ У вас ♾ бессрочный доступ", show_alert=True)
        return
    await send_stars_invoice(call.message)
    await call.answer()


async def send_stars_invoice(message: types.Message):
    status, expire_dt, days_left = get_subscription_status(message.from_user.id)
    if status == "active":
        title = "Продление подписки"
        description = (
            f"Продление доступа на {SUBSCRIPTION_DAYS} дней.\n"
            f"Текущая подписка активна до {expire_dt.strftime('%d.%m.%Y')}.\n"
            f"Новые {SUBSCRIPTION_DAYS} дней добавятся к текущей дате."
        )
    else:
        title = "Подписка на ФоксФорд ГДЗ"
        description = f"Доступ к боту на {SUBSCRIPTION_DAYS} дней."

    try:
        await bot.send_invoice(
            chat_id=message.chat.id,
            title=title,
            description=description,
            payload=f"sub_{message.from_user.id}_{int(datetime.now().timestamp())}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"Подписка на {SUBSCRIPTION_DAYS} дней", amount=PRICE_STARS)],
            start_parameter="buy_subscription",
        )
        log.info("send_stars_invoice: счёт отправлен юзеру %s", message.from_user.id)
    except Exception as e:
        log.error("Ошибка отправки счёта: %s", e)
        traceback.print_exc()
        await message.answer("❌ Не удалось создать счёт. Попробуйте позже.")


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    log.info("!!! PRE_CHECKOUT: user=%s amount=%s payload=%s",
             query.from_user.id, query.total_amount, query.invoice_payload)
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    try:
        user = message.from_user
        sp = message.successful_payment
        amount = sp.total_amount

        charge_id = (
            getattr(sp, "telegram_payment_charge_id", None)
            or getattr(sp, "provider_charge_id", None)
            or sp.invoice_payload
            or f"stars_{user.id}_{int(datetime.now().timestamp())}"
        )

        log.info("!!! SUCCESSFUL_PAYMENT: user=%s amount=%s charge=%s",
                 user.id, amount, charge_id)

        payments[charge_id] = {
            "user_id": user.id,
            "amount": amount,
            "payload": sp.invoice_payload,
            "status": "paid",
            "granted": True,
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        _save(PAYMENTS_FILE, payments)

        # Бессрочных через оплату не выдаём — только на SUBSCRIPTION_DAYS
        new_val = extend_subscription(user.id, SUBSCRIPTION_DAYS)
        touch_user(user)

        total_paid = get_user_stars_paid(user.id)
        log.info("!!! Подписка юзера %s = %s. Всего оплачено: %s ⭐",
                 user.id, new_val, total_paid)

        if new_val == INFINITE:
            exp_line = "♾ Бессрочная"
        else:
            exp_dt = datetime.fromisoformat(new_val)
            exp_line = exp_dt.strftime('%d.%m.%Y %H:%M')

        await message.answer(
            f"✅ Оплата получена! Подписка активна.\n\n"
            f"📅 Действует до: {exp_line}\n"
            f"⏰ Это {SUBSCRIPTION_DAYS} дней доступа.\n"
            f"💰 Оплачено: {amount} ⭐ (всего: {total_paid} ⭐)\n\n"
            f"Проверить статус — /mysub",
            reply_markup=subject_kb(),
        )

        try:
            await bot.send_message(
                ADMIN_ID,
                f"💰 Новая оплата звёздами!\n"
                f"👤 {user.full_name} (@{user.username or '—'})\n"
                f"🆔 {user.id}\n"
                f"⭐ Оплачено: {amount} звёзд\n"
                f"⭐ Всего от юзера: {total_paid} звёзд\n"
                f"📅 Подписка до: {exp_line}",
            )
        except Exception as e:
            log.warning("Не смог уведомить админа: %s", e)
    except Exception as e:
        log.error("!!! ОШИБКА В SUCCESSFUL_PAYMENT: %s", e)
        traceback.print_exc()


@dp.message(Command("support"))
async def cmd_support(message: types.Message):
    open_ticket = get_open_ticket(message.from_user.id)
    if open_ticket:
        await message.answer(
            f"У вас уже есть открытый тикет #{open_ticket}.\n"
            f"Напишите ваше сообщение — я передам администратору."
        )
        return
    tid = create_ticket(
        user_id=message.from_user.id,
        username=message.from_user.username or message.from_user.full_name,
        text="[пользователь открыл тикет]",
    )
    await message.answer(
        f"🆘 Тикет #{tid} создан.\n\n"
        f"Напишите ваш вопрос следующим сообщением — я передам его администратору."
    )


@dp.callback_query(F.data == "support_start")
async def cb_support_start(call: CallbackQuery):
    open_ticket = get_open_ticket(call.from_user.id)
    if open_ticket:
        await call.message.answer(
            f"У вас уже есть открытый тикет #{open_ticket}.\n"
            f"Напишите ваше сообщение."
        )
        await call.answer()
        return
    tid = create_ticket(
        user_id=call.from_user.id,
        username=call.from_user.username or call.from_user.full_name,
        text="[пользователь открыл тикет]",
    )
    await call.message.answer(
        f"🆘 Тикет #{tid} создан.\n\n"
        f"Напишите ваш вопрос следующим сообщением."
    )
    await call.answer()


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    await message.answer(admin_panel_text(), reply_markup=admin_kb())


# ============ АДМИН: РУЧНОЕ ДОБАВЛЕНИЕ ============
@dp.callback_query(F.data == "admin:add_user")
async def cb_add_user(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("⛔", show_alert=True)
        return
    admin_states[ADMIN_ID] = "awaiting_user_id_to_add"
    await call.message.answer(
        "➕ Отправь **Telegram ID** пользователя, которому нужно выдать доступ на "
        f"{SUBSCRIPTION_DAYS} дней.\n\n"
        "Узнать ID: пусть напишет @userinfobot",
        parse_mode="Markdown",
    )
    await call.answer()


@dp.message(lambda m: m.from_user.id == ADMIN_ID
            and admin_states.get(ADMIN_ID) == "awaiting_user_id_to_add"
            and m.text)
async def handle_add_user(message: types.Message):
    admin_states.pop(ADMIN_ID, None)
    text = (message.text or "").strip()
    try:
        uid = int(text)
    except ValueError:
        await message.answer("❌ Это не похоже на ID. Отправь число.")
        return
    new_val = extend_subscription(uid, SUBSCRIPTION_DAYS)
    if new_val == INFINITE:
        exp_line = "♾ Бессрочная"
    else:
        exp_dt = datetime.fromisoformat(new_val)
        exp_line = exp_dt.strftime('%d.%m.%Y %H:%M')
    await message.answer(
        f"✅ Пользователю {uid} выдан доступ на {SUBSCRIPTION_DAYS} дней.\n"
        f"📅 До: {exp_line}"
    )
    try:
        await bot.send_message(
            uid,
            f"🎁 Администратор выдал вам доступ на {SUBSCRIPTION_DAYS} дней!\n"
            f"📅 До: {exp_line}\n\n"
            f"Открой /start, чтобы начать."
        )
    except Exception as e:
        log.warning("Не смог уведомить %s: %s", uid, e)


# ============ ПОДДЕРЖКА ============
@dp.message(lambda m: m.from_user.id != ADMIN_ID
            and not (m.text or "").startswith("/")
            and (m.text or m.caption)
            and get_open_ticket(m.from_user.id))
async def handle_support_message(message: types.Message):
    tid = get_open_ticket(message.from_user.id)
    if not tid:
        return
    text = message.text or message.caption or "[медиа]"
    tickets[tid]["messages"].append({
        "from": "user", "text": text,
        "time": datetime.now().isoformat(timespec="seconds"),
    })
    _save(TICKETS_FILE, tickets)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"ticket:reply:{tid}")],
        [InlineKeyboardButton(text="✅ Закрыть тикет", callback_data=f"ticket:close:{tid}")],
    ])
    try:
        await bot.send_message(
            ADMIN_ID,
            f"🆘 Сообщение от {message.from_user.full_name} (@{message.from_user.username or '—'}, id {message.from_user.id})\n"
            f"🎫 Тикет: {tid}\n\n{text}",
            reply_markup=kb,
        )
    except Exception as e:
        log.warning("Не смог уведомить админа: %s", e)
    await message.answer("✅ Сообщение отправлено администратору. Ждите ответа.")


@dp.callback_query(F.data.startswith("ticket:"))
async def cb_ticket(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("⛔", show_alert=True)
        return
    parts = call.data.split(":")
    action, tid = parts[1], parts[2]
    if tid not in tickets:
        await call.answer("Тикет не найден", show_alert=True)
        return
    if action == "reply":
        admin_states[ADMIN_ID] = f"reply_ticket:{tid}"
        await call.message.answer(f"✏️ Напиши ответ для тикета {tid}:")
        await call.answer()
    elif action == "close":
        tickets[tid]["status"] = "closed"
        _save(TICKETS_FILE, tickets)
        uid = tickets[tid]["user_id"]
        try:
            await bot.send_message(uid, "✅ Ваш тикет закрыт. Если появятся вопросы — /support.")
        except Exception:
            pass
        await call.message.answer(f"🎫 Тикет {tid} закрыт.")
        await call.answer("Закрыт")


@dp.message(lambda m: m.from_user.id == ADMIN_ID
            and admin_states.get(ADMIN_ID, "").startswith("reply_ticket:"))
async def handle_admin_ticket_reply(message: types.Message):
    state = admin_states.pop(ADMIN_ID, "")
    _, tid = state.split(":", 1)
    if tid not in tickets:
        await message.answer("Тикет не найден.")
        return
    reply_text = message.text or "[пусто]"
    tickets[tid]["messages"].append({
        "from": "admin", "text": reply_text,
        "time": datetime.now().isoformat(timespec="seconds"),
    })
    _save(TICKETS_FILE, tickets)
    uid = tickets[tid]["user_id"]
    try:
        await bot.send_message(uid, f"📨 Ответ поддержки:\n\n{reply_text}")
        await message.answer(f"✅ Ответ отправлен пользователю {uid}.")
    except Exception as e:
        await message.answer(f"❌ Не смог отправить: {e}")


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
    del passwords[pwd_key]
    _save(PASSWORDS_FILE, passwords)
    await message.answer(f"✅ Пароль `{pwd_key}` удалён.")


# ============ ХЕЛПЕР: создать пароль ============
def _create_passwords(count: int, days):
    """Создаёт count паролей. days=None → бесконечные."""
    new_pwds = []
    for _ in range(count):
        pwd = generate_password()
        while pwd in passwords:
            pwd = generate_password()
        passwords[pwd] = {
            "created": datetime.now().isoformat(timespec="seconds"),
            "used_by": None,
            "used_at": None,
            "note": "",
            "days": days,
        }
        new_pwds.append(pwd)
    _save(PASSWORDS_FILE, passwords)
    return new_pwds


# ============ CALLBACK: АДМИН-ПАНЕЛЬ ============
@dp.callback_query(F.data.startswith("admin:"))
async def cb_admin(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("⛔", show_alert=True)
        return
    action = call.data.split(":", 1)[1]

    if action == "gen1":
        pwds = _create_passwords(1, SUBSCRIPTION_DAYS)
        await call.message.answer(
            f"🔑 Новый пароль ({SUBSCRIPTION_DAYS} дней):\n\n`{pwds[0]}`\n\n"
            f"Скопируй и продай покупателю.",
            parse_mode="Markdown",
        )
        await call.answer("Создан")

    elif action == "gen5":
        pwds = _create_passwords(5, SUBSCRIPTION_DAYS)
        await call.message.answer(
            f"🔑 5 паролей ({SUBSCRIPTION_DAYS} дней каждый):\n\n"
            + "\n".join(f"`{p}`" for p in pwds),
            parse_mode="Markdown",
        )
        await call.answer("Создано 5")

    elif action == "gen1_inf":
        pwds = _create_passwords(1, None)
        await call.message.answer(
            f"♾ Новый БЕССРОЧНЫЙ пароль:\n\n`{pwds[0]}`\n\n"
            f"Даёт ♾ бессрочный доступ.",
            parse_mode="Markdown",
        )
        await call.answer("Создан бессрочный")

    elif action == "gen5_inf":
        pwds = _create_passwords(5, None)
        await call.message.answer(
            "♾ 5 БЕССРОЧНЫХ паролей:\n\n"
            + "\n".join(f"`{p}`" for p in pwds),
            parse_mode="Markdown",
        )
        await call.answer("Создано 5 бессрочных")

    elif action == "list":
        if not passwords:
            await call.message.answer("Паролей пока нет.")
            await call.answer()
            return
        lines = ["📋 Список паролей:\n"]
        free = [(p, v) for p, v in passwords.items() if not v.get("used_by")]
        used = [(p, v) for p, v in passwords.items() if v.get("used_by")]

        def _tag(v):
            d = v.get("days", SUBSCRIPTION_DAYS)
            return "♾" if d is None else f"{d}д"

        if free:
            lines.append(f"🆓 Свободные ({len(free)}):")
            for p, v in free:
                lines.append(f"   `{p}` [{_tag(v)}]")
            lines.append("")
        if used:
            lines.append(f"✅ Использованные ({len(used)}):")
            for p, v in used:
                name = v.get("used_by_name", "?")
                uname = v.get("used_by_username", "")
                uname_str = f" @{uname}" if uname else ""
                lines.append(f"   `{p}` [{_tag(v)}] → {name}{uname_str}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n... (обрезано)"
        await call.message.answer(text, parse_mode="Markdown")
        await call.answer()

    elif action == "del_menu":
        await call.message.answer("🗑 Управление паролями:", reply_markup=admin_del_kb())
        await call.answer()

    elif action == "del_one":
        admin_states[ADMIN_ID] = "awaiting_password_to_delete"
        await call.message.answer("Отправь пароль для удаления:")
        await call.answer()

    elif action == "del_free":
        free = [p for p, v in passwords.items() if not v.get("used_by")]
        for p in free:
            del passwords[p]
        _save(PASSWORDS_FILE, passwords)
        await call.message.answer(f"🗑 Удалено свободных: {len(free)}")
        await call.answer()

    elif action == "del_used":
        used = [(p, v) for p, v in passwords.items() if v.get("used_by")]
        for p, v in used:
            del passwords[p]
        _save(PASSWORDS_FILE, passwords)
        await call.message.answer(f"🗑 Удалено использованных: {len(used)}")
        await call.answer()

    elif action == "stats":
        total_tasks = sum(s.get("tasks", 0) for s in stats.values())
        total_texts = sum(s.get("texts", 0) for s in stats.values())
        total_photos = sum(s.get("photos", 0) for s in stats.values())
        used_pwd = sum(1 for v in passwords.values() if v.get("used_by"))
        paid = [p for p in payments.values() if p.get("status") == "paid"]
        total_stars = sum(p.get("amount", 0) for p in paid)

        stars_by_user = {}
        for p in paid:
            uid = p.get("user_id")
            if uid is None:
                continue
            stars_by_user[uid] = stars_by_user.get(uid, 0) + p.get("amount", 0)
        top_payers = sorted(stars_by_user.items(), key=lambda x: x[1], reverse=True)[:10]

        lines = [
            "📊 Статистика",
            "",
            f"🔑 Паролей: {len(passwords)} (исп. {used_pwd})",
            f"💳 Подписок: {len(authorized)}",
            f"🚫 Заблокировано: {len(blocked)}",
            f"💰 Оплат: {len(paid)} на {total_stars} ⭐",
            f"✅ Задач: {total_tasks}",
            f"   📝 текстом: {total_texts}",
            f"   📷 фото: {total_photos}",
            "",
            "💰 Топ-10 по оплате:",
        ]
        for i, (uid, stars) in enumerate(top_payers, 1):
            s = stats.get(str(uid), {})
            uname = s.get("username", "?")
            lines.append(f"{i}. @{uname} (id {uid}): {stars} ⭐")

        lines.append("")
        lines.append("📋 Топ-10 по задачам:")
        sorted_users = sorted(stats.items(), key=lambda x: x[1].get("tasks", 0), reverse=True)[:10]
        for i, (uid, s) in enumerate(sorted_users, 1):
            uname = s.get("username", "?")
            lines.append(f"{i}. @{uname} (id {uid}): {s.get('tasks', 0)} задач")

        await call.message.answer("\n".join(lines))
        await call.answer()

    elif action == "subs":
        if not authorized:
            await call.message.answer("Подписок пока нет.")
            await call.answer()
            return
        # Собираем всё в один список
        items = []
        for uid, val in authorized.items():
            s = stats.get(str(uid), {})
            uname = s.get("username", "?")
            stars = get_user_stars_paid(uid)
            if val == INFINITE:
                items.append((None, uid, uname, stars, "♾"))
            else:
                try:
                    exp_dt = datetime.fromisoformat(val)
                    now = datetime.now()
                    days = (exp_dt - now).days
                    items.append((exp_dt, uid, uname, stars, "✅" if exp_dt > now else "⌛"))
                except Exception:
                    pass
        # Бессрочные — в начале, потом по дате
        items.sort(key=lambda x: (x[0] is not None, x[0] or datetime.max), reverse=False)
        # Хотим: сначала бессрочные, потом активные по убыванию даты, потом истёкшие
        infinite_items = [x for x in items if x[4] == "♾"]
        active_items = sorted([x for x in items if x[4] == "✅"], key=lambda x: x[0], reverse=True)
        expired_items = sorted([x for x in items if x[4] == "⌛"], key=lambda x: x[0], reverse=True)
        ordered = infinite_items + active_items + expired_items

        lines = ["💳 Подписки:\n"]
        for exp_dt, uid, uname, stars, status in ordered[:40]:
            if status == "♾":
                lines.append(f"♾ @{uname} (id {uid}) — БЕССРОЧНАЯ · 💰 {stars} ⭐")
            elif status == "✅":
                days = (exp_dt - datetime.now()).days
                lines.append(
                    f"✅ @{uname} (id {uid}) — до {exp_dt.strftime('%d.%m.%Y')} "
                    f"({days} дн.) · 💰 {stars} ⭐"
                )
            else:
                lines.append(
                    f"⌛ @{uname} (id {uid}) — истекла {exp_dt.strftime('%d.%m.%Y')} "
                    f"· 💰 {stars} ⭐"
                )
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n... (обрезано)"
        await call.message.answer(text)
        await call.answer()

    elif action == "users":
        if not stats:
            await call.message.answer("Пока никого нет.")
            await call.answer()
            return
        items = sorted(stats.items(), key=lambda x: x[1].get("last_seen", ""), reverse=True)[:30]
        rows = []
        for uid, s in items:
            uid_int = int(uid)
            uname = s.get("username", "?")
            tasks = s.get("tasks", 0)
            status, _, _ = get_subscription_status(uid_int)
            if uid_int in blocked:
                st = "🚫"
            elif status == "infinite":
                st = "♾"
            elif status == "active":
                st = "✅"
            elif status == "expired":
                st = "⌛"
            elif uid_int == ADMIN_ID:
                st = "👑"
            else:
                st = "❌"
            stars = get_user_stars_paid(uid_int)
            label = f"{st} @{uname} · {tasks} задач · {stars} ⭐"
            rows.append([InlineKeyboardButton(text=label[:60], callback_data=f"usr:info:{uid_int}")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:refresh")])
        await call.message.answer("👥 Пользователи:",
                                  reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await call.answer()

    elif action == "tickets":
        open_tickets = [(tid, t) for tid, t in tickets.items() if t["status"] == "open"]
        if not open_tickets:
            await call.message.answer("🎫 Открытых тикетов нет.")
            await call.answer()
            return
        lines = ["🎫 Открытые тикеты:\n"]
        for tid, t in open_tickets[-10:]:
            last_msg = t["messages"][-1]["text"][:60] if t["messages"] else ""
            lines.append(f"#{tid}\n  👤 {t['username']} (id {t['user_id']})\n  📝 {last_msg}...")
        rows = [[InlineKeyboardButton(text=f"✏️ Ответить #{tid}",
                                      callback_data=f"ticket:reply:{tid}")]
                for tid, _ in open_tickets[-10:]]
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:refresh")])
        await call.message.answer("\n".join(lines),
                                  reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
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
    action, uid = parts[1], int(parts[2])

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
        status, expire_dt, days_left = get_subscription_status(uid)

        if is_blocked:
            status_txt = "🚫 ЗАБЛОКИРОВАН"
            exp_txt = "—"
        elif status == "infinite":
            status_txt = "♾ БЕССРОЧНАЯ"
            exp_txt = "—"
        elif status == "active":
            status_txt = "✅ Подписка активна"
            exp_txt = f"{expire_dt.strftime('%d.%m.%Y')} ({days_left} дн.)"
        elif status == "expired":
            status_txt = "⌛ Подписка истекла"
            exp_txt = expire_dt.strftime('%d.%m.%Y')
        else:
            status_txt = "❌ Нет подписки"
            exp_txt = "—"

        stars_paid = get_user_stars_paid(uid)
        pay_count = sum(
            1 for p in payments.values()
            if p.get("user_id") == uid and p.get("status") == "paid"
        )

        text = (
            f"👤 Пользователь\n\n"
            f"ID: {uid}\nИмя: {full_name}\nUsername: @{uname}\n"
            f"Статус: {status_txt}\nПодписка до: {exp_txt}\nПароль: {pwd}\n\n"
            f"💰 Оплачено: {stars_paid} ⭐ ({pay_count} раз)\n\n"
            f"📊 Задач: {tasks} (текст {texts}, фото {photos})\n"
            f"Первый раз: {first_seen}\nПоследний: {last_seen}"
        )

        buttons = []
        if is_blocked:
            buttons.append(InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"usr:unblock:{uid}"))
        else:
            buttons.append(InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"usr:block:{uid}"))
        buttons.append(InlineKeyboardButton(text=f"🎁 +{SUBSCRIPTION_DAYS}д", callback_data=f"usr:extend:{uid}"))
        buttons.append(InlineKeyboardButton(text="♾ Сделать бессрочным", callback_data=f"usr:infinite:{uid}"))
        buttons.append(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"usr:delete:{uid}"))

        kb = InlineKeyboardMarkup(inline_keyboard=[
            buttons[:2],
            buttons[2:4],
            buttons[4:],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:users")],
        ])
        await call.message.answer(text, reply_markup=kb)
        await call.answer()

    elif action == "block":
        blocked.add(uid)
        _save(BLOCKED_FILE, list(blocked))
        await call.message.answer(f"🚫 {uid} заблокирован.")
        await call.answer("Заблокирован")

    elif action == "unblock":
        blocked.discard(uid)
        _save(BLOCKED_FILE, list(blocked))
        await call.message.answer(f"✅ {uid} разблокирован.")
        await call.answer("Разблокирован")

    elif action == "extend":
        new_val = extend_subscription(uid, SUBSCRIPTION_DAYS)
        if new_val == INFINITE:
            exp_line = "♾ Бессрочная"
        else:
            exp_dt = datetime.fromisoformat(new_val)
            exp_line = exp_dt.strftime('%d.%m.%Y')
        await call.message.answer(f"🎁 {uid} продлён на {SUBSCRIPTION_DAYS} дней. До: {exp_line}")
        try:
            await bot.send_message(uid,
                f"🎁 Подписка продлена на {SUBSCRIPTION_DAYS} дней.\n📅 До: {exp_line}")
        except Exception:
            pass
        await call.answer("Продлено")

    elif action == "infinite":
        extend_subscription(uid, None)
        await call.message.answer(f"♾ Пользователю {uid} выдан БЕССРОЧНЫЙ доступ.")
        try:
            await bot.send_message(uid, "🎁 Вам выдан ♾ бессрочный доступ!")
        except Exception:
            pass
        await call.answer("Бессрочный")

    elif action == "delete":
        blocked.discard(uid)
        authorized.pop(uid, None)
        stats.pop(str(uid), None)
        for pwd, info in list(passwords.items()):
            if info.get("used_by") == uid:
                del passwords[pwd]
        save_authorized()
        _save(BLOCKED_FILE, list(blocked))
        _save(STATS_FILE, stats)
        _save(PASSWORDS_FILE, passwords)
        await call.message.answer(f"🗑 {uid} удалён.")
        await call.answer("Удалён")


# ============ CALLBACK: ВЫБОР ПРЕДМЕТА ============
@dp.callback_query(F.data.startswith("subj:"))
async def cb_subject(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        status, _, _ = get_subscription_status(call.from_user.id)
        if status not in ("active", "infinite"):
            await call.answer("🔒 Нужна подписка", show_alert=True)
            return
    subj = call.data.split(":", 1)[1]
    user_subject[call.from_user.id] = subj
    _save(SUBJECTS_FILE, {str(k): v for k, v in user_subject.items()})
    name = SUBJECT_NAMES.get(subj, subj)
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

    if not looks_like_task(message.text):
        log.info("handle_text: не похоже на задание: %r", message.text[:60])
        await message.answer(
            "🤔 Это не похоже на школьное задание.\n\n"
            "Пришли текст задачи, пример или упражнения — я решу.\n\n"
            "Например:\n"
            "• «Реши 2x + 5 = 11»\n"
            "• «Что такое фотосинтез?»\n"
            "• «Переведи how are you»\n"
            "• «Вставь пропущенные буквы: к_рова, м_локо»"
        )
        return

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
