"""Workflow commands - high-level workflow operations that combine multiple steps.

This module provides convenience commands that combine common multi-step operations
to prevent agents from accidentally skipping critical steps.
"""

import asyncio
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from ._common import console, DEFAULT_MELEE_ROOT, get_local_api_url, resolve_melee_root, AGENT_ID, get_source_file_from_claim, db_upsert_function
from .complete import _load_completed, _save_completed, _get_current_branch

workflow_app = typer.Typer(help="High-level workflow commands (recommended)")


MAX_BROKEN_BUILDS_PER_WORKTREE = 3  # Block new --force commits after this many


@workflow_app.command("finish")
def workflow_finish(
    function_name: Annotated[str, typer.Argument(help="Name of the matched function")],
    scratch_slug: Annotated[str, typer.Argument(help="Decomp.me scratch slug")],
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-m", help="Path to melee submodule (auto-detects agent worktree)")
    ] = None,
    api_url: Annotated[
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")
    ] = None,
    full_code: Annotated[
        bool, typer.Option("--full-code", help="Use full scratch code (including struct defs)")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Test without committing (runs verification only)")
    ] = False,
    notes: Annotated[
        Optional[str], typer.Option("--notes", help="Additional notes to record")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Skip build validation (requires --diagnosis)")
    ] = False,
    diagnosis: Annotated[
        Optional[str], typer.Option("--diagnosis", help="Required with --force: explain why build is broken")
    ] = None,
):
    """Finish a matched function: commit to repo AND record as completed.

    This command combines 'commit apply' and 'complete mark --committed' into a single
    step to prevent accidentally forgetting to commit. Use this instead of running
    the two commands separately.

    Example workflow:
        1. Improve a function's match in decomp.me
        2. Run: melee-agent workflow finish <function> <slug>

    This will:
        - Run a dry-run to check for compilation errors
        - Apply the code to the melee repo
        - Record the function as committed in your tracking file
        - Release any claims on the function

    Use --dry-run to verify everything would work without actually committing.

    Use --force --diagnosis "..." to skip build validation when the build is broken
    due to header mismatches or other issues that require fixes outside this function.
    The diagnosis is stored and visible in 'state status <func>'.

    Automatically uses the agent's worktree to keep work isolated from other
    parallel agents. Use --melee-root to override.
    """
    # Validate --force requires --diagnosis
    if force and not diagnosis:
        console.print("[red]Error: --force requires --diagnosis to explain why build is broken[/red]")
        console.print("[dim]Example: --force --diagnosis 'Header has UNK_RET but function returns void'[/dim]")
        raise typer.Exit(1)
    api_url = api_url or get_local_api_url()

    # Look up source file from claim to use the correct subdirectory worktree
    source_file = get_source_file_from_claim(function_name)
    claim_source = "claim"

    # If claim is missing/expired, try to infer source file from function name
    if not source_file:
        try:
            from src.extractor import FunctionExtractor
            extractor = FunctionExtractor(DEFAULT_MELEE_ROOT)
            func_info = extractor.extract_function(function_name)
            if func_info and func_info.file_path:
                source_file = func_info.file_path
                claim_source = "auto-detected"
                console.print(f"[yellow]Note: Claim expired or missing, auto-detected source file[/yellow]")
                console.print(f"[dim]Source: {source_file}[/dim]")
        except Exception:
            pass  # Fall through to melee_root resolution

    melee_root = resolve_melee_root(melee_root, target_file=source_file)

    # Show worktree state for clarity (helps after context resets)
    if source_file:
        from .worktree_utils import get_subdirectory_key, get_subdirectory_worktree_path
        subdir_key = get_subdirectory_key(source_file)
        expected_worktree = get_subdirectory_worktree_path(subdir_key)
        console.print(f"[dim]Worktree: {melee_root}[/dim]")
        console.print(f"[dim]Source file: {source_file} ({claim_source})[/dim]")

        # Warn if not using the expected worktree
        if str(melee_root) != str(expected_worktree) and "melee-worktrees" not in str(melee_root):
            console.print(f"[yellow]Warning: Working in main repo, not subdirectory worktree[/yellow]")
            console.print(f"[yellow]Expected: {expected_worktree}[/yellow]")
            console.print(f"[dim]Tip: Run 'cd {expected_worktree}' to work in the correct worktree[/dim]")
    elif not source_file:
        console.print(f"[yellow]Warning: Could not determine source file for {function_name}[/yellow]")
        console.print(f"[dim]Using: {melee_root}[/dim]")
        console.print(f"[dim]Tip: Re-claim the function to ensure correct worktree routing[/dim]")

    # Check worktree health when using --force
    if force:
        from src.db import get_db
        db = get_db()
        broken_count, broken_funcs = db.get_worktree_broken_count(str(melee_root))
        if broken_count >= MAX_BROKEN_BUILDS_PER_WORKTREE:
            console.print(f"[red]Cannot use --force: worktree already has {broken_count} broken builds[/red]")
            console.print(f"[dim]Functions needing fixes: {', '.join(broken_funcs)}[/dim]")
            console.print(f"\n[yellow]Run /decomp-fixup to fix these before adding more broken commits.[/yellow]")
            raise typer.Exit(1)

    from src.client import DecompMeAPIClient
    from src.commit import auto_detect_and_commit
    from src.commit.configure import get_file_path_from_function
    from src.commit.update import validate_function_code, _extract_function_from_code
    from src.commit.diagnostics import (
        analyze_commit_error,
        check_header_sync,
        format_signature_mismatch,
        check_callers_need_update,
        format_caller_updates_needed,
        get_header_fix_suggestion,
    )
    import subprocess

    async def finish():
        async with DecompMeAPIClient(base_url=api_url) as client:
            scratch = await client.get_scratch(scratch_slug)

            # Calculate match percentage
            if scratch.max_score > 0:
                match_pct = (scratch.max_score - scratch.score) / scratch.max_score * 100
            else:
                match_pct = 100.0 if scratch.score == 0 else 0.0

            console.print(f"\n[bold]Finishing {function_name}[/bold]")
            console.print(f"Scratch: {scratch_slug} ({match_pct:.1f}% match)")

            # Step 1: Find target file
            console.print("\n[bold]Step 1:[/bold] Locating target file...")
            file_path = await get_file_path_from_function(function_name, melee_root)
            if not file_path:
                console.print(f"[red]Could not find file containing '{function_name}'[/red]")
                raise typer.Exit(1)
            console.print(f"  Target: src/{file_path}")

            # Step 2: Validate code
            console.print("\n[bold]Step 2:[/bold] Validating code...")
            source_code = scratch.source_code.strip()
            if not full_code:
                extracted = _extract_function_from_code(source_code, function_name)
                if extracted:
                    source_code = extracted

            is_valid, msg = validate_function_code(source_code, function_name)
            if not is_valid:
                console.print(f"  [red]Validation failed:[/red] {msg}")
                raise typer.Exit(1)
            console.print("  [green]Code validation passed[/green]")

            # Check header sync
            sig_check = check_header_sync(source_code, function_name, melee_root, file_path)
            if sig_check and not sig_check["match"]:
                console.print(format_signature_mismatch(sig_check, function_name))

                # Show exact fix suggestion
                fix_suggestion = get_header_fix_suggestion(sig_check)
                if fix_suggestion:
                    console.print(fix_suggestion)

                # Check if callers need updating (when adding parameters)
                old_params = sig_check.get("header", "").count(",") + 1 if "(" in sig_check.get("header", "") else 0
                new_params = sig_check.get("scratch", "").count(",") + 1 if "(" in sig_check.get("scratch", "") else 0

                # Handle void case
                if "(void)" in sig_check.get("header", "") or "()" in sig_check.get("header", ""):
                    old_params = 0
                if "(void)" in sig_check.get("scratch", "") or "()" in sig_check.get("scratch", ""):
                    new_params = 0

                if new_params > old_params:
                    callers_needing_update = check_callers_need_update(
                        function_name, old_params, new_params, melee_root
                    )
                    if callers_needing_update:
                        console.print(format_caller_updates_needed(callers_needing_update, function_name))

                console.print("\n[red]Header signature mismatch - fix header and callers first[/red]")
                raise typer.Exit(1)

            # Step 3: Test compilation
            build_passed = True  # Track for later
            if force:
                console.print("\n[bold]Step 3:[/bold] Skipping compilation (--force)...")
                console.print(f"  [yellow]Build will be marked as broken[/yellow]")
                console.print(f"  [dim]Diagnosis: {diagnosis}[/dim]")
                build_passed = False
            else:
                console.print("\n[bold]Step 3:[/bold] Testing compilation...")

            full_path = melee_root / "src" / file_path
            original_content = full_path.read_text(encoding='utf-8')

            try:
                from src.commit.update import update_source_file
                success = await update_source_file(
                    file_path, function_name, source_code, melee_root,
                    extract_function_only=False
                )
                if not success:
                    console.print("  [red]Failed to apply code[/red]")
                    raise typer.Exit(1)

                if not force:
                    # Configure and compile
                    subprocess.run(["python", "configure.py"], cwd=melee_root, capture_output=True)
                    obj_path = f"build/GALE01/src/{file_path}".replace('.c', '.o')
                    result = subprocess.run(
                        ["ninja", obj_path], cwd=melee_root, capture_output=True, text=True
                    )

                    if result.returncode != 0:
                        console.print("  [red]Compilation failed:[/red]")
                        full_output = result.stderr + result.stdout
                        diagnostic = analyze_commit_error(
                            full_output,
                            file_path,
                            melee_root=melee_root,
                            function_name=function_name,
                            source_code=source_code,
                        )
                        console.print(diagnostic)
                        console.print("\n[yellow]Tip: Use --force --diagnosis '...' to commit anyway[/yellow]")
                        raise typer.Exit(1)

                    console.print("  [green]Compilation successful[/green]")

            finally:
                # Revert for dry-run, keep for actual commit
                if dry_run:
                    full_path.write_text(original_content, encoding='utf-8')

            if dry_run:
                console.print("\n[bold cyan]DRY RUN COMPLETE[/bold cyan]")
                if force:
                    console.print("[yellow]Would commit with broken build[/yellow]")
                else:
                    console.print("[green]All checks passed - ready to commit[/green]")
                console.print("[dim]Run without --dry-run to apply changes[/dim]")
                return match_pct, None, build_passed

            # Step 4: Commit (already applied above, just need git commit)
            console.print("\n[bold]Step 4:[/bold] Committing to git...")

            # Redo the apply properly through the workflow
            full_path.write_text(original_content, encoding='utf-8')  # Revert first
            scratch_url = f"{api_url}/scratch/{scratch_slug}"
            pr_url = await auto_detect_and_commit(
                function_name=function_name,
                new_code=scratch.source_code,
                scratch_id=scratch_slug,
                scratch_url=scratch_url,
                melee_root=melee_root,
                create_pull_request=False,
                extract_function_only=not full_code,
            )
            console.print("  [green]Committed to repository[/green]")

            return match_pct, pr_url, build_passed

    match_pct, pr_url, build_passed = asyncio.run(finish())

    if dry_run:
        return

    # Step 5: Record completion
    console.print("\n[bold]Step 5:[/bold] Recording completion...")
    branch = _get_current_branch(melee_root)
    completed = _load_completed()
    completed[function_name] = {
        "match_percent": match_pct,
        "scratch_slug": scratch_slug,
        "committed": True,
        "branch": branch,
        "notes": notes or "completed via workflow finish",
        "timestamp": time.time(),
    }
    _save_completed(completed)

    # Also update state database with committed status
    db_upsert_function(
        function_name,
        match_percent=match_pct,
        local_scratch_slug=scratch_slug,
        is_committed=True,
        status='committed_needs_fix' if not build_passed else 'committed',
        build_status='broken' if not build_passed else 'passing',
        build_diagnosis=diagnosis if not build_passed else None,
        branch=branch,
        worktree_path=str(melee_root),
        notes=notes or "completed via workflow finish",
    )
    console.print(f"  Recorded as committed on {branch}")

    # Release claim
    from .claim import _release_claim
    _release_claim(function_name)

    # Summary
    console.print("\n" + "=" * 50)
    if build_passed:
        console.print(f"[bold green]Successfully finished {function_name}![/bold green]")
        console.print(f"  Match: {match_pct:.1f}%")
        console.print(f"  Branch: {branch}")
        console.print(f"  Status: [green]Committed[/green]")
    else:
        console.print(f"[bold yellow]Committed {function_name} with broken build[/bold yellow]")
        console.print(f"  Match: {match_pct:.1f}%")
        console.print(f"  Branch: {branch}")
        console.print(f"  Status: [yellow]Committed (needs fix)[/yellow]")
        console.print(f"  Diagnosis: {diagnosis}")
        console.print(f"\n[dim]Run /decomp-fixup to resolve build issues[/dim]")
    console.print("=" * 50)


@workflow_app.command("status")
def workflow_status(
    function_name: Annotated[Optional[str], typer.Argument(help="Function name (optional)")] = None,
):
    """Check the workflow status of a function or show uncommitted work.

    Without arguments, shows all functions that are matched but NOT committed.
    With a function name, shows the detailed status of that function.
    """
    completed = _load_completed()

    if function_name:
        # Show specific function status
        if function_name not in completed:
            console.print(f"[yellow]Function '{function_name}' not found in completed list[/yellow]")
            console.print("[dim]Use 'melee-agent complete list' to see all completed functions[/dim]")
            return

        info = completed[function_name]
        console.print(f"\n[bold]{function_name}[/bold]")
        console.print(f"  Match: {info.get('match_percent', 0):.1f}%")
        console.print(f"  Scratch: {info.get('scratch_slug', '?')}")
        console.print(f"  Branch: {info.get('branch', '-')}")
        console.print(f"  Notes: {info.get('notes', '-')}")

        if info.get("committed"):
            console.print(f"  Status: [green]Committed[/green]")
        else:
            console.print(f"  Status: [red]NOT COMMITTED[/red]")
            console.print(f"\n[yellow]This function is recorded but NOT in the repository![/yellow]")
            console.print(f"Run: [cyan]melee-agent workflow finish {function_name} {info.get('scratch_slug', '<slug>')}[/cyan]")
        return

    # Show all uncommitted work
    uncommitted = {
        name: info for name, info in completed.items()
        if not info.get("committed") and info.get("match_percent", 0) >= 95.0
    }

    if not uncommitted:
        console.print("[green]No uncommitted work found![/green]")
        console.print("[dim]All matched functions (95%+) have been committed.[/dim]")
        return

    console.print(f"\n[bold red]WARNING: {len(uncommitted)} functions matched but NOT committed![/bold red]\n")

    from rich.table import Table
    table = Table(title="Uncommitted Functions (95%+)")
    table.add_column("Function", style="cyan")
    table.add_column("Match %", justify="right")
    table.add_column("Scratch")
    table.add_column("Action")

    for name, info in sorted(uncommitted.items(), key=lambda x: -x[1].get("match_percent", 0)):
        slug = info.get("scratch_slug", "?")
        table.add_row(
            name,
            f"{info.get('match_percent', 0):.1f}%",
            slug,
            f"[dim]workflow finish {name} {slug}[/dim]",
        )

    console.print(table)
    console.print("\n[yellow]These functions need to be committed to save your work![/yellow]")
