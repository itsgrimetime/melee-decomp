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
        branch = f"subdirs/{subdir_key}"

        # Get commits ahead of upstream/master
        ret, out, _ = _run_git(
            ["rev-list", "--count", f"upstream/master..{branch}"],
            melee_root
        )
        commits_ahead = int(out) if ret == 0 and out.isdigit() else 0

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
    table.add_column("Commits", justify="right")
    table.add_column("Last Activity", style="dim")
    table.add_column("Locked By", style="yellow")
    table.add_column("Status")

    total_pending = 0
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
        if wt["has_uncommitted"]:
            status_parts.append("[yellow]uncommitted[/yellow]")

        table.add_row(
            wt["subdir_key"],
            str(wt["commits_ahead"]),
            age_str,
            wt.get("locked_by", "") or "[dim]unlocked[/dim]",
            " ".join(status_parts),
        )

        # Show commits if requested
        if show_commits and wt["commit_subjects"]:
            for subject in wt["commit_subjects"]:
                table.add_row("", "", "", "", f"  [dim]{subject}[/dim]")

    console.print(table)
    console.print(f"\nTotal: {len(worktrees)} subdirectory worktrees, {total_pending} pending commits")


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
):
    """Collect all pending subdirectory commits into a single branch.

    Cherry-picks commits in subdirectory order to minimize conflicts.
    """
    worktrees = _get_worktree_info(melee_root)

    # Find worktrees with pending commits
    pending = [wt for wt in worktrees if wt["commits_ahead"] > 0]

    if not pending:
        console.print("[yellow]No pending commits in subdirectory worktrees[/yellow]")
        return

    # Sort by subdirectory key for consistent ordering
    pending.sort(key=lambda wt: wt["subdir_key"])

    # Show what we'll collect
    total_commits = sum(wt["commits_ahead"] for wt in pending)
    console.print(f"Found {total_commits} commits across {len(pending)} subdirectory worktrees:\n")

    all_commits = []
    for wt in pending:
        console.print(f"[cyan]{wt['subdir_key']}[/cyan]:")
        for subject in wt["commit_subjects"]:
            console.print(f"  {subject}")
            # Extract commit hash
            commit_hash = subject.split()[0]
            all_commits.append((commit_hash, wt["branch"], wt["subdir_key"]))
        console.print()

    if dry_run:
        console.print("[yellow]DRY RUN - no changes made[/yellow]")
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
    for commit_hash, source_branch, subdir_key in all_commits:
        by_subdir[subdir_key].append((commit_hash, source_branch))

    success_count = 0
    failed = []
    for subdir_key in sorted(by_subdir.keys()):
        commits = by_subdir[subdir_key]
        commits.reverse()  # Oldest first
        for commit_hash, source_branch in commits:
            ret, _, err = _run_git(["cherry-pick", commit_hash], melee_root)
            if ret != 0:
                _run_git(["cherry-pick", "--abort"], melee_root)
                failed.append((commit_hash, source_branch, subdir_key, err))
                console.print(f"  [red]Failed to cherry-pick {commit_hash}[/red]")
            else:
                success_count += 1
                console.print(f"  [green]✓[/green] {commit_hash} ({subdir_key})")

    console.print(f"\n[green]Collected {success_count}/{len(all_commits)} commits onto {branch_name}[/green]")

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
        pr_body = f"Batch collection of {success_count} matched functions from subdirectory worktrees.\n\n"
        pr_body += "## Commits by Subdirectory\n"
        for wt in pending:
            pr_body += f"\n### {wt['subdir_key']}\n"
            for subject in wt["commit_subjects"]:
                pr_body += f"- {subject}\n"

        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--title", f"Batch: {success_count} matched functions",
                "--body", pr_body,
                "--base", "master",
            ],
            cwd=melee_root,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            console.print(f"[green]PR created: {result.stdout.strip()}[/green]")

            # Reset pending commit counts in database
            db = get_db()
            for wt in pending:
                db.reset_pending_commits(wt["subdir_key"])
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
