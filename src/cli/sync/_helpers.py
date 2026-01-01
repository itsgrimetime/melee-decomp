"""Shared helpers for sync commands."""

import asyncio
import json
import random
from pathlib import Path

from .._common import (
    console,
    PRODUCTION_COOKIES_FILE,
)

# Rate limiting configuration for production API
RATE_LIMIT_DELAY = 1.0  # Base delay between requests (seconds)
RATE_LIMIT_MAX_RETRIES = 5  # Max retries on 429
RATE_LIMIT_BACKOFF_FACTOR = 2.0  # Exponential backoff multiplier


async def rate_limited_request(client, method: str, url: str, max_retries: int = RATE_LIMIT_MAX_RETRIES, **kwargs):
    """Make a rate-limited request with 429 handling and exponential backoff.

    Args:
        client: httpx.AsyncClient instance
        method: HTTP method (get, post, etc.)
        url: URL to request
        max_retries: Maximum number of retries on 429
        **kwargs: Additional arguments to pass to the request

    Returns:
        httpx.Response object

    Raises:
        Exception if max retries exceeded
    """
    delay = RATE_LIMIT_DELAY

    for attempt in range(max_retries + 1):
        request_method = getattr(client, method.lower())
        response = await request_method(url, **kwargs)

        if response.status_code == 429:
            # Rate limited - check for Retry-After header
            retry_after = response.headers.get('Retry-After')
            if retry_after:
                try:
                    wait_time = float(retry_after)
                except ValueError:
                    wait_time = delay * RATE_LIMIT_BACKOFF_FACTOR
            else:
                wait_time = delay * RATE_LIMIT_BACKOFF_FACTOR

            if attempt < max_retries:
                console.print(f"[yellow]Rate limited (429). Waiting {wait_time:.1f}s before retry {attempt + 1}/{max_retries}...[/yellow]")
                await asyncio.sleep(wait_time)
                delay = wait_time * RATE_LIMIT_BACKOFF_FACTOR  # Increase delay for next attempt
                continue
            else:
                raise Exception(f"Rate limit exceeded after {max_retries} retries")

        # Add delay after successful request to be polite to the server
        jitter = random.uniform(0, delay * 0.1)
        await asyncio.sleep(delay + jitter)

        return response

    raise Exception("Unexpected: loop completed without returning")


def load_production_cookies() -> dict[str, str]:
    """Load production cookies from cache file."""
    if not PRODUCTION_COOKIES_FILE.exists():
        return {}
    try:
        with open(PRODUCTION_COOKIES_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_production_cookies(cookies: dict[str, str]) -> None:
    """Save production cookies to cache file."""
    PRODUCTION_COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PRODUCTION_COOKIES_FILE, 'w') as f:
        json.dump(cookies, f, indent=2)
