"""Worktree commands - manage subdirectory worktrees and batch commits."""

import json
import os
import subprocess
import time
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
from .utils import load_json_with_expiry

# Claims file location and timeout (matches claim.py)
DECOMP_CLAIMS_FILE = os.environ.get("DECOMP_CLAIMS_FILE", "/tmp/decomp_claims.json")
DECOMP_CLAIM_TIMEOUT = int(os.environ.get("DECOMP_CLAIM_TIMEOUT", "10800"))  # 3 hours


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
    source_dir: Annotated[
        str, typer.Option("--source-dir", "-s", help="Subdirectory key to collect (e.g., 'lb', 'ft-chara-ftFox')")
    ],
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
    """Collect pending commits from a specific subdirectory worktree into a branch.

    Cherry-picks commits from the specified subdirectory's worktree.

    By default, limits to 7 function match commits per PR to keep reviews manageable.
    Fix-up commits (build fixes, header updates, etc.) don't count toward this limit.
    Use --no-limit to collect all pending commits, or --max-functions to adjust.
    """
    worktrees = _get_worktree_info(melee_root)

    # Find the specified worktree
    pending = [wt for wt in worktrees if wt["subdir_key"] == source_dir and wt["commits_ahead"] > 0]

    if not pending:
        # Check if worktree exists but has no pending commits
        exists = any(wt["subdir_key"] == source_dir for wt in worktrees)
        if exists:
            console.print(f"[yellow]No pending commits in subdirectory worktree '{source_dir}'[/yellow]")
        else:
            console.print(f"[red]Subdirectory worktree '{source_dir}' not found[/red]")
            console.print("\nAvailable worktrees:")
            for wt in worktrees:
                status = f"[green]{wt['commits_ahead']} pending[/green]" if wt["commits_ahead"] > 0 else "[dim]no pending[/dim]"
                console.print(f"  {wt['subdir_key']}: {status}")
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
        branch_name = f"batch/{source_dir}-{date_str}"

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
                "--title", f"[{source_dir}] Match {function_matches_collected} functions",
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


@worktree_app.command("current")
def worktree_current(
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON for programmatic use")
    ] = False,
):
    """Show current agent context for session recovery.

    Displays your active function claims, their scratches, and locked worktrees.
    Use this FIRST after a context reset to recover your state.

    Example:
        melee-agent worktree current
    """
    # Load claims from JSON file
    claims_path = Path(DECOMP_CLAIMS_FILE)
    if claims_path.exists():
        all_claims = load_json_with_expiry(
            claims_path,
            timeout_seconds=DECOMP_CLAIM_TIMEOUT,
            timestamp_field="timestamp",
        )
    else:
        all_claims = {}

    # Filter to this agent's claims
    my_claims = {
        func_name: info
        for func_name, info in all_claims.items()
        if info.get("agent_id") == AGENT_ID
    }

    # Get scratch info from database for each claimed function
    db = get_db()
    claim_details = []
    for func_name, claim_info in my_claims.items():
        func_data = db.get_function(func_name) if db else None
        slug = func_data.get("local_scratch_slug") if func_data else None
        match_pct = func_data.get("match_percent", 0) if func_data else 0

        age_secs = time.time() - claim_info.get("timestamp", 0)
        remaining_secs = DECOMP_CLAIM_TIMEOUT - age_secs

        claim_details.append({
            "function": func_name,
            "subdirectory": claim_info.get("subdirectory"),
            "scratch": slug,
            "match_percent": match_pct,
            "claimed_ago_mins": int(age_secs / 60),
            "remaining_mins": int(remaining_secs / 60),
        })

    # Find locked worktrees for this agent
    locked_worktrees = []
    seen_subdirs = set()
    for claim in claim_details:
        subdir = claim.get("subdirectory")
        if subdir and subdir not in seen_subdirs:
            seen_subdirs.add(subdir)
            lock_info = db_get_subdirectory_lock(subdir)
            if lock_info and lock_info.get("locked_by_agent") == AGENT_ID:
                wt_path = get_subdirectory_worktree_path(subdir)
                locked_worktrees.append({
                    "subdirectory": subdir,
                    "path": str(wt_path),
                    "branch": f"subdirs/{subdir}",
                })

    if output_json:
        print(json.dumps({
            "agent_id": AGENT_ID,
            "claims": claim_details,
            "locked_worktrees": locked_worktrees,
        }, indent=2))
        return

    # Rich output
    console.print("\n[bold]Current Agent Context[/bold]")
    console.print("=" * 60)
    console.print(f"\n[dim]Agent ID:[/dim] {AGENT_ID}\n")

    if not claim_details:
        console.print("[yellow]No active claims[/yellow]")
        console.print("[dim]Use 'melee-agent claim list' to see all claims[/dim]")
        console.print("[dim]Use 'melee-agent claim add <func>' to claim a function[/dim]")
    else:
        console.print("[bold]Active Claims:[/bold]")
        table = Table(show_header=True)
        table.add_column("Function", style="cyan")
        table.add_column("Subdirectory")
        table.add_column("Scratch", style="green")
        table.add_column("Match%", justify="right")
        table.add_column("Remaining", justify="right")

        for claim in claim_details:
            table.add_row(
                claim["function"],
                claim.get("subdirectory") or "-",
                claim.get("scratch") or "-",
                f"{claim.get('match_percent', 0):.0f}%" if claim.get("match_percent") else "-",
                f"{claim.get('remaining_mins', 0)}m",
            )
        console.print(table)

    if locked_worktrees:
        console.print("\n[bold]Locked Worktrees:[/bold]")
        for wt in locked_worktrees:
            console.print(f"  [cyan]{wt['subdirectory']}[/cyan]: {wt['path']}")

        # Recovery commands
        console.print("\n[bold]Recovery Commands:[/bold]")
        primary_wt = locked_worktrees[0]
        console.print(f"  [green]cd {primary_wt['path']}[/green]")

        if claim_details and claim_details[0].get("scratch"):
            slug = claim_details[0]["scratch"]
            console.print(f"  [green]melee-agent scratch get {slug}[/green]")
    elif claim_details:
        console.print("\n[dim]No locked worktrees found.[/dim]")
        console.print("[dim]The subdirectory lock may have expired. Re-claim to lock.[/dim]")


@worktree_app.command("rebase")
def worktree_rebase(
    subdirectory_key: Annotated[
        Optional[str], typer.Argument(help="Subdirectory key to rebase (e.g., 'lb', 'cm'). If omitted, rebases all.")
    ] = None,
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-r", help="Path to melee project root")
    ] = DEFAULT_MELEE_ROOT,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Show what would happen")
    ] = False,
    onto: Annotated[
        str, typer.Option("--onto", help="Branch to rebase onto")
    ] = "upstream/master",
):
    """Rebase worktree branches onto latest upstream/master.

    This updates worktree branches to incorporate the latest changes from
    upstream, which helps prevent build conflicts. Use before starting work
    on a function to ensure you have the latest code.

    If conflicts occur during rebase, the command will abort and you'll need
    to resolve manually.
    """
    worktrees = _get_worktree_info(melee_root)

    # Filter to specific subdirectory if provided
    if subdirectory_key:
        worktrees = [wt for wt in worktrees if wt["subdir_key"] == subdirectory_key]
        if not worktrees:
            console.print(f"[red]Worktree '{subdirectory_key}' not found[/red]")
            raise typer.Exit(1)

    # Only rebase worktrees that are behind
    to_rebase = [wt for wt in worktrees if wt["commits_behind"] > 0]

    if not to_rebase:
        console.print("[green]All worktrees are up to date[/green]")
        return

    console.print(f"Found {len(to_rebase)} worktrees behind {onto}:\n")
    for wt in to_rebase:
        console.print(f"  [cyan]{wt['subdir_key']}[/cyan]: {wt['commits_behind']} behind, {wt['commits_ahead']} ahead")

    if dry_run:
        console.print(f"\n[yellow]DRY RUN[/yellow]: Would rebase {len(to_rebase)} worktrees onto {onto}")
        return

    console.print()

    success_count = 0
    failed = []
    for wt in to_rebase:
        branch = wt["branch"]
        console.print(f"Rebasing [cyan]{wt['subdir_key']}[/cyan] ({branch})...")

        # Stash any uncommitted changes
        ret, stash_out, _ = _run_git(["stash"], wt["path"])
        has_stash = "No local changes" not in stash_out and ret == 0

        # Try rebase
        ret, out, err = _run_git(["rebase", onto], wt["path"])
        if ret != 0:
            # Abort the failed rebase
            _run_git(["rebase", "--abort"], wt["path"])
            if has_stash:
                _run_git(["stash", "pop"], wt["path"])
            failed.append((wt["subdir_key"], err or out))
            console.print(f"  [red]Failed - conflicts[/red]")
            continue

        # Pop stash if we had one
        if has_stash:
            _run_git(["stash", "pop"], wt["path"])

        success_count += 1
        console.print(f"  [green]Success[/green]")

    console.print(f"\n[green]Rebased {success_count}/{len(to_rebase)} worktrees[/green]")

    if failed:
        console.print(f"\n[yellow]Failed worktrees ({len(failed)}):[/yellow]")
        for subdir_key, error in failed:
            console.print(f"  [cyan]{subdir_key}[/cyan]: {error[:100]}")
        console.print("\n[dim]Manual resolution needed for failed worktrees.[/dim]")
        console.print("[dim]cd to worktree and run: git rebase upstream/master[/dim]")


@worktree_app.command("repair")
def worktree_repair(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-r", help="Path to melee project root")
    ] = DEFAULT_MELEE_ROOT,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Show what would be fixed")
    ] = False,
):
    """Repair worktrees with missing or broken orig/ symlinks.

    This fixes worktrees that can't build because they're missing the
    original game files (main.dol). The fix is to symlink orig/ to the
    main melee repo's orig/ directory.
    """
    import shutil

    if not MELEE_WORKTREES_DIR.exists():
        console.print("[yellow]No worktrees directory found[/yellow]")
        return

    orig_src = melee_root / "orig"
    if not orig_src.exists():
        console.print(f"[red]Source orig/ not found at {orig_src}[/red]")
        return

    # Check if source has the actual DOL file
    main_dol = orig_src / "GALE01" / "sys" / "main.dol"
    if not main_dol.exists():
        console.print(f"[red]main.dol not found at {main_dol}[/red]")
        console.print("[dim]Run the game extraction first[/dim]")
        return

    fixed = 0
    already_ok = 0
    for wt_path in sorted(MELEE_WORKTREES_DIR.iterdir()):
        if not wt_path.is_dir() or not wt_path.name.startswith("dir-"):
            continue

        orig_dst = wt_path / "orig"

        # Check if it's already a correct symlink
        if orig_dst.is_symlink():
            target = orig_dst.resolve()
            if target == orig_src.resolve():
                already_ok += 1
                continue
            console.print(f"  [yellow]{wt_path.name}:[/yellow] symlink points to wrong target")
            if not dry_run:
                orig_dst.unlink()
        elif orig_dst.exists():
            # It's a real directory - needs to be replaced
            console.print(f"  [yellow]{wt_path.name}:[/yellow] orig/ is directory, needs symlink")
            if not dry_run:
                shutil.rmtree(orig_dst)
        else:
            console.print(f"  [yellow]{wt_path.name}:[/yellow] orig/ missing")

        if not dry_run:
            orig_dst.symlink_to(orig_src.resolve())
            console.print(f"  [green]{wt_path.name}:[/green] fixed ✓")
        fixed += 1

    if dry_run:
        console.print(f"\n[yellow]DRY RUN[/yellow]: Would fix {fixed} worktrees ({already_ok} already OK)")
    else:
        console.print(f"\n[green]Fixed {fixed} worktrees[/green] ({already_ok} already OK)")


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


@worktree_app.command("health")
def worktree_health(
    subdirectory_key: Annotated[
        Optional[str], typer.Argument(help="Subdirectory key to check (e.g., 'lb'). If omitted, shows all worktrees.")
    ] = None,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show build health status for worktrees.

    Shows broken build count, affected functions, and whether the worktree
    can accept new claims (blocked when >= 3 broken builds).

    Examples:
        melee-agent worktree health           # Show all worktrees
        melee-agent worktree health lb        # Show specific worktree
        melee-agent worktree health --json    # JSON output for automation
    """
    from src.db import get_db

    db = get_db()
    max_broken = 3  # From claim.py MAX_BROKEN_BUILDS_PER_WORKTREE

    if subdirectory_key:
        # Single worktree
        wt_path = get_subdirectory_worktree_path(subdirectory_key)
        broken_count, broken_funcs = db.get_worktree_broken_count(str(wt_path))

        if output_json:
            print(json.dumps({
                "subdirectory": subdirectory_key,
                "worktree_path": str(wt_path),
                "broken_count": broken_count,
                "max_allowed": max_broken,
                "healthy": broken_count < max_broken,
                "can_accept_claims": broken_count < max_broken,
                "broken_functions": broken_funcs,
            }, indent=2))
            return

        console.print(f"\n[bold]Worktree Health: {subdirectory_key}[/bold]\n")
        console.print(f"  [dim]Path:[/dim] {wt_path}")

        if broken_count == 0:
            console.print(f"  [dim]Status:[/dim] [green]Healthy[/green] - no broken builds")
            console.print(f"  [dim]Claims:[/dim] [green]Can accept new claims[/green]")
        elif broken_count < max_broken:
            console.print(f"  [dim]Status:[/dim] [yellow]Warning[/yellow] - {broken_count} broken build(s)")
            console.print(f"  [dim]Claims:[/dim] [green]Can accept new claims[/green] ({max_broken - broken_count} more allowed)")
            console.print(f"\n  [dim]Functions needing fixes:[/dim]")
            for func in broken_funcs:
                console.print(f"    - {func}")
        else:
            console.print(f"  [dim]Status:[/dim] [red]Unhealthy[/red] - {broken_count} broken builds")
            console.print(f"  [dim]Claims:[/dim] [red]Blocked[/red] - fix existing issues first")
            console.print(f"\n  [dim]Functions needing fixes:[/dim]")
            for func in broken_funcs:
                console.print(f"    - {func}")
            console.print(f"\n[yellow]Run /decomp-fixup to resolve these issues[/yellow]")
    else:
        # All worktrees
        results = []

        if MELEE_WORKTREES_DIR.exists():
            for wt in MELEE_WORKTREES_DIR.iterdir():
                if wt.is_dir() and wt.name.startswith("dir-"):
                    subdir_key = wt.name[4:]  # Strip "dir-" prefix
                    broken_count, broken_funcs = db.get_worktree_broken_count(str(wt))
                    results.append({
                        "subdirectory": subdir_key,
                        "worktree_path": str(wt),
                        "broken_count": broken_count,
                        "healthy": broken_count < max_broken,
                        "broken_functions": broken_funcs,
                    })

        if output_json:
            print(json.dumps({
                "worktrees": results,
                "max_allowed_per_worktree": max_broken,
            }, indent=2))
            return

        if not results:
            console.print("[dim]No worktrees found[/dim]")
            return

        console.print(f"\n[bold]Worktree Health Summary[/bold]\n")

        healthy = [r for r in results if r["broken_count"] == 0]
        warning = [r for r in results if 0 < r["broken_count"] < max_broken]
        blocked = [r for r in results if r["broken_count"] >= max_broken]

        if healthy:
            console.print(f"[green]Healthy ({len(healthy)}):[/green]")
            for r in healthy:
                console.print(f"  {r['subdirectory']}")

        if warning:
            console.print(f"\n[yellow]Warning ({len(warning)}):[/yellow]")
            for r in warning:
                console.print(f"  {r['subdirectory']} - {r['broken_count']} broken: {', '.join(r['broken_functions'])}")

        if blocked:
            console.print(f"\n[red]Blocked ({len(blocked)}):[/red]")
            for r in blocked:
                console.print(f"  {r['subdirectory']} - {r['broken_count']} broken: {', '.join(r['broken_functions'])}")
            console.print(f"\n[yellow]Run /decomp-fixup <func> to fix broken builds[/yellow]")
