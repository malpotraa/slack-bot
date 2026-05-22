"""Per-turn cost estimation from token usage."""

from app.observability import estimate_cost_usd


def test_sonnet_input_output_rates():
    # 1M input + 1M output at Sonnet rates → $3 + $15.
    cost = estimate_cost_usd(
        "claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=1_000_000
    )
    assert cost == 18.0


def test_cache_read_and_write_priced_separately():
    cost = estimate_cost_usd(
        "claude-sonnet-4-6",
        cache_read_tokens=1_000_000,  # $0.30
        cache_write_tokens=1_000_000,  # $6.00 (1-hour cache)
    )
    assert cost == 6.30


def test_haiku_rates():
    cost = estimate_cost_usd(
        "claude-haiku-4-5-20251001",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost == 6.0  # $1 + $5


def test_unknown_model_is_zero():
    assert estimate_cost_usd("gpt-4", input_tokens=1_000_000) == 0.0


def test_realistic_turn_is_small():
    cost = estimate_cost_usd(
        "claude-sonnet-4-6",
        input_tokens=3000,
        output_tokens=500,
        cache_read_tokens=5000,
    )
    assert 0 < cost < 0.05
