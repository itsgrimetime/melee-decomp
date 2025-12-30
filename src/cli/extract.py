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
    get_context_file,
    check_duplicate_operation,
    resolve_melee_root,
    load_completed_functions,
    detect_local_api_url,
    AGENT_ID,
    db_upsert_function,
    db_upsert_scratch,
    get_compiler_for_source,
)

# Context file override from environment
_context_env = os.environ.get("DECOMP_CONTEXT_FILE", "")


def _get_context_file(source_file: str | None = None, melee_root: Path | None = None) -> Path:
    """Get context file path.

    Args:
        source_file: Optional source file path to find per-file .ctx context.
        melee_root: Optional melee root path (for worktree support).
    """
    if _context_env:
        return Path(_context_env)
    return get_context_file(source_file=source_file, melee_root=melee_root)

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
        Optional[Path], typer.Option("--melee-root", "-m", help="Path to melee submodule (auto-detects agent worktree)")
    ] = None,
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
    exclude_subdir: Annotated[
        Optional[list[str]], typer.Option("--exclude-subdir", help="Exclude functions in these subdirectories (can be repeated)")
    ] = None,
):
    """List unmatched functions from the melee project.

    Match percentages are read from the authoritative report.json which reflects
    the actual compiled state of decompiled code in the repository.

    By default, excludes functions already tracked as completed/attempted.
    Use --include-completed to show all functions.

    Use --matching-only to only show functions in files already marked as Matching.
    These are the only functions that can be safely committed without linker errors
    from NonMatching file dependencies.

    Use --sort score to sort by recommendation score (best candidates first).
    Use --module ft to filter to fighter module only.

    To update match percentages after committing code:
        ninja build/GALE01/report.json
    """
    # Auto-detect agent worktree
    melee_root = resolve_melee_root(melee_root)

    from src.extractor import extract_unmatched_functions
    from src.extractor.report import ReportParser

    # Check if report.json exists and warn if stale
    report_parser = ReportParser(melee_root)
    if not (melee_root / "build" / "GALE01" / "report.json").exists():
        console.print("[yellow]Warning: report.json not found. Run 'ninja build/GALE01/report.json' to generate it.[/yellow]")
    elif report_parser.is_report_stale(max_age_hours=168):  # 1 week
        age_hours = report_parser.get_report_age_seconds() / 3600
        console.print(f"[dim]Note: report.json is {age_hours:.0f}h old. Run 'ninja build/GALE01/report.json' to refresh.[/dim]")

    # Don't load ASM for listing - it's not needed and adds significant overhead
    result = asyncio.run(extract_unmatched_functions(melee_root, include_asm=False))

    # Load completed functions to exclude
    completed = set()
    if not include_completed:
        completed = set(load_completed_functions().keys())

    # Build subdirectory exclusion check
    def _is_excluded_subdir(file_path: str) -> bool:
        if not exclude_subdir:
            return False
        path_lower = file_path.lower()
        for subdir in exclude_subdir:
            if f"/{subdir.lower()}/" in path_lower:
                return True
        return False

    # Filter functions
    functions = [
        f for f in result.functions
        if min_match <= f.current_match <= max_match
        and min_size <= f.size_bytes <= max_size
        and f.name not in completed
        and (not matching_only or f.object_status == "Matching")
        and (not module or f"/{module}/" in f.file_path.lower())
        and not _is_excluded_subdir(f.file_path)
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
    subdir_msg = f", excluding {', '.join(exclude_subdir)}" if exclude_subdir else ""
    console.print(f"\n[dim]Found {len(functions)} functions (from {result.total_functions} total{excluded_msg}{matching_msg}{module_msg}{subdir_msg})[/dim]")


@extract_app.command("get")
def extract_get(
    function_name: Annotated[str, typer.Argument(help="Name of the function to extract")],
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-m", help="Path to melee submodule (auto-detects agent worktree)")
    ] = None,
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
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected if not provided)")
    ] = None,
):
    """Extract a specific function's ASM and context.

    Match percentages are read from the authoritative report.json.
    Use --create-scratch to also create a decomp.me scratch in one step.
    """
    # Auto-detect agent worktree
    melee_root = resolve_melee_root(melee_root)

    # Auto-detect API URL if creating scratch
    if create_scratch and not api_url:
        api_url = detect_local_api_url()
        if not api_url:
            console.print("[red]Error: Could not find local decomp.me server[/red]")
            console.print("[dim]Tried: nzxt-discord.local, 10.200.0.1, localhost:8000[/dim]")
            console.print("")
            console.print("[yellow]STOP: The decomp.me server should always be available.[/yellow]")
            console.print("[yellow]Report this issue to the user - do NOT attempt local-only workarounds.[/yellow]")
            raise typer.Exit(1)
        console.print(f"[dim]Using decomp.me server: {api_url}[/dim]")

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

        ctx_path = _get_context_file(source_file=func.file_path, melee_root=melee_root)
        if not ctx_path.exists():
            console.print(f"[yellow]Context file not found, building...[/yellow]")
            import subprocess
            # Build the context file - need relative path from melee_root
            try:
                ctx_relative = ctx_path.relative_to(melee_root)
            except ValueError:
                # ctx_path might be in a worktree, find the melee root for that worktree
                # The ctx_path looks like: .../melee-worktrees/<name>/build/GALE01/src/...
                # We need to run ninja from the worktree root
                parts = ctx_path.parts
                for i, part in enumerate(parts):
                    if part == "build" and i > 0:
                        ninja_cwd = Path(*parts[:i])
                        ctx_relative = Path(*parts[i:])
                        break
                else:
                    console.print(f"[red]Cannot determine ninja target for: {ctx_path}[/red]")
                    raise typer.Exit(1)
            else:
                ninja_cwd = melee_root

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
                console.print(f"[green]Built context file[/green]")
            except subprocess.TimeoutExpired:
                console.print(f"[red]Timeout building context file[/red]")
                raise typer.Exit(1)
            except FileNotFoundError:
                console.print(f"[red]ninja not found - please install it[/red]")
                raise typer.Exit(1)

        if not ctx_path.exists():
            console.print(f"[red]Context file still not found after build: {ctx_path}[/red]")
            raise typer.Exit(1)

        melee_context = ctx_path.read_text()
        console.print(f"\n[dim]Loaded {len(melee_context):,} bytes of context[/dim]")

        # Strip function definition (but keep declaration) to avoid redefinition errors
        if func.name in melee_context:
            lines = melee_context.split('\n')
            filtered = []
            in_func = False
            depth = 0
            for line in lines:
                if not in_func and func.name in line and '(' in line:
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
                    filtered.append(f'// {func.name} definition stripped')
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
            console.print(f"[dim]Stripped {func.name} definition from context[/dim]")

        # Detect correct compiler for this source file
        compiler = get_compiler_for_source(func.file_path, melee_root)
        console.print(f"[dim]Using compiler: {compiler}[/dim]")

        async def find_or_create():
            from src.client import DecompMeAPIClient, ScratchCreate
            async with DecompMeAPIClient(base_url=api_url) as client:
                # First, search for existing scratches with this function name
                console.print(f"[dim]Searching for existing scratches...[/dim]")
                existing = await client.list_scratches(search=func.name, page_size=20)

                # Filter to exact name matches and find the best one
                best_scratch = None
                best_match_pct = -1.0

                for s in existing:
                    if s.name == func.name and s.max_score > 0:
                        match_pct = (s.max_score - s.score) / s.max_score * 100
                        if match_pct > best_match_pct:
                            best_match_pct = match_pct
                            best_scratch = s

                # If we found an existing scratch, check its family for even better matches
                if best_scratch:
                    console.print(f"[dim]Found existing scratch at {best_match_pct:.1f}%, checking family...[/dim]")
                    try:
                        family = await client.get_scratch_family(best_scratch.slug)
                        for s in family:
                            if s.max_score > 0:
                                match_pct = (s.max_score - s.score) / s.max_score * 100
                                if match_pct > best_match_pct:
                                    best_match_pct = match_pct
                                    best_scratch = s
                    except Exception:
                        pass  # Family lookup failed, use what we have

                # If we found a good existing scratch, fork it to continue
                if best_scratch and best_match_pct > 0:
                    console.print(f"[green]Found existing scratch at {best_match_pct:.1f}% - forking to continue[/green]")
                    scratch = await client.fork_scratch(best_scratch.slug)
                    if scratch.claim_token:
                        from src.cli.scratch import _save_scratch_token
                        _save_scratch_token(scratch.slug, scratch.claim_token)
                        try:
                            await client.claim_scratch(scratch.slug, scratch.claim_token)
                        except Exception:
                            pass
                    return scratch, best_match_pct

                # No existing scratch found - create new
                console.print(f"[dim]No existing scratches found, creating new...[/dim]")
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
                    from src.cli.scratch import _save_scratch_token
                    _save_scratch_token(scratch.slug, scratch.claim_token)
                    try:
                        await client.claim_scratch(scratch.slug, scratch.claim_token)
                        console.print(f"[dim]Claimed ownership of scratch[/dim]")
                    except Exception as e:
                        console.print(f"[yellow]Warning: Could not claim scratch: {e}[/yellow]")

                return scratch, 0.0

        scratch, starting_pct = asyncio.run(find_or_create())
        if starting_pct > 0:
            console.print(f"[green]Continuing from {starting_pct:.1f}% match:[/green] {api_url}/scratch/{scratch.slug}")
        else:
            console.print(f"[green]Created scratch:[/green] {api_url}/scratch/{scratch.slug}")

        # Write to state database (non-blocking)
        db_upsert_scratch(
            scratch.slug,
            instance='local',
            base_url=api_url,
            function_name=func.name,
            claim_token=scratch.claim_token,
            match_percent=starting_pct,
        )
        # Determine status based on match percentage
        if starting_pct >= 95:
            status = 'matched'
        elif starting_pct > 0:
            status = 'in_progress'
        else:
            status = 'in_progress'
        db_upsert_function(
            func.name,
            local_scratch_slug=scratch.slug,
            match_percent=starting_pct,
            status=status,
        )
