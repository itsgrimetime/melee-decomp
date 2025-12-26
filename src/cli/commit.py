"""Commit commands - commit matched functions and create PRs."""

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from ._common import console, DEFAULT_MELEE_ROOT, DECOMP_CONFIG_DIR
from .complete import _load_completed, _save_completed, _get_current_branch

# API URL from environment
_api_base = os.environ.get("DECOMP_API_BASE", "")
DEFAULT_DECOMP_ME_URL = _api_base[:-4] if _api_base.endswith("/api") else _api_base

commit_app = typer.Typer(help="Commit matched functions and create PRs")


def _require_api_url(api_url: str) -> None:
    """Validate that API URL is configured."""
    if not api_url:
        console.print("[red]Error: DECOMP_API_BASE environment variable is required[/red]")
        console.print("[dim]Set it to your decomp.me instance URL, e.g.:[/dim]")
        console.print("[dim]  export DECOMP_API_BASE=http://10.200.0.1[/dim]")
        raise typer.Exit(1)


@commit_app.command("apply")
def commit_apply(
    function_name: Annotated[str, typer.Argument(help="Name of the matched function")],
    scratch_slug: Annotated[str, typer.Argument(help="Decomp.me scratch slug")],
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
    create_pr: Annotated[
        bool, typer.Option("--pr", help="Create a PR after committing")
    ] = False,
    full_code: Annotated[
        bool, typer.Option("--full-code", help="Use full scratch code (including struct defs)")
    ] = False,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage (default: 95.0)")
    ] = 95.0,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Force commit even if below min-match threshold")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be changed without applying")
    ] = False,
):
    """Apply a matched function to the melee project.

    By default, extracts just the function body from the scratch code,
    discarding any helper struct definitions. Use --full-code to include
    the complete scratch code (useful when new types are needed).

    Use --min-match to adjust the minimum match percentage (default: 95%).
    Use --force to bypass the match check entirely (use with caution).
    Use --dry-run to preview changes and verify compilation without modifying files.
    """
    _require_api_url(api_url)
    from src.client import DecompMeAPIClient
    from src.commit import auto_detect_and_commit
    from src.commit.configure import get_file_path_from_function
    from src.commit.update import validate_function_code, _extract_function_from_code

    async def apply():
        async with DecompMeAPIClient(base_url=api_url) as client:
            scratch = await client.get_scratch(scratch_slug)

            # Calculate match percentage
            if scratch.max_score > 0:
                match_pct = (scratch.max_score - scratch.score) / scratch.max_score * 100
            else:
                match_pct = 100.0 if scratch.score == 0 else 0.0

            # Verify it meets the minimum match requirement
            if match_pct < min_match and not force:
                console.print(f"[red]Scratch is only {match_pct:.1f}% match (minimum: {min_match:.1f}%)[/red]")
                console.print("[dim]Use --force to bypass this check, or --min-match to adjust threshold[/dim]")
                raise typer.Exit(1)

            if scratch.score != 0:
                if force and match_pct < min_match:
                    console.print(f"[yellow]⚠ Forcing commit at {match_pct:.1f}% match (below {min_match:.1f}% threshold)[/yellow]")
                else:
                    console.print(f"[yellow]Note: Scratch is {match_pct:.1f}% match (not 100%)[/yellow]")

            # Dry-run mode: preview changes and verify compilation
            if dry_run:
                console.print("\n[bold cyan]DRY RUN MODE[/bold cyan] - No files will be modified\n")

                # Find the target file
                file_path = await get_file_path_from_function(function_name, melee_root)
                if not file_path:
                    console.print(f"[red]Could not find file containing function '{function_name}'[/red]")
                    raise typer.Exit(1)

                console.print(f"[bold]Target file:[/bold] src/{file_path}")

                # Process the code the same way the workflow would
                source_code = scratch.source_code.strip()
                if not full_code:
                    extracted = _extract_function_from_code(source_code, function_name)
                    if extracted:
                        source_code = extracted

                # Validate the code
                is_valid, msg = validate_function_code(source_code, function_name)
                if not is_valid:
                    console.print(f"[red]Code validation failed:[/red] {msg}")
                    raise typer.Exit(1)
                if msg:
                    console.print(f"[yellow]{msg}[/yellow]")
                else:
                    console.print("[green]✓ Code validation passed[/green]")

                # Show code preview
                console.print(f"\n[bold]Code to insert ({len(source_code)} chars):[/bold]")
                preview_lines = source_code.split('\n')
                if len(preview_lines) > 20:
                    for line in preview_lines[:10]:
                        console.print(f"  {line}")
                    console.print(f"  [dim]... ({len(preview_lines) - 20} more lines) ...[/dim]")
                    for line in preview_lines[-10:]:
                        console.print(f"  {line}")
                else:
                    for line in preview_lines:
                        console.print(f"  {line}")

                # Test compilation by temporarily applying and reverting
                console.print("\n[bold]Testing compilation...[/bold]")
                full_path = melee_root / "src" / file_path
                original_content = full_path.read_text(encoding='utf-8')

                try:
                    # Temporarily apply the change
                    from src.commit.update import update_source_file
                    success = await update_source_file(
                        file_path, function_name, source_code, melee_root,
                        extract_function_only=False  # Already extracted above
                    )
                    if not success:
                        console.print("[red]Failed to apply code (validation or insertion error)[/red]")
                        raise typer.Exit(1)

                    # Try to compile
                    # Run configure.py first
                    subprocess.run(
                        ["python", "configure.py"],
                        cwd=melee_root, capture_output=True
                    )
                    # Compile the object file
                    obj_path = f"build/GALE01/src/{file_path}".replace('.c', '.o')
                    result = subprocess.run(
                        ["ninja", obj_path],
                        cwd=melee_root, capture_output=True, text=True
                    )

                    if result.returncode == 0:
                        console.print("[green]✓ Compilation successful[/green]")
                    else:
                        console.print("[red]✗ Compilation failed:[/red]")
                        # Extract error lines
                        for line in result.stderr.split('\n'):
                            if 'Error:' in line or 'error:' in line.lower():
                                console.print(f"  {line}")
                        raise typer.Exit(1)

                finally:
                    # Always revert to original
                    full_path.write_text(original_content, encoding='utf-8')
                    console.print("[dim]Reverted test changes[/dim]")

                console.print("\n[green bold]Dry run complete - all checks passed![/green bold]")
                console.print("[dim]Run without --dry-run to apply changes[/dim]")
                return None, match_pct

            scratch_url = f"{api_url}/scratch/{scratch_slug}"
            pr_url = await auto_detect_and_commit(
                function_name=function_name,
                new_code=scratch.source_code,
                scratch_id=scratch_slug,
                scratch_url=scratch_url,
                melee_root=melee_root,
                author="agent",
                create_pull_request=create_pr,
                extract_function_only=not full_code,
            )
            return pr_url, match_pct

    pr_url, match_pct = asyncio.run(apply())

    if dry_run:
        return  # Already printed results

    console.print(f"[green]Applied {function_name}[/green]")

    # Auto-mark as completed with branch info
    branch = _get_current_branch(melee_root)
    completed = _load_completed()
    completed[function_name] = {
        "match_percent": match_pct,
        "scratch_slug": scratch_slug,
        "committed": True,
        "branch": branch,
        "notes": "committed via commit apply",
        "timestamp": time.time(),
    }
    _save_completed(completed)

    branch_info = f" on {branch}" if branch else ""
    console.print(f"[dim]Marked as completed{branch_info}[/dim]")

    if pr_url:
        console.print(f"\n[bold]PR created:[/bold] {pr_url}")


@commit_app.command("format")
def commit_format(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
):
    """Run clang-format on staged changes."""
    from src.commit import format_files

    success = asyncio.run(format_files(melee_root))

    if success:
        console.print("[green]Formatting applied[/green]")
    else:
        console.print("[red]Formatting failed[/red]")
        raise typer.Exit(1)


@commit_app.command("lint")
def commit_lint(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    fix: Annotated[
        bool, typer.Option("--fix", help="Remove malformed entries")
    ] = False,
):
    """Validate scratches.txt format and report issues."""
    scratches_path = melee_root / "config" / "GALE01" / "scratches.txt"

    if not scratches_path.exists():
        console.print(f"[red]scratches.txt not found at {scratches_path}[/red]")
        raise typer.Exit(1)

    content = scratches_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    # Valid entry pattern: FunctionName = PERCENT%:STATUS; // author:NAME id:SLUG
    valid_pattern = re.compile(
        r'^[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*(?:\d+(?:\.\d+)?%|OK):[A-Z0-9x_]+;\s*//'
        r'(?:\s*author:[a-zA-Z0-9_-]+)?'
        r'(?:\s*id:[a-zA-Z0-9]+)?'
    )

    # Malformed pattern: just function = slug (missing percentage/status)
    malformed_pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*[a-zA-Z0-9]{5}$')

    issues = []
    malformed_lines = []

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('//'):
            continue  # Skip empty lines and comments

        if malformed_pattern.match(line):
            issues.append((i, line, "Malformed: missing percentage/status (format: func = slug)"))
            malformed_lines.append(i)
        elif not valid_pattern.match(line):
            # Could be a valid older format or truly invalid
            if '=' in line and '//' in line:
                pass  # Likely valid older format
            elif '=' in line:
                issues.append((i, line, "Missing comment section (// author:... id:...)"))

    if issues:
        console.print(f"[yellow]Found {len(issues)} issue(s) in scratches.txt:[/yellow]\n")
        for line_num, line_content, issue in issues[:20]:  # Show first 20
            console.print(f"  Line {line_num}: {issue}")
            console.print(f"    [dim]{line_content[:80]}{'...' if len(line_content) > 80 else ''}[/dim]")

        if len(issues) > 20:
            console.print(f"\n  [dim]... and {len(issues) - 20} more issues[/dim]")

        if fix and malformed_lines:
            # Remove malformed lines
            new_lines = [l for i, l in enumerate(lines, 1) if i not in malformed_lines]
            scratches_path.write_text('\n'.join(new_lines), encoding='utf-8')
            console.print(f"\n[green]Removed {len(malformed_lines)} malformed entries[/green]")
        elif malformed_lines:
            console.print(f"\n[dim]Run with --fix to remove {len(malformed_lines)} malformed entries[/dim]")

        raise typer.Exit(1)
    else:
        console.print("[green]scratches.txt is valid[/green]")
