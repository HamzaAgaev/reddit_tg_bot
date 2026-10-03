import asyncio
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import FSInputFile, InputMediaPhoto

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


async def _with_retry(coro_factory):
    for attempt in range(_MAX_RETRIES):
        try:
            return await coro_factory()
        except TelegramRetryAfter as exc:
            logger.warning("Flood control: sleeping %.1fs", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
    return await coro_factory()


async def send_photo(bot: Bot, chat_id: int, path: Path, caption: str | None = None) -> None:
    await _with_retry(lambda: bot.send_photo(chat_id, FSInputFile(path), caption=caption))


async def send_animation(bot: Bot, chat_id: int, path: Path, caption: str | None = None) -> None:
    await _with_retry(lambda: bot.send_animation(chat_id, FSInputFile(path), caption=caption))


async def send_video(
    bot: Bot,
    chat_id: int,
    path: Path,
    width: int = 0,
    height: int = 0,
    caption: str | None = None,
) -> None:
    kwargs = {"supports_streaming": True, "caption": caption}
    if width and height:
        kwargs["width"] = width
        kwargs["height"] = height
    await _with_retry(lambda: bot.send_video(chat_id, FSInputFile(path), **kwargs))


async def send_photo_group(
    bot: Bot, chat_id: int, paths: list[Path], caption: str | None = None
) -> None:
    media = [
        InputMediaPhoto(media=FSInputFile(p), caption=caption if i == 0 else None)
        for i, p in enumerate(paths)
    ]
    await _with_retry(lambda: bot.send_media_group(chat_id, media=media))
