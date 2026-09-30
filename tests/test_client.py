import json

import httpx
import pytest

from app.client import (
    WordleClient,
    WordleConnectionError,
    WordleHTTPError,
    WordleRateLimitError,
    WordleTimeoutError,
)
from tests.conftest import score


def build(settings, handler):
    return WordleClient(
        settings=settings,
        transport=httpx.MockTransport(handler),
        max_retries=3,
        backoff=0.0,
        max_retry_after=0.0,
    )


@pytest.mark.asyncio
async def test_connection_error_raised_after_retries(settings) -> None:
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("refused", request=request)

    client = build(settings, handler)
    with pytest.raises(WordleConnectionError):
        await client.guess_daily("crane")
    assert calls["n"] == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_timeout_error_raised(settings) -> None:
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    client = build(settings, handler)
    with pytest.raises(WordleTimeoutError):
        await client.guess_daily("crane")
    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limit_error_exposes_retry_after(settings) -> None:
    def handler(request):
        return httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={"detail": "slow down"},
            request=request,
        )

    client = build(settings, handler)
    with pytest.raises(WordleRateLimitError) as excinfo:
        await client.guess_daily("crane")
    assert excinfo.value.retry_after == 7.0
    assert excinfo.value.status_code == 429
    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds(settings) -> None:
    state = {"limited": 2}

    def handler(request):
        if state["limited"] > 0:
            state["limited"] -= 1
            return httpx.Response(
                429, headers={"Retry-After": "0"}, request=request
            )
        payload = score("crane", request.url.params.get("guess", ""))
        return httpx.Response(200, text=json.dumps(payload), request=request)

    client = build(settings, handler)
    response = await client.guess_random("slate")
    assert len(response) == 5
    assert state["limited"] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_non_retryable_status_fails_fast(settings) -> None:
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(
            404, json={"detail": "not found"}, request=request
        )

    client = build(settings, handler)
    with pytest.raises(WordleHTTPError) as excinfo:
        await client.guess_word("crane", "slate")
    assert excinfo.value.status_code == 404
    assert calls["n"] == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_server_error_retries_then_succeeds(settings, make_transport) -> None:
    client = WordleClient(
        settings=settings,
        transport=make_transport(fail_times=1),
        max_retries=3,
        backoff=0.0,
    )
    response = await client.guess_daily("crane")
    assert len(response) == 5
    await client.aclose()
