import json
from typing import Callable, List

import httpx
import pytest

from app.client import WordleClient
from app.config import Settings


def score(answer: str, guess: str) -> List[dict]:
    """Mirror the live API convention: a letter is ``present`` when it appears
    anywhere in the answer (regardless of multiplicity), ``correct`` when it is
    in the same slot, and ``absent`` otherwise.
    """
    results = []
    for index, char in enumerate(guess):
        if index < len(answer) and answer[index] == char:
            kind = "correct"
        elif char in answer:
            kind = "present"
        else:
            kind = "absent"
        results.append({"slot": index, "guess": char, "result": kind})
    return results


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url="http://testserver",
        timeout=5.0,
        default_size=5,
        max_attempts=6,
        verify_ssl=False,
    )


@pytest.fixture
def make_transport() -> Callable[..., httpx.MockTransport]:
    def factory(answer: str = "crane", fail_times: int = 0) -> httpx.MockTransport:
        state = {"failures": fail_times}

        def handler(request: httpx.Request) -> httpx.Response:
            if state["failures"] > 0:
                state["failures"] -= 1
                return httpx.Response(503, text="unavailable", request=request)
            path = request.url.path
            params = request.url.params
            guess = params.get("guess", "")
            if path.startswith("/word/"):
                target = path.rsplit("/", 1)[-1]
            else:
                target = answer
            payload = score(target, guess)
            return httpx.Response(
                200, text=json.dumps(payload), request=request
            )

        return httpx.MockTransport(handler)

    return factory


@pytest.fixture
def client(settings, make_transport) -> WordleClient:
    return WordleClient(
        settings=settings, transport=make_transport(), max_retries=3, backoff=0.0
    )
