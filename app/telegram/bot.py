from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from app.config import settings


def create_bot() -> Bot:
    api_server = TelegramAPIServer.from_base(settings.local_bot_api_url)
    session = AiohttpSession(api=api_server)
    return Bot(token=settings.bot_token, session=session)


def create_dispatcher() -> Dispatcher:
    return Dispatcher()
