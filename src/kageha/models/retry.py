"""Transient provider retry policy for OpenAI-compatible model calls."""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from typing import Any

import httpx


_RETRY_AFTER_RE = re.compile(r"retry[-_]after[=:\s]+([0-9]+(?:\.[0-9]+)?)", re.I)
_HARD_QUOTA_RE = re.compile(
    r"insufficient[_\s-]?quota|billing|credit|payment.?required|account.?overdue",
    re.I,
)
_RATE_LIMIT_RE = re.compile(r"\b(429|rate.?limit|too many requests|tpm|rpm)\b", re.I)
_TRANSIENT_HTTP_RE = re.compile(r"\b(408|409|425|500|502|503|504)\b")
_TIMEOUT_RE = re.compile(r"\b(timeout|timed out|deadline exceeded)\b", re.I)
_CONN_RE = re.compile(r"\b(connection|temporar|reset by peer|broken pipe)\b", re.I)
_EMPTY_RE = re.compile(r"empty (model|stream) response", re.I)


@dataclass(frozen=True)
class RetryDecision:
    """Whether to retry the same model and how long to wait."""

    retryable: bool
    failure_class: str
    delay_s: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.75
    max_delay_s: float = 20.0
    jitter: float = 0.35


DEFAULT_RETRY_POLICY = RetryPolicy()


class ProviderHTTPError(RuntimeError):
    """HTTP failure with status / Retry-After preserved for the router."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_s: float | None = None,
        body: str = "",
        request_id: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_s = retry_after_s
        self.body = body
        self.request_id = request_id


def parse_retry_after(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, 120.0)


def extract_retry_after(err: BaseException | str) -> float | None:
    if isinstance(err, ProviderHTTPError) and err.retry_after_s is not None:
        return err.retry_after_s
    if isinstance(err, httpx.HTTPStatusError):
        return parse_retry_after(err.response.headers.get("Retry-After"))
    text = str(err)
    m = _RETRY_AFTER_RE.search(text)
    if not m:
        return None
    try:
        return min(float(m.group(1)), 120.0)
    except ValueError:
        return None


def raise_for_status(resp: httpx.Response, *, body: str | None = None) -> None:
    """Like ``Response.raise_for_status`` but preserves Retry-After + body."""
    if resp.is_success:
        return
    text = body
    if text is None:
        try:
            # Prefer already-read content; avoid forcing stream consumption.
            text = (getattr(resp, "text", None) or "")[:400]
        except Exception:  # noqa: BLE001
            text = ""
    retry_after = parse_retry_after(resp.headers.get("Retry-After"))
    request_id = (
        resp.headers.get("x-request-id")
        or resp.headers.get("x-dashscope-requestid")
        or resp.headers.get("cf-ray")
        or ""
    )
    detail = f"HTTP {resp.status_code}"
    try:
        detail = f"{detail} for {resp.request.url}"
    except Exception:  # noqa: BLE001
        pass
    if text:
        detail = f"{detail}: {text}"
    if retry_after is not None:
        detail = f"{detail} (retry-after={retry_after})"
    if request_id:
        detail = f"{detail} (request-id={request_id})"
    raise ProviderHTTPError(
        detail,
        status_code=int(resp.status_code),
        retry_after_s=retry_after,
        body=text or "",
        request_id=str(request_id),
    )


def classify_error(err: BaseException | str) -> str:
    """Return a coarse failure class string for routing / circuits."""
    if isinstance(err, ProviderHTTPError):
        status = err.status_code or 0
        body = f"{err} {err.body}"
        if status in {401, 403}:
            return "auth"
        if status == 429:
            return "quota" if _HARD_QUOTA_RE.search(body) else "rate_limit"
        if status in {408, 409, 425, 500, 502, 503, 504}:
            return "transient"
        if status >= 500:
            return "transient"
    text = str(err)
    low = text.lower()
    if "401" in low or "403" in low or "unauthori" in low or "forbidden" in low:
        return "auth"
    if _HARD_QUOTA_RE.search(text):
        return "quota"
    if _RATE_LIMIT_RE.search(text):
        return "rate_limit"
    if _EMPTY_RE.search(text):
        return "transient"
    if "thoughtsignature" in low or "thought signature" in low:
        return "transient"
    if "function call turn" in low:
        return "transient"
    if _TIMEOUT_RE.search(text):
        return "timeout"
    if _TRANSIENT_HTTP_RE.search(text) or _CONN_RE.search(text):
        return "transient"
    if isinstance(err, (httpx.TimeoutException, httpx.TransportError, TimeoutError)):
        return "timeout" if isinstance(err, (httpx.TimeoutException, TimeoutError)) else "transient"
    return "hard_fail"


def is_retryable(err: BaseException | str) -> bool:
    return classify_error(err) in {"rate_limit", "transient", "timeout"}


def compute_delay(
    attempt: int,
    err: BaseException | str,
    *,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> float:
    """Backoff delay for 1-based attempt index after a failure."""
    retry_after = extract_retry_after(err)
    if retry_after is not None:
        base = retry_after
    else:
        exp = max(0, int(attempt) - 1)
        base = min(policy.max_delay_s, policy.base_delay_s * (2**exp))
    jitter = base * policy.jitter
    delay = base + random.uniform(-jitter, jitter)
    return max(0.05, min(policy.max_delay_s, delay))


def decide_retry(
    attempt: int,
    err: BaseException | str,
    *,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> RetryDecision:
    """Decide whether another same-model attempt should run."""
    failure_class = classify_error(err)
    if attempt >= policy.max_attempts:
        return RetryDecision(
            retryable=False,
            failure_class=failure_class,
            reason="max_attempts",
        )
    if failure_class not in {"rate_limit", "transient", "timeout"}:
        return RetryDecision(
            retryable=False,
            failure_class=failure_class,
            reason="not_retryable",
        )
    return RetryDecision(
        retryable=True,
        failure_class=failure_class,
        delay_s=compute_delay(attempt, err, policy=policy),
        reason=failure_class,
    )


async def sleep_backoff(delay_s: float) -> None:
    if delay_s > 0:
        await asyncio.sleep(delay_s)


def short_err(err: BaseException | str, *, limit: int = 180) -> str:
    text = " ".join(str(err).split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def retry_notice(
    *,
    model_id: str,
    attempt: int,
    max_attempts: int,
    error: str,
    delay_s: float,
) -> dict[str, Any]:
    return {
        "model": model_id,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "error": error,
        "delay_s": round(float(delay_s), 3),
        "kind": "model_retry",
    }
