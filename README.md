# wordle-solver

A production-ready Python client and solver for the Wordle API at
`https://wordle.votee.dev:8000` (ReDoc: `/redoc`, OpenAPI: `/openapi.json`).

It provides:

- An async HTTP client (`httpx.AsyncClient`) with typed models and structured
  error handling (non-200 statuses, timeouts, connection failures, rate limits).
- A candidate-elimination solver driven by letter-frequency scoring and
  information-gain probing.
- A Typer CLI that can play the daily puzzle, random puzzles, or a chosen word
  automatically, with logging.

## How it works

The API exposes three GET endpoints, all returning a list of
`{slot, guess, result}` where `result` is `absent`, `present`, or `correct`:

| Endpoint | Purpose |
| --- | --- |
| `GET /daily?guess=...&size=5` | Guess against the daily puzzle |
| `GET /random?guess=...&size=5&seed=...` | Guess against a random word |
| `GET /word/{word}?guess=...` | Guess against a chosen word |

The solver keeps every `(guess, feedback)` observation and eliminates any
candidate that does not reproduce all observed feedback exactly. The next guess
is chosen by:

1. A fixed opener (`crane` for 5-letter puzzles).
2. The letter-frequency scorer when the answer is within reach.
3. An information-gain probe (minimizes the expected number of remaining
   candidates) while candidates are still numerous.

Two behaviours of the live API are handled explicitly:

- `/random` returns a **new** word on every request unless `seed` is supplied,
  so the CLI always pins a seed (and uses `seed + i` for multiple puzzles).
- Duplicate letters use a **loose** convention: `present` means the letter
  appears anywhere in the answer, so multiplicity is not tracked.

## Project structure

```
wordle-solver/
├── requirements.txt
├── .env.example
├── app/
│   ├── __init__.py
│   ├── config.py        # pydantic-settings configuration
│   ├── models.py        # ResultKind, GuessResult, GuessResponse
│   ├── client.py        # async WordleClient + exceptions
│   ├── solver.py        # candidate-elimination solver
│   ├── main.py          # Typer CLI (play, solve, version)
│   └── data/
│       ├── wordlist.txt # common guess words
│       └── answers.txt  # broad candidate universe
└── tests/
    ├── conftest.py
    ├── test_client.py
    └── test_solver.py
```

## Requirements

- Python 3.9+
- Dependencies: `httpx`, `pydantic`, `pydantic-settings`, `typer`, `rich`,
  `python-dotenv`, `pytest`, `pytest-asyncio`.

## Step-by-step setup

1. Clone and enter the project:

   ```bash
   git clone <repo-url> wordle-solver
   cd wordle-solver
   ```

2. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. (Optional) Create a local environment file. Defaults work without it:

   ```bash
   cp .env.example .env
   ```

   | Variable | Default | Purpose |
   | --- | --- | --- |
   | `WORDLE_BASE_URL` | `https://wordle.votee.dev:8000` | API base URL |
   | `WORDLE_TIMEOUT` | `10` | HTTP timeout in seconds |
   | `WORDLE_DEFAULT_SIZE` | `5` | Default word length |
   | `WORDLE_MAX_ATTEMPTS` | `6` | Max guesses per puzzle |
   | `WORDLE_VERIFY_SSL` | `true` | Verify TLS certificates |

## Step-by-step execution

Run all commands from the project root with the virtual environment active.

### 1. Check the CLI is available

```bash
python -m app.main --help
```

### 2. Play the daily puzzle

```bash
python -m app.main play --mode daily --no-interactive
```

### 3. Play random puzzles (reproducible with a seed)

```bash
python -m app.main play --mode random --count 5 --seed 7 --no-interactive
```

Without `--seed`, the CLI generates one and prints it so the run is
reproducible.

### 4. Solve a specific word

```bash
python -m app.main play --mode word --word slate --no-interactive
```

### 5. Interactive mode (default)

Omit `--mode` and the CLI prompts for it:

```bash
python -m app.main play
```

```
Puzzle mode (daily/random/word) [random]:
How many puzzles? [1]: 
```

### 6. Adjust logging

```bash
python -m app.main play --mode daily --log-level DEBUG
python -m app.main play --mode random --count 3 --log-level WARNING
```

Logging is written to stderr; the guess tables are printed to stdout.

### 7. Solve a single puzzle (non-interactive helper)

```bash
python -m app.main solve daily
python -m app.main solve random --seed 42
python -m app.main solve word --word crane
```

## Command reference: `play`

| Option | Description |
| --- | --- |
| `-m, --mode` | `daily`, `random`, or `word` (prompted if omitted) |
| `-n, --count` | Number of puzzles (random mode) |
| `-w, --word` | Target word (word mode) |
| `-s, --size` | Word length (defaults to config) |
| `--seed` | Seed for random mode (reproducible) |
| `--delay` | Seconds to wait between guesses |
| `--wordlist` | Path to a newline-separated guess list |
| `--max-attempts` | Maximum guesses per puzzle |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `--interactive / --no-interactive` | Prompt for missing options |

## Example output

```
$ python -m app.main play --mode daily --no-interactive

   Puzzle 1/1
┏━━━┳━━━━━━━━━━━┓
┃ # ┃ Guess     ┃
┡━━━╇━━━━━━━━━━━┩
│ 1 │ C R A N E │
│ 2 │ T O N U S │
│ 3 │ P U D G Y │
│ 4 │ P U N K Y │
└───┴───────────┘
Solved in 4 attempt(s)!
Solved 1/1 puzzle(s).
```

## Using the library directly

```python
import asyncio

from app.client import WordleClient
from app.config import get_settings
from app.models import GuessMode
from app.solver import Solver, load_answers, load_wordlist


async def main() -> None:
    settings = get_settings()
    async with WordleClient(settings) as client:
        solver = Solver(
            client,
            wordlist=load_answers(size=5),
            guesslist=load_wordlist(size=5),
            max_attempts=6,
        )
        report = await solver.solve(GuessMode.random, size=5, seed=7)
        print("solved:", report.solved, "answer:", report.answer)


asyncio.run(main())
```

## Error handling

`app/client.py` defines a small exception hierarchy:

- `WordleError` — base class.
- `WordleHTTPError` — any non-2xx response (`status_code`, `detail`, `url`).
- `WordleRateLimitError` — HTTP 429, exposes `retry_after` (honours
  `Retry-After`).
- `WordleConnectionError` — DNS/TLS/connection failures.
- `WordleTimeoutError` — request exceeded the configured timeout.

Transient errors (429 and 5xx) are retried with backoff; other 4xx responses
fail fast.

## Testing

```bash
pytest -q
```

Tests use `httpx.MockTransport`, so no network access is required. Coverage
includes response parsing, retries, timeouts, rate limits, the solver's
elimination logic, and the frequency scorer.

## Notes and limitations

- The solver's answer universe is a bundle of known Wordle word lists. A small
  number of the API's random answers fall outside any public list, which places
  a ceiling on the random-mode solve rate (~76% within 6 guesses over a 100-seed
  sample; daily and most random puzzles solve in 3-4 guesses).
- Guesses are validated by the API as words but the API accepts arbitrary
  5-letter strings, so the bundled guess list is used purely to keep guesses
  informative.
