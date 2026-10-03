import io
import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import Document, Message

from app.config import settings
from app.queue.worker import Job, Worker
from app.telegram.middlewares import IsTrusted
from app.utils.link_extractor import extract_post_ids

logger = logging.getLogger(__name__)

router = Router()


async def _read_document_text(bot: Bot, document: Document) -> str:
    """The local Bot API server runs in --local mode, where getFile returns an
    absolute filesystem path instead of one downloadable over HTTP — in that
    case read the file directly (the bot container mounts the same volume)
    rather than trying to download it, which 404s."""
    file = await bot.get_file(document.file_id)
    if file.file_path and file.file_path.startswith("/"):
        return Path(file.file_path).read_text(encoding="utf-8", errors="ignore")

    buffer = io.BytesIO()
    await bot.download(document, destination=buffer)
    return buffer.getvalue().decode("utf-8", errors="ignore")


@router.message(IsTrusted(), F.document)
async def handle_batch_file(message: Message, worker: Worker) -> None:
    document = message.document
    file_name = document.file_name or ""
    if document.mime_type not in ("text/plain", None) and not file_name.endswith(".txt"):
        await message.answer("Пришлите .txt файл со ссылками на посты, одна ссылка/ID на строку.")
        return

    text = await _read_document_text(message.bot, document)

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
