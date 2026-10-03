import re

# matches .../comments/<id>/... on any reddit.com subdomain, and redd.it/<id> short links
_COMMENTS_RE = re.compile(
    r"reddit\.com/r/[^/\s]+/comments/([a-z0-9]+)", re.IGNORECASE
)
_SHORT_RE = re.compile(r"redd\.it/([a-z0-9]+)", re.IGNORECASE)
_BARE_ID_RE = re.compile(r"^[a-z0-9]{5,8}$", re.IGNORECASE)

# mobile-app "share" links: reddit.com/r/<sub>/s/<token> — these are redirects,
# the real post id is only known after following the redirect (done in the worker).
_SHARE_RE = re.compile(
    r"https?://(?:www\.|m\.)?reddit\.com/r/[^/\s]+/s/[a-z0-9]+", re.IGNORECASE
)


def extract_post_ids(text: str) -> list[str]:
    """Find reddit submission references in free-form text (message body or a
    batch file). Returns either a resolved post id, or (for mobile share links
    whose real id is only known after following a redirect) the full share URL
    — callers must resolve such refs before using them as a post id."""
    if not text:
        return []

    found: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        line_matches = [m.group(1) for m in _COMMENTS_RE.finditer(line)]
        line_matches += [m.group(1) for m in _SHORT_RE.finditer(line)]
        line_matches += [m.group(0) for m in _SHARE_RE.finditer(line)]

        if not line_matches and _BARE_ID_RE.match(line):
            line_matches.append(line)

        found.extend(line_matches)

    # de-dup while preserving order
    seen: set[str] = set()
    result = []
    for ref in found:
        if ref not in seen:
            seen.add(ref)
            result.append(ref)
    return result
