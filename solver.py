import base64
import os
import logging
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("solver")

GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()

if not GIGACHAT_CREDENTIALS:
    log.error("GIGACHAT_CREDENTIALS ПУСТОЙ! Задай переменную в панели BotHost.")
else:
    log.info("GIGACHAT_CREDENTIALS получен, длина %d", len(GIGACHAT_CREDENTIALS))

# Создаём клиент даже с пустым ключом, чтобы импорт не падал.
client = GigaChat(
    credentials=GIGACHAT_CREDENTIALS or "dummy",
    scope=GIGACHAT_SCOPE,
    verify_ssl_certs=False,
    model="GigaChat",
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
    # GigaChat пока без Vision в этом коде — работаем по подписи
    hint = {
        "math": "Это математика. Реши и дай ответ.",
        "history": "Это история. Ответь на вопрос по картинке.",
        "general": "",
    }.get(subject, "")

    payload = Chat(
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT + "\n" + hint),
            Messages(role=MessagesRole.USER, content=caption or "Реши задачу с картинки и дай ответ."),
        ],
        temperature=0.2,
    )

    response = await client.achat(payload)
    return response.choices[0].message.content
