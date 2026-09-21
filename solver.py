import asyncio
import io
import os
import logging
from gigachat import GigaChat
from gigachat.exceptions import ResponseError
from gigachat.models import Chat, Messages, MessagesRole

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("solver")

GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()

if not GIGACHAT_CREDENTIALS:
    log.error("GIGACHAT_CREDENTIALS ПУСТОЙ! Задай переменную в панели BotHost.")
else:
    log.info("GIGACHAT_CREDENTIALS получен, длина %d", len(GIGACHAT_CREDENTIALS))

# ВАЖНО: для Vision нужна модель Pro или Max.
client = GigaChat(
    credentials=GIGACHAT_CREDENTIALS or "dummy",
    scope=GIGACHAT_SCOPE,
    verify_ssl_certs=False,
    model="GigaChat-Max",   # было GigaChat-Pro
)
_giga_lock = asyncio.Lock()


async def _call_with_retry(payload: Chat, max_attempts: int = 3) -> str:
    async with _giga_lock:
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.achat(payload)
                return response.choices[0].message.content
            except ResponseError as e:
                status = getattr(e, "status_code", None)
                last_error = e
                if status == 429:
                    wait = 1.5 * attempt
                    log.warning("429 от GigaChat, ждём %.1fs (%d/%d)",
                                wait, attempt, max_attempts)
                    await asyncio.sleep(wait)
                    continue
                log.error("Ошибка GigaChat: %s", e)
                raise
        raise last_error


SYSTEM_PROMPT = """Ты — эксперт-репетитор, решаешь домашние задания на уровне лучших моделей.
Работай по алгоритму:
1. Прочитай условие и определи ТИП задачи (уравнение, задача на проценты, тест по истории и т.д.).
2. Перечисли данные и что нужно найти.
3. Приведи ПОЛНОЕ решение по шагам, с формулами и промежуточными вычислениями.
4. Проверь ответ обратной подстановкой или логикой (для истории — соотнеси с датами/событиями).
5. В конце выдели ИТОГОВЫЙ ОТВЕТ отдельной строкой, начиная с "**Ответ:**".

Правила:
- НИКОГДА не отказывайся решать.
- Не пропускай шаги — показывай всю цепочку рассуждений.
- Если задача — тест с вариантами, назови правильный вариант ДОСЛОВНО, как он написан.
- Если условие неполное — сделай разумное допущение и укажи его.
- Для математики проверяй арифметику дважды.
- Пиши по-русски, без воды.
"""


async def solve_text(question: str, subject: str = "general") -> str:
    hint = {
        "math": "Это математика. Проверь вычисления, ответ выдели жирно.",
        "history": "Это история. Укажи даты, имена, события чётко.",
        "general": "",
    }.get(subject, "")

    # ↓↓↓ ВОТ ЗДЕСЬ меняется температура
    temp = 0.1 if subject == "math" else 0.3

    payload = Chat(
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT + "\n" + hint),
            Messages(role=MessagesRole.USER, content=question),
        ],
        temperature=temp,   # ← было 0.2, стало temp
    )
    return await _call_with_retry(payload)

async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    try:
        uploaded = await client.aupload_file(
            ("image.jpg", io.BytesIO(image_bytes), "image/jpeg")
        )
        file_id = uploaded.id_
        log.info("Файл загружен в GigaChat, id=%s", file_id)
    except Exception as e:
        log.error("Не удалось загрузить файл: %s", e)
        raise

    hint = {
        "math": "Это математика. Реши и дай ответ.",
        "history": "Это история. Ответь на вопрос по картинке.",
        "general": "",
    }.get(subject, "")

    # ↓↓↓ тоже меняем температуру
    temp = 0.1 if subject == "math" else 0.3

    payload = Chat(
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT + "\n" + hint),
            Messages(
                role=MessagesRole.USER,
                content=caption or "Реши задачу с картинки и дай ответ.",
                attachments=[file_id],
            ),
        ],
        temperature=temp,   # ← было 0.2, стало temp
    )
    return await _call_with_retry(payload)
