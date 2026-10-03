import logging

from aiogram import Router
from aiogram.types import Message

from app.config import settings
from app.queue.worker import Job, Worker
from app.utils.link_extractor import extract_post_ids

logger = logging.getLogger(__name__)

router = Router()


@router.channel_post()
async def handle_channel_post(message: Message, worker: Worker) -> None:
    logger.info(
        "channel_post received: chat_id=%s chat_title=%r (configured SOURCE_CHANNEL_ID=%s)",
        message.chat.id,
        message.chat.title,
        settings.source_channel_id,
    )
    if message.chat.id != settings.source_channel_id:
        logger.info("Ignoring post: chat_id does not match SOURCE_CHANNEL_ID")
        return

    text = message.text or message.caption or ""
    post_ids = extract_post_ids(text)
    if not post_ids:
        return

    logger.info("Found %d reddit link(s) in source channel post", len(post_ids))
    for post_id in post_ids:
        await worker.enqueue(
            Job(post_id=post_id, target_chat_id=settings.dest_channel_id, source="listener")
        )
