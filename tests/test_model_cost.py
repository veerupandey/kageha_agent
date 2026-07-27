"""Model cost estimation helpers."""

from __future__ import annotations

from kageha.models.registry import ModelConfig, estimate_model_usd


def test_estimate_model_usd_split_and_default():
    mc = ModelConfig(
        id="x",
        provider="gemini",
        model="m",
        usd_per_1k_input=0.001,
        usd_per_1k_output=0.002,
    )
    # 1000 in + 1000 out => 0.001 + 0.002
    assert abs(estimate_model_usd(mc, 1000, 1000) - 0.003) < 1e-9
    flat = ModelConfig(id="y", provider="p", model="m", usd_per_1k=0.01)
    assert abs(estimate_model_usd(flat, 1000, 1000) - 0.02) < 1e-9
    assert estimate_model_usd(None, 1000, 0, default_per_1k=0.001) == 0.001


def test_estimate_model_usd_credits_cache_hits(monkeypatch):
    monkeypatch.delenv("KAGEHA_CACHE_READ_MULTIPLIER", raising=False)
    mc = ModelConfig(
        id="x",
        provider="anthropic",
        model="m",
        usd_per_1k_input=0.001,
        usd_per_1k_output=0.002,
    )
    # 1000 uncached + 9000 cached @ 0.1× + 1000 out
    # => 1.0*0.001 + 9.0*0.0001 + 1.0*0.002 = 0.001 + 0.0009 + 0.002 = 0.0039
    got = estimate_model_usd(mc, 10_000, 1000, cached_tokens=9000)
    assert abs(got - 0.0039) < 1e-9

    # Explicit cached rate overrides multiplier.
    mc2 = ModelConfig(
        id="y",
        provider="p",
        model="m",
        usd_per_1k_input=0.001,
        usd_per_1k_output=0.002,
        usd_per_1k_cached_input=0.00005,
        usd_per_1k_cache_write=0.00125,
    )
    # 500 uncached + 500 cached @ 0.00005 + 100 write @ 0.00125 + 0 out
    # => 0.5*0.001 + 0.5*0.00005 + 0.1*0.00125 = 0.0005 + 0.000025 + 0.000125
    got2 = estimate_model_usd(
        mc2, 1000, 0, cached_tokens=500, cache_write_tokens=100
    )
    assert abs(got2 - 0.00065) < 1e-9

    # Additive Anthropic-style: prompt is uncached-only, cached billed on top.
    got3 = estimate_model_usd(mc, 1000, 0, cached_tokens=9000)
    # 1.0*0.001 + 9.0*0.0001 = 0.0019
    assert abs(got3 - 0.0019) < 1e-9

    monkeypatch.setenv("KAGEHA_CACHE_READ_MULTIPLIER", "0.5")
    got4 = estimate_model_usd(mc, 1000, 0, cached_tokens=1000)
    # 0 uncached + 1.0 * 0.001 * 0.5 = 0.0005
    assert abs(got4 - 0.0005) < 1e-9
