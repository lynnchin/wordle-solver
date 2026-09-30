import asyncio
from typing import Any, Dict, Optional

import httpx

from app.config import Settings, get_settings
from app.models import GuessResponse

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class WordleError(Exception):
    """Base class for all Wordle client errors."""


class WordleHTTPError(WordleError):
    """A non-2xx HTTP response from the API."""

    def __init__(
        self, status_code: int, detail: str, url: Optional[str] = None
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.url = url
        location = f" ({url})" if url else ""
        super().__init__(f"HTTP {status_code}{location}: {detail}")


class WordleConnectionError(WordleError):
    """The API could not be reached (DNS, TLS, connection reset, etc.)."""

    def __init__(self, message: str, url: Optional[str] = None) -> None:
        self.url = url
        super().__init__(f"{message}{f' ({url})' if url else ''}")


class WordleTimeoutError(WordleConnectionError):
    """The request exceeded the configured timeout."""


class WordleRateLimitError(WordleHTTPError):
    """The API returned 429 Too Many Requests."""

    def __init__(
        self,
        detail: str,
        retry_after: Optional[float] = None,
        url: Optional[str] = None,
    ) -> None:
        self.retry_after = retry_after
        suffix = (
            f" (retry after {retry_after:g}s)" if retry_after is not None else ""
        )
        super().__init__(429, f"{detail}{suffix}", url)


class WordleClient:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        max_retries: int = 3,
        backoff: float = 0.5,
        max_retry_after: float = 30.0,
    ) -> None:
        self._settings = settings or get_settings()
        self._max_retries = max_retries
        self._backoff = backoff
        self._max_retry_after = max_retry_after
        self._client = httpx.AsyncClient(
            base_url=self._settings.base_url,
            timeout=self._settings.timeout,
            verify=self._settings.verify_ssl,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self) -> "WordleClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, path: str, params: Dict[str, Any]) -> GuessResponse:
        url = str(self._client.base_url.join(path))
        last_error: Optional[WordleError] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await self._client.get(path, params=params)
            except httpx.TimeoutException as exc:
                last_error = WordleTimeoutError(
                    f"request timed out after {self._settings.timeout:g}s: {exc}",
                    url,
                )
            except httpx.TransportError as exc:
                last_error = WordleConnectionError(
                    f"connection failed: {exc.__class__.__name__}: {exc}", url
                )
            else:
                if response.is_success:
                    return GuessResponse.model_validate(response.json())
                last_error = self._http_error(response, url)
                if last_error.status_code not in RETRYABLE_STATUS:
                    raise last_error

            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_delay(last_error, attempt))

        assert last_error is not None
        raise last_error

    def _retry_delay(self, error: WordleError, attempt: int) -> float:
        if isinstance(error, WordleRateLimitError) and error.retry_after is not None:
            return min(error.retry_after, self._max_retry_after)
        return self._backoff * attempt

    def _http_error(self, response: httpx.Response, url: str) -> WordleHTTPError:
        detail = self._extract_detail(response)
        if response.status_code == 429:
            return WordleRateLimitError(
                detail, self._parse_retry_after(response), url
            )
        return WordleHTTPError(response.status_code, detail, url)

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> Optional[float]:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return max(float(raw), 0.0)
        except ValueError:
            return None

    @staticmethod
    def _extract_detail(response: httpx.Response) -> str:
        if response.status_code == 429 and not response.content:
            return "rate limit exceeded"
        try:
            payload = response.json()
        except ValueError:
            return response.text or response.reason_phrase
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        return str(detail)

    async def guess_daily(
        self, guess: str, size: Optional[int] = None
    ) -> GuessResponse:
        return await self._request(
            "/daily",
            {"guess": guess, "size": size or self._settings.default_size},
        )

    async def guess_random(
        self,
        guess: str,
        size: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> GuessResponse:
        params: Dict[str, Any] = {
            "guess": guess,
            "size": size or self._settings.default_size,
        }
        if seed is not None:
            params["seed"] = seed
        return await self._request("/random", params)

    async def guess_word(self, word: str, guess: str) -> GuessResponse:
        return await self._request(f"/word/{word}", {"guess": guess})
