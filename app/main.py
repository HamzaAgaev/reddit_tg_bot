import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.db.repo import Repo
from app.queue.worker import Worker
from app.reddit.client import create_reddit_client
from app.telegram.bot import create_bot, create_dispatcher
from app.telegram.handlers_batch import router as batch_router
from app.telegram.handlers_commands import router as commands_router
from app.telegram.handlers_listener import router as listener_router
from app.utils.logging import setup_logging

logger = logging.getLogger(__name__)


def _clear_tmp_dir(tmp_dir: str) -> None:
    """tmp/ only ever holds in-flight downloads — if the bot was killed mid-job
    (crash, OOM, forced restart) a leftover file could survive; nothing in
    there is meant to persist across restarts, so start from a clean slate."""
    path = Path(tmp_dir)
    if not path.exists():
        return
    removed = 0
    for child in path.iterdir():
        try:
            if child.is_file():
                child.unlink()
                removed += 1
        except OSError:
            logger.warning("Failed to remove leftover tmp file %s", child)
    if removed:
        logger.info("Cleared %d leftover file(s) from %s", removed, tmp_dir)


async def main() -> None:
    secrets = [
        settings.bot_token,
        settings.tg_api_id,
        settings.tg_api_hash,
        settings.reddit_client_id,
        settings.reddit_client_secret,
        settings.reddit_username,
        settings.reddit_password,
    ]
    setup_logging(log_dir=str(Path(settings.db_path).parent), secrets=secrets)
    logger.info("Starting reddit_tg_bot")

    _clear_tmp_dir(settings.tmp_dir)

    repo = Repo(settings.db_path)
    await repo.connect()

    reddit = create_reddit_client()
    bot = create_bot()
    dp = create_dispatcher()

    try:
        await asyncio.wait_for(reddit.user.me(), timeout=20)
        logger.info("Reddit API authentication OK")
    except asyncio.TimeoutError:
        logger.error(
            "Reddit API authentication timed out after 20s. This host's IP may be "
            "blocked by Reddit's anti-scraping network policy for the OAuth token "
            "endpoint (www.reddit.com), in which case the bot cannot fetch any "
            "posts at all from here."
        )
    except Exception:
        logger.exception("Reddit API authentication failed")

    worker = Worker(
        bot=bot,
        reddit=reddit,
        repo=repo,
        tmp_dir=settings.tmp_dir,
        send_delay=settings.send_delay_seconds,
        concurrency=settings.worker_concurrency,
    )

    dp.include_router(commands_router)
    dp.include_router(batch_router)
    dp.include_router(listener_router)
    dp["worker"] = worker

    worker_task = asyncio.create_task(worker.run())

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        worker_task.cancel()
        await reddit.close()
        await repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
