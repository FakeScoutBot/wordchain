from typing import Optional

from on9wordchainbot.constants import GameSettings
from on9wordchainbot.models.game.hard_mode import HardModeGame
from on9wordchainbot.utils import get_random_word
from on9wordchainbot.words import Places


class CountriesGame(HardModeGame):
    name = "countries game"
    command = "startcountries"
    wordlist = Places
    has_word_length_limit = False

    def __init__(self, group_id: int) -> None:
        super().__init__(group_id)
        self.time_limit = GameSettings.MIN_TURN_SECONDS
        self.min_letters_limit = 1

    def is_valid_answer_text(self, text: str) -> bool:
        return (
            0 < len(text) <= 100
            and any(c.isalpha() for c in text)
            and all(c.isalpha() or c in " '-." for c in text)
        )

    def normalize_answer_text(self, text: str) -> str:
        return " ".join(text.split()).casefold()

    def get_word_start_letter(self, word: str) -> str:
        return next(c for c in word if c.isalpha())

    def get_word_end_letter(self, word: str) -> str:
        return next(c for c in reversed(word) if c.isalpha())

    def get_random_valid_answer(self) -> Optional[str]:
        return get_random_word(
            exclude_words=self.used_words,
            wordlist=self.wordlist,
            predicate=lambda word: self.get_word_start_letter(word) == self.get_word_end_letter(self.current_word)
        )
