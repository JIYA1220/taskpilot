"""Confirm the configured Groq models exist: python -m scripts.check_models

Reads GROQ_API_KEY from .env at runtime. Prints model ids only, never the key.
"""

import sys

import httpx

from backend.config import get_settings


def main() -> int:
    settings = get_settings()
    key = settings.groq_api_key.get_secret_value()
    if not key:
        print("GROQ_API_KEY is not set in .env")
        return 1
    try:
        response = httpx.get(
            f"{settings.groq_base_url}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "no response")
        print(f"Groq request failed: {type(exc).__name__} (status: {status})")
        return 1
    available = {m["id"] for m in response.json()["data"]}
    missing = 0
    for label, name in (("main", settings.groq_main_model), ("fast", settings.groq_fast_model)):
        found = name in available
        missing += not found
        print(f"{label}: {name} -> {'available' if found else 'NOT FOUND'}")
    if missing:
        print("Available models:", ", ".join(sorted(available)))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
