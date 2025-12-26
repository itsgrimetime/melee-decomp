"""Async HTTP client for the decomp.me REST API."""

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import TypeAdapter

# Persistent config directory
DECOMP_CONFIG_DIR = Path.home() / ".config" / "decomp-me"

# Persistent cookies file for session management
# Supports per-agent isolation via DECOMP_AGENT_ID env var
# Auto-generates ID from Claude Code's PID if not set
def _get_agent_id() -> str:
    """Get or generate a unique agent ID for session isolation.

    Walks up the process tree to find Claude Code's PID, which is stable
    for the entire conversation. Different Claude windows have different
    PIDs = automatic isolation.
    """
    # Explicit ID takes priority (for manual parallel agent coordination)
    if os.environ.get("DECOMP_AGENT_ID"):
        return os.environ["DECOMP_AGENT_ID"]

    # Walk up process tree to find 'claude' process
    import subprocess
    pid = os.getpid()
    for _ in range(10):  # Max 10 levels up
        try:
            result = subprocess.run(
                ['ps', '-p', str(pid), '-o', 'ppid=,comm='],
                capture_output=True, text=True, timeout=1
            )
            if result.returncode != 0:
                break
            parts = result.stdout.strip().split(None, 1)
            if len(parts) < 2:
                break
            ppid, comm = int(parts[0]), parts[1]
            if 'claude' in comm.lower():
                return f"claude{pid}"  # Use the claude process PID
            pid = ppid
        except Exception:
            break

    # Fallback to immediate parent PID
    return f"ppid{os.getppid()}"

_agent_id = _get_agent_id()
_cookies_suffix = f"_{_agent_id}" if _agent_id else ""
DECOMP_COOKIES_FILE = os.environ.get(
    "DECOMP_COOKIES_FILE",
    str(DECOMP_CONFIG_DIR / f"cookies{_cookies_suffix}.json")
)

from .models import (
    CompilationResult,
    CompileRequest,
    CompilerInfo,
    DecompilationResult,
    ForkRequest,
    PresetInfo,
    Scratch,
    ScratchCreate,
    ScratchUpdate,
    TerseScratch,
)

logger = logging.getLogger(__name__)


class DecompMeAPIError(Exception):
    """Base exception for decomp.me API errors."""

    pass


def _load_cookies() -> dict[str, str]:
    """Load persistent cookies from file."""
    cookies_path = Path(DECOMP_COOKIES_FILE)
    if not cookies_path.exists():
        return {}
    try:
        with open(cookies_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_cookies(cookies: dict[str, str]) -> None:
    """Save cookies to file."""
    cookies_path = Path(DECOMP_COOKIES_FILE)
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cookies_path, 'w') as f:
        json.dump(cookies, f, indent=2)


class DecompMeAPIClient:
    """Async HTTP client for decomp.me REST API.

    This client wraps the decomp.me backend API endpoints with retry logic
    and proper error handling. Session cookies are persisted to disk for
    ownership across CLI invocations.

    Args:
        base_url: Base URL for the API (default: http://localhost:8000)
        timeout: Request timeout in seconds (default: 30)
        max_retries: Maximum number of retries for transient failures (default: 3)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

        # Configure retry transport with cookie persistence
        transport = httpx.AsyncHTTPTransport(retries=max_retries)

        # Headers matching Firefox browser for Cloudflare bypass
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) Gecko/20100101 Firefox/146.0",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.5",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        # Load persistent cookies from file
        persistent_cookies = _load_cookies()

        # Build cookies - prefer persistent, fallback to env vars
        cookies = httpx.Cookies()
        cf_clearance = persistent_cookies.get("cf_clearance") or os.environ.get("CF_CLEARANCE", "")
        session_id = persistent_cookies.get("sessionid") or os.environ.get("DECOMP_SESSION_ID", "")

        # Determine domain from base_url (for local vs production)
        from urllib.parse import urlparse
        domain = urlparse(self.base_url).hostname or "decomp.me"

        if cf_clearance:
            cookies.set("cf_clearance", cf_clearance, domain=domain)
        if session_id:
            cookies.set("sessionid", session_id, domain=domain)

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
            headers=headers,
            cookies=cookies,
        )

    def _update_cookies_from_response(self, response: httpx.Response) -> None:
        """Extract and persist session cookies from response."""
        cookies = _load_cookies()
        for cookie in response.cookies.jar:
            if cookie.name in ("sessionid", "csrftoken", "cf_clearance"):
                cookies[cookie.name] = cookie.value
        _save_cookies(cookies)

    async def __aenter__(self) -> "DecompMeAPIClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """Handle HTTP response and raise errors if needed.

        Args:
            response: HTTP response from the API

        Returns:
            Parsed JSON response data

        Raises:
            DecompMeAPIError: If the API returns an error status
        """
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_msg = f"API request failed: {e.response.status_code}"
            try:
                error_data = e.response.json()
                error_msg += f" - {error_data}"
            except Exception:
                error_msg += f" - {e.response.text}"
            logger.error(error_msg)
            raise DecompMeAPIError(error_msg) from e

        return response.json()

    # Scratch CRUD Operations

    async def create_scratch(self, scratch: ScratchCreate) -> Scratch:
        """Create a new scratch.

        Args:
            scratch: Scratch creation parameters

        Returns:
            Created scratch with claim_token

        Raises:
            DecompMeAPIError: If creation fails
        """
        logger.info(f"Creating scratch: {scratch.name or 'Untitled'}")
        response = await self._client.post(
            "/api/scratch",
            json=scratch.model_dump(exclude_none=True, mode="json"),
        )
        self._update_cookies_from_response(response)
        data = self._handle_response(response)
        return Scratch.model_validate(data)

    async def get_scratch(self, slug: str) -> Scratch:
        """Get scratch details by slug.

        Args:
            slug: Scratch slug/ID

        Returns:
            Scratch details

        Raises:
            DecompMeAPIError: If scratch not found
        """
        logger.debug(f"Fetching scratch: {slug}")
        response = await self._client.get(f"/api/scratch/{slug}")
        data = self._handle_response(response)
        return Scratch.model_validate(data)

    async def claim_scratch(self, slug: str, claim_token: str) -> bool:
        """Claim ownership of a scratch.

        Args:
            slug: Scratch slug/ID
            claim_token: Token returned when scratch was created

        Returns:
            True if claim succeeded

        Raises:
            DecompMeAPIError: If claim fails
        """
        logger.info(f"Claiming scratch: {slug}")
        response = await self._client.post(
            f"/api/scratch/{slug}/claim",
            json={"token": claim_token},
        )
        self._update_cookies_from_response(response)
        data = self._handle_response(response)
        return data.get("success", False)

    async def update_scratch(self, slug: str, updates: ScratchUpdate) -> Scratch:
        """Update an existing scratch.

        The scratch must be owned by the current session profile (via claim).
        Session cookies are persisted to disk for ownership across CLI invocations.

        Args:
            slug: Scratch slug/ID
            updates: Fields to update

        Returns:
            Updated scratch

        Raises:
            DecompMeAPIError: If update fails (e.g., permission denied)
        """
        logger.info(f"Updating scratch: {slug}")
        response = await self._client.patch(
            f"/api/scratch/{slug}",
            json=updates.model_dump(exclude_none=True, mode="json"),
        )
        self._update_cookies_from_response(response)
        data = self._handle_response(response)
        return Scratch.model_validate(data)

    async def delete_scratch(self, slug: str) -> None:
        """Delete a scratch.

        Args:
            slug: Scratch slug/ID

        Raises:
            DecompMeAPIError: If deletion fails (e.g., permission denied)
        """
        logger.info(f"Deleting scratch: {slug}")
        response = await self._client.delete(f"/api/scratch/{slug}")
        self._handle_response(response)

    async def list_scratches(
        self,
        platform: str | None = None,
        compiler: str | None = None,
        preset: str | None = None,
        search: str | None = None,
        ordering: str | None = None,
        page_size: int = 10,
    ) -> list[TerseScratch]:
        """List scratches with optional filters.

        Args:
            platform: Filter by platform ID
            compiler: Filter by compiler ID
            preset: Filter by preset ID
            search: Search in name and diff_label
            ordering: Sort field (creation_time, last_updated, score, match_percent)
            page_size: Number of results per page (max 100)

        Returns:
            List of scratches (terse format)

        Raises:
            DecompMeAPIError: If request fails
        """
        params: dict[str, Any] = {"page_size": min(page_size, 100)}
        if platform:
            params["platform"] = platform
        if compiler:
            params["compiler"] = compiler
        if preset:
            params["preset"] = preset
        if search:
            params["search"] = search
        if ordering:
            params["ordering"] = ordering

        logger.debug(f"Listing scratches with filters: {params}")
        response = await self._client.get("/api/scratch", params=params)
        data = self._handle_response(response)

        # Handle paginated response
        results = data.get("results", data)
        adapter = TypeAdapter(list[TerseScratch])
        return adapter.validate_python(results)

    # Compilation

    async def compile_scratch(
        self,
        slug: str,
        overrides: CompileRequest | None = None,
        save_score: bool = False,
    ) -> CompilationResult:
        """Compile a scratch and get diff output.

        Args:
            slug: Scratch slug/ID
            overrides: Optional compilation overrides (source_code, compiler_flags, etc.)
            save_score: If True, use GET to save score to scratch (default: False)

        Returns:
            Compilation result with diff output

        Raises:
            DecompMeAPIError: If compilation request fails
        """
        logger.info(f"Compiling scratch: {slug}")

        if save_score or overrides is None:
            # GET request - updates scratch score
            response = await self._client.get(f"/api/scratch/{slug}/compile")
        else:
            # POST request - does not update scratch score
            response = await self._client.post(
                f"/api/scratch/{slug}/compile",
                json=overrides.model_dump(exclude_none=True, mode="json"),
            )

        data = self._handle_response(response)
        return CompilationResult.model_validate(data)

    # Decompilation

    async def decompile_scratch(
        self,
        slug: str,
        context: str | None = None,
        compiler: str | None = None,
    ) -> DecompilationResult:
        """Auto-decompile scratch using m2c decompiler.

        Args:
            slug: Scratch slug/ID
            context: Optional context code override
            compiler: Optional compiler override

        Returns:
            Decompilation result

        Raises:
            DecompMeAPIError: If decompilation fails
        """
        logger.info(f"Decompiling scratch: {slug}")
        payload: dict[str, Any] = {}
        if context is not None:
            payload["context"] = context
        if compiler is not None:
            payload["compiler"] = compiler

        response = await self._client.post(
            f"/api/scratch/{slug}/decompile",
            json=payload,
        )
        data = self._handle_response(response)
        return DecompilationResult.model_validate(data)

    # Scratch Management

    async def fork_scratch(self, slug: str, fork_params: ForkRequest | None = None) -> Scratch:
        """Fork a scratch.

        Args:
            slug: Scratch slug/ID to fork
            fork_params: Optional parameters for the fork (name, source_code, etc.)

        Returns:
            Forked scratch

        Raises:
            DecompMeAPIError: If fork fails
        """
        logger.info(f"Forking scratch: {slug}")
        payload = fork_params.model_dump(exclude_none=True, mode="json") if fork_params else {}
        response = await self._client.post(f"/api/scratch/{slug}/fork", json=payload)
        data = self._handle_response(response)
        return Scratch.model_validate(data)

    async def get_scratch_family(self, slug: str) -> list[TerseScratch]:
        """Get all related scratches (same target assembly or family).

        Args:
            slug: Scratch slug/ID

        Returns:
            List of related scratches

        Raises:
            DecompMeAPIError: If request fails
        """
        logger.debug(f"Fetching scratch family: {slug}")
        response = await self._client.get(f"/api/scratch/{slug}/family")
        data = self._handle_response(response)
        adapter = TypeAdapter(list[TerseScratch])
        return adapter.validate_python(data)

    # Utilities

    async def list_compilers(self) -> list[CompilerInfo]:
        """List all available compilers.

        Returns:
            List of compiler information

        Raises:
            DecompMeAPIError: If request fails
        """
        logger.debug("Fetching compiler list")
        response = await self._client.get("/api/compiler")
        data = self._handle_response(response)
        adapter = TypeAdapter(list[CompilerInfo])
        return adapter.validate_python(data)

    async def list_presets(self) -> list[PresetInfo]:
        """List all available presets.

        Returns:
            List of preset information

        Raises:
            DecompMeAPIError: If request fails
        """
        logger.debug("Fetching preset list")
        response = await self._client.get("/api/preset")
        data = self._handle_response(response)
        adapter = TypeAdapter(list[PresetInfo])
        return adapter.validate_python(data)

    async def export_scratch(self, slug: str, target_only: bool = False) -> bytes:
        """Export a scratch as a ZIP file.

        Args:
            slug: Scratch slug/ID
            target_only: If True, exclude current.o from export

        Returns:
            ZIP file bytes

        Raises:
            DecompMeAPIError: If export fails
        """
        logger.info(f"Exporting scratch: {slug}")
        params = {"target_only": "1"} if target_only else {}
        response = await self._client.get(f"/api/scratch/{slug}/export", params=params)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_msg = f"Export failed: {e.response.status_code}"
            logger.error(error_msg)
            raise DecompMeAPIError(error_msg) from e

        return response.content
