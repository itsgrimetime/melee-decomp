"""Worktree commands - manage subdirectory worktrees and batch commits."""

import subprocess
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional, List

import typer
from rich.table import Table

from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
    MELEE_WORKTREES_DIR,
    AGENT_ID,
    db_lock_subdirectory,
    db_unlock_subdirectory,
    db_get_subdirectory_lock,
    get_subdirectory_worktree_path,
)
from src.db import get_db


worktree_app = typer.Typer(help="Manage subdirectory worktrees and batch commits")


def _run_git(args: List[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _get_worktree_info(melee_root: Path) -> List[dict]:
    """Get information about all subdirectory worktrees."""
    if not MELEE_WORKTREES_DIR.exists():
        return []

    worktrees = []
    for wt_path in sorted(MELEE_WORKTREES_DIR.iterdir()):
        if not wt_path.is_dir():
            continue

        name = wt_path.name
        # Only include subdirectory worktrees (prefixed with "dir-")
        if not name.startswith("dir-"):
            continue

        subdir_key = name[4:]  # Remove "dir-" prefix

        # Get the actual branch name from the worktree
        ret, branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], wt_path)
        if ret != 0 or not branch:
            branch = f"subdirs/{subdir_key}"  # Fallback

        # Get commits ahead of upstream/master
        ret, out, _ = _run_git(
            ["rev-list", "--count", f"upstream/master..{branch}"],
            melee_root
        )
        commits_ahead = int(out) if ret == 0 and out.isdigit() else 0

        # Get commits behind upstream/master
        ret, out, _ = _run_git(
            ["rev-list", "--count", f"{branch}..upstream/master"],
            melee_root
        )
        commits_behind = int(out) if ret == 0 and out.isdigit() else 0

        # Get commit subjects if any
        commit_subjects = []
        if commits_ahead > 0:
            ret, out, _ = _run_git(
                ["log", "--oneline", f"upstream/master..{branch}"],
                melee_root
            )
            if ret == 0 and out:
                commit_subjects = out.split('\n')

        # Get last commit date on branch
        ret, out, _ = _run_git(
            ["log", "-1", "--format=%ci", branch],
            melee_root
        )
        last_commit_date = None
        if ret == 0 and out:
            try:
                last_commit_date = datetime.fromisoformat(out.replace(' ', 'T').rsplit('-', 1)[0])
            except ValueError:
                pass

        # Check for uncommitted changes in worktree
        ret, out, _ = _run_git(["status", "--porcelain"], wt_path)
        has_uncommitted = bool(out) if ret == 0 else False

        # Get lock status from database
        lock_info = db_get_subdirectory_lock(subdir_key)

        worktrees.append({
            "name": name,
            "subdir_key": subdir_key,
            "path": wt_path,
            "branch": branch,
            "commits_ahead": commits_ahead,
            "commits_behind": commits_behind,
            "commit_subjects": commit_subjects,
            "last_commit_date": last_commit_date,
            "has_uncommitted": has_uncommitted,
            "locked_by": lock_info.get("locked_by_agent") if lock_info else None,
        })

    return worktrees


@worktree_app.command("list")
def worktree_list(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-r", help="Path to melee project root")
    ] = DEFAULT_MELEE_ROOT,
    show_commits: Annotated[
        bool, typer.Option("--commits", "-c", help="Show individual commit messages")
    ] = False,
):
    """List all subdirectory worktrees with their status.

    Subdirectory worktrees are named dir-{subdir_key} and contain commits
    for a specific source subdirectory (e.g., dir-ft-chara-ftFox for Fox character files).
    """
    worktrees = _get_worktree_info(melee_root)

    if not worktrees:
        console.print("[yellow]No subdirectory worktrees found[/yellow]")
        console.print("[dim]Subdirectory worktrees are created when you claim functions.[/dim]")
        return

    table = Table(title="Subdirectory Worktrees")
    table.add_column("Subdirectory", style="cyan")
    table.add_column("Ahead", justify="right")
    table.add_column("Behind", justify="right")
    table.add_column("Last Activity", style="dim")
    table.add_column("Locked By", style="yellow")
    table.add_column("Status")

    total_pending = 0
    total_behind = 0
    for wt in worktrees:
        # Format last activity
        if wt["last_commit_date"]:
            age = datetime.now() - wt["last_commit_date"]
            if age < timedelta(hours=1):
                age_str = f"{int(age.total_seconds() / 60)}m ago"
            elif age < timedelta(days=1):
                age_str = f"{int(age.total_seconds() / 3600)}h ago"
            else:
                age_str = f"{age.days}d ago"
        else:
            age_str = "unknown"

        # Format status
        status_parts = []
        if wt["commits_ahead"] > 0:
            status_parts.append(f"[green]{wt['commits_ahead']} pending[/green]")
            total_pending += wt["commits_ahead"]
        else:
            status_parts.append("[dim]clean[/dim]")
        total_behind += wt["commits_behind"]
        if wt["has_uncommitted"]:
            status_parts.append("[yellow]uncommitted[/yellow]")

        # Format ahead/behind
        ahead_str = f"[green]{wt['commits_ahead']}[/green]" if wt["commits_ahead"] > 0 else "[dim]0[/dim]"
        behind_str = f"[red]{wt['commits_behind']}[/red]" if wt["commits_behind"] > 0 else "[dim]0[/dim]"

        table.add_row(
            wt["subdir_key"],
            ahead_str,
            behind_str,
            age_str,
            wt.get("locked_by", "") or "[dim]unlocked[/dim]",
            " ".join(status_parts),
        )

        # Show commits if requested
        if show_commits and wt["commit_subjects"]:
            for subject in wt["commit_subjects"]:
                table.add_row("", "", "", "", "", f"  [dim]{subject}[/dim]")

    console.print(table)
    summary = f"\nTotal: {len(worktrees)} subdirectory worktrees, {total_pending} pending commits"
    if total_behind > 0:
        summary += f", [red]{total_behind} behind upstream[/red]"
    console.print(summary)


@worktree_app.command("prune")
def worktree_prune(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-r", help="Path to melee project root")
    ] = DEFAULT_MELEE_ROOT,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Show what would be removed")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Remove even if has uncommitted changes")
    ] = False,
    max_age_days: Annotated[
        int, typer.Option("--max-age", help="Only prune worktrees older than N days")
    ] = 0,
):
    """Remove worktrees that have no unmerged commits.

    Only removes worktrees where the branch is fully merged to master.
    Use --force to also remove worktrees with uncommitted changes.
    """
    worktrees = _get_worktree_info(melee_root)

    to_remove = []
    for wt in worktrees:
        # Skip if has pending commits
        if wt["commits_ahead"] > 0:
            continue

        # Skip if has uncommitted changes (unless forced)
        if wt["has_uncommitted"] and not force:
            continue

        # Skip if too recent
        if max_age_days > 0 and wt["last_commit_date"]:
            age = datetime.now() - wt["last_commit_date"]
            if age < timedelta(days=max_age_days):
                continue

        to_remove.append(wt)

    if not to_remove:
        console.print("[green]No worktrees to prune[/green]")
        return

    if dry_run:
        console.print("[yellow]DRY RUN - would remove:[/yellow]")
        for wt in to_remove:
            status = "[yellow]has uncommitted[/yellow]" if wt["has_uncommitted"] else ""
            console.print(f"  {wt['subdir_key']} {status}")
        console.print(f"\nWould remove {len(to_remove)} worktrees")
        return

    # Actually remove
    removed = 0
    for wt in to_remove:
        console.print(f"Removing {wt['subdir_key']}...")

        # Remove worktree
        ret, _, err = _run_git(["worktree", "remove", str(wt["path"])], melee_root)
        if ret != 0:
            # Try force remove
            ret, _, err = _run_git(["worktree", "remove", "--force", str(wt["path"])], melee_root)
            if ret != 0:
                console.print(f"  [red]Failed: {err}[/red]")
                continue

        # Delete the branch
        ret, _, err = _run_git(["branch", "-d", wt["branch"]], melee_root)
        if ret != 0:
            # Try force delete
            _run_git(["branch", "-D", wt["branch"]], melee_root)

        removed += 1
        console.print(f"  [green]Removed[/green]")

    console.print(f"\n[green]Pruned {removed} worktrees[/green]")


def _is_function_match_commit(subject: str) -> bool:
    """Determine if a commit is a function match (vs a fix-up commit).

    Function match commits typically:
    - Start with "Match " or "Implement "
    - Contain function names with percentage (e.g., "fn_80123456: 100%")
    - Add new decompiled functions

    Fix-up commits typically:
    - Fix build issues, headers, signatures
    - Update types, includes, NonMatching annotations
    - Are smaller maintenance commits
    """
    import re
    subject_lower = subject.lower()

    # Fix-up commit patterns (these are NOT function matches)
    fixup_patterns = [
        r'\bfix\b',
        r'\bupdate\b.*\b(header|signature|type|include)\b',
        r'\badd\b.*\b(include|header)\b',
        r'\brevert\b',
        r'\bnonmatching\b',
        r'\bbuild\b',
        r'\bformat\b',
        r'\bcleanup\b',
        r'\brefactor\b',
        r'\brename\b',
        r'\bremove\b.*\bunused\b',
    ]

    for pattern in fixup_patterns:
        if re.search(pattern, subject_lower):
            return False

    # Function match patterns
    match_patterns = [
        r'^match\b',
        r'^implement\b',
        r'\b\d+(\.\d+)?%',  # Contains percentage
        r'\b(fn|ft|gr|lb|gm|it|if|mp|vi)_[0-9A-Fa-f]{8}\b',  # Contains function address
    ]

    for pattern in match_patterns:
        if re.search(pattern, subject_lower):
            return True

    # Default: assume it's a function match if we can't tell
    return True


@worktree_app.command("collect")
def worktree_collect(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-r", help="Path to melee project root")
    ] = DEFAULT_MELEE_ROOT,
    branch_name: Annotated[
        Optional[str], typer.Option("--branch", "-b", help="Name for the collection branch")
    ] = None,
    create_pr: Annotated[
        bool, typer.Option("--create-pr", help="Create a GitHub PR after collecting")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Show what would be collected")
    ] = False,
    max_functions: Annotated[
        int, typer.Option("--max-functions", "-m", help="Max function match commits (fix-ups don't count)")
    ] = 7,
    no_limit: Annotated[
        bool, typer.Option("--no-limit", help="Disable the function match limit")
    ] = False,
):
    """Collect all pending subdirectory commits into a single branch.

    Cherry-picks commits in subdirectory order to minimize conflicts.

    By default, limits to 7 function match commits per PR to keep reviews manageable.
    Fix-up commits (build fixes, header updates, etc.) don't count toward this limit.
    Use --no-limit to collect all pending commits, or --max-functions to adjust.
    """
    worktrees = _get_worktree_info(melee_root)

    # Find worktrees with pending commits
    pending = [wt for wt in worktrees if wt["commits_ahead"] > 0]

    if not pending:
        console.print("[yellow]No pending commits in subdirectory worktrees[/yellow]")
        return

    # Sort by subdirectory key for consistent ordering
    pending.sort(key=lambda wt: wt["subdir_key"])

    # Classify and collect commits
    all_commits = []
    function_match_count = 0
    fixup_count = 0

    for wt in pending:
        for subject in wt["commit_subjects"]:
            commit_hash = subject.split()[0]
            # Get just the subject without hash for classification
            subject_text = " ".join(subject.split()[1:]) if len(subject.split()) > 1 else subject
            is_match = _is_function_match_commit(subject_text)

            all_commits.append({
                "hash": commit_hash,
                "subject": subject,
                "subject_text": subject_text,
                "branch": wt["branch"],
                "subdir_key": wt["subdir_key"],
                "is_function_match": is_match,
            })

            if is_match:
                function_match_count += 1
            else:
                fixup_count += 1

    # Apply limit if not disabled
    limit_active = not no_limit and function_match_count > max_functions
    commits_to_collect = []
    commits_deferred = []
    collected_function_matches = 0

    for commit in all_commits:
        if commit["is_function_match"]:
            if no_limit or collected_function_matches < max_functions:
                commits_to_collect.append(commit)
                collected_function_matches += 1
            else:
                commits_deferred.append(commit)
        else:
            # Fix-up commits always included (if their function match is included or no limit)
            # For simplicity, include all fix-ups - they're usually small
            commits_to_collect.append(commit)

    # Show what we'll collect
    console.print(f"Found {len(all_commits)} commits ({function_match_count} function matches, {fixup_count} fix-ups)")
    if limit_active:
        console.print(f"[yellow]Limiting to {max_functions} function matches (use --no-limit to override)[/yellow]")
    console.print()

    # Group by subdirectory for display
    by_subdir_collect = defaultdict(list)
    for c in commits_to_collect:
        by_subdir_collect[c["subdir_key"]].append(c)

    by_subdir_defer = defaultdict(list)
    for c in commits_deferred:
        by_subdir_defer[c["subdir_key"]].append(c)

    console.print("[bold]Commits to collect:[/bold]")
    for subdir_key in sorted(by_subdir_collect.keys()):
        console.print(f"  [cyan]{subdir_key}[/cyan]:")
        for c in by_subdir_collect[subdir_key]:
            tag = "[green]match[/green]" if c["is_function_match"] else "[blue]fixup[/blue]"
            console.print(f"    {c['hash'][:8]} {tag} {c['subject_text'][:60]}")
    console.print()

    if commits_deferred:
        console.print("[bold yellow]Commits deferred (will remain on worktree branches):[/bold yellow]")
        for subdir_key in sorted(by_subdir_defer.keys()):
            console.print(f"  [cyan]{subdir_key}[/cyan]:")
            for c in by_subdir_defer[subdir_key]:
                console.print(f"    {c['hash'][:8]} {c['subject_text'][:60]}")
        console.print()

    if dry_run:
        console.print("[yellow]DRY RUN - no changes made[/yellow]")
        console.print(f"Would collect {len(commits_to_collect)} commits, defer {len(commits_deferred)}")
        return

    if not commits_to_collect:
        console.print("[yellow]No commits to collect after applying limits[/yellow]")
        return

    # Generate branch name if not provided
    if not branch_name:
        date_str = datetime.now().strftime("%Y%m%d")
        branch_name = f"batch/{date_str}"

    # Check if branch already exists
    ret, _, _ = _run_git(["rev-parse", "--verify", branch_name], melee_root)
    if ret == 0:
        console.print(f"[red]Branch {branch_name} already exists. Use --branch to specify a different name.[/red]")
        raise typer.Exit(1)

    # Create new branch from upstream/master
    console.print(f"Creating branch [cyan]{branch_name}[/cyan] from upstream/master...")
    ret, _, err = _run_git(["checkout", "-b", branch_name, "upstream/master"], melee_root)
    if ret != 0:
        console.print(f"[red]Failed to create branch: {err}[/red]")
        raise typer.Exit(1)

    # Cherry-pick commits in order (oldest first within each subdirectory)
    # Group by subdirectory, then reverse within each group
    by_subdir = defaultdict(list)
    for commit in commits_to_collect:
        by_subdir[commit["subdir_key"]].append(commit)

    success_count = 0
    function_matches_collected = 0
    failed = []
    for subdir_key in sorted(by_subdir.keys()):
        commits = by_subdir[subdir_key]
        commits.reverse()  # Oldest first
        for commit in commits:
            ret, _, err = _run_git(["cherry-pick", commit["hash"]], melee_root)
            if ret != 0:
                _run_git(["cherry-pick", "--abort"], melee_root)
                failed.append((commit["hash"], commit["branch"], subdir_key, err))
                console.print(f"  [red]Failed to cherry-pick {commit['hash'][:8]}[/red]")
            else:
                success_count += 1
                if commit["is_function_match"]:
                    function_matches_collected += 1
                tag = "[green]match[/green]" if commit["is_function_match"] else "[blue]fixup[/blue]"
                console.print(f"  [green]✓[/green] {commit['hash'][:8]} {tag} ({subdir_key})")

    console.print(f"\n[green]Collected {success_count}/{len(commits_to_collect)} commits onto {branch_name}[/green]")
    console.print(f"  ({function_matches_collected} function matches, {success_count - function_matches_collected} fix-ups)")
    if commits_deferred:
        console.print(f"  [yellow]{len(commits_deferred)} commits deferred for next PR[/yellow]")

    if failed:
        console.print(f"\n[yellow]Failed commits ({len(failed)}):[/yellow]")
        for commit_hash, source_branch, subdir_key, err in failed:
            console.print(f"  {commit_hash} from {subdir_key}")
            console.print(f"    [dim]{err}[/dim]")

    # Create PR if requested
    if create_pr and success_count > 0:
        console.print("\nCreating pull request...")

        # Push the branch
        ret, _, err = _run_git(["push", "-u", "origin", branch_name], melee_root)
        if ret != 0:
            console.print(f"[red]Failed to push branch: {err}[/red]")
            console.print("You can push manually and create a PR")
            return

        # Create PR with gh
        pr_body = f"Batch collection of {function_matches_collected} matched functions"
        if success_count > function_matches_collected:
            pr_body += f" + {success_count - function_matches_collected} fix-up commits"
        pr_body += ".\n\n"

        pr_body += "## Commits by Subdirectory\n"
        for subdir_key in sorted(by_subdir_collect.keys()):
            pr_body += f"\n### {subdir_key}\n"
            for c in by_subdir_collect[subdir_key]:
                tag = "🎯" if c["is_function_match"] else "🔧"
                pr_body += f"- {tag} {c['subject_text']}\n"

        if commits_deferred:
            pr_body += f"\n---\n*{len(commits_deferred)} additional commits deferred for next PR*\n"

        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--title", f"Match {function_matches_collected} functions",
                "--body", pr_body,
                "--base", "master",
            ],
            cwd=melee_root,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            console.print(f"[green]PR created: {result.stdout.strip()}[/green]")

            # Only reset pending commits for subdirectories that had ALL their commits collected
            # (i.e., no deferred commits from that subdirectory)
            deferred_subdirs = set(c["subdir_key"] for c in commits_deferred)
            db = get_db()
            for subdir_key in by_subdir_collect.keys():
                if subdir_key not in deferred_subdirs:
                    db.reset_pending_commits(subdir_key)
        else:
            console.print(f"[red]Failed to create PR: {result.stderr}[/red]")
            console.print(f"Branch {branch_name} is ready - create PR manually")

    # Switch back to master
    _run_git(["checkout", "master"], melee_root)
    console.print(f"\nSwitched back to master. Branch [cyan]{branch_name}[/cyan] is ready for review.")


@worktree_app.command("lock")
def worktree_lock(
    subdirectory_key: Annotated[
        str, typer.Argument(help="Subdirectory key to lock (e.g., 'ft-chara-ftFox', 'lb')")
    ],
    agent_id: Annotated[
        Optional[str], typer.Option("--agent", "-a", help="Agent ID (defaults to current agent)")
    ] = None,
    timeout: Annotated[
        int, typer.Option("--timeout", "-t", help="Lock timeout in minutes")
    ] = 30,
):
    """Lock a subdirectory for exclusive access.

    This prevents other agents from committing to the same subdirectory,
    reducing merge conflicts. Locks automatically expire after the timeout.
    """
    aid = agent_id or AGENT_ID

    # Check if worktree exists
    wt_path = get_subdirectory_worktree_path(subdirectory_key)
    if not wt_path.exists():
        console.print(f"[yellow]Subdirectory worktree does not exist: {subdirectory_key}[/yellow]")
        console.print("[dim]It will be created when you claim a function in that subdirectory.[/dim]")

    success, error = db_lock_subdirectory(subdirectory_key, aid)
    if success:
        console.print(f"[green]Locked subdirectory '{subdirectory_key}' for agent '{aid}'[/green]")
        console.print(f"[dim]Lock expires in {timeout} minutes[/dim]")
    else:
        console.print(f"[red]Failed to lock: {error}[/red]")
        raise typer.Exit(1)


@worktree_app.command("unlock")
def worktree_unlock(
    subdirectory_key: Annotated[
        str, typer.Argument(help="Subdirectory key to unlock (e.g., 'ft-chara-ftFox', 'lb')")
    ],
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Force unlock even if owned by another agent")
    ] = False,
):
    """Unlock a subdirectory, allowing other agents to use it."""
    aid = None if force else AGENT_ID

    success = db_unlock_subdirectory(subdirectory_key, aid)
    if success:
        console.print(f"[green]Unlocked subdirectory '{subdirectory_key}'[/green]")
    else:
        console.print(f"[red]Failed to unlock: subdirectory is locked by another agent[/red]")
        console.print("[dim]Use --force to override[/dim]")
        raise typer.Exit(1)


@worktree_app.command("status")
def worktree_status(
    subdirectory_key: Annotated[
        str, typer.Argument(help="Subdirectory key to check (e.g., 'ft-chara-ftFox', 'lb')")
    ],
):
    """Show detailed status for a subdirectory worktree."""
    lock_info = db_get_subdirectory_lock(subdirectory_key)
    wt_path = get_subdirectory_worktree_path(subdirectory_key)

    console.print(f"\n[bold]Subdirectory: {subdirectory_key}[/bold]\n")

    # Worktree info
    if wt_path.exists():
        console.print(f"  [dim]Worktree:[/dim]  {wt_path}")
        console.print(f"  [dim]Branch:[/dim]    subdirs/{subdirectory_key}")

        # Get git status
        ret, out, _ = _run_git(["status", "--short"], wt_path)
        if ret == 0 and out:
            console.print(f"  [dim]Status:[/dim]    [yellow]uncommitted changes[/yellow]")
        else:
            console.print(f"  [dim]Status:[/dim]    clean")
    else:
        console.print(f"  [dim]Worktree:[/dim]  [yellow]not created[/yellow]")

    # Lock info
    if lock_info:
        console.print(f"\n  [dim]Locked by:[/dim] {lock_info.get('locked_by_agent') or '[dim]unlocked[/dim]'}")
        if lock_info.get('lock_expires_at'):
            import time
            remaining = lock_info['lock_expires_at'] - time.time()
            if remaining > 0:
                console.print(f"  [dim]Expires:[/dim]   {int(remaining / 60)} minutes")
            else:
                console.print(f"  [dim]Expires:[/dim]   [yellow]expired[/yellow]")
    else:
        console.print(f"\n  [dim]Lock status:[/dim] not tracked")
