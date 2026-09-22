import base64
import os
import logging
import httpx
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("solver")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

if not OPENAI_API_KEY:
    log.error("OPENAI_API_KEY ПУСТОЙ! Задай переменную в панели BotHost.")
else:
    log.info("OPENAI_API_KEY получен, длина %d", len(OPENAI_API_KEY))


class CustomAsyncHTTPClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        kwargs.pop("proxies", None)
        super().__init__(*args, **kwargs)


client = AsyncOpenAI(
    api_key=OPENAI_API_KEY or "dummy",
    http_client=CustomAsyncHTTPClient(),
)


SYSTEM_PROMPT = """Ты — бот-помощник по школьным домашним заданиям.
Ты помогаешь с предметами: математика, русский, литература, история, физика, химия, биология, география, информатика, английский и т.п.

════════ КОГДА ПОМОГАТЬ ════════
Помогай, если запрос хотя бы отдалённо связан со школой:
- задачи, примеры, уравнения, переводы, сочинения, тесты;
- вопросы по темам школьных предметов;
- просьбы объяснить правило, формулу, дату, термин;
- переводы на английский и с английского (даже короткие фразы вроде "переведи how are you");
- приветствия и болтовня — отвечай коротко и мягко возвращай к теме: "Привет! Что задали? Пришли задачу — решу."

════════ КОГДА ОТКАЗЫВАТЬ ════════
Отказывай ТОЛЬКО на явно нешкольные темы:
- игры (Minecraft, Roblox, CS и т.п.), моды, читы;
- починка/настройка компьютера, телефона, программ;
- рецепты, быт, отношения, политика, религия;
- программирование как хобби (но если это урок информатики — помогай).

Отказ должен быть коротким: "Это не школьное задание. Пришли задачу по предмету."
Без извинений, без морали.

════════ КАК ОТВЕЧАТЬ НА ШКОЛЬНОЕ ════════
Отвечай СРАЗУ ПО ДЕЛУ, без вступлений.

ЗАПРЕЩЕНО писать:
- "Определим тип задания", "Разбор задания", "Решение", "Шаги", "Проверка"
- markdown-заголовки ###, ---, ***, жирный через **
- вступления "Конечно", "Давайте разберём", "Итак"

ФОРМАТ ОТВЕТА — простой текст, БЕЗ звёздочек и решёток:

1. ТЕСТ → "Правильный ответ: <текст варианта>". Одна строка почему.
2. СООТНЕСЕНИЕ → каждая пара с новой строки: "термин — определение".
3. РАСПРЕДЕЛЕНИЕ по группам → "Группа 1: слово, слово" и т.д.
4. ВЫБОР СЛОВ → перечисли нужные слова через запятую.
5. ПЕРЕВОД → сначала перевод, потом (если нужно) краткое пояснение.
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

    resp = await client.chat.completions.create(
        model="gpt-4o",
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
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + hint},
            {"role": "user", "content": user_content},
        ],
        temperature=temp,
        max_tokens=2000,
    )
    return resp.choices[0].message.content
