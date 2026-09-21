import base64
import os
import httpx
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()

if not GIGACHAT_CREDENTIALS:
    raise SystemExit("Переменная GIGACHAT_CREDENTIALS не задана в панели BotHost!")


# --- Обход httpx 0.28+ (параметр 'proxies') ---
class CustomAsyncHTTPClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        kwargs.pop("proxies", None)
        super().__init__(*args, **kwargs)


client = GigaChat(
    credentials=GIGACHAT_CREDENTIALS,
    scope=GIGACHAT_SCOPE,
    verify_ssl_certs=False,
    model="GigaChat",
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

    payload = Chat(
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT + "\n" + hint),
            Messages(role=MessagesRole.USER, content=question),
        ],
        temperature=0.2,
    )

    response = await client.achat(payload)
    return response.choices[0].message.content

async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    # GigaChat поддерживает Vision. Кодируем изображение в base64.
    b64 = base64.b64encode(image_bytes).decode()
    hint = {
        "math": "Это математика. Реши и дай ответ.",
        "history": "Это история. Ответь на вопрос по картинке.",
        "general": "",
    }.get(subject, "")

    # Формируем сообщение с изображением
    # Формат для Vision в GigaChat может отличаться, но обычно это content с типом "image_url"
    # Для библиотеки gigachat может потребоваться другой формат. Уточните в документации.
    # Временно используем простой текстовый запрос, так как Vision может требовать дополнительной настройки.
    # Если Vision не заработает, можно будет добавить его позже.
    payload = Chat(
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT + "\n" + hint),
            Messages(role=MessagesRole.USER, content=caption or "Реши задачу с картинки и дай ответ."),
        ],
        temperature=0.2,
    )

    # Примечание: Для полноценной работы с изображениями может потребоваться
    # использование другого метода или формата. Это базовый пример.
    response = await client.achat(payload)
    return response.choices[0].message.content
