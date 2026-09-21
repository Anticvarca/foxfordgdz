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


# --- КОРОТКИЙ промт: только ответ, без воды ---
SYSTEM_PROMPT = """Ты решаешь домашку. Даёшь ТОЛЬКО ответ, без объяснений и рассуждений.

ФОРМАТ ОТВЕТА:
- Тест с вариантами → напиши ДОСЛОВНО правильный вариант (или его номер).
- Задача → только итоговое значение (число, слово, короткая фраза).
- Соотнесение понятий → пары вида "1-5, 2-3, 3-2, 4-1, 5-4".
- Несколько подпунктов → каждый с новой строки, без пояснений.
- Формулы → коротко, только финальная формула/значение.

ЗАПРЕЩЕНО:
- Писать "Решение", "Шаги", "Проверка", "Объяснение".
- Комментировать, рассуждать, вступать в диалог.
- Повторять условие.
- Давать больше 3 строк, если это не список подпунктов.
"""

# Подсказки по предметам
SUBJECT_HINTS = {
    "algebra": "Это алгебра. Посчитай дважды, дай только финальный ответ.",
    "geometry": "Это геометрия. Дай только финальный ответ (число/формула).",
    "russian": "Это русский язык. Дай только ответ (букву/слово/знаки).",
    "literature": "Это литература. Дай только ответ (имя/название/цитата коротко).",
    "history": "Это история. Дай только ответ (дата/имя/вариант).",
    "geography": "Это география. Дай только ответ.",
    "biology": "Это биология. Дай только ответ.",
    "physics": "Это физика. Дай только финальный ответ с единицей измерения.",
    "cs": "Это информатика. Дай только ответ (число/код/команду).",
    "english": "Это английский. Дай только ответ (слово/форму/букву).",
    "chemistry": "Это химия. Дай только ответ (формулу/значение).",
    "general": "",
}


async def solve_text(question: str, subject: str = "general") -> str:
    hint = SUBJECT_HINTS.get(subject, "")
    temp = 0.05 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.2

    payload = Chat(
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT + "\n" + hint),
            Messages(role=MessagesRole.USER, content=question),
        ],
        temperature=temp,
        max_tokens=300,   # ограничиваем длину ответа
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
    temp = 0.05 if subject in ("algebra", "geometry", "physics", "cs", "chemistry") else 0.2

    payload = Chat(
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT + "\n" + hint),
            Messages(
                role=MessagesRole.USER,
                content=caption or "Реши задачу с картинки. Дай только ответ.",
                attachments=[file_id],
            ),
        ],
        temperature=temp,
        max_tokens=300,
    )
    return await _call_with_retry(payload)
