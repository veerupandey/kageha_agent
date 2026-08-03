"""Unit tests for model retry / backoff helpers."""

from __future__ import annotations

import pytest

from kageha.models.retry import (
    ProviderHTTPError,
    RetryPolicy,
    classify_error,
    compute_delay,
    decide_retry,
    parse_retry_after,
)


def test_classify_rate_limit_vs_hard_quota():
    assert classify_error("HTTP 429 Too Many Requests") == "rate_limit"
    assert classify_error("429 insufficient_quota") == "quota"
    assert classify_error("empty stream response") == "transient"
    assert classify_error("401 unauthorized") == "auth"


def test_decide_retry_respects_max_attempts_and_retry_after():
    err = ProviderHTTPError(
        "HTTP 429",
        status_code=429,
        retry_after_s=1.5,
    )
    decision = decide_retry(1, err, policy=RetryPolicy(max_attempts=3, jitter=0.0))
    assert decision.retryable is True
    assert decision.failure_class == "rate_limit"
    assert decision.delay_s == pytest.approx(1.5, abs=0.05)

    exhausted = decide_retry(3, err, policy=RetryPolicy(max_attempts=3))
    assert exhausted.retryable is False


def test_parse_retry_after_and_delay_cap():
    assert parse_retry_after("2") == 2.0
    assert parse_retry_after("nope") is None
    delay = compute_delay(
        1,
        ProviderHTTPError("x", status_code=429, retry_after_s=999),
        policy=RetryPolicy(max_delay_s=20.0, jitter=0.0),
    )
    assert delay == 20.0
