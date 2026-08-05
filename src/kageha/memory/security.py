"""Security classification and redaction for persisted memory content."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from kageha.memory.models import MemorySensitivity


_NAMED_SECRET = re.compile(
    r"(?i)(?<![?&])\b(api[_-]?key|authorization|bearer|access[_-]?token|refresh[_-]?token|"
    r"password|passwd|client[_-]?secret|private[_-]?key)\b\s*[:=]\s*['\"]?([^\s'\"]+)"
)
_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_./+=-]{24,}(?![A-Za-z0-9])")
_URL = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_SAFE_ARTIFACT_PATH = re.compile(
    r"^(?:artifacts|outputs|diagrams|carousel|research|slides)/"
    r"[A-Za-z0-9_./+=-]+\.(?:png|jpe?g|webp|gif|pdf|txt|md|json|html|csv)$",
    re.IGNORECASE,
)
_SENSITIVE_URL_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"signature|sig|x-amz-signature|x-goog-signature)=)([^&#\s]+)"
)
_PROMPT_INJECTION = re.compile(
    r"(?i)\b(ignore|disregard|override)\b.{0,30}\b(previous|system|developer|instructions?)\b|"
    r"\b(system prompt|developer message|reveal your prompt|you are now)\b"
)
_HEALTH = re.compile(
    r"(?i)\b(diagnos(?:is|ed)|medical|medication|prescription|therapy|mental health|"
    r"cancer|diabetes|pregnan(?:t|cy)|disability)\b"
)
_FINANCIAL = re.compile(
    r"(?i)\b(bank account|routing number|credit card|debit card|tax id|social security|"
    r"salary|income|mortgage)\b"
)
_PERSONAL = re.compile(
    r"(?i)\b(home address|date of birth|phone number|personal email|passport|driver'?s license)\b"
)


@dataclass
class SecurityResult:
    safe_text: str
    sensitivity: str
    blocked: bool
    findings: list[str]


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    size = len(value)
    return -sum((n / size) * math.log2(n / size) for n in counts.values())


def _looks_like_secret_token(value: str) -> bool:
    if len(value) < 24 or _entropy(value) < 3.6:
        return False
    classes = sum(
        bool(re.search(pattern, value))
        for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[_./+=-]")
    )
    return classes >= 3


def _inside_public_url(match: re.Match[str], text: str) -> bool:
    """Return whether a token candidate is wholly contained in an HTTP(S) URL.

    The entropy heuristic otherwise treats complete URLs as credentials because
    they are long and contain several character classes. Credential-bearing
    query parameters are handled separately by ``_SENSITIVE_URL_QUERY``.
    """
    return any(
        url.start() <= match.start() and match.end() <= url.end()
        for url in _URL.finditer(text)
    )


def _is_safe_artifact_path(value: str) -> bool:
    normalized = value.replace("\\", "/").lstrip("/")
    return ".." not in normalized.split("/") and bool(
        _SAFE_ARTIFACT_PATH.fullmatch(normalized)
    )


def inspect_memory_text(text: str) -> SecurityResult:
    """Return redacted text and a persistence classification.

    Secret-bearing content is blocked entirely. Sensitive personal content is
    retained only as a candidate unless the user explicitly confirms it.
    """
    raw = (text or "").strip()
    findings: list[str] = []
    redacted = raw

    if _NAMED_SECRET.search(raw):
        findings.append("named_secret")
        redacted = _NAMED_SECRET.sub(r"\1=[REDACTED]", redacted)

    if _SENSITIVE_URL_QUERY.search(redacted):
        findings.append("url_query_secret")
        redacted = _SENSITIVE_URL_QUERY.sub(r"\1[REDACTED]", redacted)

    token_secrets = [
        m.group(0)
        for m in _TOKEN.finditer(raw)
        if _looks_like_secret_token(m.group(0))
        and not _inside_public_url(m, raw)
        and not _is_safe_artifact_path(m.group(0))
    ]
    if token_secrets:
        findings.append("high_entropy_secret")
        for token in token_secrets:
            redacted = redacted.replace(token, "[REDACTED]")

    if findings:
        return SecurityResult(
            safe_text=redacted,
            sensitivity=MemorySensitivity.SECRET.value,
            blocked=True,
            findings=findings,
        )
    if _PROMPT_INJECTION.search(raw):
        return SecurityResult(
            safe_text="[QUARANTINED PROMPT-LIKE CONTENT]",
            sensitivity=MemorySensitivity.PROMPT_INJECTION.value,
            blocked=False,
            findings=["prompt_injection"],
        )
    if _HEALTH.search(raw):
        return SecurityResult(raw, MemorySensitivity.HEALTH.value, False, ["health"])
    if _FINANCIAL.search(raw):
        return SecurityResult(raw, MemorySensitivity.FINANCIAL.value, False, ["financial"])
    if _PERSONAL.search(raw):
        return SecurityResult(raw, MemorySensitivity.PERSONAL.value, False, ["personal"])
    return SecurityResult(raw, MemorySensitivity.NORMAL.value, False, [])
