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

client = GigaChat(
    credentials=GIGACHAT_CREDENTIALS or "dummy",
    scope=GIGACHAT_SCOPE,
    verify_ssl_certs=False,
    model="GigaChat-Max",
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


SYSTEM_PROMPT = """Ты — эксперт-репетитор. Решаешь школьные задания по фото и тексту.

ГЛАВНОЕ ПРАВИЛО: сначала определи, ЧТО именно требуется в задании, и отвечай в том формате, который нужен.

Типы заданий и форматы ответа:

1. ТЕСТ с вариантами (кружки для выбора):
   → Назови номер и текст правильного варианта. Кратко почему.

2. СООТНЕСЕНИЕ (даны два столбца, нужно сопоставить):
   → Перечисли пары в виде "термин — определение" (полными словами, не цифрами).
   Пример:
   Фермер — владелец или арендатор земли...
   Буржуазия — господствующий слой...

3. РАСПРЕДЕЛЕНИЕ по категориям/цветам (нужно разложить слова по группам):
   → Разбей на группы с заголовками. В каждой группе перечисли подходящие слова полностью.
   Пример:
   Средневековая эпоха (голубой): крепостное право, натуральное хозяйство, оброк, традиционное общество.
   Новое время (жёлтый): каравелла, биржа, мануфактура.

4. ОТКРЫТЫЙ ВОПРОС (нужно написать ответ своими словами):
   → Дай развёрнутый ответ на 2-4 предложения.

5. ЗАДАЧА (математика, физика):
   → Реши, покажи ход, в конце выдели ответ.

6. ВЫБОР СЛОВ ИЗ СПИСКА (нужно нажать на слова):
   → Перечисли слова полностью, через запятую.

ЗАПРЕЩЕНО:
- Отвечать цифрами "1-5, 2-3" если в задании нет нумерованных вариантов.
- Использовать шаблонный формат, который не подходит к заданию.
- Пропускать термины/слова — перечисляй ВСЕ подходящие.

Пиши по-русски, ясно и по делу. Без лишних вступлений.
"""

SUBJECT_HINTS = {
    "algebra": "Это алгебра. Реши и дай финальный ответ.",
    "geometry": "Это геометрия. Реши и дай финальный ответ.",
    "russian": "Это русский язык.",
    "literature": "Это литература.",
    "history": "Это история.",
    "geography": "Это география.",
    "biology": "Это биология.",
    "physics": "Это физика. Дай финальный ответ с единицей измерения.",
    "cs": "Это информатика.",
    "english": "Это английский язык.",
    "chemistry": "Это химия.",
    "general": "",
}


async def solve_text(question: str, subject: str = "general") -> str:
    hint = SUBJECT_HINTS.get(subject, "")
    temp = 0.2 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.4

    payload = Chat(
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT + "\n" + hint),
            Messages(role=MessagesRole.USER, content=question),
        ],
        temperature=temp,
        max_tokens=1000,
    )
    return await _call_with_retry(payload)


async def solve_image(image_bytes: bytes, caption: str = "", subject: str = "general") -> str:
    try:
        uploaded = await client.aupload_file(
            ("image.jpg", io.BytesIO(image_bytes), "image/jpeg")
        )
        file_id = uploaded.id_
        log.info("Файл загружен, id=%s", file_id)
    except Exception as e:
        log.error("Не удалось загрузить файл: %s", e)
        raise

    hint = SUBJECT_HINTS.get(subject, "")
    temp = 0.2 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.4

    payload = Chat(
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT + "\n" + hint),
            Messages(
                role=MessagesRole.USER,
                content=caption or "Реши задание с картинки. Определи тип задания и ответь в нужном формате.",
                attachments=[file_id],
            ),
        ],
        temperature=temp,
        max_tokens=1000,
    )
    return await _call_with_retry(payload)
