import html
import re
from dataclasses import dataclass

import asyncpraw.models

_REDGIFS_RE = re.compile(r"redgifs\.com/(?:watch/|ifr/)?([A-Za-z0-9]+)", re.IGNORECASE)
_IMAGE_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)(?:\?.*)?$", re.IGNORECASE)
_GIF_EXT_RE = re.compile(r"\.gif(?:\?.*)?$", re.IGNORECASE)


@dataclass
class ImageItem:
    url: str


@dataclass
class GifItem:
    url: str


@dataclass
class VideoItem:
    video_url: str
    width: int
    height: int


@dataclass
class RedgifsItem:
    redgifs_id: str


@dataclass
class GalleryItem:
    items: list  # list[ImageItem | GifItem]


MediaItem = ImageItem | GifItem | VideoItem | RedgifsItem | GalleryItem


def _post_url(submission: asyncpraw.models.Submission) -> str:
    return getattr(submission, "url_overridden_by_dest", None) or submission.url or ""


def _detect_gallery(submission: asyncpraw.models.Submission) -> GalleryItem | None:
    gallery_data = getattr(submission, "gallery_data", None)
    media_metadata = getattr(submission, "media_metadata", None)
    if not gallery_data or not media_metadata:
        return None

    items: list[ImageItem | GifItem] = []
    for entry in gallery_data.get("items", []):
        media_id = entry.get("media_id")
        meta = media_metadata.get(media_id)
        if not meta or meta.get("status") != "valid":
            continue

        mime = meta.get("m", "")
        source = meta.get("s", {})
        if "gif" in mime and source.get("gif"):
            items.append(GifItem(url=html.unescape(source["gif"])))
        elif source.get("u"):
            items.append(ImageItem(url=html.unescape(source["u"])))

    return GalleryItem(items=items) if items else None


def _detect_reddit_video(submission: asyncpraw.models.Submission) -> VideoItem | None:
    if not getattr(submission, "is_video", False):
        return None

    media = getattr(submission, "media", None) or {}
    reddit_video = media.get("reddit_video", {})
    video_url = reddit_video.get("fallback_url")
    if not video_url:
        return None

    return VideoItem(
        video_url=video_url,
        width=reddit_video.get("width", 0),
        height=reddit_video.get("height", 0),
    )


def _detect_preview_gif(submission: asyncpraw.models.Submission) -> GifItem | None:
    preview = getattr(submission, "preview", None)
    if not preview:
        return None

    images = preview.get("images", [])
    if not images:
        return None

    variants = images[0].get("variants", {})
    gif_variant = variants.get("gif")
    if not gif_variant:
        return None

    gif_url = gif_variant.get("source", {}).get("url")
    return GifItem(url=html.unescape(gif_url)) if gif_url else None


def detect_media(submission: asyncpraw.models.Submission) -> MediaItem | None:
    url = _post_url(submission)

    redgifs_match = _REDGIFS_RE.search(url)
    if redgifs_match:
        return RedgifsItem(redgifs_id=redgifs_match.group(1))

    gallery = _detect_gallery(submission)
    if gallery:
        return gallery

    video = _detect_reddit_video(submission)
    if video:
        return video

    if _GIF_EXT_RE.search(url):
        return GifItem(url=url)

    preview_gif = _detect_preview_gif(submission)
    if preview_gif:
        return preview_gif

    if _IMAGE_EXT_RE.search(url):
        return ImageItem(url=url)

    return None
