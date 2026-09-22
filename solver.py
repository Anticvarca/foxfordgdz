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

# Обычный клиент OpenAI
client = AsyncOpenAI(
    api_key=OPENAI_API_KEY or "dummy",
    timeout=60.0,
)

# Одна модель для всего — gpt-4o
MODEL = "gpt-4o"


SYSTEM_PROMPT = """Ты — бот, который РЕШАЕТ школьные домашние задания. Ты не задаёшь вопросы и не даёшь задания — ты выполняешь их.

ГЛАВНОЕ: пользователь присылает тебе готовое задание. Твоя задача — дать готовый ответ.
НИКОГДА не переспрашивай, не проси уточнить, не пересказывай условие.

════════ ФОРМАТ ОТВЕТА ════════
Простой текст, БЕЗ markdown-заголовков, БЕЗ звёздочек, БЕЗ решёток.

- ТЕСТ → "Правильный ответ: <текст варианта>".
- СООТНЕСЕНИЕ → каждая пара с новой строки: "термин — определение".
- РАСПРЕДЕЛЕНИЕ по группам → "Группа 1: слово, слово".
- ВЫБОР СЛОВ → перечисли нужные слова через запятую.
- ПЕРЕВОД → сначала перевод, потом краткое пояснение.
- ОТКРЫТЫЙ ВОПРОС → короткий чёткий ответ (2-4 предложения).
- ЗАДАЧА по математике/физике → решение по шагам, в конце "Ответ: <значение>".

════════ ОСОБОЕ ПРАВИЛО ДЛЯ ТЕСТОВ ════════
Если в задании есть варианты ответа (кружки, галочки, список):
- ВЫБЕРИ ОДИН вариант из списка.
- Напиши его ТОЧНО ТАК, как он написан в задании.
- НЕ выдумывай свой ответ.
- НЕ перечисляй все варианты подряд.

════════ КРИТИЧЕСКИЕ ЗАПРЕТЫ ════════
- НЕ повторяй одно и то же слово или фразу несколько раз.
- НЕ перечисляй длинные списки стран, регионов, провинций.
- Отвечай КОРОТКО.

ЗАПРЕЩЕНО:
- Задавать вопросы вместо ответа.
- Повторять условие задания.
- Писать "Определим тип задания", "Разбор задания", "Решение", "Шаги".
- Писать "Конечно", "Давайте разберём", "Итак".
"""

SUBJECT_HINTS = {
    "algebra": "ТЕКУЩИЙ РЕЖИМ: Алгебра. Решай математические примеры и уравнения.",
    "geometry": "ТЕКУЩИЙ РЕЖИМ: Геометрия. Решай задачи по геометрии.",
    "russian": "ТЕКУЩИЙ РЕЖИМ: Русский язык. Решай упражнения по русскому.",
    "literature": "ТЕКУЩИЙ РЕЖИМ: Литература. Отвечай на вопросы по литературе.",
    "history": "ТЕКУЩИЙ РЕЖИМ: История. Отвечай на вопросы по истории.",
    "geography": "ТЕКУЩИЙ РЕЖИМ: География. В тестах ВЫБИРАЙ ОДИН вариант из списка.",
    "biology": "ТЕКУЩИЙ РЕЖИМ: Биология. Отвечай на вопросы по биологии.",
    "physics": "ТЕКУЩИЙ РЕЖИМ: Физика. Решай задачи, ответ с единицей измерения.",
    "cs": "ТЕКУЩИЙ РЕЖИМ: Информатика. Решай задачи по информатике.",
    "english": "ТЕКУЩИЙ РЕЖИМ: Английский язык. Переводи и решай задания.",
    "chemistry": "ТЕКУЩИЙ РЕЖИМ: Химия. Решай задачи по химии.",
    "general": "ТЕКУЩИЙ РЕЖИМ: Общий. Определи предмет сам и реши задание.",
}


async def _call_with_retry(messages: list, temp: float, max_attempts: int = 3, timeout: float = 60.0) -> str:
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            log.info("Попытка %d/%d, модель=%s", attempt, max_attempts, MODEL)
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=temp,
                    max_tokens=1500,
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
    temp = 0.2 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.3

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + hint},
        {"role": "user", "content": question},
    ]

    try:
        return await _call_with_retry(messages, temp, timeout=60.0)
    except Exception as e:
        log.error("Ошибка (текст): %s", e)
        return f"Ошибка: {e}"


async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    b64 = base64.b64encode(image_bytes).decode()
    hint = SUBJECT_HINTS.get(subject, SUBJECT_HINTS["general"])
    temp = 0.1 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.2

    user_content = [
        {"type": "text", "text": caption or "Реши задание с картинки. Если это тест — выбери ОДИН вариант из списка. Отвечай коротко."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + hint},
        {"role": "user", "content": user_content},
    ]

    log.info("Отправляю запрос (фото), модель=%s, режим=%s, размер=%d байт",
             MODEL, subject, len(image_bytes))
    try:
        return await _call_with_retry(messages, temp, timeout=60.0)
    except Exception as e:
        log.error("Ошибка (фото): %s", e)
        return f"Ошибка: {e}"
