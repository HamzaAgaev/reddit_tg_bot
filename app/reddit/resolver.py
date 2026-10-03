import logging
import re

import asyncpraw
import asyncpraw.models
import asyncprawcore

logger = logging.getLogger(__name__)

REMOVED_SELFTEXT = {"[removed]", "[deleted]"}

_COMMENTS_PATH_RE = re.compile(r"/comments/([a-z0-9]+)", re.IGNORECASE)


class PostUnavailable(Exception):
    """Raised when a submission can't be fetched at all (private/banned subreddit, etc.)."""


async def fetch_submission(
    reddit: asyncpraw.Reddit, post_id: str
) -> asyncpraw.models.Submission:
    submission = await reddit.submission(id=post_id)
    try:
        await submission.load()
    except (asyncprawcore.exceptions.NotFound, asyncprawcore.exceptions.Forbidden) as exc:
        raise PostUnavailable(f"{post_id}: {exc}") from exc
    return submission


async def resolve_share_link(
    reddit: asyncpraw.Reddit, subreddit: str, token: str
) -> str | None:
    """Resolve a mobile-app share link (reddit.com/r/<sub>/s/<token>) to its real
    post id.

    These links carry no id in the URL itself — Reddit resolves them with an
    HTTP redirect. Fetching reddit.com directly to follow that redirect is
    blocked on most server/VPS IPs by Reddit's anti-scraping network policy, so
    instead we hit the same path through the authenticated API (oauth.reddit.com).
    asyncprawcore raises a ``Redirect`` exception carrying the target path,
    which is exactly what PRAW/asyncpraw already use internally for similar
    redirect-based lookups (e.g. r/<sub>/random).
    """
    logger.info("Resolving share link r/%s/s/%s via Reddit API", subreddit, token)
    try:
        await reddit.get(f"/r/{subreddit}/s/{token}")
    except asyncprawcore.exceptions.Redirect as redirect:
        match = _COMMENTS_PATH_RE.search(redirect.path)
        if match:
            return match.group(1)
        logger.warning("Share link r/%s/s/%s redirected to unexpected path %s", subreddit, token, redirect.path)
        return None
    except Exception:
        logger.exception("Failed to resolve share link r/%s/s/%s via API", subreddit, token)
        return None

    logger.warning("Share link r/%s/s/%s did not redirect as expected", subreddit, token)
    return None


def is_removed(submission: asyncpraw.models.Submission) -> bool:
    if getattr(submission, "removed_by_category", None):
        return True
    if submission.author is None:
        return True
    selftext = getattr(submission, "selftext", "") or ""
    if selftext.strip() in REMOVED_SELFTEXT:
        return True
    return False
