import base64
import os
import logging
import asyncio
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("solver")

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()

if not NVIDIA_API_KEY:
    log.error("NVIDIA_API_KEY ПУСТОЙ! Задай переменную в панели BotHost.")
else:
    log.info("NVIDIA_API_KEY получен, длина %d", len(NVIDIA_API_KEY))

client = AsyncOpenAI(
    api_key=NVIDIA_API_KEY or "dummy",
    base_url="https://integrate.api.nvidia.com/v1",
    timeout=120.0,
)

PRIMARY_TEXT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
FALLBACK_TEXT_MODEL = "nvidia/nemotron-3-nano-30b-a3b"
# Быстрая vision-модель вместо тяжёлой 90B
IMAGE_MODEL = "meta/llama-3.2-11b-vision-instruct"


SYSTEM_PROMPT = """Ты — бот, который РЕШАЕТ школьные домашние задания. Ты не задаёшь вопросы и не даёшь задания — ты выполняешь их.

ГЛАВНОЕ: пользователь присылает тебе готовое задание. Твоя задача — дать готовый ответ.
НИКОГДА не переспрашивай, не проси уточнить, не пересказывай условие, не задавай вопросы обратно.

════════ ЧТО ДЕЛАТЬ ════════
1. Прочитай задание (текст или фото).
2. Реши его.
3. Выведи ответ в нужном формате (см. ниже).

════════ ФОРМАТ ОТВЕТА ════════
Простой текст, БЕЗ markdown-заголовков, БЕЗ звёздочек, БЕЗ решёток.

- ТЕСТ → "Правильный ответ: <текст варианта>".
- СООТНЕСЕНИЕ → каждая пара с новой строки: "термин — определение".
- РАСПРЕДЕЛЕНИЕ по группам → "Группа 1: слово, слово".
- ВЫБОР СЛОВ → перечисли нужные слова через запятую.
- ПЕРЕВОД → сначала перевод, потом краткое пояснение.
- ОТКРЫТЫЙ ВОПРОС → короткий чёткий ответ (2-4 предложения).
- ЗАДАЧА по математике/физике → решение по шагам, в конце "Ответ: <значение>".

ЗАПРЕЩЕНО:
- Задавать пользователю вопросы вместо ответа.
- Повторять условие задания.
- Писать "Определим тип задания", "Разбор задания", "Решение", "Шаги", "Проверка".
- Писать "Конечно", "Давайте разберём", "Итак".
"""

SUBJECT_HINTS = {
    "algebra": "ТЕКУЩИЙ РЕЖИМ: Алгебра. Решай математические примеры и уравнения.",
    "geometry": "ТЕКУЩИЙ РЕЖИМ: Геометрия. Решай задачи по геометрии.",
    "russian": "ТЕКУЩИЙ РЕЖИМ: Русский язык. Решай упражнения по русскому.",
    "literature": "ТЕКУЩИЙ РЕЖИМ: Литература. Отвечай на вопросы по литературным произведениям. НЕ задавай вопросы — ОТВЕЧАЙ.",
    "history": "ТЕКУЩИЙ РЕЖИМ: История. Отвечай на вопросы по истории.",
    "geography": "ТЕКУЩИЙ РЕЖИМ: География. Отвечай на вопросы по географии.",
    "biology": "ТЕКУЩИЙ РЕЖИМ: Биология. Отвечай на вопросы по биологии.",
    "physics": "ТЕКУЩИЙ РЕЖИМ: Физика. Решай задачи, ответ с единицей измерения.",
    "cs": "ТЕКУЩИЙ РЕЖИМ: Информатика. Решай задачи по информатике.",
    "english": "ТЕКУЩИЙ РЕЖИМ: Английский язык. Переводи и решай задания по английскому.",
    "chemistry": "ТЕКУЩИЙ РЕЖИМ: Химия. Решай задачи по химии.",
    "general": "ТЕКУЩИЙ РЕЖИМ: Общий. Определи предмет сам и реши задание.",
}


async def _call_with_retry(model: str, messages: list, temp: float, max_attempts: int = 3, timeout: float = 120.0) -> str:
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            log.info("Попытка %d/%d, модель=%s", attempt, max_attempts, model)
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
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
            if status in (503, 429) and attempt < max_attempts:
                wait = 2 ** attempt
                log.info("Ждём %d секунд...", wait)
                await asyncio.sleep(wait)
                continue
            break
    raise last_error


async def solve_text(question: str, subject: str = "general") -> str:
    hint = SUBJECT_HINTS.get(subject, SUBJECT_HINTS["general"])
    temp = 0.2 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.4

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + hint},
        {"role": "user", "content": question},
    ]

    try:
        return await _call_with_retry(PRIMARY_TEXT_MODEL, messages, temp, timeout=60.0)
    except Exception as e:
        log.warning("Основная модель не справилась: %s. Пробую запасную", e)

    try:
        return await _call_with_retry(FALLBACK_TEXT_MODEL, messages, temp, timeout=60.0)
    except Exception as e:
        log.error("Обе модели не справились: %s", e)
        return f"Ошибка: {e}"


async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    b64 = base64.b64encode(image_bytes).decode()
    hint = SUBJECT_HINTS.get(subject, SUBJECT_HINTS["general"])
    temp = 0.2 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.4

    user_content = [
        {"type": "text", "text": caption or "Реши задание с картинки. Сразу дай готовый ответ."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + hint},
        {"role": "user", "content": user_content},
    ]

    log.info("Отправляю запрос (фото), модель=%s, режим=%s, размер=%d байт",
             IMAGE_MODEL, subject, len(image_bytes))
    try:
        return await _call_with_retry(IMAGE_MODEL, messages, temp, timeout=120.0)
    except Exception as e:
        log.error("Ошибка (фото): %s", e)
        return f"Ошибка: {e}"
