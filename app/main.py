import asyncio
import logging
import random
import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from app.client import WordleClient, WordleError
from app.config import get_settings
from app.models import GuessMode, ResultKind
from app.solver import (
    SolveReport,
    Solver,
    load_answers,
    load_wordlist,
)

app = typer.Typer(
    name="wordle",
    help="Wordle API client and solver.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
logger = logging.getLogger("wordle.cli")

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_DATEFMT = "%H:%M:%S"

RESULT_STYLE = {
    ResultKind.correct: "[bold green]",
    ResultKind.present: "[bold yellow]",
    ResultKind.absent: "[dim]",
}


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
        datefmt=LOG_DATEFMT,
        stream=sys.stderr,
        force=True,
    )


def render_guess(results: list) -> str:
    ordered = sorted(results, key=lambda item: item.slot)
    return " ".join(
        f"{RESULT_STYLE.get(item.result, '')}{item.guess.upper()}[/]"
        for item in ordered
    )


def print_report(report: SolveReport, title: Optional[str] = None) -> None:
    if report.mode is GuessMode.random and report.seed is not None:
        console.print(f"[dim]random seed: {report.seed}[/]")
    table = Table(title=title or f"Wordle [{report.mode.value}]")
    table.add_column("#", justify="right")
    table.add_column("Guess")
    for index, (guess, results) in enumerate(report.guesses, start=1):
        table.add_row(str(index), render_guess(results))
    console.print(table)
    if report.solved:
        console.print(f"[bold green]Solved in {report.attempts} attempt(s)![/]")
    elif report.answer:
        console.print(
            f"[yellow]Not solved; likely answer: {report.answer.upper()}[/]"
        )
    else:
        console.print("[red]Not solved within attempt limit.[/]")


def resolve_mode(mode: Optional[str], interactive: bool) -> GuessMode:
    if mode is None:
        if not interactive:
            raise typer.BadParameter("--mode is required in --no-interactive mode")
        mode = typer.prompt("Puzzle mode (daily/random/word)", default="random")
    try:
        return GuessMode(mode.strip().lower())
    except ValueError:
        raise typer.BadParameter(f"unknown mode: {mode}")


def _load_lists(wordlist: Optional[Path], size: int) -> tuple:
    guesses = load_wordlist(wordlist, size)
    if not guesses:
        logger.warning("no guess list loaded; deriving guesses from answers")
    answers = load_answers(None, size) or guesses
    logger.debug(
        "loaded %d guess(s) and %d answer candidate(s) of size %d",
        len(guesses),
        len(answers),
        size,
    )
    return guesses, answers


def build_solver(client: WordleClient, wordlist: Optional[Path],
                 size: int, max_attempts: int) -> Solver:
    guesses, answers = _load_lists(wordlist, size)
    return Solver(
        client,
        wordlist=answers,
        guesslist=guesses,
        max_attempts=max_attempts,
    )


@app.command()
def play(
    mode: Optional[str] = typer.Option(
        None, "--mode", "-m", help="Puzzle mode: daily, random, or word."
    ),
    count: Optional[int] = typer.Option(
        None, "--count", "-n", min=1, help="Number of puzzles (random mode)."
    ),
    word: Optional[str] = typer.Option(
        None, "--word", "-w", help="Target word (word mode)."
    ),
    size: Optional[int] = typer.Option(
        None, "--size", "-s", help="Word size (defaults to config)."
    ),
    seed: Optional[int] = typer.Option(
        None, "--seed", help="Seed for random mode (reproducible puzzles)."
    ),
    delay: float = typer.Option(
        0.0, "--delay", min=0.0, help="Seconds to wait between guesses."
    ),
    wordlist: Optional[Path] = typer.Option(
        None, "--wordlist", help="Path to a newline-separated word list."
    ),
    max_attempts: Optional[int] = typer.Option(
        None, "--max-attempts", help="Maximum number of guesses per puzzle."
    ),
    log_level: str = typer.Option(
        "INFO", "--log-level", help="DEBUG, INFO, WARNING, or ERROR."
    ),
    interactive: bool = typer.Option(
        True, "--interactive/--no-interactive", help="Prompt for missing options."
    ),
) -> None:
    """Automatically play daily or random Wordle puzzles, with logging."""
    configure_logging(log_level)
    settings = get_settings()
    guess_mode = resolve_mode(mode, interactive)

    if guess_mode is GuessMode.word and not word:
        if interactive:
            word = typer.prompt("Target word")
        else:
            raise typer.BadParameter("--word is required for word mode")
    if guess_mode is GuessMode.word:
        count = 1
    elif count is None:
        if interactive and guess_mode is GuessMode.random:
            count = typer.prompt("How many puzzles?", default=1, type=int)
        else:
            count = 1
    if guess_mode is GuessMode.daily:
        count = 1

    resolved_size = size or (len(word) if word else settings.default_size)
    resolved_attempts = max_attempts or settings.max_attempts

    async def _run() -> List[SolveReport]:
        reports: List[SolveReport] = []
        base_seed = seed
        if guess_mode is GuessMode.random and base_seed is None:
            base_seed = random.randrange(2**31)
            logger.info("no seed provided; using base seed=%d", base_seed)
        async with WordleClient(settings) as client:
            for index in range(count or 1):
                puzzle_seed = (
                    base_seed + index
                    if guess_mode is GuessMode.random and base_seed is not None
                    else None
                )
                label = f"Puzzle {index + 1}/{count} [{guess_mode.value}]"
                if puzzle_seed is not None:
                    label += f" seed={puzzle_seed}"
                console.rule(label)
                logger.info("starting %s", label)
                solver = build_solver(
                    client, wordlist, resolved_size, resolved_attempts
                )
                report = await solver.solve(
                    guess_mode, resolved_size, target=word, seed=puzzle_seed
                )
                print_report(report, title=label)
                reports.append(report)
                if delay:
                    await asyncio.sleep(delay)
        return reports

    try:
        reports = asyncio.run(_run())
    except WordleError as exc:
        logger.error("api error: %s", exc)
        console.print(f"[bold red]API error:[/] {exc}")
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted.[/]")
        raise typer.Exit(code=130)

    solved = sum(1 for report in reports if report.solved)
    console.print(f"[bold]Solved {solved}/{len(reports)} puzzle(s).[/]")


@app.command()
def solve(
    mode: str = typer.Argument(
        "daily", help="Puzzle mode: daily, random, or word."
    ),
    word: Optional[str] = typer.Option(
        None, "--word", "-w", help="Target word (required for word mode)."
    ),
    size: Optional[int] = typer.Option(
        None, "--size", "-s", help="Word size (defaults to config)."
    ),
    seed: Optional[int] = typer.Option(
        None, "--seed", help="Seed for random mode (reproducible puzzles)."
    ),
    wordlist: Optional[Path] = typer.Option(
        None, "--wordlist", help="Path to a newline-separated word list."
    ),
    max_attempts: Optional[int] = typer.Option(
        None, "--max-attempts", help="Maximum number of guesses."
    ),
) -> None:
    """Solve a single Wordle puzzle, printing each guess and its result."""
    try:
        guess_mode = GuessMode(mode.lower())
    except ValueError:
        raise typer.BadParameter(f"unknown mode: {mode}")

    if guess_mode is GuessMode.word and not word:
        raise typer.BadParameter("--word is required for word mode")

    settings = get_settings()
    resolved_size = size or (len(word) if word else settings.default_size)
    resolved_attempts = max_attempts or settings.max_attempts

    async def _run() -> SolveReport:
        async with WordleClient(settings) as client:
            solver = build_solver(
                client, wordlist, resolved_size, resolved_attempts
            )
            return await solver.solve(
                guess_mode, resolved_size, target=word, seed=seed
            )

    try:
        report = asyncio.run(_run())
    except WordleError as exc:
        console.print(f"[bold red]API error:[/] {exc}")
        raise typer.Exit(code=1)

    print_report(report)


@app.command()
def version() -> None:
    """Print the package version."""
    console.print("wordle-solver 0.1.0")


if __name__ == "__main__":
    app()
