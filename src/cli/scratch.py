"""Scratch commands - manage decomp.me scratches.

This module handles all scratch operations: create, compile, update, get, search.
"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from ._common import console, DEFAULT_MELEE_ROOT, DECOMP_CONFIG_DIR, get_agent_context_file
from src.client.api import _get_agent_id

# Paths - use same agent ID logic as api.py for session isolation
_agent_id = _get_agent_id()
_agent_suffix = f"_{_agent_id}" if _agent_id else ""

DECOMP_SCRATCH_TOKENS_FILE = os.environ.get(
    "DECOMP_SCRATCH_TOKENS_FILE",
    str(DECOMP_CONFIG_DIR / f"scratch_tokens{_agent_suffix}.json")
)

# API URL from environment
_api_base = os.environ.get("DECOMP_API_BASE", "")
DEFAULT_DECOMP_ME_URL = _api_base[:-4] if _api_base.endswith("/api") else _api_base

# Context file override from environment
_context_env = os.environ.get("DECOMP_CONTEXT_FILE", "")


def _get_context_file() -> Path:
    """Get context file path, using agent's worktree if available."""
    if _context_env:
        return Path(_context_env)
    return get_agent_context_file()

scratch_app = typer.Typer(help="Manage decomp.me scratches")


def _load_scratch_tokens() -> dict[str, str]:
    """Load scratch claim tokens from file."""
    tokens_path = Path(DECOMP_SCRATCH_TOKENS_FILE)
    if not tokens_path.exists():
        return {}
    try:
        with open(tokens_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_scratch_token(slug: str, token: str) -> None:
    """Save a scratch claim token."""
    tokens = _load_scratch_tokens()
    tokens[slug] = token
    tokens_path = Path(DECOMP_SCRATCH_TOKENS_FILE)
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tokens_path, 'w') as f:
        json.dump(tokens, f, indent=2)


def _require_api_url(api_url: str) -> None:
    """Validate that API URL is configured."""
    if not api_url:
        console.print("[red]Error: DECOMP_API_BASE environment variable is required[/red]")
        console.print("[dim]Set it to your decomp.me instance URL, e.g.:[/dim]")
        console.print("[dim]  export DECOMP_API_BASE=http://10.200.0.1[/dim]")
        raise typer.Exit(1)


@scratch_app.command("create")
def scratch_create(
    function_name: Annotated[str, typer.Argument(help="Name of the function")],
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
    context_file: Annotated[
        Optional[Path], typer.Option("--context", "-c", help="Path to context file")
    ] = None,
):
    """Create a new scratch for a function on decomp.me."""
    _require_api_url(api_url)
    from src.client import DecompMeAPIClient
    from src.extractor import extract_function

    ctx_path = context_file or _get_context_file()
    if not ctx_path.exists():
        console.print(f"[red]Context file not found: {ctx_path}[/red]")
        console.print("[dim]Run 'ninja' in melee/ to generate build/ctx.c[/dim]")
        raise typer.Exit(1)

    melee_context = ctx_path.read_text()
    console.print(f"[dim]Loaded {len(melee_context):,} bytes of context[/dim]")

    async def create():
        func = await extract_function(melee_root, function_name)
        if func is None:
            console.print(f"[red]Function '{function_name}' not found[/red]")
            raise typer.Exit(1)

        async with DecompMeAPIClient(base_url=api_url) as client:
            from src.client import ScratchCreate
            scratch = await client.create_scratch(
                ScratchCreate(
                    name=func.name,
                    target_asm=func.asm,
                    context=melee_context,
                    compiler="mwcc_233_163n",
                    compiler_flags="-O4,p -nodefaults -fp hard -Cpp_exceptions off -enum int -fp_contract on -inline auto",
                    source_code="// TODO: Decompile this function\n",
                    diff_label=func.name,
                )
            )

            if scratch.claim_token:
                _save_scratch_token(scratch.slug, scratch.claim_token)
                try:
                    await client.claim_scratch(scratch.slug, scratch.claim_token)
                    console.print(f"[dim]Claimed ownership of scratch[/dim]")
                except Exception as e:
                    console.print(f"[yellow]Warning: Could not claim scratch: {e}[/yellow]")

        return scratch

    scratch = asyncio.run(create())
    console.print(f"[green]Created scratch:[/green] {api_url}/scratch/{scratch.slug}")


def _extract_text(text_data) -> str:
    """Extract plain text from diff text data (list of dicts or string)."""
    if isinstance(text_data, str):
        return text_data
    if isinstance(text_data, list):
        return "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in text_data)
    return str(text_data) if text_data else ""


def _format_diff_output(diff_output, max_lines: int = 0) -> None:
    """Format and print the instruction diff."""
    if not diff_output.rows:
        console.print("[dim]No diff rows available[/dim]")
        return

    console.print(f"\n[bold]Instruction Diff:[/bold] (target | current)\n")

    diff_count = 0
    shown = 0

    for row in diff_output.rows:
        base_text = ""
        curr_text = ""

        if row.base and "text" in row.base:
            base_text = _extract_text(row.base["text"])
        if row.current and "text" in row.current:
            curr_text = _extract_text(row.current["text"])

        # Normalize whitespace for comparison
        base_norm = " ".join(base_text.split())
        curr_norm = " ".join(curr_text.split())

        is_diff = base_norm != curr_norm
        if is_diff:
            diff_count += 1

        # Format output
        base_display = base_text.strip()[:40].ljust(42)
        curr_display = curr_text.strip()[:40] if curr_text.strip() else "(missing)"

        if is_diff:
            console.print(f"[red]{base_display}[/red] | [yellow]{curr_display}[/yellow]")
        else:
            console.print(f"[dim]{base_display} | {curr_display}[/dim]")

        shown += 1
        if max_lines and shown >= max_lines:
            remaining = len(diff_output.rows) - shown
            if remaining > 0:
                console.print(f"[dim]... {remaining} more rows[/dim]")
            break

    console.print(f"\n[bold]Total differences:[/bold] {diff_count}")


@scratch_app.command("compile")
def scratch_compile(
    slug: Annotated[str, typer.Argument(help="Scratch slug/ID")],
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
    show_diff: Annotated[
        bool, typer.Option("--diff", "-d", help="Show instruction diff")
    ] = False,
    max_lines: Annotated[
        int, typer.Option("--max-lines", "-n", help="Max diff lines to show (0=all)")
    ] = 100,
):
    """Compile a scratch and show the diff."""
    _require_api_url(api_url)
    from src.client import DecompMeAPIClient

    async def compile_scratch():
        async with DecompMeAPIClient(base_url=api_url) as client:
            return await client.compile_scratch(slug)

    result = asyncio.run(compile_scratch())

    if result.success:
        match_pct = (
            100.0 if result.diff_output.current_score == 0
            else (1.0 - result.diff_output.current_score / result.diff_output.max_score) * 100
        )
        console.print(f"[green]Compiled successfully![/green]")
        console.print(f"Match: {match_pct:.1f}%")
        console.print(f"Score: {result.diff_output.current_score}/{result.diff_output.max_score}")

        if show_diff and result.diff_output:
            _format_diff_output(result.diff_output, max_lines)
    else:
        console.print(f"[red]Compilation failed[/red]")
        console.print(result.compiler_output)


@scratch_app.command("update")
def scratch_update(
    slug: Annotated[str, typer.Argument(help="Scratch slug/ID")],
    source_file: Annotated[Path, typer.Argument(help="Path to C source file")],
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
):
    """Update a scratch's source code from a file."""
    _require_api_url(api_url)
    from src.client import DecompMeAPIClient, ScratchUpdate, DecompMeAPIError

    source_code = source_file.read_text()

    async def update():
        async with DecompMeAPIClient(base_url=api_url) as client:
            try:
                scratch = await client.update_scratch(slug, ScratchUpdate(source_code=source_code))
            except DecompMeAPIError as e:
                if "403" in str(e):
                    tokens = _load_scratch_tokens()
                    if slug in tokens:
                        console.print("[dim]Session mismatch, re-claiming...[/dim]")
                        try:
                            await client.claim_scratch(slug, tokens[slug])
                            scratch = await client.update_scratch(slug, ScratchUpdate(source_code=source_code))
                        except Exception:
                            raise e
                    else:
                        console.print("[red]No saved token - cannot update[/red]")
                        raise typer.Exit(1)
                else:
                    raise
            result = await client.compile_scratch(slug)
            return scratch, result

    scratch, result = asyncio.run(update())

    if result.success and result.diff_output:
        match_pct = (
            100.0 if result.diff_output.current_score == 0
            else (1.0 - result.diff_output.current_score / result.diff_output.max_score) * 100
        )
        console.print(f"[green]Updated![/green] Match: {match_pct:.1f}%")
    else:
        console.print(f"[yellow]Updated but compilation failed[/yellow]")


@scratch_app.command("get")
def scratch_get(
    slug: Annotated[str, typer.Argument(help="Scratch slug/ID or URL")],
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Get full scratch information."""
    _require_api_url(api_url)
    from src.client import DecompMeAPIClient

    # Extract slug from URL if needed
    if slug.startswith("http"):
        parts = slug.strip("/").split("/")
        if "scratch" in parts:
            idx = parts.index("scratch")
            if idx + 1 < len(parts):
                slug = parts[idx + 1]

    async def get():
        async with DecompMeAPIClient(base_url=api_url) as client:
            return await client.get_scratch(slug)

    scratch = asyncio.run(get())

    if output_json:
        data = {
            "slug": scratch.slug,
            "name": scratch.name,
            "platform": scratch.platform,
            "compiler": scratch.compiler,
            "score": scratch.score,
            "max_score": scratch.max_score,
            "match_percent": ((scratch.max_score - scratch.score) / scratch.max_score * 100) if scratch.max_score > 0 else 0,
            "source_code": scratch.source_code,
        }
        print(json.dumps(data, indent=2))
    else:
        match_pct = ((scratch.max_score - scratch.score) / scratch.max_score * 100) if scratch.max_score > 0 else 0
        console.print(f"[bold cyan]{scratch.name}[/bold cyan] ({scratch.slug})")
        console.print(f"Match: {match_pct:.1f}%")
        console.print(f"\n[bold]Source Code:[/bold]")
        console.print(scratch.source_code[:2000] if len(scratch.source_code) > 2000 else scratch.source_code)


@scratch_app.command("search")
def scratch_search(
    query: Annotated[Optional[str], typer.Argument(help="Search query")] = None,
    platform: Annotated[
        Optional[str], typer.Option("--platform", "-p", help="Filter by platform")
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum results")
    ] = 10,
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Search for scratches on decomp.me."""
    _require_api_url(api_url)
    from src.client import DecompMeAPIClient

    async def search():
        async with DecompMeAPIClient(base_url=api_url) as client:
            return await client.list_scratches(platform=platform, search=query, page_size=limit)

    scratches = asyncio.run(search())

    if output_json:
        data = [{"slug": s.slug, "name": s.name, "platform": s.platform} for s in scratches[:limit]]
        print(json.dumps(data, indent=2))
    else:
        table = Table(title="Scratches")
        table.add_column("Slug", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Platform")

        for s in scratches[:limit]:
            table.add_row(s.slug, s.name, s.platform)

        console.print(table)


@scratch_app.command("search-context")
def scratch_search_context(
    slug: Annotated[str, typer.Argument(help="Scratch slug/ID")],
    pattern: Annotated[str, typer.Argument(help="Regex pattern to search for")],
    context_lines: Annotated[int, typer.Option("--context", "-C", help="Context lines")] = 3,
    max_results: Annotated[int, typer.Option("--max", "-n", help="Maximum matches")] = 20,
    api_url: Annotated[str, typer.Option("--api-url", help="Decomp.me API URL")] = DEFAULT_DECOMP_ME_URL,
):
    """Search through a scratch's context for patterns."""
    _require_api_url(api_url)
    from src.client import DecompMeAPIClient

    async def get():
        async with DecompMeAPIClient(base_url=api_url) as client:
            return await client.get_scratch(slug)

    scratch = asyncio.run(get())

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        console.print(f"[red]Invalid regex: {e}[/red]")
        raise typer.Exit(1)

    lines = scratch.context.splitlines()
    matches = []

    for i, line in enumerate(lines):
        if regex.search(line):
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            matches.append({"line_num": i + 1, "context": lines[start:end], "start": start + 1})
            if len(matches) >= max_results:
                break

    if not matches:
        console.print(f"[yellow]No matches found for: {pattern}[/yellow]")
        return

    console.print(f"[bold]Found {len(matches)} matches[/bold]\n")
    for idx, match in enumerate(matches[:10], 1):
        console.print(f"[cyan]Match {idx}[/cyan] (line {match['line_num']})")
        for j, line in enumerate(match["context"]):
            ln = match["start"] + j
            marker = ">>> " if ln == match["line_num"] else "    "
            console.print(f"{marker}{ln:5d}: {line}")
        console.print()
