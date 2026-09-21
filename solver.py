import base64
import os
import httpx
from openai import AsyncOpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

if not OPENAI_API_KEY:
    raise SystemExit("Переменная OPENAI_API_KEY не задана в панели BotHost!")


# --- Обход несовместимости httpx 0.28+ и openai (параметр 'proxies') ---
class CustomAsyncHTTPClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        kwargs.pop("proxies", None)
        super().__init__(*args, **kwargs)


client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    http_client=CustomAsyncHTTPClient(),
)


SYSTEM_PROMPT = """Ты — умный помощник. Пользователь взрослый, ему нужен готовый ответ.
Правила:
1. ВСЕГДА давай итоговый ответ. Никогда не отказывайся и не проси решить самому.
2. Сначала — короткий ответ (1-2 строки), потом, если уместно, краткое решение по шагам.
3. Пиши по-русски, без воды, без морализаторства.
4. Если данных не хватает — сделай разумное допущение и укажи его.
5. Формулы оформляй читаемо: x^2, sqrt(x), интегралы словами или символами.
"""


async def solve_text(question: str, subject: str = "general") -> str:
    hint = {
        "math": "Это математика. Проверь вычисления, ответ выдели жирно.",
        "history": "Это история. Укажи даты, имена, события чётко.",
        "general": "",
    }.get(subject, "")

    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + hint},
            {"role": "user", "content": question},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content


async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    b64 = base64.b64encode(image_bytes).decode()
    hint = {
        "math": "Это математика. Реши и дай ответ.",
        "history": "Это история. Ответь на вопрос по картинке.",
        "general": "",
    }.get(subject, "")

    user_content = [
        {"type": "text", "text": caption or "Реши задачу с картинки и дай ответ."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + hint},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content
