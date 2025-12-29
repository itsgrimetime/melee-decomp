"""PR commands - track functions through PR lifecycle."""

import json
import re
import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
    PRODUCTION_DECOMP_ME,
    load_completed_functions,
    save_completed_functions,
    load_all_tracking_data,
    load_slug_map,
    categorize_functions,
    extract_pr_info,
    get_pr_status_from_gh,
    get_agent_melee_root,
)

# Constants
OBJDIFF_CLI = "build/tools/objdiff-cli"

pr_app = typer.Typer(help="Track functions through PR lifecycle")


@pr_app.command("link")
def pr_link(
    pr_url: Annotated[
        str, typer.Argument(help="GitHub PR URL")
    ],
    functions: Annotated[
        list[str], typer.Argument(help="Function names to link")
    ],
):
    """Link functions to a GitHub PR.

    Example: melee-agent pr link https://github.com/doldecomp/melee/pull/123 func1 func2
    """
    repo, pr_number = extract_pr_info(pr_url)
    if not pr_number:
        console.print(f"[red]Invalid PR URL: {pr_url}[/red]")
        console.print("[dim]Expected format: https://github.com/owner/repo/pull/123[/dim]")
        raise typer.Exit(1)

    completed = load_completed_functions()
    linked = []
    not_found = []

    for func in functions:
        if func in completed:
            completed[func]["pr_url"] = pr_url
            completed[func]["pr_number"] = pr_number
            completed[func]["pr_repo"] = repo
            linked.append(func)
        else:
            not_found.append(func)

    if linked:
        save_completed_functions(completed)
        console.print(f"[green]Linked {len(linked)} functions to PR #{pr_number}[/green]")
        for func in linked:
            console.print(f"  {func}")

    if not_found:
        console.print(f"\n[yellow]Not found in tracking ({len(not_found)}):[/yellow]")
        for func in not_found:
            console.print(f"  {func}")


@pr_app.command("link-batch")
def pr_link_batch(
    pr_url: Annotated[
        str, typer.Argument(help="GitHub PR URL")
    ],
    category: Annotated[
        str, typer.Option("--category", "-c", help="Link all functions in category: complete, synced")
    ] = "complete",
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
):
    """Link all functions in a category to a PR.

    Example: melee-agent pr link-batch https://github.com/doldecomp/melee/pull/123 --category complete
    """
    repo, pr_number = extract_pr_info(pr_url)
    if not pr_number:
        console.print(f"[red]Invalid PR URL: {pr_url}[/red]")
        raise typer.Exit(1)

    data = load_all_tracking_data(melee_root)
    categories = categorize_functions(data)

    cat_map = {"complete": "complete", "synced": "synced_not_in_file"}
    if category not in cat_map:
        console.print(f"[red]Invalid category: {category}[/red]")
        console.print("Valid: complete, synced")
        raise typer.Exit(1)

    entries = categories[cat_map[category]]
    if not entries:
        console.print(f"[yellow]No functions in category '{category}'[/yellow]")
        return

    completed = load_completed_functions()
    linked = 0

    for entry in entries:
        func = entry["function"]
        if func in completed:
            completed[func]["pr_url"] = pr_url
            completed[func]["pr_number"] = pr_number
            completed[func]["pr_repo"] = repo
            linked += 1

    save_completed_functions(completed)
    console.print(f"[green]Linked {linked} functions to PR #{pr_number}[/green]")


@pr_app.command("unlink")
def pr_unlink(
    functions: Annotated[
        list[str], typer.Argument(help="Function names to unlink")
    ],
):
    """Remove PR association from functions."""
    completed = load_completed_functions()
    unlinked = []

    for func in functions:
        if func in completed and "pr_url" in completed[func]:
            del completed[func]["pr_url"]
            if "pr_number" in completed[func]:
                del completed[func]["pr_number"]
            if "pr_repo" in completed[func]:
                del completed[func]["pr_repo"]
            unlinked.append(func)

    if unlinked:
        save_completed_functions(completed)
        console.print(f"[green]Unlinked {len(unlinked)} functions[/green]")


@pr_app.command("status")
def pr_status(
    check_github: Annotated[
        bool, typer.Option("--check", "-c", help="Check actual PR status via gh CLI")
    ] = False,
):
    """Show PR status summary for all tracked functions."""
    completed = load_completed_functions()

    by_pr: dict[str, list[tuple[str, dict]]] = {}
    no_pr = []

    for func, info in completed.items():
        # Skip functions already in upstream (not our work)
        if info.get("already_in_upstream"):
            continue
        pr_url = info.get("pr_url")
        if pr_url:
            if pr_url not in by_pr:
                by_pr[pr_url] = []
            by_pr[pr_url].append((func, info))
        elif info.get("match_percent", 0) >= 95:
            no_pr.append((func, info))

    console.print("[bold]PR Tracking Status[/bold]\n")

    if by_pr:
        for pr_url, funcs in sorted(by_pr.items()):
            repo, pr_num = extract_pr_info(pr_url)

            status_str = ""
            if check_github and repo and pr_num:
                gh_status = get_pr_status_from_gh(repo, pr_num)
                if gh_status:
                    state = gh_status.get("state", "unknown")
                    is_draft = gh_status.get("isDraft", False)
                    review = gh_status.get("reviewDecision", "")

                    if state == "MERGED":
                        status_str = " [green]MERGED[/green]"
                    elif state == "CLOSED":
                        status_str = " [red]CLOSED[/red]"
                    elif is_draft:
                        status_str = " [dim]DRAFT[/dim]"
                    elif review == "APPROVED":
                        status_str = " [green]APPROVED[/green]"
                    elif review == "CHANGES_REQUESTED":
                        status_str = " [yellow]CHANGES REQUESTED[/yellow]"
                    else:
                        status_str = " [cyan]OPEN[/cyan]"

            # Check if all functions share the same branch
            branches = set(info.get("branch") for _, info in funcs if info.get("branch"))
            branch_str = ""
            if len(branches) == 1:
                branch_str = f" [dim]branch: {list(branches)[0]}[/dim]"

            console.print(f"[bold]PR #{pr_num}[/bold]{status_str}")
            console.print(f"  {pr_url}{branch_str}")
            console.print(f"  Functions: {len(funcs)}")
            for func, info in funcs[:5]:
                pct = info.get("match_percent", 0)
                console.print(f"    - {func} ({pct}%)")
            if len(funcs) > 5:
                console.print(f"    [dim]... and {len(funcs) - 5} more[/dim]")
            console.print()

    if no_pr:
        console.print(f"[yellow]Not linked to any PR ({len(no_pr)} functions at 95%+):[/yellow]")
        for func, info in sorted(no_pr, key=lambda x: -x[1].get("match_percent", 0))[:10]:
            pct = info.get("match_percent", 0)
            console.print(f"  {func}: {pct}%")
        if len(no_pr) > 10:
            console.print(f"  [dim]... and {len(no_pr) - 10} more[/dim]")
        console.print("\n[dim]Link with: melee-agent pr link <pr_url> <function>...[/dim]")

    if not by_pr and not no_pr:
        console.print("[dim]No functions tracked yet[/dim]")


@pr_app.command("list")
def pr_list(
    pr_url: Annotated[
        Optional[str], typer.Argument(help="Filter by PR URL (optional)")
    ] = None,
    no_pr: Annotated[
        bool, typer.Option("--no-pr", help="Show only functions without a PR")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List functions by PR association."""
    completed = load_completed_functions()

    results = []
    for func, info in completed.items():
        func_pr = info.get("pr_url", "")

        if no_pr and func_pr:
            continue
        if pr_url and func_pr != pr_url:
            continue
        if not no_pr and not pr_url and not func_pr:
            continue

        results.append({
            "function": func,
            "match_percent": info.get("match_percent", 0),
            "pr_url": func_pr,
            "pr_number": info.get("pr_number", 0),
            "scratch_slug": info.get("scratch_slug", ""),
        })

    results.sort(key=lambda x: -x["match_percent"])

    if output_json:
        print(json.dumps(results, indent=2))
        return

    if not results:
        if no_pr:
            console.print("[green]All 95%+ functions are linked to PRs[/green]")
        else:
            console.print("[dim]No matching functions[/dim]")
        return

    table = Table(title="Functions" + (f" for PR" if pr_url else " without PR" if no_pr else ""))
    table.add_column("Function", style="cyan")
    table.add_column("Match %", justify="right")
    table.add_column("PR #", justify="right")
    table.add_column("Slug")

    for r in results[:50]:
        table.add_row(
            r["function"],
            f"{r['match_percent']:.1f}%",
            str(r["pr_number"]) if r["pr_number"] else "-",
            r["scratch_slug"] or "-"
        )

    console.print(table)
    if len(results) > 50:
        console.print(f"[dim]... and {len(results) - 50} more[/dim]")


def _get_extended_pr_info(repo: str, pr_number: int) -> dict | None:
    """Get extended PR info including body, commits, and base branch."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json",
             "state,isDraft,title,body,mergeable,reviewDecision,baseRefName,headRefName,commits,url"],
            capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def _extract_functions_from_commits(commits: list[dict]) -> list[dict]:
    """Extract function names from commit messages."""
    functions = []
    seen = set()

    for commit in commits:
        msg = commit.get("messageHeadline", "") or commit.get("message", "")
        # Pattern: "Match func_name (100%)" or similar
        matches = re.findall(r'Match\s+(\w+)\s*(?:\([^)]*\))?', msg)
        for func in matches:
            if func not in seen and '_' in func:  # Basic validation
                seen.add(func)
                functions.append({
                    "function": func,
                    "commit": commit.get("oid", "")[:7],
                    "message": msg[:60],
                })
    return functions


def _validate_pr_description(body: str, functions: list[str], slug_map: dict) -> list[str]:
    """Validate PR description for issues.

    Returns list of warning messages.
    """
    warnings = []
    body_lower = body.lower() if body else ""

    # Check for local decomp.me URLs (should be production)
    local_patterns = [
        r'localhost:\d+/scratch/',
        r'127\.0\.0\.1:\d+/scratch/',
        r'nzxt-discord\.local[:/]',
        r'10\.200\.0\.\d+[:/]',
    ]
    for pattern in local_patterns:
        if re.search(pattern, body or "", re.IGNORECASE):
            warnings.append("Contains local decomp.me URLs (should use https://decomp.me)")
            break

    # Check if functions from commits are mentioned in body
    if functions and body:
        missing_funcs = []
        for func in functions[:10]:  # Check first 10
            if func not in body:
                missing_funcs.append(func)
        if missing_funcs:
            if len(missing_funcs) == len(functions[:10]):
                warnings.append(f"Description doesn't mention any matched functions")
            else:
                warnings.append(f"Description missing {len(missing_funcs)} function(s): {', '.join(missing_funcs[:3])}...")

    # Check for production scratch URLs
    has_scratch_links = "decomp.me/scratch/" in (body or "")
    if functions and not has_scratch_links:
        warnings.append("No decomp.me scratch links in description")

    # Check for expected sections
    if body and len(body) > 50:
        if "match" not in body_lower and "function" not in body_lower:
            warnings.append("Description may not follow expected format (no 'match' or 'function' keywords)")

    return warnings


@pr_app.command("check")
def pr_check(
    pr_url: Annotated[
        str, typer.Argument(help="GitHub PR URL to check")
    ],
    validate: Annotated[
        bool, typer.Option("--validate", "-v", help="Validate PR description")
    ] = True,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Check PR status and validate description.

    Shows:
    - PR state, review status, mergeability
    - Base and head branches
    - Functions mentioned in commits
    - Warnings if description has issues (local URLs, missing function names, etc.)
    """
    repo, pr_number = extract_pr_info(pr_url)
    if not pr_number:
        console.print(f"[red]Invalid PR URL: {pr_url}[/red]")
        console.print("[dim]Expected format: https://github.com/owner/repo/pull/123[/dim]")
        raise typer.Exit(1)

    pr_info = _get_extended_pr_info(repo, pr_number)
    if not pr_info:
        console.print("[red]Could not fetch PR info[/red]")
        console.print("[dim]Make sure 'gh' CLI is installed and authenticated[/dim]")
        raise typer.Exit(1)

    # Extract data
    state = pr_info.get("state", "unknown")
    is_draft = pr_info.get("isDraft", False)
    title = pr_info.get("title", "Unknown")
    body = pr_info.get("body", "")
    review = pr_info.get("reviewDecision", "PENDING")
    mergeable = pr_info.get("mergeable", "UNKNOWN")
    base_branch = pr_info.get("baseRefName", "?")
    head_branch = pr_info.get("headRefName", "?")
    commits = pr_info.get("commits", [])

    # Extract functions from commits
    commit_functions = _extract_functions_from_commits(commits)
    func_names = [f["function"] for f in commit_functions]

    # Validate description if requested
    slug_map = load_slug_map() if validate else {}
    warnings = _validate_pr_description(body, func_names, slug_map) if validate else []

    if output_json:
        output = {
            "pr_number": pr_number,
            "url": pr_url,
            "title": title,
            "state": state,
            "is_draft": is_draft,
            "review": review,
            "mergeable": mergeable,
            "base_branch": base_branch,
            "head_branch": head_branch,
            "commit_count": len(commits),
            "functions": commit_functions,
            "warnings": warnings,
        }
        print(json.dumps(output, indent=2))
        return

    # Display PR info
    console.print(f"[bold]PR #{pr_number}[/bold]: {title}\n")

    # State
    if state == "MERGED":
        console.print("[green]Status: MERGED[/green]")
    elif state == "CLOSED":
        console.print("[red]Status: CLOSED[/red]")
    elif is_draft:
        console.print("[dim]Status: DRAFT[/dim]")
    else:
        console.print("[cyan]Status: OPEN[/cyan]")

    console.print(f"Review: {review or 'PENDING'}")
    console.print(f"Mergeable: {mergeable}")

    # Branches
    console.print(f"\n[bold]Branches:[/bold]")
    console.print(f"  Base: {base_branch}")
    console.print(f"  Head: {head_branch}")

    # Commits and functions
    console.print(f"\n[bold]Commits:[/bold] {len(commits)}")
    if commit_functions:
        console.print(f"[bold]Functions matched:[/bold] {len(commit_functions)}")
        for func_info in commit_functions[:10]:
            console.print(f"  - {func_info['function']} [dim]({func_info['commit']})[/dim]")
        if len(commit_functions) > 10:
            console.print(f"  [dim]... and {len(commit_functions) - 10} more[/dim]")
    else:
        console.print("[dim]No Match commits found[/dim]")

    # Warnings
    if warnings:
        console.print(f"\n[bold yellow]⚠ Warnings ({len(warnings)}):[/bold yellow]")
        for warning in warnings:
            console.print(f"  [yellow]• {warning}[/yellow]")
    elif validate:
        console.print(f"\n[green]✓ Description looks good[/green]")


def _get_production_scratch_url(func_name: str, slug_map: dict) -> str | None:
    """Get production decomp.me scratch URL for a function.

    Looks up the function in slug_map (production_slug -> {function: ...}).
    Returns the full URL or None if not found.
    """
    for prod_slug, info in slug_map.items():
        if info.get("function") == func_name:
            return f"{PRODUCTION_DECOMP_ME}/scratch/{prod_slug}"
    return None


def _run_objdiff_changes(
    objdiff_cli: Path, base_report: Path, current_report: Path
) -> dict | None:
    """Run objdiff-cli report changes and return parsed JSON."""
    if not objdiff_cli.exists():
        console.print(f"[red]objdiff-cli not found at {objdiff_cli}[/red]")
        console.print("[dim]Run 'ninja' to build the tools first[/dim]")
        return None

    try:
        result = subprocess.run(
            [
                str(objdiff_cli),
                "report",
                "changes",
                "-f", "json",
                str(base_report),
                str(current_report),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]objdiff-cli error: {e.stderr}[/red]")
        return None
    except json.JSONDecodeError as e:
        console.print(f"[red]Failed to parse objdiff output: {e}[/red]")
        return None


def _parse_objdiff_changes(changes: dict) -> list[dict]:
    """Parse objdiff changes output to extract improved/newly matched functions.

    Returns list of {function, from_pct, to_pct, unit} dicts for functions that improved.
    """
    results = []

    for unit in changes.get("units", []):
        unit_name = unit.get("name", "")
        for func in unit.get("functions", []):
            if func is None:
                continue

            name = func.get("name", "")
            from_info = func.get("from") or {}
            to_info = func.get("to") or {}

            from_pct = from_info.get("fuzzy_match_percent", 0) or 0
            to_pct = to_info.get("fuzzy_match_percent", 0) or 0

            # Include if function improved (higher match %)
            if to_pct > from_pct:
                results.append({
                    "function": name,
                    "from_pct": from_pct,
                    "to_pct": to_pct,
                    "unit": unit_name,
                })

    # Sort by to_pct descending, then by name
    results.sort(key=lambda x: (-x["to_pct"], x["function"]))
    return results


def _get_modified_functions_from_diff(repo_path: Path, base_ref: str = "upstream/master") -> set[str]:
    """Extract function names that were actually modified in git diff.

    Uses two methods:
    1. Hunk context lines (@@ ... @@ function_name) - functions with internal changes
    2. Added function definitions - new or rewritten functions

    Returns set of function names that had their code directly modified.
    """
    modified_funcs = set()

    try:
        # Get the diff for C source files
        result = subprocess.run(
            ["git", "diff", base_ref, "--", "src/*.c"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        diff_output = result.stdout
    except subprocess.CalledProcessError:
        return modified_funcs

    # Pattern 1: Hunk headers with function context
    # Example: @@ -374,8 +375,14 @@ void ftCo_800D71D8(Fighter_GObj* gobj)
    hunk_pattern = re.compile(r'^@@[^@]+@@\s*(?:\w+\s+)*(\w+)\s*\(', re.MULTILINE)
    for match in hunk_pattern.finditer(diff_output):
        func_name = match.group(1)
        # Filter out common non-function matches
        if func_name not in ('if', 'for', 'while', 'switch', 'return'):
            modified_funcs.add(func_name)

    # Pattern 2: Added function definitions (new implementations)
    # Look for lines starting with + that define functions
    # Example: +void fn_8002087C(void) {
    added_func_pattern = re.compile(
        r'^\+\s*(?:static\s+)?(?:inline\s+)?'
        r'(?:void|bool|int|u8|u16|u32|s8|s16|s32|f32|f64|float|double|Fighter\*|HSD_GObj\*|\w+\*?)\s+'
        r'(\w+)\s*\(',
        re.MULTILINE
    )
    for match in added_func_pattern.finditer(diff_output):
        func_name = match.group(1)
        modified_funcs.add(func_name)

    return modified_funcs


def _check_upstream_status(melee_root: Path) -> tuple[str, bool, int]:
    """Check upstream/master status.

    Returns: (commit_hash, is_behind, commits_behind_count)
    """
    try:
        # Fetch upstream to get latest refs
        subprocess.run(
            ["git", "fetch", "upstream", "--quiet"],
            cwd=melee_root,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        console.print("[yellow]Warning: Could not fetch upstream[/yellow]")

    # Get local upstream/master commit
    try:
        result = subprocess.run(
            ["git", "rev-parse", "upstream/master"],
            cwd=melee_root,
            capture_output=True,
            text=True,
            check=True,
        )
        local_commit = result.stdout.strip()
    except subprocess.CalledProcessError:
        return "", False, 0

    # Check how far behind we are
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "upstream/master..upstream/master@{upstream}"],
            cwd=melee_root,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            behind_count = int(result.stdout.strip())
            return local_commit, behind_count > 0, behind_count
    except (subprocess.CalledProcessError, ValueError):
        pass

    return local_commit, False, 0


def _get_cached_baseline_path(commit_hash: str) -> Path:
    """Get path to cached baseline report for a commit."""
    from ._common import DECOMP_CONFIG_DIR
    cache_dir = DECOMP_CONFIG_DIR / "baseline_reports"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"report_{commit_hash[:12]}.json"


def _build_baseline_report(melee_root: Path, target_commit: str) -> Path | None:
    """Build baseline report.json for a specific commit.

    Uses the main melee repo to checkout and build.
    Returns path to the generated report.json or None on failure.
    """
    cached_path = _get_cached_baseline_path(target_commit)

    # Check cache first
    if cached_path.exists():
        console.print(f"[dim]Using cached baseline for {target_commit[:8]}[/dim]")
        return cached_path

    console.print(f"[cyan]Building baseline report for upstream/master ({target_commit[:8]})...[/cyan]")

    # Check if main melee repo is clean
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=melee_root,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        console.print("[yellow]Warning: Main melee repo has uncommitted changes[/yellow]")
        console.print("[dim]Using a detached HEAD checkout to avoid conflicts[/dim]")

    # Save current HEAD
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=melee_root,
        capture_output=True,
        text=True,
        check=True,
    )
    original_head = result.stdout.strip()

    try:
        # Checkout target commit (detached HEAD)
        subprocess.run(
            ["git", "checkout", "--detach", target_commit],
            cwd=melee_root,
            capture_output=True,
            check=True,
        )

        # Run configure and build
        console.print("[dim]Running configure.py...[/dim]")
        subprocess.run(
            ["python", "configure.py"],
            cwd=melee_root,
            capture_output=True,
            check=True,
        )

        console.print("[dim]Building with ninja (this may take a minute)...[/dim]")
        subprocess.run(
            ["ninja", "all_source", "build/GALE01/report.json"],
            cwd=melee_root,
            capture_output=True,
            check=True,
        )

        # Copy report to cache
        report_path = melee_root / "build" / "GALE01" / "report.json"
        if report_path.exists():
            import shutil
            shutil.copy2(report_path, cached_path)
            console.print(f"[green]Baseline cached at {cached_path}[/green]")
            return cached_path
        else:
            console.print("[red]Build succeeded but report.json not found[/red]")
            return None

    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e.stderr if e.stderr else e}[/red]")
        return None

    finally:
        # Restore original HEAD
        console.print("[dim]Restoring original state...[/dim]")
        subprocess.run(
            ["git", "checkout", original_head],
            cwd=melee_root,
            capture_output=True,
        )


def _detect_melee_root_from_cwd() -> Path:
    """Detect melee root from current working directory.

    Walks up from cwd looking for a melee repo (has src/melee/ directory).
    Falls back to get_agent_melee_root() if not found.
    """
    cwd = Path.cwd()

    # Check if we're in a worktree or the main melee repo
    for parent in [cwd] + list(cwd.parents):
        # Check for melee repo markers
        if (parent / "src" / "melee").exists() and (parent / "configure.py").exists():
            return parent

    # Fall back to agent-based detection
    return get_agent_melee_root(create_if_missing=False)


@pr_app.command("describe")
def pr_describe(
    base_report: Annotated[
        Optional[Path], typer.Argument(help="Path to base report.json (auto-generates from upstream/master if not provided)")
    ] = None,
    current_report: Annotated[
        Optional[Path], typer.Option("--current", "-c", help="Path to current report.json")
    ] = None,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
    copy_to_clipboard: Annotated[
        bool, typer.Option("--copy", help="Copy to clipboard (macOS)")
    ] = False,
    skip_fetch: Annotated[
        bool, typer.Option("--skip-fetch", help="Skip fetching upstream")
    ] = False,
):
    """Generate PR description by comparing progress reports.

    Compares the current branch against upstream/master and generates a
    description with links to production decomp.me scratches for improved
    functions.

    If no base report is provided, automatically builds one from upstream/master
    (cached for reuse).

    Example:
        # Auto-generate baseline from upstream/master
        melee-agent pr describe

        # Use a specific baseline
        melee-agent pr describe /path/to/base-report.json
    """
    # Detect repo from current directory, not environment
    repo_path = _detect_melee_root_from_cwd()
    main_melee = DEFAULT_MELEE_ROOT

    if not output_json:
        console.print(f"[dim]Using repo: {repo_path}[/dim]")

    # Get current report
    if current_report is None:
        current_report = repo_path / "build" / "GALE01" / "report.json"

    if not current_report.exists():
        if not output_json:
            console.print(f"[yellow]Current report not found, building...[/yellow]")
        try:
            # Run configure if needed
            if not (repo_path / "build.ninja").exists():
                if not output_json:
                    console.print("[dim]Running configure.py...[/dim]")
                subprocess.run(
                    ["python", "configure.py"],
                    cwd=repo_path,
                    check=True,
                )

            if not output_json:
                console.print("[dim]Building with ninja...[/dim]")
            subprocess.run(
                ["ninja", "all_source", "build/GALE01/report.json"],
                cwd=repo_path,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Build failed: {e}[/red]")
            raise typer.Exit(1)

        if not current_report.exists():
            console.print(f"[red]Build succeeded but report.json not found[/red]")
            raise typer.Exit(1)

    # Handle base report
    if base_report is not None:
        # User provided explicit base report
        if not base_report.exists():
            console.print(f"[red]Base report not found: {base_report}[/red]")
            raise typer.Exit(1)
    else:
        # Auto-generate from upstream/master
        if not output_json:
            console.print("[dim]Checking upstream/master status...[/dim]")

        commit_hash, is_behind, behind_count = _check_upstream_status(main_melee)
        if not commit_hash:
            console.print("[red]Could not determine upstream/master commit[/red]")
            console.print("[dim]Make sure 'upstream' remote is configured[/dim]")
            raise typer.Exit(1)

        if is_behind and not output_json:
            console.print(
                f"[yellow]Warning: upstream/master is {behind_count} commits behind remote[/yellow]"
            )
            console.print("[dim]Run 'git fetch upstream' in melee/ to update[/dim]")

        # Build or get cached baseline
        base_report = _get_cached_baseline_path(commit_hash)
        if not base_report.exists():
            base_report = _build_baseline_report(main_melee, commit_hash)
            if base_report is None:
                console.print("[red]Failed to build baseline report[/red]")
                raise typer.Exit(1)

    if not output_json:
        console.print(f"[dim]Comparing reports:[/dim]")
        console.print(f"[dim]  Base:    {base_report}[/dim]")
        console.print(f"[dim]  Current: {current_report}[/dim]\n")

    # Find objdiff-cli (prefer current repo, fall back to main)
    objdiff_cli = repo_path / "build" / "tools" / "objdiff-cli"
    if not objdiff_cli.exists():
        objdiff_cli = main_melee / "build" / "tools" / "objdiff-cli"

    # Run objdiff-cli
    changes = _run_objdiff_changes(objdiff_cli, base_report, current_report)
    if changes is None:
        raise typer.Exit(1)

    # Parse changes to find improved functions
    improved = _parse_objdiff_changes(changes)

    if not improved:
        console.print("[yellow]No improved functions found[/yellow]")
        raise typer.Exit(0)

    # Get functions that were actually modified in git diff
    modified_funcs = _get_modified_functions_from_diff(repo_path)
    if not output_json:
        console.print(f"[dim]Found {len(modified_funcs)} functions modified in git diff[/dim]")

    # Load slug_map for production URLs
    slug_map = load_slug_map()

    # Build function list with URLs and direct/incidental flag
    func_list = []
    for item in improved:
        prod_url = _get_production_scratch_url(item["function"], slug_map)
        func_list.append({
            "function": item["function"],
            "from_pct": item["from_pct"],
            "to_pct": item["to_pct"],
            "unit": item["unit"],
            "production_url": prod_url,
            "direct": item["function"] in modified_funcs,
        })

    if output_json:
        print(json.dumps({
            "base_report": str(base_report),
            "current_report": str(current_report),
            "functions": func_list,
        }, indent=2))
        return

    # Generate markdown description
    lines = []
    lines.append("## Matched Functions\n")

    # Separate direct vs incidental improvements
    direct_funcs = [f for f in func_list if f["direct"]]
    incidental_funcs = [f for f in func_list if not f["direct"]]

    # Group direct improvements by match percentage
    direct_perfect = [f for f in direct_funcs if f["to_pct"] == 100]
    direct_near = [f for f in direct_funcs if 95 <= f["to_pct"] < 100]
    direct_partial = [f for f in direct_funcs if f["to_pct"] < 95]

    def format_func(f: dict, show_improvement: bool = True) -> str:
        name = f["function"]
        to_pct = f["to_pct"]
        from_pct = f["from_pct"]
        url = f["production_url"]

        # Show improvement if not from 0 and requested
        if show_improvement and from_pct > 0:
            pct_str = f"{from_pct:.1f}% -> {to_pct:.1f}%"
        else:
            pct_str = f"{to_pct:.1f}%"

        if url:
            return f"- [`{name}`]({url}) ({pct_str})"
        else:
            return f"- `{name}` ({pct_str})"

    if direct_perfect:
        lines.append(f"### 100% Matches ({len(direct_perfect)})\n")
        for f in direct_perfect:
            lines.append(format_func(f))
        lines.append("")

    if direct_near:
        lines.append(f"### Near-Perfect Matches ({len(direct_near)})\n")
        for f in direct_near:
            lines.append(format_func(f))
        lines.append("")

    if direct_partial:
        lines.append(f"### Partial Matches ({len(direct_partial)})\n")
        for f in direct_partial:
            lines.append(format_func(f))
        lines.append("")

    # Show incidental improvements in a separate section
    if incidental_funcs:
        lines.append(f"### Incidental Improvements ({len(incidental_funcs)})\n")
        lines.append("*These functions improved due to changes in related code (shared inlines, etc.)*\n")
        for f in incidental_funcs:
            lines.append(format_func(f, show_improvement=True))
        lines.append("")

    # Summary
    total = len(func_list)
    direct_count = len(direct_funcs)
    incidental_count = len(incidental_funcs)
    with_urls = sum(1 for f in func_list if f["production_url"])
    lines.append("---")
    if incidental_count > 0:
        lines.append(f"*{total} functions improved ({direct_count} direct, {incidental_count} incidental)*")
    else:
        lines.append(f"*{total} functions improved*")
    if with_urls < total:
        lines.append(f"*({with_urls}/{total} have decomp.me links)*")

    description = "\n".join(lines)

    # Output
    console.print(description)

    if copy_to_clipboard:
        try:
            subprocess.run(
                ["pbcopy"],
                input=description.encode(),
                check=True,
            )
            console.print("\n[green]Copied to clipboard[/green]")
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("\n[yellow]Could not copy to clipboard[/yellow]")
