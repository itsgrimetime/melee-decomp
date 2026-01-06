"""Diff remotes command - compare function status between git remotes."""

import re
import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from .._common import console, ensure_dol_in_worktree
from src.db import get_db


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _get_100_percent_functions_from_report(repo_path: Path) -> set[str]:
    """Get all 100% matched functions from local report.json."""
    import json

    report_path = repo_path / "build" / "GALE01" / "report.json"
    if not report_path.exists():
        return set()
    try:
        with open(report_path) as f:
            report = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()

    functions = set()
    for unit in report.get("units", []):
        for func in unit.get("functions", []):
            name = func.get("name")
            match_pct = func.get("fuzzy_match_percent", 0)
            if name and match_pct >= 100.0:
                functions.add(name)

    return functions


def _build_ref_and_get_report(repo_path: Path, ref: str) -> set[str]:
    """Create a worktree for ref, build it, and return 100% functions from report.json.

    This creates a temporary worktree, runs configure.py and ninja, then
    parses the resulting report.json. The worktree is cleaned up afterward.
    """
    import shutil
    import json

    # Create worktree in a temp-like location
    safe_ref = ref.replace("/", "-").replace("\\", "-")
    worktree_path = repo_path.parent / f".diff-worktree-{safe_ref}"

    # Clean up any existing worktree at this path
    if worktree_path.exists():
        console.print(f"[dim]Cleaning up existing worktree at {worktree_path}...[/dim]")
        _run_git(["worktree", "remove", "--force", str(worktree_path)], repo_path)
        if worktree_path.exists():
            shutil.rmtree(worktree_path)

    # Create the worktree
    console.print(f"[dim]Creating worktree for {ref}...[/dim]")
    ret, _, stderr = _run_git(
        ["worktree", "add", "--detach", str(worktree_path), ref],
        repo_path,
    )
    if ret != 0:
        console.print(f"[red]Failed to create worktree for {ref}: {stderr}[/red]")
        return set()

    try:
        # Ensure the base DOL file exists in the worktree
        if not ensure_dol_in_worktree(worktree_path):
            console.print("[red]Base DOL not found. Run 'melee-agent setup dol --auto' first.[/red]")
            return set()

        # Run configure.py
        console.print(f"[dim]Running configure.py for {ref}...[/dim]")
        result = subprocess.run(
            ["python", "configure.py"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]configure.py failed for {ref} (exit code {result.returncode}):[/red]")
            if result.stdout.strip():
                console.print(f"[dim]stdout:[/dim]")
                for line in result.stdout.strip().split('\n')[-20:]:
                    console.print(f"  {line}")
            if result.stderr.strip():
                console.print(f"[dim]stderr:[/dim]")
                for line in result.stderr.strip().split('\n')[-20:]:
                    console.print(f"  [red]{line}[/red]")
            return set()

        # Run ninja
        console.print(f"[bold]Building {ref}... (this may take a few minutes)[/bold]")
        result = subprocess.run(
            ["ninja"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]ninja build failed for {ref} (exit code {result.returncode}):[/red]")
            # Show last lines of both stdout and stderr
            if result.stdout.strip():
                console.print(f"[dim]stdout (last 20 lines):[/dim]")
                for line in result.stdout.strip().split('\n')[-20:]:
                    console.print(f"  {line}")
            if result.stderr.strip():
                console.print(f"[dim]stderr (last 20 lines):[/dim]")
                for line in result.stderr.strip().split('\n')[-20:]:
                    console.print(f"  [red]{line}[/red]")
            return set()

        console.print(f"[green]Build complete for {ref}[/green]")

        # Read report.json from the worktree
        report_path = worktree_path / "build" / "GALE01" / "report.json"
        if not report_path.exists():
            console.print(f"[red]report.json not found after build[/red]")
            return set()

        with open(report_path) as f:
            report = json.load(f)

        functions = set()
        for unit in report.get("units", []):
            for func in unit.get("functions", []):
                name = func.get("name")
                match_pct = func.get("fuzzy_match_percent", 0)
                if name and match_pct >= 100.0:
                    functions.add(name)

        return functions

    finally:
        # Clean up worktree
        console.print(f"[dim]Cleaning up worktree for {ref}...[/dim]")
        _run_git(["worktree", "remove", "--force", str(worktree_path)], repo_path)
        if worktree_path.exists():
            shutil.rmtree(worktree_path)


def _get_matched_functions_from_source(repo_path: Path, ref: str) -> set[str]:
    """Get matched functions by parsing source files at a git ref.

    A function is considered matched if it has a C implementation (not asm).
    Functions defined with `asm funcname(...) {` are not matched.
    """
    # Get list of .c files in src/melee
    ret, stdout, _ = _run_git(
        ["ls-tree", "-r", "--name-only", ref, "--", "src/melee"],
        repo_path,
    )
    if ret != 0:
        return set()

    c_files = [f for f in stdout.strip().split('\n') if f.endswith('.c')]

    matched_functions = set()

    # Pattern for function definitions
    # Matches: `returntype funcname(` or `static returntype funcname(`
    # But NOT: `asm returntype funcname(` (these are unmatched)
    # Function names in this codebase follow patterns like:
    # - fn_80XXXXXX, prefix_80XXXXXX (address-based)
    # - CamelCase names
    func_def_pattern = re.compile(
        r'^(?!.*\basm\b)'  # Negative lookahead: not an asm function
        r'(?:static\s+)?'  # Optional static
        r'(?:inline\s+)?'  # Optional inline
        r'(?:const\s+)?'   # Optional const
        r'[\w\s\*]+?'      # Return type (words, spaces, pointers)
        r'\b(\w+)\s*\('    # Function name followed by (
        r'[^;]*$',         # Not a declaration (no semicolon at end)
        re.MULTILINE
    )

    # Also match asm functions to exclude them explicitly
    asm_func_pattern = re.compile(
        r'^\s*asm\s+[\w\s\*]+?\b(\w+)\s*\(',
        re.MULTILINE
    )

    for c_file in c_files:
        ret, content, _ = _run_git(["show", f"{ref}:{c_file}"], repo_path)
        if ret != 0:
            continue

        # Find all asm functions first (these are NOT matched)
        asm_funcs = set(asm_func_pattern.findall(content))

        # Find function definitions that look like implementations
        # (have opening brace on same or next line)
        lines = content.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]

            # Skip asm functions
            if re.match(r'^\s*asm\s+', line):
                i += 1
                continue

            # Look for function definition pattern
            # Must have return type, name, and opening paren
            match = re.match(
                r'^(?:static\s+)?(?:inline\s+)?(?:const\s+)?'
                r'(?:unsigned\s+|signed\s+)?'
                r'(?:void|int|s8|s16|s32|u8|u16|u32|f32|f64|bool|char|'
                r'[A-Z]\w*\s*\**)\s+'  # Common types or CamelCase types
                r'(\w+)\s*\([^;]*$',   # Function name + ( + not ending in ;
                line
            )

            if match:
                func_name = match.group(1)
                # Check if this is followed by a { (function body)
                # Look at current line and next few lines
                has_body = False
                for j in range(i, min(i + 3, len(lines))):
                    if '{' in lines[j]:
                        has_body = True
                        break
                    if ';' in lines[j]:
                        # It's a declaration, not definition
                        break

                if has_body and func_name not in asm_funcs:
                    # Filter out common non-function matches
                    if not func_name.startswith(('if', 'while', 'for', 'switch', 'return')):
                        matched_functions.add(func_name)

            i += 1

    return matched_functions


def diff_remotes_command(
    repo_path: Annotated[
        Optional[Path], typer.Option("--repo", "-r", help="Path to melee repo")
    ] = None,
    origin: Annotated[
        str, typer.Option("--origin", help="Origin remote/branch")
    ] = "origin/master",
    upstream: Annotated[
        str, typer.Option("--upstream", help="Upstream remote/branch")
    ] = "upstream/master",
    build: Annotated[
        bool, typer.Option("--build/--no-build", help="Build both refs in worktrees for accurate report.json comparison (slow)")
    ] = False,
    update_status: Annotated[
        bool, typer.Option("--update-status/--no-update-status", help="Update DB status for matched functions")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Limit output rows")
    ] = 50,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show detailed output")
    ] = False,
):
    """Show functions that differ between origin and upstream.

    This compares your fork (origin/master) against upstream (upstream/master)
    to show which matched functions haven't been merged yet.

    By default, compares by parsing source files at each ref to find functions
    with C implementations (vs asm stubs). This is fast but approximate.

    Use --build to build both refs in worktrees and compare actual report.json
    files. This is slow but gives exact 100% match counts.

    Example:
        melee-agent state diff-remotes                    # Fast source parsing
        melee-agent state diff-remotes --build            # Slow but accurate
        melee-agent state diff-remotes --update-status    # Fix 'merged' -> 'committed'
    """
    # Find repo path
    if repo_path is None:
        if (Path.cwd() / "config" / "GALE01").exists():
            repo_path = Path.cwd()
        elif (Path.cwd() / "melee" / "config" / "GALE01").exists():
            repo_path = Path.cwd() / "melee"
        else:
            console.print("[red]Could not find melee repo. Use --repo to specify.[/red]")
            raise typer.Exit(1)

    db = get_db()

    # Fetch latest from remotes
    console.print(f"[dim]Fetching from remotes...[/dim]")
    _run_git(["fetch", "origin"], repo_path)
    _run_git(["fetch", "upstream"], repo_path)

    # Get local functions from report.json (most accurate)
    console.print(f"[dim]Reading local report.json...[/dim]")
    local_funcs = _get_100_percent_functions_from_report(repo_path)
    if not local_funcs:
        console.print("[yellow]Could not read report.json. Run 'ninja' to build first.[/yellow]")
        raise typer.Exit(1)
    console.print(f"[dim]Found {len(local_funcs)} functions at 100% locally[/dim]")

    if build:
        # Build both refs in worktrees for accurate comparison
        console.print(f"\n[bold]Building both refs for accurate comparison...[/bold]")
        console.print(f"[yellow]This will take several minutes.[/yellow]\n")

        # Build origin
        console.print(f"[bold cyan]═══ Building {origin} ═══[/bold cyan]")
        origin_funcs = _build_ref_and_get_report(repo_path, origin)
        if not origin_funcs:
            console.print(f"[red]Failed to build {origin}[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]Found {len(origin_funcs)} functions at 100% in {origin}[/dim]\n")

        # Build upstream
        console.print(f"[bold cyan]═══ Building {upstream} ═══[/bold cyan]")
        upstream_funcs = _build_ref_and_get_report(repo_path, upstream)
        if not upstream_funcs:
            console.print(f"[red]Failed to build {upstream}[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]Found {len(upstream_funcs)} functions at 100% in {upstream}[/dim]\n")
    else:
        # Fast source parsing method
        console.print(f"[dim]Parsing source files at {upstream}...[/dim]")
        upstream_funcs = _get_matched_functions_from_source(repo_path, upstream)
        if not upstream_funcs:
            console.print(f"[yellow]Could not parse source files from {upstream}[/yellow]")
            raise typer.Exit(1)
        console.print(f"[dim]Found {len(upstream_funcs)} matched functions in {upstream}[/dim]")

        console.print(f"[dim]Parsing source files at {origin}...[/dim]")
        origin_funcs = _get_matched_functions_from_source(repo_path, origin)
        console.print(f"[dim]Found {len(origin_funcs)} matched functions in {origin}[/dim]")

    origin_only = origin_funcs - upstream_funcs
    upstream_only = upstream_funcs - origin_funcs

    console.print(f"\n[bold]Matched in {origin} but NOT in {upstream}:[/bold]")
    console.print(f"[green]{len(origin_only)} functions[/green]")

    # Get DB info for these functions
    with db.connection() as conn:
        if origin_only:
            placeholders = ",".join(["?"] * len(origin_only))
            cursor = conn.execute(
                f"""
                SELECT function_name, match_percent, status, local_scratch_slug
                FROM functions
                WHERE function_name IN ({placeholders})
                ORDER BY match_percent DESC
                """,
                list(origin_only),
            )
            db_funcs = {row['function_name']: dict(row) for row in cursor.fetchall()}
        else:
            db_funcs = {}

    # Display origin-only functions
    if origin_only:
        table = Table(title=f"In {origin} only (not yet upstream)")
        table.add_column("Function", style="cyan")
        table.add_column("Match %", justify="right")
        table.add_column("Status", style="yellow")
        table.add_column("In DB", justify="center")

        sorted_funcs = sorted(origin_only)
        for func_name in sorted_funcs[:limit]:
            db_info = db_funcs.get(func_name, {})
            match_pct = db_info.get('match_percent', 0) or 0
            status = db_info.get('status', '-')
            in_db = "✓" if db_info else "-"
            table.add_row(
                func_name,
                f"{match_pct:.1f}" if match_pct else "-",
                status,
                in_db,
            )

        if len(origin_only) > limit:
            table.add_row(f"... ({len(origin_only) - limit} more)", "", "", "")

        console.print(table)

    # Show upstream-only if verbose
    if verbose and upstream_only:
        console.print(f"\n[bold]In {upstream} but NOT in {origin}:[/bold]")
        console.print(f"[yellow]{len(upstream_only)} functions[/yellow]")
        for func in sorted(upstream_only)[:20]:
            console.print(f"  {func}")
        if len(upstream_only) > 20:
            console.print(f"  ... ({len(upstream_only) - 20} more)")

    # Update status if requested
    if update_status and origin_only:
        console.print(f"\n[dim]Updating status for functions in origin only...[/dim]")
        updated = 0

        with db.transaction() as conn:
            for func_name in origin_only:
                cursor = conn.execute(
                    """
                    UPDATE functions
                    SET status = 'committed', updated_at = unixepoch('now', 'subsec')
                    WHERE function_name = ? AND status = 'merged'
                    """,
                    (func_name,),
                )
                updated += cursor.rowcount

        if updated > 0:
            console.print(f"[green]Updated {updated} functions from 'merged' to 'committed'[/green]")
        else:
            console.print("[dim]No status updates needed[/dim]")

    # Summary
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  In {origin} only:   {len(origin_only)}")
    console.print(f"  In {upstream} only: {len(upstream_only)}")
    console.print(f"  Tracked in DB:      {len(db_funcs)}/{len(origin_only)}")
