import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_REDACTED = "***REDACTED***"


class RedactingFormatter(logging.Formatter):
    """Scrubs known secret values out of the fully rendered log line — message
    and traceback both — so a credential can never reach the log file/console
    even if some future code path accidentally logs it."""

    def __init__(self, fmt: str, secrets: list[str]):
        super().__init__(fmt)
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        for secret in self._secrets:
            formatted = formatted.replace(secret, _REDACTED)
        return formatted


def setup_logging(log_dir: str = "./data", secrets: list[str] | None = None) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / "bot.log"

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter_cls = RedactingFormatter if secrets else logging.Formatter
    args = (fmt, secrets) if secrets else (fmt,)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter_cls(*args))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter_cls(*args))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # noisy third-party loggers
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("asyncprawcore").setLevel(logging.WARNING)
