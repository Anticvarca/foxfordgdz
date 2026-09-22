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
    timeout=90.0,
)

# Основная и запасная модели для текста
PRIMARY_TEXT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
FALLBACK_TEXT_MODEL = "nvidia/nemotron-3-nano-30b-a3b"
IMAGE_MODEL = "meta/llama-3.2-90b-vision-instruct"


SYSTEM_PROMPT = """Ты — бот-помощник по школьным домашним заданиям.
Ты помогаешь с предметами: математика, русский, литература, история, физика, химия, биология, география, информатика, английский и т.п.

════════ КОГДА ПОМОГАТЬ ════════
Помогай, если запрос хотя бы отдалённо связан со школой:
- задачи, примеры, уравнения, переводы, сочинения, тесты;
- вопросы по темам школьных предметов;
- просьбы объяснить правило, формулу, дату, термин;
- переводы на английский и с английского;
- приветствия и болтовня — отвечай коротко и мягко возвращай к теме.

════════ КОГДА ОТКАЗЫВАТЬ ════════
Отказывай ТОЛЬКО на явно нешкольные темы:
- игры, моды, читы;
- починка/настройка компьютера, телефона;
- рецепты, быт, отношения, политика, религия;
- программирование как хобби (но если это урок информатики — помогай).

Отказ: "Это не школьное задание. Пришли задачу по предмету."

════════ КАК ОТВЕЧАТЬ НА ШКОЛЬНОЕ ════════
Отвечай СРАЗУ ПО ДЕЛУ, без вступлений.

ЗАПРЕЩЕНО писать:
- "Определим тип задания", "Разбор задания", "Решение", "Шаги", "Проверка"
- markdown-заголовки ###, ---, ***, жирный через **
- вступления "Конечно", "Давайте разберём", "Итак"

ФОРМАТ ОТВЕТА — простой текст, БЕЗ звёздочек и решёток:

1. ТЕСТ → "Правильный ответ: <текст варианта>".
2. СООТНЕСЕНИЕ → каждая пара с новой строки: "термин — определение".
3. РАСПРЕДЕЛЕНИЕ по группам → "Группа 1: слово, слово".
4. ВЫБОР СЛОВ → перечисли нужные слова через запятую.
5. ПЕРЕВОД → сначала перевод, потом краткое пояснение.
6. ОТКРЫТЫЙ ВОПРОС → короткий чёткий ответ (2-4 предложения).
7. ЗАДАЧА по математике/физике → решение по шагам, в конце "Ответ: <значение>".

ПИШИ СРАЗУ ОТВЕТ. Перечисляй слова полностью.
"""

SUBJECT_HINTS = {
    "algebra": "Это алгебра.",
    "geometry": "Это геометрия.",
    "russian": "Это русский язык.",
    "literature": "Это литература.",
    "history": "Это история.",
    "geography": "Это география.",
    "biology": "Это биология.",
    "physics": "Это физика. Ответ с единицей измерения.",
    "cs": "Это информатика.",
    "english": "Это английский язык.",
    "chemistry": "Это химия.",
    "general": "",
}


async def _call_with_retry(model: str, messages: list, temp: float, max_attempts: int = 3) -> str:
    """Отправляет запрос с retry при 503/429."""
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
                timeout=90.0,
            )
            log.info("Успех на попытке %d", attempt)
            return resp.choices[0].message.content
        except Exception as e:
            last_error = e
            status = getattr(e, "status_code", None)
            log.warning("Попытка %d не удалась: %s (status=%s)", attempt, e, status)
            if status in (503, 429) and attempt < max_attempts:
                wait = 2 ** attempt  # 2, 4, 8 секунд
                log.info("Ждём %d секунд и повторяем...", wait)
                await asyncio.sleep(wait)
                continue
            break
    raise last_error


async def solve_text(question: str, subject: str = "general") -> str:
    hint = SUBJECT_HINTS.get(subject, "")
    temp = 0.2 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.4

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n" + hint},
        {"role": "user", "content": question},
    ]

    # Сначала пробуем основную модель
    try:
        return await _call_with_retry(PRIMARY_TEXT_MODEL, messages, temp)
    except Exception as e:
        log.warning("Основная модель %s не справилась: %s. Пробую запасную %s",
                    PRIMARY_TEXT_MODEL, e, FALLBACK_TEXT_MODEL)

    # Если основная не справилась — пробуем запасную
    try:
        return await _call_with_retry(FALLBACK_TEXT_MODEL, messages, temp)
    except Exception as e:
        log.error("Обе модели не справились: %s", e)
        return f"Ошибка: {e}"


async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    b64 = base64.b64encode(image_bytes).decode()
    hint = SUBJECT_HINTS.get(subject, "")
    temp = 0.2 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.4

    user_content = [
        {"type": "text", "text": caption or "Реши задание с картинки. Сразу дай ответ."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n" + hint},
        {"role": "user", "content": user_content},
    ]

    log.info("Отправляю запрос в NVIDIA (фото), модель=%s, размер=%d байт", IMAGE_MODEL, len(image_bytes))
    try:
        return await _call_with_retry(IMAGE_MODEL, messages, temp)
    except Exception as e:
        log.error("Ошибка NVIDIA (фото): %s", e)
        return f"Ошибка: {e}"
