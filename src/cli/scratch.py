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

from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
    DECOMP_CONFIG_DIR,
    get_context_file,
    detect_local_api_url,
    get_local_api_url,
    record_match_score,
    format_match_history,
    db_upsert_scratch,
    db_record_match_score,
    db_upsert_function,
    get_compiler_for_source,
    AGENT_ID,
)
from .complete import _get_current_branch
from .utils import file_lock, load_json_safe

# Shared scratch tokens file - all agents use the same file
# Tokens are keyed by scratch slug, so no conflicts between agents
DECOMP_SCRATCH_TOKENS_FILE = os.environ.get(
    "DECOMP_SCRATCH_TOKENS_FILE",
    str(DECOMP_CONFIG_DIR / "scratch_tokens.json")
)

# Lock file for token operations
_TOKENS_LOCK_FILE = DECOMP_CONFIG_DIR / "scratch_tokens.lock"

# Context file override from environment
_context_env = os.environ.get("DECOMP_CONTEXT_FILE", "")


def _get_context_file(source_file: str | None = None) -> Path:
    """Get context file path.

    Args:
        source_file: Optional source file path to find per-file .ctx context.
    """
    if _context_env:
        return Path(_context_env)
    return get_context_file(source_file=source_file)

scratch_app = typer.Typer(help="Manage decomp.me scratches")


def _load_scratch_tokens() -> dict[str, str]:
    """Load scratch claim tokens from file."""
    tokens_path = Path(DECOMP_SCRATCH_TOKENS_FILE)
    return load_json_safe(tokens_path)


def _save_scratch_token(slug: str, token: str) -> None:
    """Save a scratch claim token with locking.

    Uses exclusive lock to prevent race conditions when multiple
    agents create scratches simultaneously.
    """
    tokens_path = Path(DECOMP_SCRATCH_TOKENS_FILE)
    tokens_path.parent.mkdir(parents=True, exist_ok=True)

    with file_lock(_TOKENS_LOCK_FILE, exclusive=True):
        tokens = load_json_safe(tokens_path)
        tokens[slug] = token
        with open(tokens_path, "w") as f:
            json.dump(tokens, f, indent=2)


async def _handle_403_error(client, slug: str, error: Exception, operation: str = "update") -> bool:
    """Handle 403 Forbidden errors with helpful messaging and recovery attempts.

    Returns True if recovery succeeded, False if it failed.
    """
    from src.client import DecompMeAPIError

    tokens = _load_scratch_tokens()

    # Try to get scratch info to understand the ownership situation
    try:
        scratch = await client.get_scratch(slug)
        if scratch.owner:
            owner_info = f"owned by '{scratch.owner.username}'"
        else:
            owner_info = "no owner info"
    except Exception:
        owner_info = "unable to fetch owner info"

    console.print(f"\n[red]403 Forbidden:[/red] Cannot {operation} scratch '{slug}' ({owner_info})")

    if slug in tokens:
        console.print("[dim]Found saved token, attempting to re-claim...[/dim]")
        try:
            success = await client.claim_scratch(slug, tokens[slug])
            if success:
                console.print("[green]Re-claimed successfully![/green]")
                return True
            else:
                console.print("[red]Re-claim returned false - token may be invalid[/red]")
        except DecompMeAPIError as claim_error:
            console.print(f"[red]Re-claim failed:[/red] {claim_error}")

    # Provide actionable suggestions
    console.print("\n[yellow]Possible causes and solutions:[/yellow]")
    console.print("  1. [bold]Session mismatch:[/bold] Another process created this scratch")
    console.print("     → Create a new scratch: [cyan]melee-agent extract get <func> --create-scratch[/cyan]")
    console.print("  2. [bold]Token expired:[/bold] The claim token is no longer valid")
    console.print("     → Fork the scratch: [cyan]melee-agent scratch fork {slug}[/cyan]")
    console.print("  3. [bold]Wrong scratch:[/bold] You may be trying to edit someone else's scratch")
    console.print("     → Check scratch URL and create your own copy")

    return False


async def _verify_scratch_ownership(client, slug: str) -> tuple[bool, str]:
    """Check if we can likely update a scratch before attempting.

    Returns (can_update, reason) tuple.
    """
    tokens = _load_scratch_tokens()

    if slug not in tokens:
        return False, "No saved token for this scratch"

    try:
        scratch = await client.get_scratch(slug)
        # If scratch has an owner and we have a token, we should be able to update
        # (The actual check happens server-side, but this helps detect obvious issues)
        if scratch.owner and scratch.owner.is_anonymous:
            return True, "Anonymous owner with saved token"
        elif scratch.owner:
            return True, f"Owned by {scratch.owner.username}"
        else:
            return False, "Scratch has no owner"
    except Exception as e:
        return False, f"Could not verify: {e}"


@scratch_app.command("create")
def scratch_create(
    function_name: Annotated[str, typer.Argument(help="Name of the function")],
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    api_url: Annotated[
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")
    ] = None,
    context_file: Annotated[
        Optional[Path], typer.Option("--context", "-c", help="Path to context file")
    ] = None,
):
    """Create a new scratch for a function on decomp.me."""
    api_url = api_url or get_local_api_url()
    from src.client import DecompMeAPIClient
    from src.extractor import extract_function

    # Extract function first to get source file path for context
    func = asyncio.run(extract_function(melee_root, function_name))
    if func is None:
        console.print(f"[red]Function '{function_name}' not found[/red]")
        raise typer.Exit(1)

    # Get context file using the function's source file path
    ctx_path = context_file or _get_context_file(source_file=func.file_path)

    # Always rebuild context to pick up header changes
    import subprocess
    # Build the context file - need relative path from melee_root
    try:
        ctx_relative = ctx_path.relative_to(melee_root)
        ninja_cwd = melee_root
    except ValueError:
        # ctx_path might be in a worktree, find the melee root for that worktree
        # The ctx_path looks like: .../melee-worktrees/<name>/build/GALE01/src/...
        # We need to run ninja from the worktree root
        parts = ctx_path.parts
        ninja_cwd = None
        for i, part in enumerate(parts):
            if part == "build" and i > 0:
                ninja_cwd = Path(*parts[:i])
                ctx_relative = Path(*parts[i:])
                break
        if ninja_cwd is None:
            console.print(f"[red]Cannot determine ninja target for: {ctx_path}[/red]")
            raise typer.Exit(1)

    try:
        result = subprocess.run(
            ["ninja", str(ctx_relative)],
            cwd=ninja_cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            console.print(f"[red]Failed to build context file:[/red]")
            console.print(result.stderr or result.stdout)
            raise typer.Exit(1)
        # Only show message if ninja actually did something
        if "no work to do" not in result.stdout.lower():
            console.print(f"[green]Built context file[/green]")
    except subprocess.TimeoutExpired:
        console.print(f"[red]Timeout building context file[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print(f"[red]ninja not found - please install it[/red]")
        raise typer.Exit(1)

    if not ctx_path.exists():
        console.print(f"[red]Context file not found after build: {ctx_path}[/red]")
        raise typer.Exit(1)

    melee_context = ctx_path.read_text()
    console.print(f"[dim]Loaded {len(melee_context):,} bytes of context from {ctx_path.name}[/dim]")

    # Strip function definition (but keep declaration) to avoid redefinition errors
    if function_name in melee_context:
        lines = melee_context.split('\n')
        filtered = []
        in_func = False
        depth = 0
        for line in lines:
            if not in_func and function_name in line and '(' in line:
                s = line.strip()
                # Skip comments, control flow
                if s.startswith('//') or s.startswith('if') or s.startswith('while'):
                    filtered.append(line)
                    continue
                # Keep declarations (prototypes) - they end with );
                if s.endswith(';'):
                    filtered.append(line)
                    continue
                # This is a function definition
                in_func = True
                depth = line.count('{') - line.count('}')
                filtered.append(f'// {function_name} definition stripped')
                if '{' not in line:
                    depth = 0
                elif depth <= 0:
                    in_func = False
                continue
            if in_func:
                depth += line.count('{') - line.count('}')
                if depth <= 0:
                    in_func = False
                continue
            filtered.append(line)
        melee_context = '\n'.join(filtered)
        console.print(f"[dim]Stripped {function_name} definition from context[/dim]")

    # Detect correct compiler for this source file
    compiler = get_compiler_for_source(func.file_path, melee_root)
    console.print(f"[dim]Using compiler: {compiler}[/dim]")

    async def create():

        async with DecompMeAPIClient(base_url=api_url) as client:
            from src.client import ScratchCreate
            scratch = await client.create_scratch(
                ScratchCreate(
                    name=func.name,
                    target_asm=func.asm,
                    context=melee_context,
                    compiler=compiler,
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

    # Write to state database (non-blocking)
    db_upsert_scratch(
        scratch.slug,
        instance='local',
        base_url=api_url,
        function_name=function_name,
        claim_token=scratch.claim_token,
    )
    db_upsert_function(
        function_name,
        local_scratch_slug=scratch.slug,
        status='in_progress',
    )


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
    source_file: Annotated[
        Optional[Path], typer.Option("--source", "-s", help="Update source from file before compiling")
    ] = None,
    code: Annotated[
        Optional[str], typer.Option("--code", "-c", help="Update source from inline code before compiling (WARNING: may have shell escaping issues, prefer --source or --stdin)")
    ] = None,
    from_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read source code from stdin (avoids shell escaping issues)")
    ] = False,
    api_url: Annotated[
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")
    ] = None,
    show_diff: Annotated[
        bool, typer.Option("--diff", "-d", help="Show instruction diff")
    ] = False,
    max_lines: Annotated[
        int, typer.Option("--max-lines", "-n", help="Max diff lines to show (0=all)")
    ] = 100,
):
    """Compile a scratch and show the diff.

    If --source is provided, updates the scratch source code from a file before compiling.
    If --stdin is provided, reads source code from stdin (recommended for programmatic use).
    If --code is provided, updates from inline string (may have shell escaping issues).
    Only one of --source, --stdin, or --code can be specified.
    """
    api_url = api_url or get_local_api_url()
    from src.client import DecompMeAPIClient, ScratchUpdate, DecompMeAPIError

    # Validate mutually exclusive options
    options_count = sum([source_file is not None, code is not None, from_stdin])
    if options_count > 1:
        console.print("[red]Cannot specify multiple source options (--source, --code, --stdin)[/red]")
        raise typer.Exit(1)

    # Get source code from file, stdin, or inline
    source_code = None
    if source_file is not None:
        if not source_file.exists():
            console.print(f"[red]Source file not found: {source_file}[/red]")
            raise typer.Exit(1)
        source_code = source_file.read_text()
    elif from_stdin:
        import sys
        source_code = sys.stdin.read()
    elif code is not None:
        source_code = code

    async def compile_scratch():
        async with DecompMeAPIClient(base_url=api_url) as client:
            # Early ownership verification if we're going to update
            if source_code is not None:
                can_update, reason = await _verify_scratch_ownership(client, slug)
                if not can_update:
                    console.print(f"[yellow]Warning:[/yellow] {reason}")
                    console.print("[dim]Update may fail - consider creating a new scratch if it does[/dim]")

            # Update source first if provided
            if source_code is not None:
                try:
                    await client.update_scratch(slug, ScratchUpdate(source_code=source_code))
                except DecompMeAPIError as e:
                    if "403" in str(e):
                        # Use improved error handler with recovery attempt
                        if await _handle_403_error(client, slug, e, "update"):
                            # Recovery succeeded, retry the update
                            await client.update_scratch(slug, ScratchUpdate(source_code=source_code))
                        else:
                            raise typer.Exit(1)
                    else:
                        raise
            return await client.compile_scratch(slug)

    result = asyncio.run(compile_scratch())

    if result.success:
        match_pct = (
            100.0 if result.diff_output.current_score == 0
            else (1.0 - result.diff_output.current_score / result.diff_output.max_score) * 100
        )

        # Record match score for history tracking
        record_match_score(slug, result.diff_output.current_score, result.diff_output.max_score)

        # Also record to state database (non-blocking)
        db_record_match_score(slug, result.diff_output.current_score, result.diff_output.max_score)

        console.print(f"[green]Compiled successfully![/green]")
        console.print(f"Match: {match_pct:.1f}%")
        console.print(f"Score: {result.diff_output.current_score}/{result.diff_output.max_score}")

        # Show match history if there's progression
        history_str = format_match_history(slug)
        if history_str:
            console.print(f"[dim]History: {history_str}[/dim]")

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
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")
    ] = None,
):
    """Update a scratch's source code from a file."""
    api_url = api_url or get_local_api_url()
    from src.client import DecompMeAPIClient, ScratchUpdate, DecompMeAPIError

    source_code = source_file.read_text()

    async def update():
        async with DecompMeAPIClient(base_url=api_url) as client:
            # Early ownership verification
            can_update, reason = await _verify_scratch_ownership(client, slug)
            if not can_update:
                console.print(f"[yellow]Warning:[/yellow] {reason}")
                console.print("[dim]Update may fail - consider creating a new scratch if it does[/dim]")

            try:
                scratch = await client.update_scratch(slug, ScratchUpdate(source_code=source_code))
            except DecompMeAPIError as e:
                if "403" in str(e):
                    # Use improved error handler with recovery attempt
                    if await _handle_403_error(client, slug, e, "update"):
                        # Recovery succeeded, retry the update
                        scratch = await client.update_scratch(slug, ScratchUpdate(source_code=source_code))
                    else:
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

        # Record match score for history tracking
        record_match_score(slug, result.diff_output.current_score, result.diff_output.max_score)

        console.print(f"[green]Updated![/green] Match: {match_pct:.1f}%")

        # Show match history if there's progression
        history_str = format_match_history(slug)
        if history_str:
            console.print(f"[dim]History: {history_str}[/dim]")
    else:
        console.print(f"[yellow]Updated but compilation failed[/yellow]")


@scratch_app.command("get")
def scratch_get(
    slug: Annotated[str, typer.Argument(help="Scratch slug/ID or URL")],
    api_url: Annotated[
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")
    ] = None,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
    show_diff: Annotated[
        bool, typer.Option("--diff", "-d", help="Show instruction diff")
    ] = False,
    max_lines: Annotated[
        int, typer.Option("--max-lines", "-n", help="Max diff lines to show (0=all)")
    ] = 100,
    show_context: Annotated[
        bool, typer.Option("--context", "-c", help="Show context instead of source code")
    ] = False,
    grep_context: Annotated[
        Optional[str], typer.Option("--grep", "-g", help="Search context for pattern (implies --context)")
    ] = None,
    context_lines: Annotated[
        int, typer.Option("-C", help="Context lines around grep matches")
    ] = 3,
):
    """Get full scratch information."""
    api_url = api_url or get_local_api_url()
    from src.client import DecompMeAPIClient

    # --grep implies --context
    if grep_context:
        show_context = True

    # Extract slug from URL if needed
    if slug.startswith("http"):
        parts = slug.strip("/").split("/")
        if "scratch" in parts:
            idx = parts.index("scratch")
            if idx + 1 < len(parts):
                slug = parts[idx + 1]

    async def get():
        async with DecompMeAPIClient(base_url=api_url) as client:
            scratch = await client.get_scratch(slug)
            diff_output = None
            if show_diff:
                result = await client.compile_scratch(slug)
                if result.success:
                    diff_output = result.diff_output
            return scratch, diff_output

    scratch, diff_output = asyncio.run(get())

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
        if show_context:
            data["context"] = scratch.context
        print(json.dumps(data, indent=2))
    else:
        match_pct = ((scratch.max_score - scratch.score) / scratch.max_score * 100) if scratch.max_score > 0 else 0
        console.print(f"[bold cyan]{scratch.name}[/bold cyan] ({scratch.slug})")
        console.print(f"Match: {match_pct:.1f}%")

        if show_context:
            if grep_context:
                # Search context for pattern
                lines = scratch.context.splitlines()
                matches = []
                try:
                    regex = re.compile(grep_context, re.IGNORECASE)
                except re.error as e:
                    console.print(f"[red]Invalid regex: {e}[/red]")
                    raise typer.Exit(1)

                for i, line in enumerate(lines):
                    if regex.search(line):
                        start = max(0, i - context_lines)
                        end = min(len(lines), i + context_lines + 1)
                        matches.append({"line_num": i + 1, "context": lines[start:end], "start": start + 1})

                if not matches:
                    console.print(f"[yellow]No matches for: {grep_context}[/yellow]")
                else:
                    console.print(f"\n[bold]Context matches for '{grep_context}':[/bold] ({len(matches)} found)\n")
                    for idx, match in enumerate(matches[:10], 1):
                        console.print(f"[cyan]Match {idx}[/cyan] (line {match['line_num']})")
                        for j, line in enumerate(match["context"]):
                            ln = match["start"] + j
                            marker = ">>> " if ln == match["line_num"] else "    "
                            console.print(f"{marker}{ln:5d}: {line}", markup=False)
                        console.print()
                    if len(matches) > 10:
                        console.print(f"[dim]... and {len(matches) - 10} more matches[/dim]")
            else:
                # Show full context (truncated)
                console.print(f"\n[bold]Context:[/bold] ({len(scratch.context):,} bytes)")
                ctx_display = scratch.context[:5000] if len(scratch.context) > 5000 else scratch.context
                console.print(ctx_display, markup=False)
                if len(scratch.context) > 5000:
                    console.print(f"\n[dim]... truncated ({len(scratch.context) - 5000:,} more bytes, use --grep to search)[/dim]")
        else:
            console.print(f"\n[bold]Source Code:[/bold]")
            # Use markup=False to prevent Rich from interpreting brackets like [t0] as tags
            source_display = scratch.source_code[:2000] if len(scratch.source_code) > 2000 else scratch.source_code
            console.print(source_display, markup=False)

    if show_diff and diff_output:
        _format_diff_output(diff_output, max_lines)


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
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")
    ] = None,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Search for scratches on decomp.me."""
    api_url = api_url or get_local_api_url()
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
    patterns: Annotated[list[str], typer.Argument(help="Regex pattern(s) to search for")],
    context_lines: Annotated[int, typer.Option("--context", "-C", help="Context lines")] = 3,
    max_results: Annotated[int, typer.Option("--max", "-n", help="Maximum matches per pattern")] = 10,
    api_url: Annotated[Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")] = None,
):
    """Search through a scratch's context for patterns.

    Supports multiple patterns in a single call:
        melee-agent scratch search-context <slug> "HSD_GObj" "FtCmd2" "ColorOverlay"
    """
    api_url = api_url or get_local_api_url()
    from src.client import DecompMeAPIClient

    async def get():
        async with DecompMeAPIClient(base_url=api_url) as client:
            return await client.get_scratch(slug)

    scratch = asyncio.run(get())
    lines = scratch.context.splitlines()

    # Process each pattern
    for pattern_idx, pattern in enumerate(patterns):
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            console.print(f"[red]Invalid regex '{pattern}': {e}[/red]")
            continue

        matches = []
        for i, line in enumerate(lines):
            if regex.search(line):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                matches.append({"line_num": i + 1, "context": lines[start:end], "start": start + 1})
                if len(matches) >= max_results:
                    break

        # Print separator between patterns if multiple
        if pattern_idx > 0:
            console.print("\n" + "─" * 60 + "\n")

        if not matches:
            console.print(f"[yellow]No matches for: {pattern}[/yellow]")
            continue

        console.print(f"[bold cyan]Pattern:[/bold cyan] {pattern} [dim]({len(matches)} matches)[/dim]\n")
        for idx, match in enumerate(matches[:5], 1):  # Show max 5 per pattern
            console.print(f"[cyan]Match {idx}[/cyan] (line {match['line_num']})")
            for j, line in enumerate(match["context"]):
                ln = match["start"] + j
                marker = ">>> " if ln == match["line_num"] else "    "
                console.print(f"{marker}{ln:5d}: {line}")
            console.print()

        if len(matches) > 5:
            console.print(f"[dim]... and {len(matches) - 5} more matches[/dim]")


@scratch_app.command("sync-from-repo")
def scratch_sync_from_repo(
    function_name: Annotated[str, typer.Argument(help="Function name to sync")],
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    api_url: Annotated[
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")
    ] = None,
):
    """Update a scratch's source code from the repo.

    Finds the function in the melee repo, extracts its code, and updates
    the corresponding scratch. Useful when a function is 100% in the repo
    but the scratch has placeholder/outdated code.

    Example: melee-agent scratch sync-from-repo ft_8008A1FC
    """
    api_url = api_url or get_local_api_url()
    from src.client import DecompMeAPIClient, ScratchUpdate, DecompMeAPIError
    from src.commit.configure import get_file_path_from_function
    from src.commit.update import _extract_function_from_code
    from ._common import load_completed_functions

    # Look up the scratch slug from the DB
    completed = load_completed_functions()
    if function_name not in completed:
        console.print(f"[red]Function '{function_name}' not found in tracking database[/red]")
        console.print("[dim]Use 'melee-agent state status' to see tracked functions[/dim]")
        raise typer.Exit(1)

    func_info = completed[function_name]
    slug = func_info.get('scratch_slug')
    if not slug:
        console.print(f"[red]No local scratch found for '{function_name}'[/red]")
        console.print("[dim]Use 'melee-agent extract get --create-scratch' to create one first[/dim]")
        raise typer.Exit(1)

    console.print(f"[bold]Syncing {function_name}[/bold] from repo to scratch {slug}")

    async def sync():
        # Find the source file
        file_path = await get_file_path_from_function(function_name, melee_root)
        if not file_path:
            console.print(f"[red]Could not find file containing '{function_name}'[/red]")
            raise typer.Exit(1)

        full_path = melee_root / "src" / file_path
        if not full_path.exists():
            console.print(f"[red]Source file not found: {full_path}[/red]")
            raise typer.Exit(1)

        console.print(f"[dim]Found in: src/{file_path}[/dim]")

        # Read and extract the function
        source_content = full_path.read_text(encoding='utf-8')
        function_code = _extract_function_from_code(source_content, function_name)

        if not function_code:
            console.print(f"[red]Could not extract function '{function_name}' from source[/red]")
            console.print("[dim]The function may be a stub or use non-standard formatting[/dim]")
            raise typer.Exit(1)

        console.print(f"[dim]Extracted {len(function_code)} bytes of code[/dim]")

        # Load fresh context and strip the function definition from it
        ctx_path = melee_root / "build" / "GALE01" / "src" / file_path.replace('.c', '.ctx')
        fresh_context = None
        if ctx_path.exists():
            fresh_context = ctx_path.read_text(encoding='utf-8')
            original_len = len(fresh_context)

            # Strip function definition (but keep declaration) to avoid redefinition
            if function_name in fresh_context:
                lines = fresh_context.split('\n')
                filtered_lines = []
                in_function = False
                brace_depth = 0
                for line in lines:
                    if not in_function and function_name in line and '(' in line:
                        s = line.strip()
                        # Skip comments, control flow, and declarations (end with ;)
                        if s.startswith('//') or s.startswith('if') or s.startswith('while'):
                            filtered_lines.append(line)
                            continue
                        # Keep declarations (prototypes) - they end with );
                        if s.endswith(';'):
                            filtered_lines.append(line)
                            continue
                        # This is a function definition
                        in_function = True
                        brace_depth = line.count('{') - line.count('}')
                        filtered_lines.append(f'// {function_name} definition stripped')
                        # If no brace on this line, wait for it
                        if '{' not in line:
                            brace_depth = 0
                        elif brace_depth <= 0:
                            in_function = False
                        continue
                    if in_function:
                        brace_depth += line.count('{') - line.count('}')
                        if brace_depth <= 0:
                            in_function = False
                        continue
                    filtered_lines.append(line)
                fresh_context = '\n'.join(filtered_lines)

            stripped_bytes = original_len - len(fresh_context)
            console.print(f"[dim]Loaded fresh context ({len(fresh_context):,} bytes, stripped {stripped_bytes:,})[/dim]")
        else:
            console.print(f"[yellow]Context file not found: {ctx_path}[/yellow]")
            console.print(f"[dim]Run 'ninja {ctx_path.relative_to(melee_root)}' to generate[/dim]")

        # Update the scratch
        async with DecompMeAPIClient(base_url=api_url) as client:
            # Verify ownership
            can_update, reason = await _verify_scratch_ownership(client, slug)
            if not can_update:
                console.print(f"[yellow]Warning:[/yellow] {reason}")

            update_data = ScratchUpdate(source_code=function_code)
            if fresh_context:
                update_data.context = fresh_context

            try:
                scratch = await client.update_scratch(slug, update_data)
            except DecompMeAPIError as e:
                if "403" in str(e):
                    if await _handle_403_error(client, slug, e, "update"):
                        scratch = await client.update_scratch(slug, update_data)
                    else:
                        raise typer.Exit(1)
                else:
                    raise

            # Compile to get new match %
            result = await client.compile_scratch(slug)
            return scratch, result

    scratch, result = asyncio.run(sync())

    if result.success and result.diff_output:
        match_pct = (
            100.0 if result.diff_output.current_score == 0
            else (1.0 - result.diff_output.current_score / result.diff_output.max_score) * 100
        )

        # Record match score
        record_match_score(slug, result.diff_output.current_score, result.diff_output.max_score)

        # Update DB with new match %
        db_upsert_function(
            function_name,
            match_percent=match_pct,
            status='matched' if match_pct >= 95 else 'in_progress',
        )

        # Record branch-specific progress for recovery/traceability
        branch = _get_current_branch(melee_root)
        if branch:
            from src.db import get_db
            try:
                db = get_db()
                db.upsert_branch_progress(
                    function_name=function_name,
                    branch=branch,
                    scratch_slug=slug,
                    match_percent=match_pct,
                    score=result.diff_output.current_score,
                    max_score=result.diff_output.max_score,
                    agent_id=AGENT_ID,
                )
            except Exception:
                pass  # Don't fail compile for branch tracking issues

        console.print(f"[green]Synced![/green] Match: {match_pct:.1f}%")

        if match_pct >= 100:
            console.print("[green]Function is now 100% - ready to sync to production[/green]")
    else:
        console.print(f"[yellow]Updated but compilation failed[/yellow]")
