import time
from dataclasses import dataclass

import aiohttp

_TOKEN_URL = "https://api.redgifs.com/v2/auth/temporary"
_GIF_URL = "https://api.redgifs.com/v2/gifs/{id}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


@dataclass
class RedgifsResolved:
    video_url: str
    width: int
    height: int


class RedgifsClient:
    def __init__(self):
        self._token: str | None = None
        self._token_expiry: float = 0.0

    async def _get_token(self, session: aiohttp.ClientSession) -> str:
        if self._token and time.time() < self._token_expiry:
            return self._token

        async with session.get(_TOKEN_URL, headers=_HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()

        self._token = data["token"]
        self._token_expiry = time.time() + 1800
        return self._token

    async def resolve(
        self, session: aiohttp.ClientSession, redgifs_id: str
    ) -> RedgifsResolved | None:
        token = await self._get_token(session)
        headers = {**_HEADERS, "Authorization": f"Bearer {token}"}

        async with session.get(
            _GIF_URL.format(id=redgifs_id.lower()), headers=headers
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()

        gif = data.get("gif", {})
        urls = gif.get("urls", {})
        video_url = urls.get("hd") or urls.get("sd")
        if not video_url:
            return None

        return RedgifsResolved(
            video_url=video_url,
            width=gif.get("width", 0),
            height=gif.get("height", 0),
        )
