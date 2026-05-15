import random
from decimal import Decimal
from functools import wraps
from string import ascii_lowercase
from typing import Any, Awaitable, Callable, Coroutine, Optional, TypeVar

from aiocache import cached
from aiogram import types
from bson.decimal128 import Decimal128

from on9wordchainbot.constants import ADMIN_GROUP_ID, VIP
from on9wordchainbot.resources import bot, on9bot, get_db
from on9wordchainbot.words import Words


def is_word(s: str) -> bool:
    return all(c in ascii_lowercase for c in s)


def check_word_existence(word: str) -> bool:
    return word in Words.dawg


def filter_words(
    min_len: int = 1,
    prefix: Optional[str] = None,
    required_letter: Optional[str] = None,
    banned_letters: Optional[list[str]] = None,
    exclude_words: Optional[set[str]] = None
) -> list[str]:
    words: list[str] = Words.dawg.keys(prefix) if prefix else Words.dawg.keys()
    if min_len > 1:
        words = [w for w in words if len(w) >= min_len]
    if required_letter:
        words = [w for w in words if required_letter in w]
    if banned_letters:
        words = [w for w in words if all(i not in w for i in banned_letters)]
    if exclude_words:
        words = [w for w in words if w not in exclude_words]
    return words


def get_random_word(
    min_len: int = 1,
    prefix: Optional[str] = None,
    required_letter: Optional[str] = None,
    banned_letters: Optional[list[str]] = None,
    exclude_words: Optional[set[str]] = None
) -> Optional[str]:
    words = filter_words(min_len, prefix, required_letter, banned_letters, exclude_words)
    return random.choice(words) if words else None


async def send_admin_group(*args: Any, **kwargs: Any) -> types.Message:
    return await bot.send_message(ADMIN_GROUP_ID, *args, **kwargs)


@cached(ttl=15)
async def amt_donated(user_id: int) -> Decimal:
    db = get_db()
    res = await db.donation.aggregate(
        [
            {"$match": {"user_id": user_id}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
    ).to_list(length=1)
    if not res:
        return Decimal("0")
    total = res[0]["total"]
    if isinstance(total, Decimal128):
        return total.to_decimal()
    return Decimal(str(total))


@cached(ttl=15)
async def has_star(user_id: int) -> bool:
    return user_id in VIP or user_id == on9bot.id or await amt_donated(user_id) > 0


def inline_keyboard_from_button(button: types.InlineKeyboardButton) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[button]])


ADD_TO_GROUP_KEYBOARD = inline_keyboard_from_button(
    types.InlineKeyboardButton(text="Add to group", url="https://t.me/on9wordchainbot?startgroup=_")
)
ADD_ON9BOT_TO_GROUP_KEYBOARD = inline_keyboard_from_button(
    types.InlineKeyboardButton(text="Add On9Bot to group", url="https://t.me/On9Bot?startgroup=_")
)


def send_private_only_message(f: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(f)
    async def inner(message: types.Message, *args: Any, **kwargs: Any) -> None:
        if message.chat.id < 0:
            await message.reply("Please use this command in private.")
            return
        await f(message, *args, **kwargs)

    return inner


def send_groups_only_message(f: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(f)
    async def inner(message: types.Message, *args: Any, **kwargs: Any) -> None:
        if message.chat.id > 0:
            await message.reply(
                "This command can only be used in groups.",
                reply_markup=ADD_TO_GROUP_KEYBOARD
            )
            return
        await f(message, *args, **kwargs)

    return inner



T = TypeVar("T")


def awaitable_to_coroutine(awaitable: Awaitable[T]) -> Coroutine[Any, Any, T]:
    # Convert awaitable like aiogram TelegramMethod to a coroutine
    # so that it can be used in asyncio.create_task()
    async def _runner() -> T:
        return await awaitable

    return _runner()
