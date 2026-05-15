import asyncio
import random
from datetime import datetime
from typing import Any, Optional

from aiocache import cached
from aiogram import types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.chat_member import ADMINS, MEMBERS

from on9wordchainbot.resources import GlobalState, bot, on9bot, get_db
from on9wordchainbot.models.player import Player
from on9wordchainbot.constants import GameSettings, GameState, OWNER_ID
from on9wordchainbot.utils import (
    ADD_ON9BOT_TO_GROUP_KEYBOARD,
    check_word_existence,
    get_random_word,
    send_admin_group,
)
from on9wordchainbot.words import WordList, Words


class ClassicGame:
    name = "classic game"
    command = "startclassic"
    wordlist: type[WordList] = Words
    has_word_length_limit = True

    __slots__ = (
        "group_id", "players", "players_in_game", "state", "start_time", "end_time",
        "extended_user_ids", "min_players", "max_players", "time_left", "time_limit",
        "min_letters_limit", "current_word", "longest_word", "longest_word_sender_id",
        "answered", "accepting_answers", "turns", "used_words", "join_lock"
    )

    def __init__(self, group_id: int) -> None:
        self.group_id = group_id
        self.players: list[Player] = []
        self.players_in_game: list[Player] = []
        self.state = GameState.JOINING
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        # Store user ids rather than Player object since players may quit then join to extend again
        self.extended_user_ids: set[int] = set()

        # Game settings
        self.min_players = GameSettings.MIN_PLAYERS
        self.max_players = GameSettings.MAX_PLAYERS
        self.time_left = GameSettings.JOINING_PHASE_SECONDS
        self.time_limit = GameSettings.MAX_TURN_SECONDS
        self.min_letters_limit = GameSettings.MIN_WORD_LENGTH_LIMIT

        # Game attributes
        self.current_word: Optional[str] = None
        self.longest_word = ""
        self.longest_word_sender_id: Optional[int] = None  # TODO: Change to Player object instead of id
        self.answered = False
        self.accepting_answers = False
        self.turns = 0
        self.used_words: set[str] = set()

        self.join_lock = asyncio.Lock()  # Prevent same user / vp joining as multiple players

    def user_in_game(self, user_id: int) -> bool:
        return any(p.user_id == user_id for p in self.players)

    async def send_message(self, *args: Any, **kwargs: Any) -> types.Message:
        return await bot.send_message(self.group_id, *args, **kwargs)

    @cached(ttl=15)
    async def is_admin(self, user_id: int) -> bool:
        try:
            user = await bot.get_chat_member(self.group_id, user_id)
        except TelegramBadRequest as e:
            if "CHAT_ADMIN_REQUIRED" in str(e):
                return False
            else:
                raise e
        else:
            return isinstance(user, ADMINS)

    async def join(self, message: types.Message) -> None:
        async with self.join_lock:
            if self.state != GameState.JOINING or len(self.players) >= self.max_players:
                return

            # Try to detect game not starting
            if self.time_left < 0:
                asyncio.create_task(self.scan_for_stale_timer())
                return

            # Check if user already joined
            user = message.from_user
            if self.user_in_game(user.id):
                return

            player = await Player.create(user)
            self.players.append(player)

            await self.send_message(
                f"{player.name} joined. There {'is' if len(self.players) == 1 else 'are'} now "
                f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}.",
                parse_mode=ParseMode.HTML
            )

            # Start game when max players reached
            if len(self.players) >= self.max_players:
                self.time_left = -99999

    async def forcejoin(self, message: types.Message) -> None:
        async with self.join_lock:
            if self.state == GameState.KILLGAME or len(self.players) >= self.max_players:
                return

            if message.reply_to_message:
                user = message.reply_to_message.from_user
            else:
                user = message.from_user

            # Check if user already joined
            if self.user_in_game(user.id):
                return

            player = await Player.create(user)
            self.players.append(player)
            if self.state == GameState.RUNNING:
                self.players_in_game.append(player)

            await self.send_message(
                f"{player.name} was forced to join. There {'is' if len(self.players) == 1 else 'are'} now "
                f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}.",
                parse_mode=ParseMode.HTML
            )

            # Start game when max players reached
            if len(self.players) >= self.max_players:
                self.time_left = -99999

    async def flee(self, message: types.Message) -> None:
        async with self.join_lock:
            if self.state != GameState.JOINING:
                return

            # Find player to remove
            user_id = message.from_user.id
            for i in range(len(self.players)):
                if self.players[i].user_id == user_id:
                    player = self.players.pop(i)
                    break
            else:
                return

            await self.send_message(
                f"{player.name} fled. There {'is' if len(self.players) == 1 else 'are'} now "
                f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}.",
                parse_mode=ParseMode.HTML
            )

    async def forceflee(self, message: types.Message) -> None:
        async with self.join_lock:
            # Player to be fled = Sender of replies message
            if self.state != GameState.JOINING or not message.reply_to_message:
                return

            # Find player to remove
            user_id = message.reply_to_message.from_user.id
            for i in range(len(self.players)):
                if self.players[i].user_id == user_id:
                    player = self.players.pop(i)
                    break
            else:
                return

            await self.send_message(
                f"{player.name} was forced to flee. There {'is' if len(self.players) == 1 else 'are'} now "
                f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}.",
                parse_mode=ParseMode.HTML
            )

    async def addvp(self, message: types.Message) -> None:
        async with self.join_lock:
            if self.state != GameState.JOINING or len(self.players) >= self.max_players:
                return

            # Check if On9Bot already joined
            if any(p.is_vp for p in self.players):
                return

            # Check if vp adder is player/admin/owner
            if (
                message.from_user.id != OWNER_ID
                and not self.user_in_game(message.from_user.id)
                and not await self.is_admin(message.from_user.id)
            ):
                await self.send_message("Imagine not playing")
                return

            try:
                vp_member = await bot.get_chat_member(self.group_id, on9bot.id)
                assert isinstance(vp_member, MEMBERS)  # VP must be chat member
            except (TelegramBadRequest, AssertionError):
                await self.send_message(
                    f"Add [On9Bot](tg://user?id={on9bot.id}) here to play as a virtual player.",
                    reply_markup=ADD_ON9BOT_TO_GROUP_KEYBOARD
                )
                return

            vp = await Player.vp()
            self.players.append(vp)

            bot_user = await bot.me()
            await on9bot.send_message(self.group_id, "/join@" + bot_user.username)
            await self.send_message(
                (
                    f"{vp.name} joined. There {'is' if len(self.players) == 1 else 'are'} now "
                    f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}."
                ),
                parse_mode=ParseMode.HTML
            )

            # Start game when max players reached
            if len(self.players) >= self.max_players:
                self.time_left = -99999

    async def remvp(self, message: types.Message) -> None:
        async with self.join_lock:
            if self.state != GameState.JOINING:
                return

            # Check if On9Bot has joined
            if not any(p.is_vp for p in self.players):
                return

            # Check if vp remover is player/admin
            if (
                message.from_user.id != OWNER_ID
                and not self.user_in_game(message.from_user.id)
                and not await self.is_admin(message.from_user.id)
            ):
                await self.send_message("Imagine not playing")
                return

            for i in range(len(self.players)):
                if self.players[i].is_vp:
                    vp = self.players.pop(i)
                    break
            else:
                return

            bot_user = await bot.me()
            await on9bot.send_message(self.group_id, "/flee@" + bot_user.username)
            await self.send_message(
                (
                    f"{vp.name} fled. There {'is' if len(self.players) == 1 else 'are'} now "
                    f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}."
                ),
                parse_mode=ParseMode.HTML
            )

    async def extend(self, message: types.Message) -> None:
        if self.state != GameState.JOINING:
            return

        # Check if extender is player/admin/owner
        if (
            message.from_user.id != OWNER_ID
            and not self.user_in_game(message.from_user.id)
            and not await self.is_admin(message.from_user.id)
        ):
            await self.send_message("Imagine not playing")
            return

        # Each player can only extend once and only for 30 seconds except admins
        if await self.is_admin(message.from_user.id):
            arg = message.text.partition(" ")[2]

            # Check if arg is a valid negative integer
            try:
                n = int(arg)
                is_neg = n < 0
                n = abs(n)
            except ValueError:
                n = 30
                is_neg = False
        elif message.from_user.id in self.extended_user_ids:
            await self.send_message("You can only extend once peasant")
            return
        else:
            self.extended_user_ids.add(message.from_user.id)
            n = 30
            is_neg = False

        if is_neg:
            # Reduce joining phase time (admins only)
            if not await self.is_admin(message.from_user.id):
                await self.send_message("Imagine not being admin")
                return

            if n >= self.time_left:
                # Start game immediately
                self.time_left = -99999
            else:
                self.time_left -= n
                await self.send_message(
                    f"The joining phase has been reduced by {n}s.\n"
                    f"You have {self.time_left}s to /join."
                )
        else:
            # Extend joining phase time
            # Max joining phase duration is capped
            added_duration = min(n, GameSettings.MAX_JOINING_PHASE_SECONDS - self.time_left)
            self.time_left += added_duration
            await self.send_message(
                f"The joining phase has been extended by {added_duration}s.\n"
                f"You have {self.time_left}s to /join."
            )

    async def send_turn_message(self) -> None:
        min_letters_text = (
            f" and include <b>at least {self.min_letters_limit} letters</b>"
            if self.has_word_length_limit else ""
        )
        await self.send_message(
            (
                f"Turn: {self.players_in_game[0].mention} (Next: {self.players_in_game[1].name})\n"
                f"Your word must start with <i>{self.get_word_end_letter(self.current_word).upper()}</i>"
                f"{min_letters_text}.\n"
                f"You have <b>{self.time_limit}s</b> to answer.\n"
                f"Players remaining: {len(self.players_in_game)}/{len(self.players)}\n"
                f"Total words: {self.turns}"
            ),
            parse_mode=ParseMode.HTML
        )

        # Reset per-turn attributes
        self.answered = False
        self.accepting_answers = True
        self.time_left = self.time_limit

        if self.players_in_game[0].is_vp:
            await self.vp_answer()

    def get_random_valid_answer(self) -> Optional[str]:
        return get_random_word(
            min_len=self.min_letters_limit if self.has_word_length_limit else 1,
            prefix=self.get_word_end_letter(self.current_word),
            exclude_words=self.used_words,
            wordlist=self.wordlist
        )

    async def vp_answer(self) -> None:
        # Wait before answering to prevent exceeding 20 msg/min message limit
        # Also simulate thinking/input time like human players, wowzers
        await asyncio.sleep(random.uniform(5, 8))

        word = self.get_random_valid_answer()

        if not word:  # No valid words to choose from
            await on9bot.send_message(self.group_id, "/forceskip bey")
            self.time_left = 0
            return

        await on9bot.send_message(self.group_id, word.capitalize())

        self.post_turn_processing(word)
        await self.send_post_turn_message(word)

    async def additional_answer_checkers(self, word: str, message: types.Message) -> bool:
        # To be overridden by other game modes
        # True/False: valid/invalid answer
        return True

    async def handle_answer(self, message: types.Message) -> None:
        # Prevent circular imports
        from on9wordchainbot.models.game.elimination import EliminationGame

        word = self.normalize_answer_text(message.text)
        required_letter = self.get_word_end_letter(self.current_word)

        # Check if answer is invalid
        if self.get_word_start_letter(word) != required_letter:
            await message.reply(
                f"_{word.capitalize()}_ does not start with _{required_letter.upper()}_."
            )
            return
        # No minimum letters limit for elimination game modes
        if (
            self.has_word_length_limit
            and not isinstance(self, EliminationGame)
            and len(word) < self.min_letters_limit
        ):
            await message.reply(
                f"_{word.capitalize()}_ has less than {self.min_letters_limit} letters."
            )
            return
        if word in self.used_words:
            await message.reply(f"_{word.capitalize()}_ has been used.")
            return
        if not check_word_existence(word, self.wordlist):
            await message.reply(f"_{word.capitalize()}_ is not in my list of words.")
            return
        if not await self.additional_answer_checkers(word, message):
            return

        self.post_turn_processing(word)
        await self.send_post_turn_message(word)

    def post_turn_processing(self, word: str) -> None:
        # Prevent circular imports
        from on9wordchainbot.models.game.chosen_first_letter import ChosenFirstLetterGame

        # Update attributes
        self.used_words.add(word)
        self.turns += 1

        # self.current_word is constant for ChosenFirstLetterGame
        if not isinstance(self, ChosenFirstLetterGame):
            self.current_word = word

        self.players_in_game[0].word_count += 1
        self.players_in_game[0].letter_count += len(word)
        # If both words have the same length, it will (probably) default to the first argument
        self.players_in_game[0].longest_word = max(word, self.players_in_game[0].longest_word, key=len)

        if len(word) > len(self.longest_word):
            self.longest_word = word
            self.longest_word_sender_id = self.players_in_game[0].user_id

        # Set per-turn attributes
        self.answered = True
        self.accepting_answers = False

    async def send_post_turn_message(self, word: str) -> None:
        text = f"_{word.capitalize()}_ is accepted.\n\n"
        # Reduce limits if possible every set number of turns
        if self.turns % GameSettings.TURNS_BETWEEN_LIMITS_CHANGE == 0:
            if self.time_limit > GameSettings.MIN_TURN_SECONDS:
                self.time_limit -= GameSettings.TURN_SECONDS_REDUCTION_PER_LIMIT_CHANGE
                text += (
                    f"Time limit decreased from "
                    f"*{self.time_limit + GameSettings.TURN_SECONDS_REDUCTION_PER_LIMIT_CHANGE}s* "
                    f"to *{self.time_limit}s*.\n"
                )
            if self.has_word_length_limit and self.min_letters_limit < GameSettings.MAX_WORD_LENGTH_LIMIT:
                self.min_letters_limit += GameSettings.WORD_LENGTH_LIMIT_INCREASE_PER_LIMIT_CHANGE
                text += (
                    f"Minimum letters per word increased from "
                    f"*{self.min_letters_limit - GameSettings.WORD_LENGTH_LIMIT_INCREASE_PER_LIMIT_CHANGE}* "
                    f"to *{self.min_letters_limit}*.\n"
                )
        await self.send_message(text)

    async def running_initialization(self) -> None:
        # Random starting word
        self.current_word = get_random_word(
            min_len=self.min_letters_limit if self.has_word_length_limit else 1,
            wordlist=self.wordlist
        )
        self.used_words.add(self.current_word)
        self.start_time = datetime.now().replace(microsecond=0)

        await self.send_message(
            (
                f"The first word is <i>{self.current_word.capitalize()}</i>.\n\n"
                "Turn order:\n"
                + "\n".join(p.mention for p in self.players_in_game)
            ),
            parse_mode=ParseMode.HTML
        )

    def is_valid_answer_text(self, text: str) -> bool:
        return 0 < len(text) <= 100 and text.isascii() and text.isalpha()

    def normalize_answer_text(self, text: str) -> str:
        return text.lower()

    def get_word_start_letter(self, word: str) -> str:
        return word[0]

    def get_word_end_letter(self, word: str) -> str:
        return word[-1]

    async def running_phase_tick(self) -> bool:
        # Return values
        # True: Game has ended
        # False: Game is still ongoing
        if self.answered:
            # Move player who just answered to the end of queue
            self.players_in_game.append(self.players_in_game.pop(0))
        else:
            self.time_left -= 1
            if self.time_left > 0:
                return False

            # Timer ran out
            self.accepting_answers = False
            await self.send_message(
                f"{self.players_in_game[0].mention} ran out of time! They have been eliminated.",
                parse_mode=ParseMode.HTML
            )
            del self.players_in_game[0]

            if len(self.players_in_game) == 1:
                await self.handle_game_end()
                return True

        await self.send_turn_message()
        return False

    async def handle_game_end(self) -> None:
        # Calculate game length
        self.end_time = datetime.now().replace(microsecond=0)
        td = self.end_time - self.start_time
        game_len_str = f"{int(td.total_seconds()) // 3600:02}{str(td)[-6:]}"

        winner = self.players_in_game[0].mention if self.players_in_game else "No one"
        text = f"{winner} won the game out of {len(self.players)} players!\n"
        text += f"Total words: {self.turns}\n"
        if self.longest_word:
            longest_word_sender_name = [p for p in self.players if p.user_id == self.longest_word_sender_id][0].name
            text += f"Longest word: <i>{self.longest_word.capitalize()}</i> from {longest_word_sender_name}\n"
        text += f"Game length: <code>{game_len_str}</code>"
        await self.send_message(text, parse_mode=ParseMode.HTML)

        GlobalState.games.pop(self.group_id, None)

    async def update_db(self) -> None:
        db = get_db()
        res = await db.game.insert_one(
            {
                "group_id": self.group_id,
                "players": len(self.players),
                "game_mode": self.__class__.__name__,
                "winner": self.players_in_game[0].user_id if self.players_in_game else None,
                "start_time": self.start_time,
                "end_time": self.end_time,
            }
        )
        game_id = res.inserted_id
        for player in self.players:  # Update db players in parallel
            asyncio.create_task(self.update_db_player(game_id, player))

    async def update_db_player(self, game_id: object, player: Player) -> None:
        db = get_db()
        existing = await db.player.find_one(
            {"user_id": player.user_id},
            {"longest_word": 1, "_id": 0},
        )
        if existing:
            current_longest = existing.get("longest_word") or ""
            candidate = player.longest_word or ""
            new_longest = max(current_longest, candidate, key=len) or None
            await db.player.update_one(
                {"user_id": player.user_id},
                {
                    "$inc": {
                        "game_count": 1,
                        "win_count": int(player in self.players_in_game),
                        "word_count": player.word_count,
                        "letter_count": player.letter_count,
                    },
                    "$set": {"longest_word": new_longest},
                },
            )
        else:
            await db.player.insert_one(
                {
                    "user_id": player.user_id,
                    "game_count": 1,
                    "win_count": int(player in self.players_in_game),
                    "word_count": player.word_count,
                    "letter_count": player.letter_count,
                    "longest_word": player.longest_word or None,
                }
            )

        await db.gameplayer.insert_one(
            {
                "user_id": player.user_id,
                "group_id": self.group_id,
                "game_id": game_id,
                "won": player in self.players_in_game,
                "word_count": player.word_count,
                "letter_count": player.letter_count,
                "longest_word": player.longest_word or None,
            }
        )

    async def scan_for_stale_timer(self) -> None:
        # Check if game timer is stuck
        timer = self.time_left
        for _ in range(5):
            await asyncio.sleep(1)
            if timer != self.time_left and timer >= 0:
                return  # Timer not stuck
            if self.state == GameState.KILLGAME or self.group_id not in GlobalState.games:
                return  # Game already killed

        await send_admin_group(f"Prolonged stale/negative timer detected in group `{self.group_id}`. Game terminated.")
        try:
            await self.send_message("Game timer is malfunctioning. Game terminated.")
        except:
            pass

        GlobalState.games.pop(self.group_id, None)

    async def main_loop(self, message: types.Message) -> None:
        # Attempt to fix issue of stuck game with negative timer.
        negative_timer = 0
        try:
            await self.send_message(
                f"A{'n' if self.name[0] in 'aeiou' else ''} {self.name} is starting.\n"
                f"{self.min_players}-{self.max_players} players are needed.\n"
                f"{self.time_left}s to /join."
            )
            await self.join(message)

            while True:
                await asyncio.sleep(1)
                if self.state == GameState.JOINING:
                    if self.time_left > 0:
                        self.time_left -= 1
                        if self.time_left in (15, 30, 60):
                            await self.send_message(f"{self.time_left}s left to /join.")
                    elif len(self.players) < self.min_players:
                        await self.send_message("Not enough players. Game terminated.")
                        del GlobalState.games[self.group_id]
                        return
                    else:
                        self.state = GameState.RUNNING
                        await self.send_message("Game is starting...")

                        random.shuffle(self.players)
                        self.players_in_game = self.players[:]

                        await self.running_initialization()
                        await self.send_turn_message()
                elif self.state == GameState.RUNNING:
                    # Check for prolonged negative timer
                    if self.time_left < 0:
                        negative_timer += 1
                    if negative_timer >= 5:
                        raise ValueError("Prolonged negative timer.")

                    if await self.running_phase_tick():  # True: Game ended
                        await self.update_db()
                        return
                elif self.state == GameState.KILLGAME:
                    await self.send_message("Game ended forcibly.")
                    GlobalState.games.pop(self.group_id, None)
                    return
        except Exception as e:
            GlobalState.games.pop(self.group_id, None)
            try:
                await self.send_message(
                    f"Game ended due to the following error:\n`{e.__class__.__name__}: {e}`.\n"
                    "My owner will be notified."
                )
            except:
                pass
            raise
