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
    timeout=120.0,
)

MODEL = "gpt-4o-mini"


SYSTEM_PROMPT = """Ты — эксперт-репетитор, который решает школьные задания без ошибок.

════════ КАК ТЫ РАБОТАЕШЬ ════════
1. СНАЧАЛА ПОДУМАЙ ПРО СЕБЯ (внутренний монолог):
   - Внимательно прочитай задание.
   - Определи, ЧТО именно требуется.
   - Перечисли варианты / шаги решения.
   - Проверь каждый вариант, отбрось неправильные.
   - Убедись, что ответ действительно верный.

2. ПОТОМ ВЫДАЙ ТОЛЬКО ГОТОВЫЙ ОТВЕТ.
   - НЕ показывай свои размышления.
   - НЕ пиши "подумаем", "рассмотрим", "вариант А не подходит".
   - Пользователь видит только финальный ответ.

════════ ФОРМАТ ОТВЕТА ════════
Простой текст, БЕЗ markdown, БЕЗ звёздочек, БЕЗ решёток.

- ТЕСТ с вариантами → "Правильный ответ: <точный текст варианта>".
- СООТНЕСЕНИЕ → каждая пара с новой строки: "термин — определение".
- РАСПРЕДЕЛЕНИЕ по группам → "Группа 1: слово, слово".
- ВЫБОР СЛОВ → перечисли нужные слова через запятую.
- ПЕРЕВОД → сначала перевод, потом краткое пояснение.
- ОТКРЫТЫЙ ВОПРОС → короткий чёткий ответ (2-4 предложения).
- ЗАДАЧА по математике/физике → полное решение по шагам, в конце "Ответ: <значение>".

════════ КАК ВЫБИРАТЬ В ТЕСТАХ ════════
Если в задании есть варианты ответа:
1. Прочитай ВСЕ варианты.
2. Отбрось заведомо неправильные.
3. Выбери один правильный.
4. Напиши его ТОЧНО ТАК, как он написан в задании (полный текст).
5. НЕ выдумывай свой ответ. НЕ пиши название объекта из вопроса.
6. НЕ перечисляй все варианты подряд.

Пример:
Вопрос: "В результате какого взаимодействия образовались Гималаи?"
Варианты: 1) столкновение океанической и материковой 2) столкновение двух материковых 3) параллельное смещение 4) расхождение
Ответ: "Правильный ответ: столкновение двух материковых плит"

════════ КРИТИЧЕСКИЕ ЗАПРЕТЫ ════════
- НЕ повторяй одно и то же слово или фразу.
- НЕ перечисляй длинные списки стран, регионов, провинций.
- НЕ пиши "Определим тип задания", "Разбор задания", "Решение", "Шаги".
- НЕ пиши "Конечно", "Давайте разберём", "Итак".
- НЕ задавай вопросы вместо ответа.

════════ ТОЧНОСТЬ ВАЖНЕЕ СКОРОСТИ ════════
Лучше подумать 5 секунд и дать правильный ответ, чем ответить за 1 секунду и ошибиться.
Проверь свой ответ ДВАЖДЫ перед выводом.
"""

SUBJECT_HINTS = {
    "algebra": "ТЕКУЩИЙ РЕЖИМ: Алгебра. Реши пример/уравнение, проверь подстановкой. Ответ: только число или выражение.",
    "geometry": "ТЕКУЩИЙ РЕЖИМ: Геометрия. Реши задачу. Ответ: число с единицей измерения или формула.",
    "russian": "ТЕКУЩИЙ РЕЖИМ: Русский язык. Реши упражнение. Ответ: буква/слово/знаки препинания.",
    "literature": "ТЕКУЩИЙ РЕЖИМ: Литература. Отвечай на вопрос по произведению точно и кратко.",
    "history": "ТЕКУЩИЙ РЕЖИМ: История. Отвечай на вопрос по истории. Проверь даты и события.",
    "geography": "ТЕКУЩИЙ РЕЖИМ: География. В тестах ВЫБИРАЙ ОДИН вариант из списка, пиши его точно.",
    "biology": "ТЕКУЩИЙ РЕЖИМ: Биология. Отвечай на вопрос по биологии точно.",
    "physics": "ТЕКУЩИЙ РЕЖИМ: Физика. Реши задачу, проверь единицы измерения.",
    "cs": "ТЕКУЩИЙ РЕЖИМ: Информатика. Реши задачу по информатике, проверь алгоритм.",
    "english": "ТЕКУЩИЙ РЕЖИМ: Английский язык. Переведи или реши задание, проверь грамматику.",
    "chemistry": "ТЕКУЩИЙ РЕЖИМ: Химия. Реши задачу, проверь баланс уравнений.",
    "general": "ТЕКУЩИЙ РЕЖИМ: Общий. Определи предмет и реши задание.",
}


async def _call_with_retry(messages: list, temp: float, max_attempts: int = 3, timeout: float = 120.0) -> str:
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            log.info("Попытка %d/%d, модель=%s", attempt, max_attempts, MODEL)
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=temp,
                    max_tokens=4000,  # ← увеличили с 1500 до 4000
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
    # Для точных наук ниже, для гуманитарных выше
    temp = 0.1 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.3

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + hint},
        {"role": "user", "content": question},
    ]

    try:
        return await _call_with_retry(messages, temp, timeout=120.0)
    except Exception as e:
        log.error("Ошибка (текст): %s", e)
        return f"Ошибка: {e}"


async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    b64 = base64.b64encode(image_bytes).decode()
    hint = SUBJECT_HINTS.get(subject, SUBJECT_HINTS["general"])
    temp = 0.1 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.2

    user_content = [
        {"type": "text", "text": caption or "Внимательно прочитай задание на картинке. Если это тест — выбери ОДИН правильный вариант и напиши его точно. Отвечай коротко."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + hint},
        {"role": "user", "content": user_content},
    ]

    log.info("Отправляю запрос (фото), модель=%s, режим=%s, размер=%d байт",
             MODEL, subject, len(image_bytes))
    try:
        return await _call_with_retry(messages, temp, timeout=120.0)
    except Exception as e:
        log.error("Ошибка (фото): %s", e)
        return f"Ошибка: {e}"
