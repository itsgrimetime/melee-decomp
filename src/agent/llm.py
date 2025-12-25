"""
LLM client for decompilation agent.

This module provides an async interface to Claude via the `claude` CLI
for generating and refining decompiled C code.
"""

import asyncio
import shutil
from typing import Optional

from .prompts import SYSTEM_PROMPT


class LLMClient:
    """Async client for calling Claude via CLI."""

    def __init__(
        self,
        model: Optional[str] = None,
        max_tokens: int = 16000,
    ):
        """Initialize the LLM client.

        Args:
            model: Model to use (optional, uses CLI default if not set)
            max_tokens: Maximum tokens in response
        """
        self.model = model or "default"
        self.max_tokens = max_tokens

        # Verify claude CLI is available
        self.claude_path = shutil.which("claude")
        if not self.claude_path:
            raise ValueError(
                "claude CLI not found in PATH. "
                "Please install Claude Code: https://claude.ai/code"
            )

    async def generate_code(
        self,
        prompt: str,
        retry_count: int = 3,
        retry_delay: float = 1.0,
    ) -> Optional[str]:
        """Generate code using Claude CLI.

        Args:
            prompt: The prompt to send to Claude
            retry_count: Number of retries on failure
            retry_delay: Base delay between retries (exponential backoff)

        Returns:
            The generated response text, or None on failure
        """
        # Combine system prompt with user prompt
        full_prompt = f"{SYSTEM_PROMPT}\n\n---\n\n{prompt}"

        for attempt in range(retry_count):
            try:
                result = await self._call_cli(full_prompt)
                if result:
                    return result

            except Exception as e:
                if attempt < retry_count - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(
                        f"CLI error, retrying in {wait_time:.1f}s "
                        f"(attempt {attempt + 1}/{retry_count}): {e}"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    print(f"CLI error after {retry_count} attempts: {e}")
                    raise

        return None

    async def _call_cli(self, prompt: str, timeout: float = 600.0) -> str:
        """Make the actual CLI call.

        Args:
            prompt: The full prompt (system + user)
            timeout: Maximum time to wait for response (seconds)

        Returns:
            The CLI response text
        """
        # Build command - use stdin instead of -p flag to avoid shell escaping issues
        cmd = [
            self.claude_path,
            "--output-format", "text",
        ]

        # Add model if specified
        if self.model and self.model != "default":
            cmd.extend(["--model", self.model])

        print(f"DEBUG LLM: Running claude CLI with prompt len={len(prompt)}", flush=True)

        # Run the CLI command with prompt on stdin
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=prompt.encode()),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"claude CLI timed out after {timeout}s")

        print(f"DEBUG LLM: CLI returned, returncode={process.returncode}", flush=True)

        if process.returncode != 0:
            error_msg = stderr.decode().strip() if stderr else "Unknown error"
            raise RuntimeError(f"claude CLI failed: {error_msg}")

        result = stdout.decode().strip()
        print(f"DEBUG LLM: Response len={len(result)}", flush=True)
        return result

    async def close(self):
        """Close the client (no-op for CLI-based client)."""
        pass

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
