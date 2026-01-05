"""Diff remotes command - compare function status between git remotes."""

import subprocess
import re
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from .._common import console
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


def _get_matched_functions_from_commits(
    repo_path: Path,
    include_ref: str,
    exclude_ref: str,
) -> set[str]:
    """Get function names from 'Match X' commits in include_ref but not exclude_ref.

    Parses commit messages like:
    - "Match fn_80003100 (100%)"
    - "Match fn_80003100"
    - "Match fn_80003100, fn_80003200"
    """
    ret, stdout, _ = _run_git(
        ["log", f"{include_ref}", f"^{exclude_ref}", "--format=%s"],
        repo_path,
    )
    if ret != 0:
        return set()

    functions = set()

    # Common words to exclude (not function names)
    exclude_words = {
        'and', 'with', 'in', 'to', 'the', 'fix', 'update', 'add', 'at', 'of',
        'for', 'from', 'on', 'is', 'a', 'an', 'or', 'not', 'as', 'by', 'be',
        'access', 'call', 'struct', 'layout', 'missing', 'declarations',
        'capture', 'grab', 'functions', 'c', 'h', 'reorder', 'variable',
        'match', 'catch', 'shield', 'kirby', 'physics', 'callback', 'size',
        'floor', 'anim', 'coll', 'phys', 'iasa', 'enter', 'exit', 'init',
        'special', 'calculation', 'retrigger',
    }

    # Pattern for Melee function names:
    # - Prefix_Address format (e.g., fn_80003100, Camera_8002FC7C)
    # - CamelCase multi-word names (e.g., GetNameText, IsNameValid) - must have multiple capitals
    # - Prefixed names (e.g., ftCo_Catch_Phys, grBigBlue_801E68B8)
    func_patterns = [
        # Address-based: prefix_80XXXXXX (most reliable)
        re.compile(r'\b([a-zA-Z]{2,}_80[0-9A-Fa-f]{6})\b'),
        # Prefixed functions like ftCo_X, grCastle_X, itDosei_X, mnName_X etc
        re.compile(r'\b((?:ft|gr|it|mn|ef|lb|mp|un)[A-Z][a-zA-Z]*_[A-Za-z0-9_]+)\b'),
        # CamelCase with underscore (e.g., GetName_Something)
        re.compile(r'\b([A-Z][a-z]+[A-Z][a-zA-Z]*_[A-Za-z]+)\b'),
        # Multi-capital CamelCase only (GetNameText has G, N, T)
        re.compile(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+){2,})\b'),
    ]

    for line in stdout.strip().split('\n'):
        if not line:
            continue
        # Only process lines starting with "Match"
        if not line.lower().startswith('match'):
            continue

        # Try each pattern
        for pattern in func_patterns:
            for match in pattern.finditer(line):
                name = match.group(1)
                if name.lower() not in exclude_words and len(name) > 3:
                    functions.add(name)

    return functions


def _get_matched_functions_from_source(
    repo_path: Path,
    ref: str,
) -> set[str]:
    """Get functions that are NOT marked NONMATCHING at a given ref.

    This parses all .c files and finds functions that don't have
    #pragma NONMATCHING or ASM markers.
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
    nonmatching_pattern = re.compile(r'#pragma\s+NONMATCHING')
    asm_pattern = re.compile(r'asm\s+\w+\s*\(')
    func_pattern = re.compile(r'^(?:static\s+)?(?:inline\s+)?(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*\{', re.MULTILINE)

    for c_file in c_files:
        ret, content, _ = _run_git(["show", f"{ref}:{c_file}"], repo_path)
        if ret != 0:
            continue

        # Split into function blocks and check each
        # Simple heuristic: find function definitions and check if preceded by NONMATCHING
        lines = content.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            # Check for function definition
            func_match = func_pattern.match(line)
            if func_match:
                func_name = func_match.group(1)
                # Check previous lines for NONMATCHING pragma
                is_nonmatching = False
                for j in range(max(0, i - 5), i):
                    if nonmatching_pattern.search(lines[j]):
                        is_nonmatching = True
                        break
                    if asm_pattern.search(lines[j]):
                        is_nonmatching = True
                        break

                if not is_nonmatching:
                    matched_functions.add(func_name)
            i += 1

    return matched_functions


def _get_100_percent_functions_from_report(
    repo_path: Path,
    ref: str,
) -> set[str]:
    """Get all 100% matched functions from report.json at a specific git ref.

    Note: report.json is in the build/ directory which is gitignored,
    so this only works for the current working tree, not for git refs.
    """
    import json

    # report.json is gitignored, so we can't get it from git refs
    # Only works for current working tree
    if ref in ("HEAD", ""):
        report_path = repo_path / "build" / "GALE01" / "report.json"
        if not report_path.exists():
            return set()
        try:
            with open(report_path) as f:
                report = json.load(f)
        except (json.JSONDecodeError, OSError):
            return set()
    else:
        # Try git show (will fail for gitignored files)
        ret, stdout, _ = _run_git(
            ["show", f"{ref}:build/GALE01/report.json"],
            repo_path,
        )
        if ret != 0:
            return set()
        try:
            report = json.loads(stdout)
        except json.JSONDecodeError:
            return set()

    functions = set()

    # Parse units -> functions
    for unit in report.get("units", []):
        for func in unit.get("functions", []):
            name = func.get("name")
            match_pct = func.get("fuzzy_match_percent", 0)
            if name and match_pct >= 100.0:
                functions.add(name)

    return functions


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
    method: Annotated[
        str, typer.Option("--method", "-m", help="Method: 'commits' (parse commit messages) or 'source' (compare NONMATCHING pragmas)")
    ] = "commits",
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

    Methods:
    - commits: Parse "Match X" commit messages (fast, includes WIP)
    - source: Compare NONMATCHING pragmas in .c files (slower, only 100% matches)

    The 'commits' method finds more functions because it includes anything
    mentioned in a "Match X" commit, even partial matches. The 'source' method
    is more accurate as it only finds functions that are actually matching
    (no NONMATCHING pragma in the code).

    Example:
        melee-agent state diff-remotes                    # Fast, using commits
        melee-agent state diff-remotes --method source    # Accurate, compare code
        melee-agent state diff-remotes --update-status    # Fix 'merged' -> 'committed'
    """
    # Find repo path
    if repo_path is None:
        # Try current directory or parent
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

    if method == "commits":
        # Get functions from commit messages
        console.print(f"[dim]Parsing commit messages...[/dim]")

        origin_only = _get_matched_functions_from_commits(repo_path, origin, upstream)
        upstream_only = _get_matched_functions_from_commits(repo_path, upstream, origin)

        console.print(f"\n[bold]Functions matched in {origin} but NOT in {upstream}:[/bold]")
        console.print(f"[green]{len(origin_only)} functions[/green]")

    elif method == "source":
        # Compare NONMATCHING pragmas in source files
        console.print(f"[dim]Comparing source files (checking NONMATCHING pragmas)...[/dim]")
        console.print(f"[dim]This may take a moment...[/dim]")

        origin_funcs = _get_matched_functions_from_source(repo_path, origin)
        upstream_funcs = _get_matched_functions_from_source(repo_path, upstream)

        if not origin_funcs:
            console.print(f"[yellow]Could not parse source from {origin}[/yellow]")
        if not upstream_funcs:
            console.print(f"[yellow]Could not parse source from {upstream}[/yellow]")

        console.print(f"[dim]Found {len(origin_funcs)} matching functions in {origin}[/dim]")
        console.print(f"[dim]Found {len(upstream_funcs)} matching functions in {upstream}[/dim]")

        origin_only = origin_funcs - upstream_funcs
        upstream_only = upstream_funcs - origin_funcs

        console.print(f"\n[bold]Matched in {origin} but NOT in {upstream}:[/bold]")
        console.print(f"[green]{len(origin_only)} functions[/green]")

    else:
        console.print(f"[red]Unknown method: {method}[/red]")
        raise typer.Exit(1)

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
                # Only update if function is tracked and status is 'merged'
                # (which would be wrong since it's not in upstream yet)
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
