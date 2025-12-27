"""Audit commands - audit and recover tracked work."""

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
    load_all_tracking_data,
    categorize_functions,
    load_completed_functions,
    save_completed_functions,
    load_slug_map,
    parse_scratches_txt,
    extract_pr_info,
)

audit_app = typer.Typer(help="Audit and recover tracked work")


@audit_app.command("status")
def audit_status(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    check_prs: Annotated[
        bool, typer.Option("--check", "-c", help="Check live PR status via gh CLI")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show all entries")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show unified status of all tracked work.

    Categories (95%+ matches):
    - Merged: PR merged (done!)
    - In Review: PR is open, awaiting review
    - Committed: Committed to repo but no PR yet
    - Ready: Synced + in scratches.txt, ready to include in PR

    Issues needing attention:
    - Synced, not in file: Needs re-add to scratches.txt
    - In file, not synced: Local slug, needs sync to production
    - Lost: 95%+ but not tracked (needs recovery)

    Use --check to query live PR status from GitHub.
    """
    data = load_all_tracking_data(melee_root)
    categories = categorize_functions(data, check_pr_status=check_prs)

    if output_json:
        # Convert sets to lists for JSON serialization
        serializable = {}
        for key, value in categories.items():
            serializable[key] = value
        print(json.dumps(serializable, indent=2))
        return

    console.print("[bold]Tracking Audit Summary[/bold]\n")

    # Progress section
    table = Table(title="Progress")
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Description")

    table.add_row(
        "[green]Merged[/green]",
        str(len(categories["merged"])),
        "PR merged - done!"
    )
    table.add_row(
        "[cyan]In Review[/cyan]",
        str(len(categories["in_review"])),
        "PR open, awaiting review"
    )
    table.add_row(
        "[blue]Committed[/blue]",
        str(len(categories["committed"])),
        "Committed locally, needs PR"
    )
    table.add_row(
        "[green]Ready[/green]",
        str(len(categories["ready"])),
        "Synced + in file, ready for PR"
    )
    table.add_row(
        "[dim]Work in progress[/dim]",
        str(len(categories["work_in_progress"])),
        "< 95% match"
    )

    console.print(table)

    # Issues section
    issues_count = (len(categories["synced_not_in_file"]) +
                   len(categories["in_file_not_synced"]) +
                   len(categories["lost_high_match"]))

    if issues_count > 0:
        console.print()
        issues_table = Table(title="Issues Needing Attention")
        issues_table.add_column("Issue", style="bold")
        issues_table.add_column("Count", justify="right")
        issues_table.add_column("Fix")

        if categories["synced_not_in_file"]:
            issues_table.add_row(
                "[yellow]Synced, not in file[/yellow]",
                str(len(categories["synced_not_in_file"])),
                "audit recover --add-to-file"
            )
        if categories["in_file_not_synced"]:
            issues_table.add_row(
                "[yellow]In file, not synced[/yellow]",
                str(len(categories["in_file_not_synced"])),
                "sync production"
            )
        if categories["lost_high_match"]:
            issues_table.add_row(
                "[red]Lost (95%+)[/red]",
                str(len(categories["lost_high_match"])),
                "audit recover --sync-lost"
            )

        console.print(issues_table)

    # Verbose details
    if verbose or categories["lost_high_match"]:
        if categories["lost_high_match"]:
            console.print("\n[red bold]Lost matches needing recovery:[/red bold]")
            for entry in categories["lost_high_match"][:10]:
                console.print(f"  {entry['function']}: {entry['match_percent']}% (local:{entry['local_slug']})")
            if len(categories["lost_high_match"]) > 10:
                console.print(f"  [dim]... and {len(categories['lost_high_match']) - 10} more[/dim]")

    if verbose and categories["in_review"]:
        console.print("\n[cyan bold]In Review:[/cyan bold]")
        for entry in categories["in_review"][:10]:
            pr_url = entry.get("pr_url", "")
            console.print(f"  {entry['function']}: {entry['match_percent']}% - {pr_url}")
        if len(categories["in_review"]) > 10:
            console.print(f"  [dim]... and {len(categories['in_review']) - 10} more[/dim]")

    if verbose and categories["synced_not_in_file"]:
        console.print("\n[yellow bold]Synced but missing from scratches.txt:[/yellow bold]")
        for entry in categories["synced_not_in_file"][:10]:
            console.print(f"  {entry['function']}: {entry['match_percent']}% (prod:{entry['production_slug']})")
        if len(categories["synced_not_in_file"]) > 10:
            console.print(f"  [dim]... and {len(categories['synced_not_in_file']) - 10} more[/dim]")


@audit_app.command("recover")
def audit_recover(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    add_to_file: Annotated[
        bool, typer.Option("--add-to-file", help="Add synced functions to scratches.txt")
    ] = False,
    sync_lost: Annotated[
        bool, typer.Option("--sync-lost", help="Add lost functions to scratches.txt (with local slugs)")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be done")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum entries to process")
    ] = 20,
):
    """Recover lost or missing tracking entries.

    --add-to-file: Add entries for functions that are already synced to production
                   but missing from scratches.txt (uses production slugs)

    --sync-lost: Add entries for "lost" 95%+ functions to scratches.txt using
                 their LOCAL slugs. After running this, use 'sync production'
                 to push them to production and update the slugs.
    """
    data = load_all_tracking_data(melee_root)
    categories = categorize_functions(data)

    if add_to_file:
        entries = categories["synced_not_in_file"][:limit]
        if not entries:
            console.print("[green]No synced functions missing from scratches.txt[/green]")
            return

        console.print(f"[bold]Adding {len(entries)} entries to scratches.txt[/bold]\n")

        scratches_file = melee_root / "config" / "GALE01" / "scratches.txt"
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        lines_to_add = []
        for entry in entries:
            prod_slug = entry["production_slug"]
            func = entry["function"]
            pct = entry["match_percent"]

            pct_str = "100%" if pct == 100 else f"{pct:.1f}%"
            line = f"{func} = {pct_str}:MATCHED; // author:agent id:{prod_slug} updated:{now} created:{now}"
            lines_to_add.append(line)

            if dry_run:
                console.print(f"  [dim]Would add:[/dim] {func} (id:{prod_slug})")
            else:
                console.print(f"  [green]Adding:[/green] {func} (id:{prod_slug})")

        if not dry_run:
            with open(scratches_file, 'a') as f:
                f.write("\n" + "\n".join(lines_to_add) + "\n")
            console.print(f"\n[green]Added {len(lines_to_add)} entries to scratches.txt[/green]")

            # Also update completed_functions.json
            completed = load_completed_functions()
            updated_count = 0
            for entry in entries:
                func = entry["function"]
                if func not in completed:
                    completed[func] = {
                        "match_percent": entry["match_percent"],
                        "scratch_slug": entry.get("local_slug", ""),
                        "production_slug": entry["production_slug"],
                        "committed": False,
                        "notes": "Recovered via audit recover --add-to-file",
                        "timestamp": time.time(),
                    }
                    updated_count += 1
                elif not completed[func].get("production_slug"):
                    completed[func]["production_slug"] = entry["production_slug"]
                    updated_count += 1

            if updated_count > 0:
                save_completed_functions(completed)
                console.print(f"[green]Updated {updated_count} entries in completed_functions.json[/green]")
        else:
            console.print(f"\n[cyan]Would add {len(lines_to_add)} entries (dry run)[/cyan]")

    if sync_lost:
        entries = categories["lost_high_match"][:limit]
        if not entries:
            console.print("[green]No lost scratches to sync[/green]")
            return

        console.print(f"[bold]Adding {len(entries)} lost functions to scratches.txt[/bold]\n")
        console.print("[dim]These have LOCAL slugs - run 'sync production' next to push to production[/dim]\n")

        scratches_file = melee_root / "config" / "GALE01" / "scratches.txt"
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        lines_to_add = []
        for entry in entries:
            local_slug = entry["local_slug"]
            func = entry["function"]
            pct = entry["match_percent"]

            if not local_slug:
                console.print(f"  [yellow]Skipping {func} - no local slug[/yellow]")
                continue

            pct_str = "100%" if pct == 100 else f"{pct:.1f}%"
            line = f"{func} = {pct_str}:MATCHED; // author:itsgrimetime id:{local_slug} updated:{now} created:{now}"
            lines_to_add.append(line)

            if dry_run:
                console.print(f"  [dim]Would add:[/dim] {func} ({pct_str}) id:{local_slug}")
            else:
                console.print(f"  [green]Adding:[/green] {func} ({pct_str}) id:{local_slug}")

        if not lines_to_add:
            console.print("[yellow]No entries to add[/yellow]")
            return

        if not dry_run:
            with open(scratches_file, 'a') as f:
                f.write("\n" + "\n".join(lines_to_add) + "\n")
            console.print(f"\n[green]Added {len(lines_to_add)} entries to scratches.txt[/green]")
            console.print("\n[bold]Next step:[/bold] Run 'melee-agent sync production' to push to production")
        else:
            console.print(f"\n[cyan]Would add {len(lines_to_add)} entries (dry run)[/cyan]")

    if not add_to_file and not sync_lost:
        console.print("[yellow]Specify --add-to-file or --sync-lost[/yellow]")
        console.print("\nRun 'melee-agent audit status' to see what needs recovery")


@audit_app.command("list")
def audit_list(
    category: Annotated[
        str, typer.Argument(help="Category: merged, review, committed, ready, synced, lost, wip, all")
    ] = "all",
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage")
    ] = 0.0,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List tracked functions by category.

    Categories:
    - merged: PR merged (done)
    - review: PR open, in review
    - committed: Committed but no PR
    - ready: Synced + in file, ready for PR
    - synced: Synced but not in scratches.txt
    - lost: 95%+ but not tracked
    - wip: Work in progress (<95%)
    - all: Everything
    """
    data = load_all_tracking_data(melee_root)
    categories = categorize_functions(data)

    cat_map = {
        "merged": "merged",
        "review": "in_review",
        "committed": "committed",
        "ready": "ready",
        "synced": "synced_not_in_file",
        "lost": "lost_high_match",
        "wip": "work_in_progress",
    }

    if category == "all":
        entries = []
        for cat_entries in categories.values():
            entries.extend(cat_entries)
    elif category in cat_map:
        entries = categories[cat_map[category]]
    else:
        console.print(f"[red]Unknown category: {category}[/red]")
        console.print("Valid: merged, review, committed, ready, synced, lost, wip, all")
        raise typer.Exit(1)

    entries = [e for e in entries if e["match_percent"] >= min_match]
    entries.sort(key=lambda x: -x["match_percent"])

    if output_json:
        print(json.dumps(entries, indent=2))
        return

    table = Table(title=f"Functions: {category}")
    table.add_column("Function", style="cyan")
    table.add_column("Match %", justify="right")
    table.add_column("Local Slug")
    table.add_column("Prod Slug")
    table.add_column("Notes", style="dim")

    for entry in entries[:50]:
        table.add_row(
            entry["function"],
            f"{entry['match_percent']:.1f}%",
            entry["local_slug"] or "-",
            entry["production_slug"] or "-",
            entry["notes"][:30] if entry["notes"] else ""
        )

    console.print(table)
    if len(entries) > 50:
        console.print(f"[dim]... and {len(entries) - 50} more[/dim]")


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


def _extract_functions_from_pr(pr: dict) -> list[dict]:
    """Extract function matches from PR body and title.

    Looks for patterns like:
    - func_name (100%)
    - Match: func_name
    - func_name = 100%
    """
    functions = []
    seen = set()

    text = (pr.get("title", "") + "\n" + pr.get("body", "") or "")

    # Pattern 1: func_name (100%) or func_name (95.5%)
    for match in re.finditer(r'(\w+)\s*\((\d+(?:\.\d+)?%)\)', text):
        func_name = match.group(1)
        pct_str = match.group(2).rstrip('%')
        if func_name not in seen:
            seen.add(func_name)
            functions.append({
                "function": func_name,
                "match_percent": float(pct_str),
            })

    # Pattern 2: Match: func_name or Match func_name
    for match in re.finditer(r'[Mm]atch[:\s]+(\w+)', text):
        func_name = match.group(1)
        if func_name not in seen:
            seen.add(func_name)
            functions.append({
                "function": func_name,
                "match_percent": 100.0,  # Assume 100% if just "Match:"
            })

    # Pattern 3: func_name = 100% (scratches.txt format)
    for match in re.finditer(r'^(\w+)\s*=\s*(\d+(?:\.\d+)?)%', text, re.MULTILINE):
        func_name = match.group(1)
        pct_str = match.group(2)
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
    PR titles and bodies. Updates completed_functions.json with PR associations.

    Example: melee-agent audit discover-prs --author itsgrimetime --state merged
    """
    console.print(f"[bold]Scanning GitHub PRs[/bold]")
    console.print(f"  Repo: {repo}")
    console.print(f"  Author: {author}")
    console.print(f"  State: {state}")
    console.print()

    # Handle 'all' state by querying both merged and open
    if state == "all":
        states_to_query = ["merged", "open"]
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
    results = []
    total_linked = 0
    total_updated = 0

    for pr in all_prs:
        pr_url = pr.get("url", "")
        pr_number = pr.get("number", 0)
        pr_state = pr.get("state", "UNKNOWN")
        is_merged = pr.get("mergedAt") is not None or pr.get("_queried_state") == "merged"

        functions = _extract_functions_from_pr(pr)
        if not functions:
            continue

        linked_funcs = []
        for func_info in functions:
            func = func_info["function"]
            if func in completed:
                current = completed[func]
                needs_update = False

                # Link if no PR currently
                if not current.get("pr_url"):
                    current["pr_url"] = pr_url
                    current["pr_number"] = pr_number
                    current["pr_repo"] = repo
                    needs_update = True
                    total_linked += 1

                # Update state if merged
                if is_merged and current.get("pr_url") == pr_url:
                    if current.get("pr_state") != "MERGED":
                        current["pr_state"] = "MERGED"
                        needs_update = True
                        total_updated += 1

                if needs_update:
                    linked_funcs.append(func)

        if linked_funcs:
            results.append({
                "pr_number": pr_number,
                "pr_url": pr_url,
                "state": "MERGED" if is_merged else pr_state,
                "functions": linked_funcs,
            })

    if output_json:
        print(json.dumps(results, indent=2))
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
    console.print(f"  Functions marked merged: {total_updated}")

    if not dry_run and (total_linked > 0 or total_updated > 0):
        save_completed_functions(completed)
        console.print(f"\n[green]Saved changes to completed_functions.json[/green]")
    elif dry_run:
        console.print(f"\n[cyan](dry run - no changes saved)[/cyan]")


@audit_app.command("rebuild")
def audit_rebuild(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    author: Annotated[
        str, typer.Option("--author", "-a", help="Author for GitHub queries")
    ] = "itsgrimetime",
    skip_github: Annotated[
        bool, typer.Option("--skip-github", help="Skip GitHub PR discovery")
    ] = False,
    skip_git: Annotated[
        bool, typer.Option("--skip-git", help="Skip local git commit analysis")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would change")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show detailed progress")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output summary as JSON")
    ] = False,
):
    """Rebuild tracking data from all sources.

    Reconciles state from:
    - slug_map.json (production slug mappings)
    - scratches.txt (entries in the repo file)
    - GitHub PRs (if --skip-github not set)
    - Local git commits (if --skip-git not set)

    Updates completed_functions.json with consolidated data.

    Example: melee-agent audit rebuild --dry-run --verbose
    """
    stats = {
        "slug_map_merged": 0,
        "scratches_txt_merged": 0,
        "github_prs_found": 0,
        "github_functions_linked": 0,
        "git_committed_found": 0,
        "errors": [],
    }

    completed = load_completed_functions()
    initial_count = len(completed)

    if verbose:
        console.print("[bold]Phase 1: Merging from slug_map.json[/bold]")

    # Step 1: Merge from slug_map
    slug_map = load_slug_map()
    for prod_slug, info in slug_map.items():
        func = info.get("function")
        if not func:
            continue

        if func not in completed:
            completed[func] = {
                "match_percent": info.get("match_percent", 100.0),
                "scratch_slug": info.get("local_slug", ""),
                "production_slug": prod_slug,
                "committed": False,
                "notes": "Recovered from slug_map",
                "timestamp": time.time(),
            }
            stats["slug_map_merged"] += 1
            if verbose:
                console.print(f"  + {func} (from slug_map)")
        elif not completed[func].get("production_slug"):
            completed[func]["production_slug"] = prod_slug
            completed[func]["scratch_slug"] = info.get("local_slug",
                completed[func].get("scratch_slug", ""))
            stats["slug_map_merged"] += 1
            if verbose:
                console.print(f"  ~ {func} (added production_slug)")

    if verbose:
        console.print(f"  Merged {stats['slug_map_merged']} entries\n")
        console.print("[bold]Phase 2: Merging from scratches.txt[/bold]")

    # Step 2: Merge from scratches.txt
    scratches_file = melee_root / "config" / "GALE01" / "scratches.txt"
    scratches_entries = parse_scratches_txt(scratches_file)

    for entry in scratches_entries:
        func = entry["function"]
        pct = entry["match_percent"]

        if pct < 95:
            continue  # Skip low-match entries

        if func not in completed:
            completed[func] = {
                "match_percent": pct,
                "scratch_slug": entry.get("slug", ""),
                "production_slug": entry.get("slug", ""),  # Assume production slug in file
                "committed": False,
                "notes": "Recovered from scratches.txt",
                "timestamp": time.time(),
            }
            stats["scratches_txt_merged"] += 1
            if verbose:
                console.print(f"  + {func} ({pct}%)")
        elif completed[func].get("match_percent", 0) < pct:
            # Update if scratches.txt has higher match
            completed[func]["match_percent"] = pct
            stats["scratches_txt_merged"] += 1
            if verbose:
                console.print(f"  ~ {func} (updated to {pct}%)")

    if verbose:
        console.print(f"  Merged {stats['scratches_txt_merged']} entries\n")

    # Step 3: Discover from GitHub
    if not skip_github:
        if verbose:
            console.print("[bold]Phase 3: Discovering from GitHub PRs[/bold]")

        try:
            for state in ["merged", "open"]:
                prs = _list_github_prs("doldecomp/melee", author, state, 100)
                stats["github_prs_found"] += len(prs)

                for pr in prs:
                    pr_url = pr.get("url", "")
                    pr_number = pr.get("number", 0)
                    is_merged = pr.get("mergedAt") is not None or state == "merged"

                    functions = _extract_functions_from_pr(pr)
                    for func_info in functions:
                        func = func_info["function"]
                        if func in completed:
                            current = completed[func]
                            if not current.get("pr_url"):
                                current["pr_url"] = pr_url
                                current["pr_number"] = pr_number
                                current["pr_repo"] = "doldecomp/melee"
                                stats["github_functions_linked"] += 1
                                if verbose:
                                    console.print(f"  → {func} linked to PR #{pr_number}")

                            if is_merged and current.get("pr_state") != "MERGED":
                                current["pr_state"] = "MERGED"

            if verbose:
                console.print(f"  Found {stats['github_prs_found']} PRs, linked {stats['github_functions_linked']} functions\n")
        except Exception as e:
            stats["errors"].append(f"GitHub: {e}")
            if verbose:
                console.print(f"  [yellow]Error: {e}[/yellow]\n")

    # Step 4: Analyze git commits
    if not skip_git:
        if verbose:
            console.print("[bold]Phase 4: Analyzing git commits[/bold]")

        try:
            # Get list of functions that might have commits
            functions_to_check = [
                func for func, info in completed.items()
                if info.get("match_percent", 0) >= 95 and not info.get("committed")
            ]

            for func in functions_to_check:
                # Check if function appears in commit messages in melee repo
                cmd = [
                    "git", "log", "--oneline", "--all", "-1",
                    f"--grep={func}", "--", "src/melee/"
                ]
                try:
                    result = subprocess.run(
                        cmd, cwd=melee_root, capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        completed[func]["committed"] = True
                        stats["git_committed_found"] += 1
                        if verbose:
                            commit = result.stdout.strip().split()[0]
                            console.print(f"  ✓ {func} (commit {commit})")
                except subprocess.TimeoutExpired:
                    pass

            if verbose:
                console.print(f"  Found {stats['git_committed_found']} committed functions\n")
        except Exception as e:
            stats["errors"].append(f"Git: {e}")
            if verbose:
                console.print(f"  [yellow]Error: {e}[/yellow]\n")

    # Output results
    if output_json:
        print(json.dumps(stats, indent=2))
        return

    console.print("[bold]Rebuild Summary[/bold]")
    console.print(f"  Initial entries: {initial_count}")
    console.print(f"  Final entries: {len(completed)}")
    console.print()
    console.print(f"  From slug_map: {stats['slug_map_merged']}")
    console.print(f"  From scratches.txt: {stats['scratches_txt_merged']}")
    if not skip_github:
        console.print(f"  GitHub PRs scanned: {stats['github_prs_found']}")
        console.print(f"  Functions linked to PRs: {stats['github_functions_linked']}")
    if not skip_git:
        console.print(f"  Functions marked committed: {stats['git_committed_found']}")

    if stats["errors"]:
        console.print()
        console.print("[yellow]Errors encountered:[/yellow]")
        for err in stats["errors"]:
            console.print(f"  - {err}")

    if not dry_run:
        save_completed_functions(completed)
        console.print(f"\n[green]Saved changes to completed_functions.json[/green]")
    else:
        console.print(f"\n[cyan](dry run - no changes saved)[/cyan]")
