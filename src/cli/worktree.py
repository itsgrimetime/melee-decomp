"""Worktree commands - manage agent worktrees and batch commits."""

import asyncio
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional, List

import typer
from rich.table import Table

from ._common import console, DEFAULT_MELEE_ROOT, db_upsert_agent


worktree_app = typer.Typer(help="Manage agent worktrees and batch commits")


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
    """Get information about all agent worktrees."""
    worktrees_dir = melee_root.parent / "melee-worktrees"
    if not worktrees_dir.exists():
        return []

    worktrees = []
    for wt_path in sorted(worktrees_dir.iterdir()):
        if not wt_path.is_dir():
            continue

        name = wt_path.name
        branch = f"agent/{name}"

        # Get commits ahead of upstream/master (PRs go to upstream, not origin)
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

        # Get the base commit (merge-base with upstream/master)
        ret, out, _ = _run_git(
            ["merge-base", "upstream/master", branch],
            melee_root
        )
        base_commit = out[:7] if ret == 0 and out else None

        worktrees.append({
            "name": name,
            "path": wt_path,
            "branch": branch,
            "commits_ahead": commits_ahead,
            "commit_subjects": commit_subjects,
            "last_commit_date": last_commit_date,
            "has_uncommitted": has_uncommitted,
            "base_commit": base_commit,
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
    """List all agent worktrees with their status."""
    worktrees = _get_worktree_info(melee_root)

    if not worktrees:
        console.print("[yellow]No agent worktrees found[/yellow]")
        return

    # Update agent records in state database (non-blocking)
    for wt in worktrees:
        db_upsert_agent(
            agent_id=wt["name"],
            worktree_path=str(wt["path"]),
            branch_name=wt["branch"],
        )

    table = Table(title="Agent Worktrees")
    table.add_column("Name", style="cyan")
    table.add_column("Base", style="dim")
    table.add_column("Commits", justify="right")
    table.add_column("Last Activity", style="dim")
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
            status_parts.append("[dim]merged[/dim]")
        if wt["has_uncommitted"]:
            status_parts.append("[yellow]uncommitted[/yellow]")

        table.add_row(
            wt["name"],
            wt.get("base_commit", "?"),
            str(wt["commits_ahead"]),
            age_str,
            " ".join(status_parts),
        )

        # Show commits if requested
        if show_commits and wt["commit_subjects"]:
            for subject in wt["commit_subjects"]:
                table.add_row("", "", "", "", f"  [dim]{subject}[/dim]")

    console.print(table)
    console.print(f"\nTotal: {len(worktrees)} worktrees, {total_pending} pending commits")


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
            console.print(f"  {wt['name']} {status}")
        console.print(f"\nWould remove {len(to_remove)} worktrees")
        return

    # Actually remove
    removed = 0
    for wt in to_remove:
        console.print(f"Removing {wt['name']}...")

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
    """Collect all pending agent commits into a single branch.

    Cherry-picks all unmerged commits from agent worktrees onto a new branch,
    ready for review and merging to master.
    """
    worktrees = _get_worktree_info(melee_root)

    # Find worktrees with pending commits
    pending = [wt for wt in worktrees if wt["commits_ahead"] > 0]

    if not pending:
        console.print("[yellow]No pending commits to collect[/yellow]")
        return

    # Show what we'll collect
    total_commits = sum(wt["commits_ahead"] for wt in pending)
    console.print(f"Found {total_commits} commits across {len(pending)} worktrees:\n")

    all_commits = []
    for wt in pending:
        console.print(f"[cyan]{wt['name']}[/cyan]:")
        for subject in wt["commit_subjects"]:
            console.print(f"  {subject}")
            # Extract commit hash
            commit_hash = subject.split()[0]
            all_commits.append((commit_hash, wt["branch"]))
        console.print()

    if dry_run:
        console.print("[yellow]DRY RUN - no changes made[/yellow]")
        return

    # Generate branch name if not provided
    if not branch_name:
        date_str = datetime.now().strftime("%Y%m%d")
        branch_name = f"agent-batch/{date_str}"

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

    # Cherry-pick commits in order (oldest first)
    # Reverse the list since we collected newest first
    all_commits.reverse()

    success_count = 0
    failed = []
    for commit_hash, source_branch in all_commits:
        ret, _, err = _run_git(["cherry-pick", commit_hash], melee_root)
        if ret != 0:
            # Try to abort if cherry-pick failed
            _run_git(["cherry-pick", "--abort"], melee_root)
            failed.append((commit_hash, source_branch, err))
            console.print(f"  [red]Failed to cherry-pick {commit_hash}[/red]")
        else:
            success_count += 1
            console.print(f"  [green]✓[/green] {commit_hash}")

    console.print(f"\n[green]Collected {success_count}/{len(all_commits)} commits onto {branch_name}[/green]")

    if failed:
        console.print(f"\n[yellow]Failed commits ({len(failed)}):[/yellow]")
        for commit_hash, source_branch, err in failed:
            console.print(f"  {commit_hash} from {source_branch}")
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
        pr_body = f"Batch collection of {success_count} matched functions from agent sessions.\n\n"
        pr_body += "## Commits\n"
        for wt in pending:
            pr_body += f"\n### {wt['name']}\n"
            for subject in wt["commit_subjects"]:
                pr_body += f"- {subject}\n"

        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--title", f"Agent batch: {success_count} matched functions",
                "--body", pr_body,
                "--base", "master",
            ],
            cwd=melee_root,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            console.print(f"[green]PR created: {result.stdout.strip()}[/green]")
        else:
            console.print(f"[red]Failed to create PR: {result.stderr}[/red]")
            console.print(f"Branch {branch_name} is ready - create PR manually")

    # Switch back to master
    _run_git(["checkout", "master"], melee_root)
    console.print(f"\nSwitched back to master. Branch [cyan]{branch_name}[/cyan] is ready for review.")
