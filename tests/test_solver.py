import httpx
import pytest

from app.client import WordleClient, WordleHTTPError
from app.models import GuessMode, GuessResponse, ResultKind
from app.solver import FrequencyScorer, Solver, load_answers, load_wordlist
from tests.conftest import score


@pytest.mark.asyncio
async def test_client_parses_guess_word(client: WordleClient) -> None:
    response = await client.guess_word("crane", "slate")
    assert len(response) == 5
    assert response[0].slot == 0
    assert all(isinstance(item.result, ResultKind) for item in response)
    await client.aclose()


@pytest.mark.asyncio
async def test_client_retries_on_server_error(settings, make_transport) -> None:
    client = WordleClient(
        settings=settings,
        transport=make_transport(fail_times=2),
        max_retries=3,
        backoff=0.0,
    )
    response = await client.guess_daily("crane")
    assert len(response) == 5
    await client.aclose()


@pytest.mark.asyncio
async def test_client_raises_on_client_error(settings) -> None:
    def handler(request):
        return httpx.Response(
            422,
            json={"detail": [{"loc": ["query", "guess"], "msg": "bad"}]},
            request=request,
        )

    client = WordleClient(
        settings=settings,
        transport=httpx.MockTransport(handler),
        max_retries=1,
        backoff=0.0,
    )
    with pytest.raises(WordleHTTPError) as excinfo:
        await client.guess_daily("x")
    assert excinfo.value.status_code == 422
    await client.aclose()


def test_score_marks_duplicates() -> None:
    results = {item["slot"]: item["result"] for item in score("abbey", "bobby")}
    assert results[0] == "present"
    assert results[1] == "absent"
    assert results[2] == "correct"
    assert results[3] == "present"
    assert results[4] == "correct"


def test_solver_update_filters_candidates() -> None:
    words = load_wordlist(size=5)
    solver = Solver(client=None, wordlist=words)  # type: ignore[arg-type]
    results = [
        {"slot": index, "guess": char, "result": "absent"}
        for index, char in enumerate("slate")
    ]
    solver.update(GuessResponse.model_validate(results))
    candidates = solver.candidates(5)
    assert candidates
    assert all(not (set(word) & set("slate")) for word in candidates)
    assert "brown" in candidates
    assert "crane" not in candidates


def test_solver_tracks_correct_and_present() -> None:
    words = load_wordlist(size=5)
    solver = Solver(client=None, wordlist=words)  # type: ignore[arg-type]
    results = [
        {"slot": 0, "guess": "c", "result": "correct"},
        {"slot": 1, "guess": "r", "result": "absent"},
        {"slot": 2, "guess": "a", "result": "present"},
        {"slot": 3, "guess": "n", "result": "absent"},
        {"slot": 4, "guess": "e", "result": "correct"},
    ]
    solver.update(GuessResponse.model_validate(results))
    for word in solver.candidates(5):
        assert word[0] == "c"
        assert word[4] == "e"
        assert word[2] != "a"
        assert "r" not in word and "n" not in word
    assert "cable" in solver.candidates(5)


@pytest.mark.asyncio
async def test_solver_solves_random(settings, make_transport) -> None:
    client = WordleClient(
        settings=settings, transport=make_transport(answer="stove"), backoff=0.0
    )
    words = load_wordlist(size=5)
    solver = Solver(client, wordlist=words, max_attempts=6)
    report = await solver.solve(GuessMode.random, 5, seed=42)
    assert report.solved is True
    assert report.answer == "stove"
    assert report.attempts <= 6
    await client.aclose()


def test_feedback_matches_live_convention() -> None:
    assert Solver.feedback("bobby", "abbey") == (
        "present",
        "absent",
        "correct",
        "present",
        "correct",
    )


def test_frequency_scorer_prefers_common_letters() -> None:
    scorer = FrequencyScorer.from_candidates(["abcde", "abcdf", "abcdg"])
    common, _ = scorer.score("abcdx")
    rare, _ = scorer.score("efgxy")
    assert common > rare


def test_opener_is_used_first() -> None:
    solver = Solver(None, wordlist=load_wordlist(size=5))  # type: ignore[arg-type]
    assert solver.next_guess(5, attempts_left=6) == "crane"


def test_history_elimination_is_exact() -> None:
    solver = Solver(None, wordlist=load_wordlist(size=5))  # type: ignore[arg-type]
    response = GuessResponse.model_validate(score("crane", "crane"))
    solver.update(response)
    assert solver.candidates(5) == ["crane"]


def test_separate_guess_and_answer_lists() -> None:
    answers = load_answers(size=5)
    guesses = load_wordlist(size=5)
    assert "punky" in answers
    solver = Solver(  # type: ignore[arg-type]
        None, wordlist=answers, guesslist=guesses
    )
    assert solver.guess_pool(5) == guesses
    assert solver.next_guess(5, attempts_left=6) == "crane"
