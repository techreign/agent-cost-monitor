# Pricing table — rates as of 2026-05
# All values in USD per 1M tokens unless noted.

PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "anthropic/claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "anthropic/claude-opus-4-7": {"input": 15.00, "output": 75.00},
    "anthropic/claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    # OpenAI
    "openai/gpt-5": {"input": 10.00, "output": 30.00},
    "openai/gpt-5-mini": {"input": 0.40, "output": 1.60},
    "openai/o3": {"input": 10.00, "output": 40.00},
    "openai/o3-mini": {"input": 1.10, "output": 4.40},
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    # Google Gemini
    "gemini/gemini-2.5-pro": {"input": 3.50, "output": 10.50},
    "gemini/gemini-2.5-flash": {"input": 0.075, "output": 0.30},
    "gemini/gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    # Groq (Llama variants)
    "groq/llama-3.3-70b": {"input": 0.59, "output": 0.79},
    "groq/llama-3.1-8b": {"input": 0.05, "output": 0.08},
    "groq/mixtral-8x7b": {"input": 0.24, "output": 0.24},
}

_M = 1_000_000


def lookup_cost(
    api: str, model: str, input_tokens: int, output_tokens: int
) -> float | None:
    """Return estimated cost in USD or None if model is unknown."""
    key = f"{api.lower()}/{model.lower()}"
    rates = PRICING.get(key)
    if rates is None:
        # Fuzzy: try matching just model name
        for k, v in PRICING.items():
            if k.endswith(f"/{model.lower()}"):
                rates = v
                break
    if rates is None:
        return None
    return (input_tokens / _M) * rates["input"] + (output_tokens / _M) * rates["output"]
