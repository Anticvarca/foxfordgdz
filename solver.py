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

# Таймаут 90 секунд — если NVIDIA не отвечает, упадём с понятной ошибкой
client = AsyncOpenAI(
    api_key=NVIDIA_API_KEY or "dummy",
    base_url="https://integrate.api.nvidia.com/v1",
    timeout=90.0,
)

# Разные модели для текста и фото
TEXT_MODEL = "meta/llama-3.3-70b-instruct"
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


async def solve_text(question: str, subject: str = "general") -> str:
    hint = SUBJECT_HINTS.get(subject, "")
    temp = 0.2 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.4

    log.info("Отправляю запрос в NVIDIA (текст), модель=%s", TEXT_MODEL)
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT + "\n" + hint},
                    {"role": "user", "content": question},
                ],
                temperature=temp,
                max_tokens=1500,
            ),
            timeout=90.0,
        )
        log.info("Ответ получен от NVIDIA (текст)")
        return resp.choices[0].message.content
    except asyncio.TimeoutError:
        log.error("NVIDIA не ответил за 90 секунд (текст)")
        return "Модель не ответила вовремя. Попробуй ещё раз."
    except Exception as e:
        log.error("Ошибка NVIDIA (текст): %s", e)
        return f"Ошибка: {e}"


async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    b64 = base64.b64encode(image_bytes).decode()
    hint = SUBJECT_HINTS.get(subject, "")
    temp = 0.2 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.4

    user_content = [
        {"type": "text", "text": caption or "Реши задание с картинки. Сразу дай ответ."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    log.info("Отправляю запрос в NVIDIA (фото), модель=%s, размер=%d байт", IMAGE_MODEL, len(image_bytes))
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=IMAGE_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT + "\n" + hint},
                    {"role": "user", "content": user_content},
                ],
                temperature=temp,
                max_tokens=1500,
            ),
            timeout=90.0,
        )
        log.info("Ответ получен от NVIDIA (фото)")
        return resp.choices[0].message.content
    except asyncio.TimeoutError:
        log.error("NVIDIA не ответил за 90 секунд (фото)")
        return "Модель не ответила вовремя. Попробуй ещё раз."
    except Exception as e:
        log.error("Ошибка NVIDIA (фото): %s", e)
        return f"Ошибка: {e}"
