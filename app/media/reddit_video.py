import asyncio
import logging
from pathlib import Path

import aiohttp

from app.media.downloader import download, url_exists

logger = logging.getLogger(__name__)

_AUDIO_CANDIDATES = ["DASH_audio.mp4", "DASH_AUDIO_128.mp4", "DASH_AUDIO_64.mp4"]


def _guess_audio_urls(video_url: str) -> list[str]:
    # video_url looks like https://v.redd.it/<id>/DASH_720.mp4?source=fallback
    base = video_url.split("/DASH_")[0]
    return [f"{base}/{name}" for name in _AUDIO_CANDIDATES]


async def _find_audio_url(session: aiohttp.ClientSession, video_url: str) -> str | None:
    candidates = _guess_audio_urls(video_url)
    # check all candidates concurrently instead of one at a time, then pick the
    # highest-priority one that actually exists
    exists = await asyncio.gather(*(url_exists(session, url) for url in candidates))
    for candidate, found in zip(candidates, exists, strict=True):
        if found:
            return candidate
    return None


async def _fetch_audio(
    session: aiohttp.ClientSession, video_url: str, audio_path: Path, job_id: str
) -> Path | None:
    audio_url = await _find_audio_url(session, video_url)
    if not audio_url:
        return None
    try:
        await download(session, audio_url, audio_path)
    except Exception:
        logger.warning("Failed to download audio track for %s, using video only", job_id)
        return None
    return audio_path


async def _run_ffmpeg(*args: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='ignore')[-2000:]}")


async def build_reddit_video(
    session: aiohttp.ClientSession, video_url: str, tmp_dir: Path, job_id: str
) -> Path:
    """Download reddit video (+ audio if present) and mux into a single mp4
    without re-encoding, so quality/aspect ratio stay untouched."""
    video_path = tmp_dir / f"{job_id}_video.mp4"
    audio_path = tmp_dir / f"{job_id}_audio.mp4"
    output_path = tmp_dir / f"{job_id}_final.mp4"

    # The video download and the audio-track lookup+download are independent —
    # run them side by side instead of one after the other.
    _, audio_result = await asyncio.gather(
        download(session, video_url, video_path),
        _fetch_audio(session, video_url, audio_path, job_id),
    )

    if not audio_result:
        video_path.rename(output_path)
        return output_path

    try:
        await _run_ffmpeg(
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c", "copy",
            "-map", "0:v:0",
            # "?" marks this mapping optional: some posts have no real audio
            # track even though a DASH_audio.mp4-style URL still returns 200
            # (Reddit's CDN serves a fallback instead of a clean 404) — if the
            # downloaded "audio" has no audio stream, ffmpeg just skips it
            # instead of failing the whole merge.
            "-map", "1:a:0?",
            str(output_path),
        )
    finally:
        video_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)

    return output_path
