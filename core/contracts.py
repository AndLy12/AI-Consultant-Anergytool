from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    role: str
    content: str


class Consultant(Protocol):
    async def answer(self, history: tuple[Message, ...]) -> dict:
        """Return an untrusted answer object; history ends with current user text.

        No cookies, session credentials, environment or database handles are passed.
        An implementation must be usable from a standalone CSV adapter too.
        """
        ...
