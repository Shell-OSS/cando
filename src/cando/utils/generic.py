# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Generic utility functions for the CANDO package."""

import json
from typing import Any


def json_dumps(obj: Any) -> str:
    """Serializes obj to compact JSON."""
    return json.dumps(obj, separators=(",", ":"))


def round_floats(obj: Any, ndigits: int = 4) -> Any:
    """Recursively rounds floats in a JSON-like dict/list structure.

    Returns a new structure without mutating obj -- trims floating-point
    noise (e.g. 52.39999999999999, inherited from upstream geometry ops)
    from LLM-facing payloads before serialization, independent of any
    rounding already applied to the original data elsewhere.
    """
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [round_floats(v, ndigits) for v in obj]
    return obj


_ERROR_HINTS: dict[str, str] = {
    "AuthenticationError": (
        "Looks like a missing or invalid credential -- see "
        ".env.example for the environment variables your provider "
        "needs."
    ),
    "PermissionDeniedError": (
        "Looks like a permissions/credential issue -- see "
        ".env.example for the environment variables your provider "
        "needs."
    ),
    "NotFoundError": (
        "Looks like an invalid model/deployment name -- check "
        "CANDO_MODEL_ID against your provider's available models."
    ),
    "RateLimitError": (
        "Looks like a provider rate limit -- consider lowering "
        "--n-branches/--n-candidates or retrying later."
    ),
    "APIConnectionError": (
        "Looks like a network/connectivity issue reaching the model "
        "provider -- check OPENAI_API_BASE/AZURE_OPENAI_ENDPOINT and "
        "your network connection."
    ),
    "APITimeoutError": (
        "Looks like a request timeout from the model provider -- "
        "consider retrying or checking provider status."
    ),
}


def describe_exception_hint(exc: BaseException) -> str | None:
    """Returns an actionable hint for common provider/network failures.

    Matches on the exception class name rather than isinstance checks so
    this works across OpenAI/Azure/Google SDK exception hierarchies
    without importing any of them directly.

    Args:
        exc (BaseException): The exception to classify.

    Returns:
        str | None: A one-line actionable hint, or None if the exception
            doesn't match any known provider/network failure pattern.
    """
    name = type(exc).__name__
    for pattern, hint in _ERROR_HINTS.items():
        if pattern in name:
            return hint
    return None
