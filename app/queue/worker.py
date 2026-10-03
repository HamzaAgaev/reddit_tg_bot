import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import asyncpraw
from aiogram import Bot

from app.db.repo import Repo
from app.media.downloader import download
from app.media.redgifs_client import RedgifsClient
from app.media.reddit_video import build_reddit_video
from app.reddit.media_types import (
    GalleryItem,
    GifItem,
    ImageItem,
    RedgifsItem,
    VideoItem,
    detect_media,
)
from app.reddit.resolver import (
    PostUnavailable,
    fetch_submission,
    is_removed,
    resolve_share_link,
)
from app.telegram.sender import send_animation, send_photo, send_photo_group, send_video

logger = logging.getLogger(__name__)

_SHARE_URL_RE = re.compile(r"reddit\.com/r/([^/\s]+)/s/([a-z0-9]+)", re.IGNORECASE)


@dataclass
class Job:
    post_id: str
    target_chat_id: int
    source: str
    batch_id: str | None = None


@dataclass
class _BatchState:
    total: int
    notify_chat_id: int
    done: int = 0
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class _DownloadedPhoto:
    path: Path


@dataclass
class _DownloadedGif:
    path: Path


@dataclass
class _DownloadedVideo:
    path: Path
    width: int
    height: int


@dataclass
class _DownloadedGallery:
    photo_paths: list[Path]
    gif_paths: list[Path]


_Downloaded = _DownloadedPhoto | _DownloadedGif | _DownloadedVideo | _DownloadedGallery


class Worker:
    def __init__(
        self,
        bot: Bot,
        reddit: asyncpraw.Reddit,
        repo: Repo,
        tmp_dir: str,
        send_delay: float,
        concurrency: int = 4,
    ):
        self.bot = bot
        self.reddit = reddit
        self.repo = repo
        self.tmp_dir = Path(tmp_dir)
        self.send_delay = send_delay
        self.concurrency = concurrency
        self.redgifs_client = RedgifsClient()
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self._batches: dict[str, _BatchState] = {}
        # Only one post's media should actually be posted to Telegram at a time
        # (keeps a gallery/album contiguous and respects flood limits) — but
        # resolving/fetching/downloading of OTHER posts can happen concurrently
        # while one post is mid-send, which is where the real speedup comes from.
        self._send_lock = asyncio.Lock()
        # Guards against two workers concurrently picking up the same resolved
        # post id (e.g. two different share links pointing at the same post).
        self._in_flight: set[str] = set()

    def new_batch_id(self) -> str:
        return uuid.uuid4().hex

    def start_batch(self, batch_id: str, total: int, notify_chat_id: int) -> None:
        self._batches[batch_id] = _BatchState(total=total, notify_chat_id=notify_chat_id)

    async def enqueue(self, job: Job) -> None:
        await self.queue.put(job)

    async def run(self) -> None:
        # No cap on total request duration (large videos can legitimately take a
        # while) — instead bound the connect phase and require data to keep
        # flowing, so only genuinely stalled connections time out.
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_connect=15, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            workers = [
                asyncio.create_task(self._worker_loop(http_session, worker_id=i + 1))
                for i in range(self.concurrency)
            ]
            await asyncio.gather(*workers)

    async def _worker_loop(self, http_session: aiohttp.ClientSession, worker_id: int) -> None:
        while True:
            job = await self.queue.get()
            logger.info("[w%d] processing %s (source=%s)", worker_id, job.post_id, job.source)
            try:
                status = await asyncio.wait_for(self._process(job, http_session), timeout=300)
            except asyncio.TimeoutError:
                logger.error("[w%d] timed out processing %s after 300s", worker_id, job.post_id)
                status = "error"
            except Exception:
                logger.exception("[w%d] unhandled error processing post %s", worker_id, job.post_id)
                status = "error"
            logger.info("[w%d] finished %s with status=%s", worker_id, job.post_id, status)

            if job.batch_id:
                await self._update_batch(job.batch_id, status)

    async def _resolve_ref(self, ref: str) -> str | None:
        """Mobile-app share links (reddit.com/r/<sub>/s/<token>) carry no post id
        in the URL itself. Resolving them by fetching reddit.com directly is
        blocked on most server IPs (Reddit's anti-scraping network policy), so
        this goes through the authenticated API instead — see resolve_share_link."""
        if not ref.startswith("http"):
            return ref

        match = _SHARE_URL_RE.search(ref)
        if not match:
            logger.warning("Unrecognized link reference: %s", ref)
            return None

        subreddit, token = match.groups()
        post_id = await resolve_share_link(self.reddit, subreddit, token)
        if not post_id:
            logger.warning("Could not resolve share link %s", ref)
        return post_id

    async def _process(self, job: Job, http_session: aiohttp.ClientSession) -> str:
        logger.info("[%s] resolving reference", job.post_id)
        post_id = await self._resolve_ref(job.post_id)
        if post_id is None:
            return "error"

        logger.info("[%s] checking dedup", post_id)
        if await self.repo.is_seen(post_id):
            return "duplicate"

        if post_id in self._in_flight:
            logger.info("[%s] already being handled by another worker", post_id)
            return "duplicate"
        self._in_flight.add(post_id)

        try:
            logger.info("[%s] fetching submission from reddit", post_id)
            try:
                submission = await fetch_submission(self.reddit, post_id)
            except PostUnavailable:
                await self.repo.mark(post_id, "unavailable", job.source)
                return "unavailable"

            if is_removed(submission):
                await self.repo.mark(post_id, "removed", job.source)
                return "removed"

            logger.info("[%s] detecting media type", post_id)
            media = detect_media(submission)
            if media is None:
                await self.repo.mark(post_id, "no_media", job.source)
                return "no_media"

            try:
                logger.info("[%s] downloading media: %s", post_id, type(media).__name__)
                downloaded = await self._download_media(http_session, post_id, media)

                logger.info("[%s] sending to telegram", post_id)
                async with self._send_lock:
                    await self._dispatch_send(job.target_chat_id, downloaded)
                    await asyncio.sleep(self.send_delay)
            except Exception:
                logger.exception("Failed to process media for %s", post_id)
                await self.repo.mark(post_id, "error", job.source)
                return "error"
            finally:
                # Sweep every file for this post regardless of outcome — this
                # also catches partial files left behind by a download that
                # failed or timed out halfway through, not just the ones that
                # made it into a successfully-built `downloaded` object.
                self._cleanup_tmp_files(post_id)

            await self.repo.mark(post_id, "sent", job.source)
            return "sent"
        finally:
            self._in_flight.discard(post_id)

    def _cleanup_tmp_files(self, post_id: str) -> None:
        for path in self.tmp_dir.glob(f"{post_id}*"):
            try:
                path.unlink()
            except OSError:
                logger.warning("Failed to remove leftover temp file %s", path)

    async def _download_media(
        self, http_session: aiohttp.ClientSession, post_id: str, media
    ) -> _Downloaded:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        if isinstance(media, ImageItem):
            path = self.tmp_dir / f"{post_id}.jpg"
            await download(http_session, media.url, path)
            return _DownloadedPhoto(path)

        if isinstance(media, GifItem):
            path = self.tmp_dir / f"{post_id}.gif"
            await download(http_session, media.url, path)
            return _DownloadedGif(path)

        if isinstance(media, VideoItem):
            path = await build_reddit_video(http_session, media.video_url, self.tmp_dir, post_id)
            return _DownloadedVideo(path, media.width, media.height)

        if isinstance(media, RedgifsItem):
            resolved = await self.redgifs_client.resolve(http_session, media.redgifs_id)
            if not resolved:
                raise RuntimeError(f"redgifs {media.redgifs_id} could not be resolved")
            path = self.tmp_dir / f"{post_id}_redgifs.mp4"
            await download(http_session, resolved.video_url, path)
            return _DownloadedVideo(path, resolved.width, resolved.height)

        if isinstance(media, GalleryItem):
            return await self._download_gallery(http_session, post_id, media)

        raise ValueError(f"Unsupported media type: {type(media).__name__}")

    async def _download_gallery(
        self, http_session: aiohttp.ClientSession, post_id: str, gallery: GalleryItem
    ) -> _DownloadedGallery:
        async def fetch_one(idx: int, item):
            if isinstance(item, ImageItem):
                path = self.tmp_dir / f"{post_id}_{idx}.jpg"
                await download(http_session, item.url, path)
                return ("photo", path)
            if isinstance(item, GifItem):
                path = self.tmp_dir / f"{post_id}_gif_{idx}.gif"
                await download(http_session, item.url, path)
                return ("gif", path)
            return None

        # Download every gallery item concurrently instead of one at a time —
        # the main win for multi-image posts.
        results = await asyncio.gather(
            *(fetch_one(idx, item) for idx, item in enumerate(gallery.items)),
            return_exceptions=True,
        )

        photo_paths: list[Path] = []
        gif_paths: list[Path] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Failed to download a gallery item for %s: %s", post_id, result)
                continue
            if result is None:
                continue
            kind, path = result
            (photo_paths if kind == "photo" else gif_paths).append(path)

        if not photo_paths and not gif_paths:
            raise RuntimeError("All gallery items failed to download")

        return _DownloadedGallery(photo_paths, gif_paths)

    async def _dispatch_send(self, chat_id: int, downloaded: _Downloaded) -> None:
        if isinstance(downloaded, _DownloadedPhoto):
            await send_photo(self.bot, chat_id, downloaded.path)
        elif isinstance(downloaded, _DownloadedGif):
            await send_animation(self.bot, chat_id, downloaded.path)
        elif isinstance(downloaded, _DownloadedVideo):
            await send_video(self.bot, chat_id, downloaded.path, downloaded.width, downloaded.height)
        elif isinstance(downloaded, _DownloadedGallery):
            await self._send_gallery_paths(chat_id, downloaded.photo_paths, downloaded.gif_paths)

    async def _send_gallery_paths(
        self, chat_id: int, photo_paths: list[Path], gif_paths: list[Path]
    ) -> None:
        for start in range(0, len(photo_paths), 10):
            chunk = photo_paths[start : start + 10]
            if len(chunk) == 1:
                await send_photo(self.bot, chat_id, chunk[0])
            else:
                await send_photo_group(self.bot, chat_id, chunk)
            if start + 10 < len(photo_paths):
                await asyncio.sleep(self.send_delay)

        for path in gif_paths:
            await send_animation(self.bot, chat_id, path)
            await asyncio.sleep(self.send_delay)

    async def _update_batch(self, batch_id: str, status: str) -> None:
        batch = self._batches.get(batch_id)
        if not batch:
            return

        batch.done += 1
        batch.counts[status] = batch.counts.get(status, 0) + 1

        if batch.done < batch.total:
            if batch.done % 10 == 0:
                await self.bot.send_message(
                    batch.notify_chat_id,
                    f"Обработано {batch.done}/{batch.total}...",
                )
            return

        sent = batch.counts.get("sent", 0)
        skipped = batch.counts.get("removed", 0) + batch.counts.get("unavailable", 0)
        no_media = batch.counts.get("no_media", 0)
        errors = batch.counts.get("error", 0)
        duplicates = batch.counts.get("duplicate", 0)

        await self.bot.send_message(
            batch.notify_chat_id,
            "Готово: {total} ссылок\n"
            "Отправлено: {sent}\n"
            "Удалено/недоступно: {skipped}\n"
            "Без медиа: {no_media}\n"
            "Уже было отправлено ранее: {dup}\n"
            "Ошибок: {errors}".format(
                total=batch.total,
                sent=sent,
                skipped=skipped,
                no_media=no_media,
                dup=duplicates,
                errors=errors,
            ),
        )
        del self._batches[batch_id]
