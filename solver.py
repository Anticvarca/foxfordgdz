import base64
import os
import logging
import asyncio
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("solver")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

if not OPENAI_API_KEY:
    log.error("OPENAI_API_KEY ПУСТОЙ! Задай переменную в панели BotHost.")
else:
    log.info("OPENAI_API_KEY получен, длина %d", len(OPENAI_API_KEY))

client = AsyncOpenAI(
    api_key=OPENAI_API_KEY or "dummy",
    timeout=180.0,
)

# Новая reasoning-модель OpenAI — поддерживает текст и изображения
MODEL = "gpt-6-luna"

# Уровень «размышлений»: "minimal" / "low" / "medium" / "high"
REASONING_EFFORT = "medium"

SYSTEM_PROMPT = """Ты — бот-репетитор, который решает ТОЛЬКО школьные задания.

Ты НЕ отвечаешь на:
- вопросы про технику (айфон, андроид, приложения, компьютеры)
- бытовые советы (рецепты, ремонт, покупки)
- личные вопросы (отношения, здоровье, психология)
- крипту, инвестиции, ставки
- мемы, анекдоты, развлечения

Если запрос НЕ про школу — ответь РОВНО ЭТОЙ ФРАЗОЙ и ничего больше:
"Это не школьное задание. Я помогаю только с домашней работой по школьным предметам."

Если запрос про школу — решай его.

ФОРМАТ ОТВЕТА (простой текст, без markdown, без звёздочек, без решёток):

- ТЕСТ с вариантами → "Правильный ответ: <точный текст варианта>".
- СООТНЕСЕНИЕ → каждая пара с новой строки: "термин — определение".
- РАСПРЕДЕЛЕНИЕ по группам → "Группа 1: слово, слово".
- ВЫБОР СЛОВ → перечисли нужные слова через запятую.
- ПЕРЕВОД → сначала перевод, потом краткое пояснение.
- ОТКРЫТЫЙ ВОПРОС → короткий чёткий ответ (2-4 предложения).
- ЗАДАЧА по математике/физике → полное решение по шагам, в конце "Ответ: <значение>".

ПРАВИЛА:
- В тестах выбирай ОДИН вариант и пиши его ТОЧНО как в задании.
- В drag-and-drop заданиях давай пары "определение → термин".
- НЕ пиши "Определим тип задания", "Разбор", "Решение", "Шаги", "Конечно", "Итак".
- НЕ повторяй одно и то же слово.
- Отвечай по-русски.
"""


def get_extra_hint(subject: str) -> str:
    if subject in ("russian", "literature"):
        return RUSSIAN_MATCHING
    if subject == "history":
        return HISTORY_MATCHING
    if subject == "geography":
        return GEOGRAPHY_MATCHING
    return ""


SUBJECT_HINTS = {
    "algebra": "Предмет: Алгебра. Реши пример/уравнение, проверь подстановкой. Ответ: число или выражение.",
    "geometry": "Предмет: Геометрия. Ответ: число с единицей или формула.",
    "russian": "Предмет: Русский язык. Если drag-and-drop — пары 'определение → термин'.",
    "literature": "Предмет: Литература. Кратко и точно. Если drag-and-drop — пары 'определение → термин'.",
    "history": "Предмет: История. Проверь даты. Если соответствие — 'событие → дата'.",
    "geography": "Предмет: География. НЕ перечисляй списки. В тестах — ОДИН вариант.",
    "biology": "Предмет: Биология. Отвечай точно.",
    "physics": "Предмет: Физика. Проверь единицы измерения.",
    "cs": "Предмет: Информатика. Проверь алгоритм.",
    "english": "Предмет: Английский язык. Проверь грамматику.",
    "chemistry": "Предмет: Химия. Проверь баланс уравнений.",
    "general": "Предмет: Общий. Определи сам и реши.",
}


async def _call_with_retry(messages: list, max_attempts: int = 3, timeout: float = 180.0) -> str:
    """Отправляет запрос в gpt-6-luna с retry."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            log.info("Попытка %d/%d, модель=%s, effort=%s",
                     attempt, max_attempts, MODEL, REASONING_EFFORT)
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    max_completion_tokens=4000,
                    reasoning_effort=REASONING_EFFORT,
                ),
                timeout=timeout,
            )
            log.info("Успех на попытке %d", attempt)
            return resp.choices[0].message.content
        except asyncio.TimeoutError:
            last_error = Exception(f"Таймаут {timeout} сек")
            log.warning("Попытка %d: таймаут %s сек", attempt, timeout)
            if attempt < max_attempts:
                continue
        except Exception as e:
            last_error = e
            status = getattr(e, "status_code", None)
            log.warning("Попытка %d не удалась: %s (status=%s)", attempt, e, status)
            if status in (429, 500, 502, 503) and attempt < max_attempts:
                wait = 2 ** attempt
                log.info("Ждём %d секунд...", wait)
                await asyncio.sleep(wait)
                continue
            break
    raise last_error


async def solve_text(question: str, subject: str = "general") -> str:
    hint = SUBJECT_HINTS.get(subject, SUBJECT_HINTS["general"])
    extra = get_extra_hint(subject)

    full_prompt = SYSTEM_PROMPT + extra + "\n\n" + hint + "\n\n--- ЗАДАНИЕ ---\n" + question

    messages = [
        {"role": "user", "content": full_prompt},
    ]

    try:
        return await _call_with_retry(messages, timeout=180.0)
    except Exception as e:
        log.error("Ошибка (текст): %s", e)
        return f"Ошибка: {e}"


async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    b64 = base64.b64encode(image_bytes).decode()
    hint = SUBJECT_HINTS.get(subject, SUBJECT_HINTS["general"])
    extra = get_extra_hint(subject)

    user_instruction = caption or (
        "Прочитай задание на картинке. Определи тип задания "
        "(тест / соответствие / drag-and-drop / открытый вопрос). "
        "Если тест — выбери ОДИН вариант из списка. "
        "Если drag-and-drop — дай пары 'определение → термин'. "
        "Отвечай коротко."
    )

    user_content = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT + extra + "\n\n" + hint + "\n\n--- ЗАДАНИЕ ---\n" + user_instruction,
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        },
    ]

    messages = [
        {"role": "user", "content": user_content},
    ]

    log.info("Отправляю запрос (фото), модель=%s, режим=%s, размер=%d байт",
             MODEL, subject, len(image_bytes))
    try:
        return await _call_with_retry(messages, timeout=180.0)
    except Exception as e:
        log.error("Ошибка (фото): %s", e)
        return f"Ошибка: {e}"
