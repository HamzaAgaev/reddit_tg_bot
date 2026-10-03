from pathlib import Path

import aiohttp

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


class DownloadError(Exception):
    pass


async def download(
    session: aiohttp.ClientSession, url: str, dest_path: Path, headers: dict | None = None
) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    req_headers = {**HEADERS, **(headers or {})}

    async with session.get(url, headers=req_headers) as resp:
        if resp.status != 200:
            raise DownloadError(f"GET {url} -> HTTP {resp.status}")
        with open(dest_path, "wb") as f:
            async for chunk in resp.content.iter_chunked(1 << 16):
                f.write(chunk)

    return dest_path


async def url_exists(session: aiohttp.ClientSession, url: str) -> bool:
    try:
        async with session.head(url, headers=HEADERS, allow_redirects=True) as resp:
            return resp.status == 200
    except aiohttp.ClientError:
        return False
