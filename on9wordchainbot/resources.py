import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import aiohttp
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase  # type: ignore[import-untyped]

from on9wordchainbot.constants import TOKEN, ON9BOT_TOKEN, DB_URI

if TYPE_CHECKING:
    from on9wordchainbot.models import ClassicGame


logger = logging.getLogger(__name__)


class GlobalState:
    build_time = datetime.now().replace(microsecond=0)
    maint_mode = False

    games: dict[int, "ClassicGame"] = {}  # group id -> game instance
    games_lock: asyncio.Lock = asyncio.Lock()


bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.MARKDOWN,
        allow_sending_without_reply=True,
        link_preview_is_disabled=True,
    )
)
on9bot = Bot(ON9BOT_TOKEN)


# Initialized on startup
session: Optional[aiohttp.ClientSession] = None
client: Optional[AsyncIOMotorClient] = None
db: Optional[AsyncIOMotorDatabase] = None


def get_session() -> aiohttp.ClientSession:
    if session is None:
        raise RuntimeError("session is not initialized!")
    return session


def get_db() -> AsyncIOMotorDatabase:
    if db is None:
        raise RuntimeError("database is not initialized!")
    return db


async def init_resources() -> None:
    global session, client, db

    session = aiohttp.ClientSession()

    logger.info("Connecting to database...")
    client = AsyncIOMotorClient(DB_URI)
    db = client.get_default_database()
    if db is None:
        raise RuntimeError("No default database configured in DB_URI!")
    await asyncio.gather(
        db.player.create_index("user_id", unique=True),
        db.game.create_index([("group_id", 1), ("start_time", 1)], unique=True),
        db.gameplayer.create_index([("user_id", 1), ("game_id", 1)], unique=True),
        db.gameplayer.create_index("group_id"),
        db.game.create_index("start_time"),
        db.wordlist.create_index("word", unique=True),
        db.donation.create_index("donation_id", unique=True),
    )


async def close_resources() -> None:
    global session, client, db
    if session is not None:
        await session.close()
    if client is not None:
        client.close()
    session = None
    client = None
    db = None
