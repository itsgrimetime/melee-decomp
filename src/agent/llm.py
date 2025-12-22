"""
LLM client for decompilation agent.

This module provides an async interface to Claude API for generating
and refining decompiled C code.
"""

import asyncio
import os
from typing import Optional

from anthropic import AsyncAnthropic, APIError, RateLimitError, APITimeoutError
from anthropic.types import Message

from .prompts import SYSTEM_PROMPT


class LLMClient:
    """Async client for calling Claude API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ):
        """Initialize the LLM client.

        Args:
            api_key: Anthropic API key (or None to use ANTHROPIC_API_KEY env var)
            model: Claude model to use
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0.0 to 1.0)
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable not set. "
                "Please set it or pass api_key parameter."
            )

        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = AsyncAnthropic(api_key=self.api_key)

    async def generate_code(
        self,
        prompt: str,
        retry_count: int = 3,
        retry_delay: float = 1.0,
    ) -> Optional[str]:
        """Generate code using Claude API.

        Args:
            prompt: The prompt to send to Claude
            retry_count: Number of retries on failure
            retry_delay: Base delay between retries (exponential backoff)

        Returns:
            The generated response text, or None on failure

        Raises:
            ValueError: If API key is not set
            APIError: On unrecoverable API errors
        """
        for attempt in range(retry_count):
            try:
                message = await self._call_api(prompt)
                return self._extract_text(message)

            except RateLimitError as e:
                # Rate limit - use exponential backoff
                if attempt < retry_count - 1:
                    wait_time = retry_delay * (2**attempt)
                    print(
                        f"Rate limit hit, retrying in {wait_time:.1f}s "
                        f"(attempt {attempt + 1}/{retry_count})"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    print(f"Rate limit error after {retry_count} attempts: {e}")
                    raise

            except APITimeoutError as e:
                # Timeout - retry with backoff
                if attempt < retry_count - 1:
                    wait_time = retry_delay * (2**attempt)
                    print(
                        f"Request timeout, retrying in {wait_time:.1f}s "
                        f"(attempt {attempt + 1}/{retry_count})"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    print(f"Timeout error after {retry_count} attempts: {e}")
                    raise

            except APIError as e:
                # Other API errors - check if retryable
                if e.status_code and e.status_code >= 500:
                    # Server error - retry
                    if attempt < retry_count - 1:
                        wait_time = retry_delay * (2**attempt)
                        print(
                            f"Server error {e.status_code}, retrying in {wait_time:.1f}s "
                            f"(attempt {attempt + 1}/{retry_count})"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        print(f"Server error after {retry_count} attempts: {e}")
                        raise
                else:
                    # Client error - don't retry
                    print(f"API error (not retrying): {e}")
                    raise

            except Exception as e:
                # Unexpected error
                print(f"Unexpected error calling Claude API: {e}")
                raise

        return None

    async def _call_api(self, prompt: str) -> Message:
        """Make the actual API call.

        Args:
            prompt: The user prompt

        Returns:
            The API response message
        """
        message = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return message

    def _extract_text(self, message: Message) -> str:
        """Extract text content from API response.

        Args:
            message: The API response

        Returns:
            The text content
        """
        # Handle content blocks
        if message.content:
            # Concatenate all text blocks
            text_parts = []
            for block in message.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
            return "".join(text_parts)
        return ""

    async def close(self):
        """Close the HTTP client."""
        await self.client.close()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
