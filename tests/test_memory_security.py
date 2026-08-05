from kageha.memory.security import inspect_memory_text
from kageha.obs.events import redact


def test_session_artifact_path_is_not_treated_as_secret():
    path = "artifacts/computer/thumbs/action_0001.jpg"
    result = inspect_memory_text(path)
    assert result.blocked is False
    assert result.safe_text == path


def test_public_reference_url_is_not_treated_as_high_entropy_secret():
    text = (
        "Inspect this reference: "
        "https://www.instagram.com/reels/DbX-hGFyjD0/ and create a shot map."
    )

    result = inspect_memory_text(text)

    assert result.blocked is False
    assert result.safe_text == text
    assert redact(text) == text


def test_secret_query_parameter_is_redacted_without_losing_public_url():
    secret = "Aa1+/syntheticCredential987654321XYZ"
    text = f"Fetch https://example.com/reference.mp4?token={secret}&quality=high"

    result = inspect_memory_text(text)

    assert result.blocked is True
    assert secret not in result.safe_text
    assert "https://example.com/reference.mp4?token=[REDACTED]&quality=high" in result.safe_text
    assert "url_query_secret" in result.findings


def test_high_entropy_bare_credential_is_still_redacted():
    secret = "Aa1+/syntheticCredential987654321XYZ"

    result = inspect_memory_text(f"use credential {secret}")

    assert result.blocked is True
    assert result.safe_text == "use credential [REDACTED]"
    assert "high_entropy_secret" in result.findings
