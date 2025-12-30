"""Commit commands - commit matched functions and create PRs."""

import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from ._common import console, DEFAULT_MELEE_ROOT, DECOMP_CONFIG_DIR, get_local_api_url, resolve_melee_root, AGENT_ID, db_upsert_function, get_source_file_from_claim
from .complete import _load_completed, _save_completed, _get_current_branch
from src.commit.diagnostics import (
    analyze_commit_error,
    check_header_sync,
    format_signature_mismatch,
    find_callers,
    format_caller_updates_needed,
)

commit_app = typer.Typer(help="Commit matched functions and create PRs")


@commit_app.command("apply")
def commit_apply(
    function_name: Annotated[str, typer.Argument(help="Name of the matched function")],
    scratch_slug: Annotated[str, typer.Argument(help="Decomp.me scratch slug")],
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-m", help="Path to melee submodule (auto-detects agent worktree if not specified)")
    ] = None,
    api_url: Annotated[
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")
    ] = None,
    create_pr: Annotated[
        bool, typer.Option("--pr", help="Create a PR after committing")
    ] = False,
    full_code: Annotated[
        bool, typer.Option("--full-code", help="Use full scratch code (including struct defs)")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be changed without applying")
    ] = False,
):
    """Apply a matched function to the melee project.

    By default, extracts just the function body from the scratch code,
    discarding any helper struct definitions. Use --full-code to include
    the complete scratch code (useful when new types are needed).

    Use --dry-run to preview changes and verify compilation without modifying files.

    If --melee-root is not specified, automatically uses the agent's worktree
    to keep work isolated from other parallel agents.
    """
    api_url = api_url or get_local_api_url()

    # Look up source file from claim to use the correct subdirectory worktree
    source_file = get_source_file_from_claim(function_name)
    melee_root = resolve_melee_root(melee_root, target_file=source_file)

    # Warn if committing to main repo instead of worktree
    if AGENT_ID and source_file and "melee-worktrees" not in str(melee_root):
        console.print(f"[yellow]Warning: Committing to main melee repo, not subdirectory worktree[/yellow]")
        console.print(f"[dim]Expected worktree for: {source_file}[/dim]")
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

            console.print(f"Applying {function_name} ({match_pct:.1f}% match)")

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

                # Check header signature sync
                sig_check = check_header_sync(source_code, function_name, melee_root, file_path)
                if sig_check:
                    if sig_check["match"]:
                        console.print("[green]✓ Header signature matches[/green]")
                    else:
                        console.print(format_signature_mismatch(sig_check, function_name))

                # Show code preview (markup=False to preserve brackets like [t0])
                console.print(f"\n[bold]Code to insert ({len(source_code)} chars):[/bold]")
                preview_lines = source_code.split('\n')
                if len(preview_lines) > 20:
                    for line in preview_lines[:10]:
                        console.print(f"  {line}", markup=False)
                    console.print(f"  [dim]... ({len(preview_lines) - 20} more lines) ...[/dim]")
                    for line in preview_lines[-10:]:
                        console.print(f"  {line}", markup=False)
                else:
                    for line in preview_lines:
                        console.print(f"  {line}", markup=False)

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
                        console.print(f"[yellow]If the function stub is missing, run: melee-agent stub add {function_name}[/yellow]")
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
                        # Show diagnostics with suggestions
                        full_output = result.stderr + result.stdout
                        diagnostic = analyze_commit_error(
                            full_output,
                            file_path,
                            melee_root=melee_root,
                            function_name=function_name,
                            source_code=source_code,
                        )
                        console.print(diagnostic)
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

    # Also write to state database (non-blocking)
    db_upsert_function(
        function_name,
        match_percent=match_pct,
        local_scratch_slug=scratch_slug,
        is_committed=True,
        status='committed',
        branch=branch,
        worktree_path=str(melee_root),
        pr_url=pr_url,
        notes="committed via commit apply",
    )

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


@commit_app.command("check-callers")
def commit_check_callers(
    function_name: Annotated[str, typer.Argument(help="Name of the function to find callers for")],
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-m", help="Path to melee submodule (auto-detects agent worktree)")
    ] = None,
):
    """Find all callers of a function in the codebase.

    Use this when you've changed a function's signature and need to update callers.
    Shows file paths and line numbers for each call site.

    Example:
        melee-agent commit check-callers lbBgFlash_800205F0
    """
    melee_root = resolve_melee_root(melee_root)

    console.print(f"\n[bold]Finding callers of {function_name}...[/bold]\n")

    callers = find_callers(function_name, melee_root)

    if not callers:
        console.print(f"[green]No callers found for {function_name}[/green]")
        console.print("[dim]This function may not be called from any .c files, or only from headers.[/dim]")
        return

    console.print(f"[cyan]Found {len(callers)} call site(s):[/cyan]\n")

    from rich.table import Table
    table = Table(show_header=True, header_style="bold")
    table.add_column("File", style="cyan")
    table.add_column("Line", justify="right")
    table.add_column("Code", style="dim")

    for caller in callers:
        rel_path = caller["file"]
        if "/melee/" in rel_path:
            rel_path = "src/melee/" + rel_path.split("/melee/", 1)[1]

        content = caller["content"]
        if len(content) > 60:
            content = content[:57] + "..."

        table.add_row(rel_path, str(caller["line"]), content)

    console.print(table)
    console.print(f"\n[dim]Use grep for more context: grep -rn '{function_name}(' src/melee/[/dim]")
