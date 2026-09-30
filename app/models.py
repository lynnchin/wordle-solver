from enum import Enum
from typing import Iterator, List

from pydantic import BaseModel, Field, RootModel


class ResultKind(str, Enum):
    absent = "absent"
    present = "present"
    correct = "correct"


class GuessResult(BaseModel):
    slot: int = Field(..., ge=0)
    guess: str
    result: ResultKind


class GuessResponse(RootModel[List[GuessResult]]):
    def __iter__(self) -> Iterator[GuessResult]:
        return iter(self.root)

    def __len__(self) -> int:
        return len(self.root)

    def __getitem__(self, index: int) -> GuessResult:
        return self.root[index]

    @property
    def solved(self) -> bool:
        return bool(self.root) and all(
            item.result is ResultKind.correct for item in self.root
        )


class GuessMode(str, Enum):
    daily = "daily"
    random = "random"
    word = "word"
