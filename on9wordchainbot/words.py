import json
import logging
from typing import ClassVar

from dawg import CompletionDAWG

from on9wordchainbot.constants import PLACES_SOURCE, WORDLIST_SOURCE
from on9wordchainbot.resources import get_db, get_session

logger = logging.getLogger(__name__)


class WordList:
    # Directed acyclic word graph (DAWG)
    dawg: ClassVar[CompletionDAWG]
    count: ClassVar[int]
    source: ClassVar[str]
    include_db_words: ClassVar[bool] = False

    @classmethod
    def parse_source(cls, text: str) -> list[str]:
        return text.splitlines()

    @classmethod
    def normalize_words(cls, words: list[str]) -> list[str]:
        return [w.lower() for w in words if w.isalpha()]

    @classmethod
    async def update(cls) -> None:
        # Words retrieved from online repo and database table with additional approved words
        logger.info("Retrieving words for %s", cls.__name__)

        async def get_words_from_source() -> list[str]:
            session = get_session()
            async with session.get(cls.source) as resp:
                text = await resp.text()
                return cls.parse_source(text)

        async def get_words_from_db() -> list[str]:
            db = get_db()
            res = await db.wordlist.find({"accepted": True}, {"word": 1, "_id": 0}).to_list(length=None)
            return [row["word"] for row in res]

        wordlist = await get_words_from_source()
        if cls.include_db_words:
            wordlist += await get_words_from_db()

        logger.info("Processing words for %s", cls.__name__)

        wordlist = cls.normalize_words(wordlist)
        cls.dawg = CompletionDAWG(wordlist)
        cls.count = len(cls.dawg.keys())

        logger.info("DAWG updated for %s", cls.__name__)


class Words(WordList):
    source = WORDLIST_SOURCE
    include_db_words = True


class Places(WordList):
    source = PLACES_SOURCE

    @classmethod
    def parse_source(cls, text: str) -> list[str]:
        return json.loads(text)

    @classmethod
    def normalize_words(cls, words: list[str]) -> list[str]:
        return [
            w.casefold()
            for w in words
            if any(c.isalpha() for c in w) and all(c.isalpha() or c in " '-." for c in w)
        ]
