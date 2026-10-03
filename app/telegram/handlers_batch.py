import io
import logging

from aiogram import F, Router
from aiogram.types import Message

from app.config import settings
from app.queue.worker import Job, Worker
from app.telegram.middlewares import IsTrusted
from app.utils.link_extractor import extract_post_ids

logger = logging.getLogger(__name__)

router = Router()


@router.message(IsTrusted(), F.document)
async def handle_batch_file(message: Message, worker: Worker) -> None:
    document = message.document
    file_name = document.file_name or ""
    if document.mime_type not in ("text/plain", None) and not file_name.endswith(".txt"):
        await message.answer("Пришлите .txt файл со ссылками на посты, одна ссылка/ID на строку.")
        return

    buffer = io.BytesIO()
    await message.bot.download(document, destination=buffer)
    text = buffer.getvalue().decode("utf-8", errors="ignore")

    post_ids = extract_post_ids(text)
    if not post_ids:
        await message.answer("Не нашёл ни одной ссылки/ID поста в файле.")
        return

    batch_id = worker.new_batch_id()
    worker.start_batch(batch_id, total=len(post_ids), notify_chat_id=message.chat.id)

    for post_id in post_ids:
        await worker.enqueue(
            Job(
                post_id=post_id,
                target_chat_id=settings.dest_channel_id,
                source="batch",
                batch_id=batch_id,
            )
        )

    await message.answer(f"Принято {len(post_ids)} постов, начинаю обработку.")
