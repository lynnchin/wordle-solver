import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.client import WordleClient
from app.models import GuessMode, GuessResponse, GuessResult, ResultKind

logger = logging.getLogger("wordle.solver")

DEFAULT_WORDLIST = Path(__file__).parent / "data" / "wordlist.txt"
DEFAULT_ANSWERS = Path(__file__).parent / "data" / "answers.txt"
DEFAULT_OPENERS = {4: "rate", 5: "crane", 6: "crane"}

Pattern = Tuple[str, ...]
Observation = Tuple[str, Pattern]

PROBE_LIMIT = 150
PROBE_LIMIT_LARGE = 40
LARGE_CANDIDATE_SET = 120
MAX_EVAL_CANDIDATES = 400


@dataclass
class FrequencyScorer:
    """Letter-frequency scoring over a candidate set.

    Scores a word by (1) how often its distinct letters occur across the
    candidates and (2) how often each letter occurs in the same position.
    Higher scores mean the guess is expected to eliminate more candidates.
    """

    letters: Dict[str, int] = field(default_factory=dict)
    positions: List[Dict[str, int]] = field(default_factory=list)

    @classmethod
    def from_candidates(cls, candidates: Sequence[str]) -> "FrequencyScorer":
        letters: Counter = Counter()
        positions: List[Counter] = []
        for word in candidates:
            for letter in set(word):
                letters[letter] += 1
            for slot, letter in enumerate(word):
                while len(positions) <= slot:
                    positions.append(Counter())
                positions[slot][letter] += 1
        return cls(
            letters=dict(letters),
            positions=[dict(counter) for counter in positions],
        )

    def score(self, word: str) -> Tuple[int, int]:
        coverage = sum(self.letters.get(letter, 0) for letter in set(word))
        positional = sum(
            self.positions[slot].get(letter, 0)
            for slot, letter in enumerate(word)
            if slot < len(self.positions)
        )
        return coverage, positional

    def best(self, candidates: Sequence[str]) -> str:
        return max(
            candidates,
            key=lambda word: (self.score(word), -list(candidates).index(word)),
        )


@dataclass
class SolveReport:
    mode: GuessMode
    target: Optional[str]
    guesses: List[Tuple[str, List[GuessResult]]] = field(default_factory=list)
    solved: bool = False
    answer: Optional[str] = None
    remaining: List[str] = field(default_factory=list)
    seed: Optional[int] = None

    @property
    def attempts(self) -> int:
        return len(self.guesses)


def load_wordlist(path: Optional[Path] = None, size: Optional[int] = None) -> List[str]:
    """Load the guess word list (words the solver may submit as guesses)."""
    source = Path(path) if path else DEFAULT_WORDLIST
    if not source.exists():
        logger.warning("word list not found at %s; solver runs constraint-only", source)
        return []
    return _read_words(source, size)


def load_answers(
    path: Optional[Path] = None, size: Optional[int] = None
) -> List[str]:
    """Load the candidate universe (possible answers).

    Falls back to the guess word list when no dedicated answers file exists.
    """
    source = Path(path) if path else DEFAULT_ANSWERS
    if not source.exists():
        return load_wordlist(size=size)
    return _read_words(source, size)


def _read_words(source: Path, size: Optional[int]) -> List[str]:
    words: List[str] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        word = line.strip().lower()
        if not word or not word.isalpha():
            continue
        if size is not None and len(word) != size:
            continue
        words.append(word)
    return sorted(set(words))


class Solver:
    """Candidate-elimination solver using letter-frequency scoring.

    ``wordlist`` is the universe of possible answers (used for elimination);
    ``guesslist`` is the pool of words allowed as guesses (defaults to the
    answer universe). Keeping the two separate lets the solver guess common
    words while still eliminating against a broad dictionary.

    Elimination is exact: every (guess, feedback) observation is retained and
    a candidate survives only if it reproduces all observations. Guesses are
    chosen by frequency scoring, with an information-gain probe when several
    candidates remain and winning cannot be guaranteed.
    """

    def __init__(
        self,
        client: Optional[WordleClient],
        wordlist: Optional[List[str]] = None,
        guesslist: Optional[List[str]] = None,
        max_attempts: int = 6,
    ) -> None:
        self._client = client
        self._wordlist = wordlist or []
        self._guesslist = guesslist or self._wordlist
        self._guess_set = set(self._guesslist)
        self._max_attempts = max_attempts
        self._history: List[Observation] = []
        self._guessed: List[str] = []
        self._cache: Optional[List[str]] = None

    @property
    def constraints(self) -> Dict[str, object]:
        return {
            "guesses": self._guessed,
            "observations": [
                {"guess": guess, "pattern": "".join(p[0].upper() for p in pattern)}
                for guess, pattern in self._history
            ],
        }

    def reset(self) -> None:
        self._history.clear()
        self._guessed.clear()
        self._cache = None

    @staticmethod
    def feedback(guess: str, answer: str) -> Pattern:
        """Feedback the API would return for ``guess`` against ``answer``.

        Mirrors the live convention: a letter is ``present`` when it occurs
        anywhere in the answer (multiplicity is not tracked).
        """
        return tuple(
            ResultKind.correct.value
            if g == a
            else ResultKind.present.value
            if g in answer
            else ResultKind.absent.value
            for g, a in zip(guess, answer)
        )

    def update(self, response: GuessResponse) -> None:
        ordered = sorted(response, key=lambda item: item.slot)
        guess = "".join(item.guess for item in ordered)
        pattern = tuple(item.result.value for item in ordered)
        self._guessed.append(guess)
        self._history.append((guess, pattern))
        if self._cache is not None:
            self._cache = [word for word in self._cache if self.matches(word)]
        logger.debug("observed %s -> %s", guess, "".join(p[0].upper() for p in pattern))

    def matches(self, word: str) -> bool:
        return all(
            self.feedback(guess, word) == pattern
            for guess, pattern in self._history
        )

    def pool(self, size: int) -> List[str]:
        return [word for word in self._wordlist if len(word) == size]

    def guess_pool(self, size: int) -> List[str]:
        return [word for word in self._guesslist if len(word) == size]

    def candidates(self, size: int) -> List[str]:
        if self._cache is None:
            self._cache = [word for word in self.pool(size) if self.matches(word)]
        return self._cache

    def scorer(self, size: int) -> FrequencyScorer:
        return FrequencyScorer.from_candidates(
            self.candidates(size) or self.pool(size)
        )

    def next_guess(self, size: int, attempts_left: Optional[int] = None) -> str:
        candidates = [w for w in self.candidates(size) if w not in self._guessed]
        if not candidates:
            candidates = self.candidates(size)

        if not self._guessed and size in DEFAULT_OPENERS:
            opener = DEFAULT_OPENERS[size]
            if len(opener) == size:
                return opener

        if not candidates:
            return self._fallback(size)
        if len(candidates) == 1:
            return candidates[0]
        if attempts_left is not None and (
            attempts_left == 1 or len(candidates) <= attempts_left
        ):
            return self.scorer(size).best(candidates)
        return self._informative_guess(size, candidates)

    def _informative_guess(self, size: int, candidates: List[str]) -> str:
        frequency = FrequencyScorer.from_candidates(candidates)
        probes = [w for w in self.guess_pool(size) if w not in self._guessed]
        probes.sort(key=frequency.score, reverse=True)
        limit = PROBE_LIMIT_LARGE if len(candidates) > LARGE_CANDIDATE_SET else PROBE_LIMIT
        evaluation = list(dict.fromkeys(probes[:limit] + candidates))
        candidate_set = set(candidates)
        sample = candidates
        if len(candidates) > MAX_EVAL_CANDIDATES:
            step = -(-len(candidates) // MAX_EVAL_CANDIDATES)
            sample = candidates[::step]
        prepared = [(self._mask(c), c) for c in sample]
        total = len(sample)

        def rank(word: str) -> Tuple[float, int, int]:
            expected = self._expected_remaining(word, prepared, total)
            coverage, positional = frequency.score(word)
            return (expected, 0 if word in candidate_set else 1, -(coverage + positional))

        return min(evaluation, key=rank)

    @staticmethod
    def _mask(word: str) -> int:
        mask = 0
        for letter in word:
            mask |= 1 << (ord(letter) - 97)
        return mask

    @staticmethod
    def _expected_remaining(guess: str, prepared: List[Tuple[int, str]], total: int) -> float:
        buckets: Dict[int, int] = {}
        for candidate_mask, candidate in prepared:
            code = 0
            multiplier = 1
            for guess_letter, candidate_letter in zip(guess, candidate):
                if guess_letter == candidate_letter:
                    digit = 2
                elif (candidate_mask >> (ord(guess_letter) - 97)) & 1:
                    digit = 1
                else:
                    digit = 0
                code += digit * multiplier
                multiplier *= 3
            buckets[code] = buckets.get(code, 0) + 1
        return sum(count * count for count in buckets.values()) / total

    def _fallback(self, size: int) -> str:
        unguessed = [w for w in self.pool(size) if w not in self._guessed]
        if unguessed:
            return FrequencyScorer.from_candidates(unguessed).best(unguessed)
        guesses = [w for w in self.guess_pool(size) if w not in self._guessed]
        if guesses:
            return FrequencyScorer.from_candidates(guesses).best(guesses)
        if size in DEFAULT_OPENERS:
            return DEFAULT_OPENERS[size]
        return "a" * size

    async def solve(
        self,
        mode: GuessMode,
        size: int,
        target: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> SolveReport:
        report = SolveReport(mode=mode, target=target)
        if mode is GuessMode.random and seed is None:
            seed = random.randrange(2**31)
            logger.info("pinned random puzzle seed=%d", seed)
        report.seed = seed
        logger.info(
            "solving mode=%s size=%d target=%s attempts=%d",
            mode.value,
            size,
            target or "-",
            self._max_attempts,
        )
        for attempt in range(1, self._max_attempts + 1):
            attempts_left = self._max_attempts - attempt + 1
            guess = self.next_guess(size, attempts_left=attempts_left)
            response = await self._guess(mode, guess, target, seed)
            report.guesses.append((guess, list(response)))
            self.update(response)
            remaining = self.candidates(size)
            logger.info(
                "attempt %d/%d guess=%s remaining=%d",
                attempt,
                self._max_attempts,
                guess.upper(),
                len(remaining),
            )
            if response.solved:
                report.solved = True
                report.answer = guess
                report.remaining = remaining
                logger.info("solved %s in %d attempt(s)", guess.upper(), attempt)
                return report

        report.remaining = self.candidates(size)
        if len(report.remaining) == 1:
            report.answer = report.remaining[0]
        logger.warning(
            "not solved after %d attempts; %d candidate(s) remain",
            self._max_attempts,
            len(report.remaining),
        )
        return report

    async def _guess(
        self,
        mode: GuessMode,
        guess: str,
        target: Optional[str],
        seed: Optional[int],
    ) -> GuessResponse:
        if self._client is None:
            raise RuntimeError("a WordleClient is required to solve puzzles")
        if mode is GuessMode.daily:
            return await self._client.guess_daily(guess, size=len(guess))
        if mode is GuessMode.random:
            return await self._client.guess_random(guess, size=len(guess), seed=seed)
        if target is None:
            raise ValueError("target word is required for word mode")
        return await self._client.guess_word(target, guess)
