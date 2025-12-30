"""Audit commands - audit and recover tracked work."""

import json
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
    load_completed_functions,
    save_completed_functions,
    db_upsert_function,
)

audit_app = typer.Typer(help="Audit and recover tracked work")


# ============================================================================
# Duplicate detection utilities
# ============================================================================

@dataclass
class MatchCommit:
    """A commit that matches a function."""
    commit_hash: str
    function_name: str
    match_percent: float
    branch: str
    is_in_upstream: bool = False
    commit_date: str = ""
    subject: str = ""


@dataclass
class FunctionDuplicateInfo:
    """Information about duplicates for a single function."""
    function_name: str
    commits: list[MatchCommit] = field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        return len(self.commits) > 1

    @property
    def branches(self) -> set[str]:
        return {c.branch for c in self.commits}

    @property
    def is_in_upstream(self) -> bool:
        return any(c.is_in_upstream for c in self.commits)

    @property
    def upstream_commit(self) -> Optional[MatchCommit]:
        for c in self.commits:
            if c.is_in_upstream:
                return c
        return None

    @property
    def pending_commits(self) -> list[MatchCommit]:
        return [c for c in self.commits if not c.is_in_upstream]


def _is_valid_function_name(name: str) -> bool:
    """Check if a string looks like a valid function name.

    Filters out common words that might appear in commit messages.
    Valid function names typically:
    - Contain underscores (like fn_8001234 or ftCo_Function)
    - Start with common prefixes (ft, fn, gr, it, lb, hsd, etc.)
    - Contain hex addresses (8001234)
    """
    # Common words that appear in commit messages but aren't functions
    common_words = {
        'some', 'all', 'most', 'several', 'the', 'and', 'or', 'a', 'an',
        'many', 'few', 'various', 'multiple', 'other', 'more', 'less',
        'functions', 'function', 'match', 'matched', 'matching', 'matches',
        'pass', 'ongoing', 'partially', 'modules', 'module', 'file', 'files',
        'this', 'that', 'these', 'those', 'with', 'from', 'into', 'onto',
        'work', 'works', 'working', 'wip', 'done', 'complete', 'completed',
    }

    name_lower = name.lower()
    if name_lower in common_words:
        return False

    # Must have at least one of: underscore, or look like a hex address, or valid prefix
    if '_' in name:
        return True

    # Check for hex-address-like patterns (e.g., 80012345)
    if re.match(r'^[0-9a-fA-F]{6,8}$', name):
        return True

    # Check for known Melee function prefixes
    valid_prefixes = ('ft', 'fn', 'gr', 'it', 'lb', 'hsd', 'gm', 'if', 'mn', 'db', 'vi', 'pl')
    if name_lower.startswith(valid_prefixes):
        return True

    return False


def _parse_function_from_commit_message(subject: str) -> list[tuple[str, float]]:
    """Parse function name(s) and match percentage from commit message.

    Returns list of (function_name, match_percent) tuples.
    """
    results = []
    seen = set()

    def add_if_valid(func: str, pct: float):
        if func not in seen and _is_valid_function_name(func):
            seen.add(func)
            results.append((func, pct))

    # Pattern: "Match func_name (100%)" or "Match func_name (95.5%)"
    pattern1 = re.compile(r'Match\s+(\w+)\s*\((\d+(?:\.\d+)?)%\)')
    for match in pattern1.finditer(subject):
        func = match.group(1)
        pct = float(match.group(2))
        add_if_valid(func, pct)

    # Pattern: "Match func1 and func2 (98.1%)" - multiple functions
    pattern2 = re.compile(r'Match\s+(\w+)\s+and\s+(\w+)\s*\((\d+(?:\.\d+)?)%\)')
    for match in pattern2.finditer(subject):
        pct = float(match.group(3))
        add_if_valid(match.group(1), pct)
        add_if_valid(match.group(2), pct)

    # Pattern: "Match func_name" without percentage (assume 100%)
    if not results:
        pattern3 = re.compile(r'^[a-f0-9]+\s+Match\s+(\w+)(?:\s|$)')
        match = pattern3.match(subject)
        if match:
            add_if_valid(match.group(1), 100.0)

    return results


def _get_upstream_commits(melee_root: Path) -> set[str]:
    """Get set of commit hashes in upstream/master."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "upstream/master"],
            cwd=melee_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return set(result.stdout.strip().split('\n'))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return set()


def _get_all_match_commits(melee_root: Path) -> dict[str, FunctionDuplicateInfo]:
    """Scan all branches for Match commits and group by function."""
    functions: dict[str, FunctionDuplicateInfo] = {}

    # Get upstream commits for comparison
    upstream_commits = _get_upstream_commits(melee_root)

    # Get all Match commits from all branches
    try:
        result = subprocess.run(
            [
                "git", "log", "--all", "--oneline",
                "--format=%h|%s|%D|%ci",
                "--grep=Match",
                "--", "src/melee/*.c"
            ],
            cwd=melee_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return functions

        # Build a commit -> branches mapping
        commit_branches: dict[str, list[str]] = defaultdict(list)
        branch_result = subprocess.run(
            ["git", "branch", "-a", "--contains", "--format=%(refname:short)"],
            cwd=melee_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # This is slow, so we'll use a different approach

        # Get all branch heads
        branch_heads: dict[str, str] = {}
        br_result = subprocess.run(
            ["git", "for-each-ref", "--format=%(objectname:short) %(refname:short)", "refs/heads/"],
            cwd=melee_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if br_result.returncode == 0:
            for line in br_result.stdout.strip().split('\n'):
                if line:
                    parts = line.split(' ', 1)
                    if len(parts) == 2:
                        branch_heads[parts[0]] = parts[1]

        for line in result.stdout.strip().split('\n'):
            if not line or '|' not in line:
                continue

            parts = line.split('|')
            if len(parts) < 4:
                continue

            commit_hash = parts[0]
            subject = parts[1]
            refs = parts[2] if len(parts) > 2 else ""
            commit_date = parts[3] if len(parts) > 3 else ""

            # Determine which branch this commit is on
            # First check refs field
            branch = "unknown"
            if refs:
                # Parse refs like "HEAD -> branch, origin/branch, tag: v1.0"
                for ref in refs.split(','):
                    ref = ref.strip()
                    if '->' in ref:
                        ref = ref.split('->')[1].strip()
                    if ref.startswith('origin/'):
                        ref = ref[7:]
                    if ref and not ref.startswith('tag:'):
                        branch = ref
                        break

            # If no branch from refs, try to find which branch contains this commit
            if branch == "unknown":
                # Check agent branches first
                check_result = subprocess.run(
                    ["git", "branch", "-a", "--contains", commit_hash, "--format=%(refname:short)"],
                    cwd=melee_root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if check_result.returncode == 0 and check_result.stdout.strip():
                    branches = check_result.stdout.strip().split('\n')
                    # Prefer agent branches
                    for b in branches:
                        if b.startswith('agent/'):
                            branch = b
                            break
                    if branch == "unknown" and branches:
                        branch = branches[0]

            is_upstream = commit_hash in upstream_commits

            # Parse function names from subject
            parsed_funcs = _parse_function_from_commit_message(f"{commit_hash} {subject}")

            for func_name, match_pct in parsed_funcs:
                if func_name not in functions:
                    functions[func_name] = FunctionDuplicateInfo(function_name=func_name)

                # Check if we already have this exact commit
                existing_hashes = {c.commit_hash for c in functions[func_name].commits}
                if commit_hash not in existing_hashes:
                    functions[func_name].commits.append(MatchCommit(
                        commit_hash=commit_hash,
                        function_name=func_name,
                        match_percent=match_pct,
                        branch=branch,
                        is_in_upstream=is_upstream,
                        commit_date=commit_date,
                        subject=subject,
                    ))

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        console.print(f"[yellow]Warning: {e}[/yellow]")

    return functions


@audit_app.command("duplicates")
def audit_duplicates(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    show_all: Annotated[
        bool, typer.Option("--all", "-a", help="Show all functions, not just duplicates")
    ] = False,
    show_safe: Annotated[
        bool, typer.Option("--safe", "-s", help="Show duplicates that are safe to ignore (already in upstream)")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show detailed commit info")
    ] = False,
):
    """Find duplicate function matches across branches and worktrees.

    Scans all branches (including agent worktrees) for commits that match the
    same function. This helps identify:

    1. CONFLICTS: Same function matched in multiple pending branches (needs resolution)
    2. REDUNDANT: Same function matched in pending branch but already in upstream
    3. SAFE: Multiple commits for same function, but all in upstream (historical)

    Use this to clean up after incidents where agents committed to wrong branches.

    Examples:
        melee-agent audit duplicates              # Show conflicts needing attention
        melee-agent audit duplicates --all        # Show all matched functions
        melee-agent audit duplicates --safe       # Include already-merged duplicates
        melee-agent audit duplicates --json       # Output for scripting
    """
    if not output_json:
        console.print("[bold]Scanning for duplicate matches...[/bold]\n")

    functions = _get_all_match_commits(melee_root)

    if not functions:
        if output_json:
            print(json.dumps({"summary": {"total_functions": 0}, "conflicts": [], "redundant": []}))
        else:
            console.print("[yellow]No Match commits found[/yellow]")
        return

    # Categorize duplicates
    conflicts: list[FunctionDuplicateInfo] = []  # Multiple pending branches
    redundant: list[FunctionDuplicateInfo] = []  # Pending but already upstream
    safe: list[FunctionDuplicateInfo] = []       # All in upstream
    unique: list[FunctionDuplicateInfo] = []     # Only one commit

    for func_info in functions.values():
        if not func_info.is_duplicate:
            unique.append(func_info)
            continue

        if func_info.is_in_upstream:
            pending = func_info.pending_commits
            if pending:
                # Has both upstream and pending - redundant work
                redundant.append(func_info)
            else:
                # All commits in upstream - safe historical duplicate
                safe.append(func_info)
        else:
            # Multiple pending commits, none in upstream - conflict!
            conflicts.append(func_info)

    # JSON output
    if output_json:
        output = {
            "summary": {
                "total_functions": len(functions),
                "unique": len(unique),
                "conflicts": len(conflicts),
                "redundant": len(redundant),
                "safe": len(safe),
            },
            "conflicts": [
                {
                    "function": f.function_name,
                    "commits": [
                        {
                            "hash": c.commit_hash,
                            "branch": c.branch,
                            "match_percent": c.match_percent,
                            "date": c.commit_date,
                        }
                        for c in f.commits
                    ],
                }
                for f in conflicts
            ],
            "redundant": [
                {
                    "function": f.function_name,
                    "upstream_commit": f.upstream_commit.commit_hash if f.upstream_commit else None,
                    "pending_branches": [c.branch for c in f.pending_commits],
                }
                for f in redundant
            ],
        }
        print(json.dumps(output, indent=2))
        return

    # Summary table
    summary = Table(title="Duplicate Analysis Summary")
    summary.add_column("Category", style="bold")
    summary.add_column("Count", justify="right")
    summary.add_column("Description")

    summary.add_row(
        "[red]Conflicts[/red]",
        str(len(conflicts)),
        "Multiple pending branches - needs resolution"
    )
    summary.add_row(
        "[yellow]Redundant[/yellow]",
        str(len(redundant)),
        "Pending work already in upstream"
    )
    if show_safe:
        summary.add_row(
            "[green]Safe[/green]",
            str(len(safe)),
            "Historical duplicates (all merged)"
        )
    summary.add_row(
        "[dim]Unique[/dim]",
        str(len(unique)),
        "Single commit per function"
    )
    summary.add_row(
        "[bold]Total[/bold]",
        str(len(functions)),
        "Total matched functions found"
    )

    console.print(summary)
    console.print()

    # Show conflicts (always)
    if conflicts:
        console.print("[bold red]⚠ CONFLICTS - Same function in multiple pending branches:[/bold red]\n")

        for func_info in sorted(conflicts, key=lambda f: -len(f.commits)):
            console.print(f"[bold cyan]{func_info.function_name}[/bold cyan] ({len(func_info.commits)} commits)")
            for commit in func_info.commits:
                branch_style = "yellow" if commit.branch.startswith("agent/") else "dim"
                console.print(
                    f"  [{branch_style}]{commit.branch}[/{branch_style}] "
                    f"{commit.commit_hash} ({commit.match_percent:.0f}%)"
                )
                if verbose:
                    console.print(f"    [dim]{commit.commit_date}[/dim]")
            console.print()

        console.print("[bold]Resolution options:[/bold]")
        console.print("  1. Keep best match and drop others")
        console.print("  2. Cherry-pick preferred commit to batch branch")
        console.print("  3. Reset agent branches that have duplicate work")
        console.print()

    # Show redundant work
    if redundant:
        console.print("[bold yellow]⚡ REDUNDANT - Already in upstream but also pending:[/bold yellow]\n")

        for func_info in redundant[:20]:  # Limit display
            upstream = func_info.upstream_commit
            pending_branches = [c.branch for c in func_info.pending_commits]
            console.print(
                f"[cyan]{func_info.function_name}[/cyan]: "
                f"upstream={upstream.commit_hash if upstream else '?'}, "
                f"also in: {', '.join(pending_branches)}"
            )

        if len(redundant) > 20:
            console.print(f"  [dim]... and {len(redundant) - 20} more[/dim]")

        console.print()
        console.print("[bold]These pending branches have work that's already merged.[/bold]")
        console.print("They can be safely reset or pruned.\n")

    # Show safe duplicates if requested
    if show_safe and safe:
        console.print("[bold green]✓ SAFE - Historical duplicates (all merged):[/bold green]\n")

        for func_info in safe[:10]:
            branches = list(func_info.branches)[:3]
            console.print(f"  {func_info.function_name}: {', '.join(branches)}")

        if len(safe) > 10:
            console.print(f"  [dim]... and {len(safe) - 10} more[/dim]")
        console.print()

    # Show unique functions if --all
    if show_all and unique:
        console.print("[bold]Unique matches (no duplicates):[/bold]\n")

        table = Table()
        table.add_column("Function", style="cyan")
        table.add_column("Branch")
        table.add_column("Status")
        table.add_column("Hash", style="dim")

        for func_info in sorted(unique, key=lambda f: f.function_name)[:50]:
            commit = func_info.commits[0]
            status = "[green]merged[/green]" if commit.is_in_upstream else "[yellow]pending[/yellow]"
            table.add_row(
                func_info.function_name,
                commit.branch,
                status,
                commit.commit_hash,
            )

        console.print(table)
        if len(unique) > 50:
            console.print(f"[dim]... and {len(unique) - 50} more[/dim]")

    # Final summary
    if not conflicts and not redundant:
        console.print("[bold green]✓ No duplicate conflicts found![/bold green]")
    elif conflicts:
        console.print(f"[bold red]Found {len(conflicts)} functions with conflicting commits[/bold red]")
        console.print("Run with --json to get machine-readable output for scripting")



# NOTE: The following commands have been moved to 'melee-agent state':
#   - audit status  -> state status
#   - audit recover -> state status --category matched
#   - audit list    -> state status
#   - audit rebuild -> state rebuild


def _list_github_prs(repo: str, author: str, state: str, limit: int) -> list[dict]:
    """List PRs from GitHub using gh CLI."""
    try:
        cmd = [
            "gh", "pr", "list",
            "--repo", repo,
            "--author", author,
            "--state", state,
            "--limit", str(limit),
            "--json", "number,title,body,state,mergedAt,url"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return []
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def _get_pr_diff(repo: str, pr_number: int) -> str:
    """Get the diff for a PR."""
    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--repo", repo],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _extract_functions_from_pr(pr: dict, repo: str = "doldecomp/melee") -> list[dict]:
    """Extract function matches from PR diff.

    Looks for functions that were implemented (stub removed, definition added):
    - Removed stub comment: -/// #func_name
    - Added function definition: +type func_name(

    Also falls back to parsing PR body for percentages if available.
    """
    functions = []
    seen = set()

    pr_number = pr.get("number")
    if not pr_number:
        return functions

    # Get the actual diff
    diff = _get_pr_diff(repo, pr_number)

    if diff:
        # Pattern 1: Look for removed stub comments (most reliable indicator of a match)
        # Format: -/// #func_name or -// #func_name
        for match in re.finditer(r'^-\s*///?\s*#(\w+)', diff, re.MULTILINE):
            func_name = match.group(1)
            if func_name not in seen and '_' in func_name:
                seen.add(func_name)
                functions.append({
                    "function": func_name,
                    "match_percent": 100.0,  # Stub removed = matched
                })

        # Pattern 2: Look for added C function definitions in .c files
        # Must be in a C file context (after diff --git a/.../*.c)
        # Function names must match melee patterns: prefix_address or camelCase_name
        # Only match lines with C-style return types, not Python def
        c_types = r'(?:void|s8|s16|s32|s64|u8|u16|u32|u64|f32|f64|int|char|float|double|bool|BOOL|size_t|UNK_T|HSD_\w+|Fighter\w*|Item\w*|Ground\w*|\w+_t\*?)'
        for match in re.finditer(rf'^\+\s*{c_types}\s+(\w+_\w+)\s*\(', diff, re.MULTILINE):
            func_name = match.group(1)
            # Melee function names: lowercase prefix + underscore + hex OR CamelCase_name
            if func_name not in seen and re.match(r'^[a-z]+[A-Z_]', func_name):
                seen.add(func_name)
                functions.append({
                    "function": func_name,
                    "match_percent": 100.0,
                })

    # Fallback: Parse PR body for explicit percentages
    body = pr.get("body", "") or ""
    if body:
        # Pattern: func_name (100%) or func_name (95.5%)
        for match in re.finditer(r'(\w+_\w+)\s*\((\d+(?:\.\d+)?%)\)', body):
            func_name = match.group(1)
            pct_str = match.group(2).rstrip('%')
            if func_name not in seen:
                seen.add(func_name)
                functions.append({
                    "function": func_name,
                    "match_percent": float(pct_str),
                })

    return functions


@audit_app.command("discover-prs")
def audit_discover_prs(
    author: Annotated[
        str, typer.Option("--author", "-a", help="GitHub username to search for")
    ] = "itsgrimetime",
    repo: Annotated[
        str, typer.Option("--repo", "-r", help="GitHub repository")
    ] = "doldecomp/melee",
    state: Annotated[
        str, typer.Option("--state", "-s", help="PR state: open, merged, closed, all")
    ] = "all",
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum PRs to scan")
    ] = 50,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be linked")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Discover functions from GitHub PRs and link them.

    Scans PRs by the specified author and parses function names from
    PR diffs to find matched functions. Updates state database with PR associations.

    Example: melee-agent audit discover-prs --author itsgrimetime --state merged
    """
    console.print(f"[bold]Scanning GitHub PRs[/bold]")
    console.print(f"  Repo: {repo}")
    console.print(f"  Author: {author}")
    console.print(f"  State: {state}")
    console.print()

    # Handle 'all' state by querying merged, open, and closed
    if state == "all":
        states_to_query = ["merged", "open", "closed"]
    else:
        states_to_query = [state]

    all_prs = []
    for s in states_to_query:
        prs = _list_github_prs(repo, author, s, limit)
        for pr in prs:
            pr["_queried_state"] = s  # Track which query found it
        all_prs.extend(prs)

    if not all_prs:
        console.print("[yellow]No PRs found or gh CLI not available[/yellow]")
        console.print("[dim]Make sure 'gh' CLI is installed and authenticated[/dim]")
        return

    console.print(f"Found {len(all_prs)} PRs\n")

    completed = load_completed_functions()
    results = []          # PRs with updates (for display)
    all_discovered = []   # All PRs with functions (for JSON)
    total_linked = 0
    total_updated = 0

    for pr in all_prs:
        pr_url = pr.get("url", "")
        pr_number = pr.get("number", 0)
        pr_state = pr.get("state", "UNKNOWN")
        is_merged = pr.get("mergedAt") is not None or pr.get("_queried_state") == "merged"

        # Determine actual PR state
        actual_pr_state = "MERGED" if is_merged else pr_state.upper() if pr_state else "OPEN"

        # First, update any functions already linked to this PR (even if not in diff)
        # This handles cases where a PR is closed but the function wasn't matched
        for func, current in completed.items():
            if current.get("pr_url") == pr_url and current.get("pr_state") != actual_pr_state:
                current["pr_state"] = actual_pr_state
                total_updated += 1

        functions = _extract_functions_from_pr(pr, repo)
        if not functions:
            continue

        linked_funcs = []
        all_funcs_in_pr = []

        for func_info in functions:
            func = func_info["function"]
            match_pct = func_info.get("match_percent")
            func_entry = {
                "function": func,
                "match_percent": match_pct,
                "in_db": func in completed,
                "action": None,
            }

            if func in completed:
                current = completed[func]
                needs_update = False

                # Link if no PR currently
                if not current.get("pr_url"):
                    current["pr_url"] = pr_url
                    current["pr_number"] = pr_number
                    current["pr_repo"] = repo
                    current["pr_state"] = actual_pr_state
                    needs_update = True
                    total_linked += 1
                    func_entry["action"] = "linked"

                # Update state if this is the same PR and state changed
                elif current.get("pr_url") == pr_url:
                    if current.get("pr_state") != actual_pr_state:
                        current["pr_state"] = actual_pr_state
                        needs_update = True
                        total_updated += 1
                        func_entry["action"] = "updated"
                    else:
                        func_entry["action"] = "already_linked"
                else:
                    func_entry["action"] = "linked_to_other_pr"
                    func_entry["other_pr"] = current.get("pr_url")

                if needs_update:
                    linked_funcs.append(func)
            else:
                func_entry["action"] = "not_in_db"

            all_funcs_in_pr.append(func_entry)

        # Track all discovered PRs with functions
        all_discovered.append({
            "pr_number": pr_number,
            "pr_url": pr_url,
            "pr_title": pr.get("title", ""),
            "state": actual_pr_state,
            "functions": all_funcs_in_pr,
        })

        if linked_funcs:
            results.append({
                "pr_number": pr_number,
                "pr_url": pr_url,
                "state": actual_pr_state,
                "functions": linked_funcs,
            })

    if output_json:
        print(json.dumps(all_discovered, indent=2))
        return

    if results:
        for r in results:
            state_color = "green" if r["state"] == "MERGED" else "cyan"
            console.print(f"[bold]PR #{r['pr_number']}[/bold] [{state_color}]{r['state']}[/{state_color}]")
            console.print(f"  {r['pr_url']}")
            for func in r["functions"][:5]:
                console.print(f"    → {func}")
            if len(r["functions"]) > 5:
                console.print(f"    [dim]... and {len(r['functions']) - 5} more[/dim]")
            console.print()

    console.print(f"[bold]Summary:[/bold]")
    console.print(f"  PRs scanned: {len(all_prs)}")
    console.print(f"  Functions newly linked: {total_linked}")
    console.print(f"  PR states updated: {total_updated}")

    if not dry_run and (total_linked > 0 or total_updated > 0):
        save_completed_functions(completed)

        # Also update state database
        for func_name, info in completed.items():
            if info.get("pr_url"):
                update_fields = {
                    'pr_url': info.get("pr_url"),
                    'pr_number': info.get("pr_number"),
                    'pr_state': info.get("pr_state"),
                }
                # Only set status to merged when PR is merged; don't clear status otherwise
                if info.get("pr_state") == "MERGED":
                    update_fields['status'] = 'merged'
                db_upsert_function(func_name, **update_fields)

        console.print(f"\n[green]Saved changes to state database[/green]")
    elif dry_run:
        console.print(f"\n[cyan](dry run - no changes saved)[/cyan]")


