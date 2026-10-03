from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.queue.worker import Worker
from app.telegram.middlewares import IsTrusted

router = Router()


@router.message(Command("start"), IsTrusted())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Бот запущен.\n"
        "— Пришлите .txt файл со ссылками/ID постов для массовой загрузки.\n"
        "— /status — статистика."
    )


@router.message(Command("status"), IsTrusted())
async def cmd_status(message: Message, worker: Worker) -> None:
    stats = await worker.repo.stats()
    pending = worker.queue.qsize()
    lines = [f"В очереди: {pending}"]
    for status, count in stats.items():
        lines.append(f"{status}: {count}")
    await message.answer("\n".join(lines))
