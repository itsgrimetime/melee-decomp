"""Extract commands - list and extract unmatched functions."""

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
    DECOMP_COMPLETED_FILE,
    get_agent_melee_root,
    get_agent_context_file,
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

extract_app = typer.Typer(help="Extract and list unmatched functions")


def _compute_recommendation_score(func) -> float:
    """Compute a recommendation score for function selection.

    Higher score = better candidate for matching.
    Factors:
    - Smaller functions are easier (50-300 bytes ideal)
    - Low match % means more room for improvement
    - Certain modules (ft/, lb/) are better documented
    """
    score = 100.0

    # Size scoring: prefer 50-300 bytes
    if func.size_bytes < 50:
        score -= 20  # Too small, likely trivial
    elif func.size_bytes <= 150:
        score += 20  # Ideal small
    elif func.size_bytes <= 300:
        score += 10  # Good medium
    elif func.size_bytes <= 500:
        score += 0   # Acceptable
    elif func.size_bytes <= 800:
        score -= 10  # Getting complex
    else:
        score -= 30  # Very complex

    # Match % scoring: prefer lower (more room to improve)
    if func.current_match < 0.10:
        score += 15  # Fresh start
    elif func.current_match < 0.30:
        score += 10  # Good candidate
    elif func.current_match < 0.50:
        score += 5   # Some work done
    elif func.current_match >= 0.95:
        score -= 40  # Already nearly done, likely stuck

    # Module scoring: prefer well-documented modules
    path = func.file_path.lower()
    if "/ft/" in path or "/lb/" in path:
        score += 15  # Fighter/Library - well documented
    elif "/gr/" in path:
        score += 10  # Ground - good patterns
    elif "/it/" in path:
        score += 5   # Item - reasonable
    elif "/mn/" in path or "/db/" in path:
        score -= 10  # Menu/Debug - less common

    return score


@extract_app.command("list")
def extract_list(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage")
    ] = 0.0,
    max_match: Annotated[
        float, typer.Option("--max-match", help="Maximum match percentage")
    ] = 0.99,
    min_size: Annotated[
        int, typer.Option("--min-size", help="Minimum function size in bytes")
    ] = 0,
    max_size: Annotated[
        int, typer.Option("--max-size", help="Maximum function size in bytes")
    ] = 10000,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum number of results")
    ] = 20,
    include_completed: Annotated[
        bool, typer.Option("--include-completed", help="Include already-completed functions")
    ] = False,
    matching_only: Annotated[
        bool, typer.Option("--matching-only", "--committable", help="Only show functions in Matching files (can be committed)")
    ] = False,
    show_status: Annotated[
        bool, typer.Option("--show-status", help="Show object status column (Matching/NonMatching)")
    ] = False,
    module: Annotated[
        Optional[str], typer.Option("--module", help="Filter by module path (e.g., ft, lb, gr, it)")
    ] = None,
    sort_by: Annotated[
        str, typer.Option("--sort", help="Sort by: score (recommended), size, match")
    ] = "score",
    show_score: Annotated[
        bool, typer.Option("--show-score", help="Show recommendation score column")
    ] = False,
):
    """List unmatched functions from the melee project.

    By default, excludes functions already tracked as completed/attempted.
    Use --include-completed to show all functions.

    Use --matching-only to only show functions in files already marked as Matching.
    These are the only functions that can be safely committed without linker errors
    from NonMatching file dependencies.

    Use --sort score to sort by recommendation score (best candidates first).
    Use --module ft to filter to fighter module only.
    """
    from src.extractor import extract_unmatched_functions

    result = asyncio.run(extract_unmatched_functions(melee_root))

    # Load completed functions to exclude
    completed = set()
    if not include_completed:
        completed_path = Path(DECOMP_COMPLETED_FILE)
        if completed_path.exists():
            try:
                with open(completed_path, 'r') as f:
                    completed = set(json.load(f).keys())
            except (json.JSONDecodeError, IOError):
                pass

    # Filter functions
    functions = [
        f for f in result.functions
        if min_match <= f.current_match <= max_match
        and min_size <= f.size_bytes <= max_size
        and f.name not in completed
        and (not matching_only or f.object_status == "Matching")
        and (not module or f"/{module}/" in f.file_path.lower())
    ]

    # Sort functions
    if sort_by == "score":
        functions = sorted(functions, key=lambda f: -_compute_recommendation_score(f))
    elif sort_by == "size":
        functions = sorted(functions, key=lambda f: f.size_bytes)
    elif sort_by == "match":
        functions = sorted(functions, key=lambda f: -f.current_match)
    else:
        functions = sorted(functions, key=lambda f: -f.current_match)

    functions = functions[:limit]

    # Build table
    title = "Unmatched Functions"
    if sort_by == "score":
        title += " (sorted by recommendation)"
    table = Table(title=title)
    table.add_column("Name", style="cyan")
    table.add_column("File", style="green")
    table.add_column("Match %", justify="right")
    table.add_column("Size", justify="right")
    if show_score:
        table.add_column("Score", justify="right", style="magenta")
    if show_status:
        table.add_column("Status", style="yellow")
    table.add_column("Address", style="dim")

    for func in functions:
        row = [
            func.name,
            func.file_path,
            f"{func.current_match * 100:.1f}%",
            f"{func.size_bytes}",
        ]
        if show_score:
            score = _compute_recommendation_score(func)
            row.append(f"{score:.0f}")
        if show_status:
            row.append(func.object_status)
        row.append(func.address)
        table.add_row(*row)

    console.print(table)
    excluded_msg = f", {len(completed)} completed excluded" if completed else ""
    matching_msg = ", Matching files only" if matching_only else ""
    module_msg = f", {module}/ only" if module else ""
    console.print(f"\n[dim]Found {len(functions)} functions (from {result.total_functions} total{excluded_msg}{matching_msg}{module_msg})[/dim]")


@extract_app.command("get")
def extract_get(
    function_name: Annotated[str, typer.Argument(help="Name of the function to extract")],
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Output file for ASM")
    ] = None,
    full: Annotated[
        bool, typer.Option("--full", "-f", help="Show full assembly (no truncation)")
    ] = False,
    create_scratch: Annotated[
        bool, typer.Option("--create-scratch", "-s", help="Create a decomp.me scratch")
    ] = False,
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL (for --create-scratch)")
    ] = DEFAULT_DECOMP_ME_URL,
):
    """Extract a specific function's ASM and context.

    Use --create-scratch to also create a decomp.me scratch in one step.
    """
    from src.extractor import extract_function

    func = asyncio.run(extract_function(melee_root, function_name))

    if func is None:
        console.print(f"[red]Function '{function_name}' not found[/red]")
        raise typer.Exit(1)

    console.print(f"[bold cyan]{func.name}[/bold cyan]")
    console.print(f"File: {func.file_path}")
    console.print(f"Address: {func.address}")
    console.print(f"Size: {func.size_bytes} bytes")
    console.print(f"Match: {func.current_match * 100:.1f}%")
    console.print("\n[bold]Assembly:[/bold]")
    if func.asm:
        if full or len(func.asm) <= 4000:
            console.print(func.asm)
        else:
            console.print(func.asm[:4000] + f"\n... ({len(func.asm) - 4000} more chars, use --full to see all)")
    else:
        console.print("[yellow]ASM not available (project needs to be built first)[/yellow]")

    if output:
        if func.asm:
            output.write_text(func.asm)
            console.print(f"\n[green]ASM written to {output}[/green]")
        else:
            console.print("[red]Cannot write output - ASM not available[/red]")

    # Create scratch if requested
    if create_scratch:
        if not api_url:
            console.print("[red]Error: DECOMP_API_BASE environment variable required for --create-scratch[/red]")
            raise typer.Exit(1)

        if not func.asm:
            console.print("[red]Cannot create scratch - ASM not available[/red]")
            raise typer.Exit(1)

        ctx_path = _get_context_file()
        if not ctx_path.exists():
            console.print(f"[red]Context file not found: {ctx_path}[/red]")
            console.print("[dim]Run 'ninja' in melee/ to generate build/ctx.c[/dim]")
            raise typer.Exit(1)

        melee_context = ctx_path.read_text()
        console.print(f"\n[dim]Loaded {len(melee_context):,} bytes of context[/dim]")

        async def create():
            from src.client import DecompMeAPIClient, ScratchCreate
            async with DecompMeAPIClient(base_url=api_url) as client:
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
                    from src.cli.scratch import _save_scratch_token
                    _save_scratch_token(scratch.slug, scratch.claim_token)
                    try:
                        await client.claim_scratch(scratch.slug, scratch.claim_token)
                        console.print(f"[dim]Claimed ownership of scratch[/dim]")
                    except Exception as e:
                        console.print(f"[yellow]Warning: Could not claim scratch: {e}[/yellow]")

                return scratch

        scratch = asyncio.run(create())
        console.print(f"[green]Created scratch:[/green] {api_url}/scratch/{scratch.slug}")
