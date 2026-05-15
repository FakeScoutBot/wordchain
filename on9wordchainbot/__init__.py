import logging
import asyncio

from aiogram import Dispatcher
from periodic import Periodic

from on9wordchainbot.resources import init_resources, close_resources
from on9wordchainbot.utils import send_admin_group
from on9wordchainbot.words import Places, Words

try:
    import coloredlogs
except ImportError:
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
    logging.warning("coloredlogs not available; defaulting to logging. To install, use `pip install coloredlogs`.")
else:
    coloredlogs.install(fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

logger = logging.getLogger(__name__)

try:
    import uvloop
except ImportError:
    logger.warning("uvloop unavailable. To install, use `pip install uvloop`.")
else:
    uvloop.install()

# ----- Initialize dispatcher -----
from on9wordchainbot.handlers import routers
from on9wordchainbot.handlers.errors import error_handler

dp = Dispatcher()
dp.include_routers(*routers)
dp.error.register(error_handler)


async def update_wordlists() -> None:
    await asyncio.gather(Words.update(), Places.update())


@dp.startup()
async def startup():
    await init_resources()
    await Periodic(60 * 60, update_wordlists).start(delay=0)
    await send_admin_group("Bot starting.")

@dp.shutdown()
async def shutdown():
    await close_resources()
    await send_admin_group("Bot stopping.")
