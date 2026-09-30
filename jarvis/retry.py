"""Retry, exponential backoff and classification of transport failures.

Every external call in JARVIS is wrapped in :func:`with_retry`, which gives all
providers the same behaviour:

- exponential backoff with full jitter, so a fleet of clients does not resync
  into a thundering herd after a 429;
- an explicit timeout budget shared by the whole attempt sequence;
- only *retryable* failures are retried, so a 400 is not hammered four times;
- the final failure is re-raised as :class:`ProviderError` with a safe message.

The module also defines the small error taxonomy the rest of the agent
reasons about. Nothing here imports a vendor SDK.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Optional, TypeVar

import httpx

from .logsetup import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

#: Status codes worth trying again. 429 is rate limiting, 5xx is upstream pain.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


class ProviderError(RuntimeError):
    """A remote provider failed and the failure is not retryable."""

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class RateLimited(ProviderError):
    """The provider explicitly rate-limited us (HTTP 429 / quota exhausted)."""


class ProviderUnavailable(ProviderError):
    """The provider could not be reached at all."""


class ProviderTimeout(ProviderError):
    """The provider did not answer within the budget."""


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to retry, and how long to wait between attempts.

    Attributes:
        attempts: Total attempts including the first. 1 means no retry.
        base: First backoff delay in seconds before doubling.
        maximum: Ceiling for any single backoff delay.
        jitter: Fraction of the delay to randomise, avoiding resynchronisation.
    """

    attempts: int = 3
    base: float = 0.6
    maximum: float = 12.0
    jitter: float = 0.5

    def delay_for(self, attempt: int) -> float:
        """Compute the backoff delay before ``attempt`` (1-based).

        Args:
            attempt: The attempt number that just failed.

        Returns:
            Seconds to sleep, capped and randomised.
        """
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        raw = min(self.base * (2 ** (attempt - 1)), self.maximum)
        if self.jitter <= 0:
            return raw
        return random.uniform(raw * (1.0 - self.jitter), raw * (1.0 + self.jitter))


def is_retryable(exc: BaseException) -> bool:
    """Decide whether an exception is worth another attempt.

    Transport problems, timeouts and retryable HTTP statuses are retried.
    Programming errors and explicit 4xx responses are not.

    Args:
        exc: The exception raised by an attempt.

    Returns:
        True when the caller should retry.
    """
    if isinstance(exc, RateLimited):
        return True
    if isinstance(exc, (ProviderTimeout, ProviderUnavailable)):
        return True
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.TransportError):
        # Connection resets and DNS blips are worth one more go.
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    return False


def classify(exc: BaseException) -> ProviderError:
    """Map a low-level exception onto the provider error taxonomy.

    Args:
        exc: The original exception.

    Returns:
        A :class:`ProviderError` subclass safe to show a user.
    """
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return RateLimited(
                "The model provider is rate-limiting requests right now.",
                status=status,
            )
        if status in RETRYABLE_STATUS:
            return ProviderUnavailable(
                f"The model provider returned HTTP {status}.", status=status
            )
        return ProviderError(
            f"The model provider rejected the request (HTTP {status}).", status=status
        )
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        return ProviderTimeout("The model provider timed out.")
    if isinstance(exc, (httpx.TransportError, ConnectionError, OSError)):
        return ProviderUnavailable(f"Could not reach the model provider: {exc}")
    return ProviderError(f"Unexpected provider failure: {exc}")


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    label: str,
    timeout: Optional[float] = None,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
) -> T:
    """Run an async operation with retries, backoff and a hard timeout.

    Args:
        operation: Zero-argument callable returning the awaitable to run. A
            fresh call is made per attempt, so a retried request body must be
            constructed inside it.
        policy: Retry count and backoff shape.
        label: Human name used in logs, e.g. ``"groq.chat"``.
        timeout: Optional overall budget. When supplied it bounds the entire
            sequence, not each attempt.
        on_retry: Optional callback invoked as ``(attempt, exc, delay)`` before
            each sleep. Used to announce rate limiting to the user.

    Returns:
        The operation's result.

    Raises:
        ProviderError: When every attempt fails. The message is user-safe.
    """
    deadline = time.monotonic() + timeout if timeout else None
    last: BaseException | None = None

    for attempt in range(1, max(1, policy.attempts) + 1):
        try:
            return await operation()
        except asyncio.CancelledError:
            # Cancellation is a deliberate signal; never swallow it.
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately broad, then classified
            last = exc
            retryable = is_retryable(exc)
            budget_left = None if deadline is None else deadline - time.monotonic()

            if not retryable:
                logger.debug(
                    "non-retryable failure op=%s attempt=%d error=%s",
                    label, attempt, type(exc).__name__,
                )
                raise classify(exc) from exc

            if attempt >= policy.attempts or (budget_left is not None and budget_left <= 0):
                break

            delay = policy.delay_for(attempt)
            if budget_left is not None:
                delay = min(delay, max(0.0, budget_left))

            logger.warning(
                "retrying op=%s attempt=%d/%d delay=%.2fs error=%s",
                label, attempt, policy.attempts, delay, type(exc).__name__,
            )
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            await asyncio.sleep(delay)

    assert last is not None  # loop only breaks after storing an exception
    logger.error("giving up op=%s after %d attempt(s)", label, policy.attempts)
    raise classify(last) from last
