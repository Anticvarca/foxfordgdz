import base64
import os
import logging
import httpx
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("solver")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

if not GEMINI_API_KEY:
    log.error("GEMINI_API_KEY ПУСТОЙ! Задай переменную в панели BotHost.")
else:
    log.info("GEMINI_API_KEY получен, длина %d", len(GEMINI_API_KEY))


class CustomAsyncHTTPClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        kwargs.pop("proxies", None)
        super().__init__(*args, **kwargs)


client = AsyncOpenAI(
    api_key=GEMINI_API_KEY or "dummy",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    http_client=CustomAsyncHTTPClient(),
)


SYSTEM_PROMPT = """Ты — бот, который решает ТОЛЬКО школьные домашние задания.
Твоя единственная задача — помогать со школьными предметами (математика, русский, литература, история, физика, химия, биология, география, информатика, английский и т.п.).

════════ ГЛАВНОЕ ПРАВИЛО ════════
Если запрос НЕ про школу и НЕ про домашку — ОТКАЖИСЬ одной короткой фразой:
"Я решаю только школьные задания. Пришли задачу по предмету."
Не объясняй, не извиняйся, не предлагай альтернатив. Просто отказ и всё.

Примеры НЕшкольных запросов, на которые надо отказывать:
- "сделай мод для майнкрафта"
- "почини компьютер"
- "напиши код на Python"
- "расскажи анекдот"
- "как приготовить борщ"
- "переведи текст" (если это не задание по английскому)
- "поговори со мной"
- любые вопросы про игры, технику, быт, отношения, политику, религию

════════ ЕСЛИ ЗАПРОС ПРО ШКОЛУ ════════
Отвечай СРАЗУ ПО ДЕЛУ, без вступлений.

СТРОГО ЗАПРЕЩЕНО писать:
- "Определим тип задания", "Разбор задания", "Решение", "Шаги", "Проверка"
- markdown-заголовки ###, ---, ***, жирный шрифт через **
- вступления "Конечно", "Давайте разберём", "Итак"

ФОРМАТ ОТВЕТА — простой текст, БЕЗ звёздочек и решёток:

1. ТЕСТ (кружки/галочки) → "Правильный ответ: <текст варианта>". Одна короткая строка почему.
2. СООТНЕСЕНИЕ (два столбца) → каждая пара с новой строки: "термин — определение".
3. РАСПРЕДЕЛЕНИЕ по группам/цветам → "Группа 1: слово, слово, слово" и т.д.
4. ВЫБОР СЛОВ из списка → просто перечисли нужные слова через запятую.
5. ОТКРЫТЫЙ ВОПРОС → короткий чёткий ответ (2-4 предложения).
6. ЗАДАЧА по математике/физике → решение по шагам, в конце строка "Ответ: <значение>".

ПИШИ СРАЗУ ОТВЕТ. Никаких прелюдий. Перечисляй слова полностью.
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

    resp = await client.chat.completions.create(
        model="gemini-3.6-flash",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + hint},
            {"role": "user", "content": question},
        ],
        temperature=temp,
        max_tokens=2000,
    )
    return resp.choices[0].message.content


async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    b64 = base64.b64encode(image_bytes).decode()
    hint = SUBJECT_HINTS.get(subject, "")
    temp = 0.2 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.4

    user_content = [
        {"type": "text", "text": caption or "Реши задание с картинки. Сразу дай ответ."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    resp = await client.chat.completions.create(
        model="gemini-3.6-flash",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + hint},
            {"role": "user", "content": user_content},
        ],
        temperature=temp,
        max_tokens=2000,
    )
    return resp.choices[0].message.content
