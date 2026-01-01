"""Describe PR command with objdiff integration."""

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.panel import Panel
from rich.table import Table

from .._common import (
    console,
    DEFAULT_MELEE_ROOT,
    PRODUCTION_DECOMP_ME,
    load_slug_map,
    get_agent_melee_root,
)

# Constants
OBJDIFF_CLI = "build/tools/objdiff-cli"


def _get_production_scratch_url(func_name: str, slug_map: dict) -> str | None:
    """Get production decomp.me scratch URL for a function."""
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
    """Parse objdiff changes output to extract improved/newly matched functions."""
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

            if to_pct > from_pct:
                results.append({
                    "function": name,
                    "from_pct": from_pct,
                    "to_pct": to_pct,
                    "unit": unit_name,
                })

    results.sort(key=lambda x: (-x["to_pct"], x["function"]))
    return results


def _get_modified_functions_from_diff(repo_path: Path, base_ref: str = "upstream/master") -> set[str]:
    """Extract function names that were actually modified in git diff."""
    modified_funcs = set()

    try:
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

    # Method 1: Extract from hunk context (@@ ... @@ function_name)
    hunk_pattern = re.compile(r'^@@ .* @@ (\w+)\(', re.MULTILINE)
    for match in hunk_pattern.finditer(diff_output):
        func_name = match.group(1)
        if '_' in func_name and not func_name.startswith('_'):
            modified_funcs.add(func_name)

    # Method 2: Look for added function definitions
    added_def_pattern = re.compile(r'^\+\s*(?:static\s+)?(?:inline\s+)?(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*\{?\s*$', re.MULTILINE)
    for match in added_def_pattern.finditer(diff_output):
        func_name = match.group(1)
        if '_' in func_name and not func_name.startswith('_') and func_name not in ('if', 'while', 'for', 'switch'):
            modified_funcs.add(func_name)

    return modified_funcs


def _check_upstream_status(melee_root: Path) -> tuple[str, bool, int]:
    """Check if worktree is up to date with upstream/master."""
    try:
        subprocess.run(
            ["git", "fetch", "upstream"],
            cwd=melee_root,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        result = subprocess.run(
            ["git", "rev-parse", "upstream/master"],
            cwd=melee_root,
            capture_output=True,
            text=True,
            check=True,
        )
        upstream_commit = result.stdout.strip()
    except subprocess.CalledProcessError:
        return "", False, 0

    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"upstream/master..HEAD"],
            cwd=melee_root,
            capture_output=True,
            text=True,
            check=True,
        )
        commits_ahead = int(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        commits_ahead = 0

    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "upstream/master", "HEAD"],
            cwd=melee_root,
            capture_output=True,
            check=False,
        )
        is_descendant = result.returncode == 0
    except subprocess.CalledProcessError:
        is_descendant = False

    return upstream_commit, is_descendant, commits_ahead


def _get_cached_baseline_path(commit_hash: str) -> Path:
    """Get path to cached baseline report for a commit."""
    from .._common import DECOMP_CONFIG_DIR
    cache_dir = DECOMP_CONFIG_DIR / "baseline_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"report_{commit_hash[:12]}.json"


def _build_baseline_report(melee_root: Path, target_commit: str) -> Path | None:
    """Build baseline report.json at a specific commit."""
    cache_path = _get_cached_baseline_path(target_commit)
    if cache_path.exists():
        console.print(f"[dim]Using cached baseline report for {target_commit[:8]}[/dim]")
        return cache_path

    console.print(f"[dim]Building baseline report for {target_commit[:8]}...[/dim]")

    stash_result = subprocess.run(
        ["git", "stash", "--include-untracked", "-m", "pr-describe-temp"],
        cwd=melee_root,
        capture_output=True,
        text=True,
    )
    had_stash = "No local changes" not in stash_result.stdout

    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=melee_root,
        capture_output=True,
        text=True,
    ).stdout.strip()

    try:
        subprocess.run(
            ["git", "checkout", target_commit],
            cwd=melee_root,
            capture_output=True,
            check=True,
        )

        configure_result = subprocess.run(
            ["python", "configure.py"],
            cwd=melee_root,
            capture_output=True,
            text=True,
        )

        build_result = subprocess.run(
            ["ninja", "-k0"],
            cwd=melee_root,
            capture_output=True,
            text=True,
            timeout=300,
        )

        report_path = melee_root / "build" / "GALE01" / "report.json"
        if report_path.exists():
            import shutil
            shutil.copy(report_path, cache_path)
            console.print(f"[green]Built and cached baseline report[/green]")
            return cache_path
        else:
            console.print("[yellow]Build succeeded but report.json not found[/yellow]")
            return None

    except subprocess.TimeoutExpired:
        console.print("[red]Build timed out[/red]")
        return None
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        return None
    finally:
        subprocess.run(
            ["git", "checkout", current_branch],
            cwd=melee_root,
            capture_output=True,
        )
        subprocess.run(
            ["python", "configure.py"],
            cwd=melee_root,
            capture_output=True,
        )
        if had_stash:
            subprocess.run(
                ["git", "stash", "pop"],
                cwd=melee_root,
                capture_output=True,
            )


def _detect_melee_root_from_cwd() -> Path:
    """Try to detect melee root from current working directory."""
    cwd = Path.cwd()
    if (cwd / "configure.py").exists() and (cwd / "build.ninja").exists():
        return cwd
    if (cwd / "melee" / "configure.py").exists():
        return cwd / "melee"
    return get_agent_melee_root()


def describe_command(
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="Output format: table, markdown, json")
    ] = "table",
    include_links: Annotated[
        bool, typer.Option("--links/--no-links", help="Include decomp.me scratch links")
    ] = True,
    build_baseline: Annotated[
        bool, typer.Option("--build-baseline/--no-build-baseline", help="Build baseline if needed (slow)")
    ] = False,
    force_rebuild: Annotated[
        bool, typer.Option("--force-rebuild", help="Force rebuild of current report.json")
    ] = False,
    show_all: Annotated[
        bool, typer.Option("--all", "-a", help="Show all changed functions, not just 100% matches")
    ] = False,
    show_direct_only: Annotated[
        bool, typer.Option("--direct-only", help="Only show functions with direct code changes (from git diff)")
    ] = False,
):
    """Generate PR description showing matched functions.

    Compares current build against upstream/master baseline to identify
    newly matched or improved functions. Uses objdiff-cli for accurate
    comparison of match percentages.

    By default, auto-detects melee root from current directory.
    """
    if melee_root is None:
        melee_root = _detect_melee_root_from_cwd()
    
    if not melee_root.exists():
        console.print(f"[red]Melee root not found: {melee_root}[/red]")
        raise typer.Exit(1)

    objdiff_cli = melee_root / OBJDIFF_CLI
    current_report = melee_root / "build" / "GALE01" / "report.json"

    if force_rebuild or not current_report.exists():
        console.print("[dim]Building current report.json...[/dim]")
        subprocess.run(
            ["python", "configure.py"],
            cwd=melee_root,
            capture_output=True,
        )
        subprocess.run(
            ["ninja"],
            cwd=melee_root,
            capture_output=True,
        )

    if not current_report.exists():
        console.print("[red]report.json not found after build[/red]")
        raise typer.Exit(1)

    upstream_commit, is_descendant, commits_ahead = _check_upstream_status(melee_root)
    if not upstream_commit:
        console.print("[red]Could not determine upstream/master commit[/red]")
        raise typer.Exit(1)

    console.print(f"[dim]Upstream: {upstream_commit[:8]} | Ahead by: {commits_ahead} commits[/dim]")

    baseline_path = _get_cached_baseline_path(upstream_commit)
    if not baseline_path.exists():
        if build_baseline:
            baseline_path = _build_baseline_report(melee_root, upstream_commit)
            if not baseline_path:
                console.print("[red]Failed to build baseline report[/red]")
                raise typer.Exit(1)
        else:
            console.print("[yellow]No cached baseline found[/yellow]")
            console.print("[dim]Run with --build-baseline to generate (takes a few minutes)[/dim]")
            raise typer.Exit(1)

    changes = _run_objdiff_changes(objdiff_cli, baseline_path, current_report)
    if not changes:
        console.print("[red]Failed to compare reports[/red]")
        raise typer.Exit(1)

    improved = _parse_objdiff_changes(changes)

    if show_direct_only:
        direct_modified = _get_modified_functions_from_diff(melee_root)
        improved = [f for f in improved if f["function"] in direct_modified]

    if not show_all:
        improved = [f for f in improved if f["to_pct"] >= 100]

    if not improved:
        console.print("[green]No improved functions found (or all already at 100%)[/green]")
        return

    slug_map = load_slug_map() if include_links else {}

    if output_format == "json":
        output_data = []
        for f in improved:
            entry = {
                "function": f["function"],
                "from_pct": f["from_pct"],
                "to_pct": f["to_pct"],
                "unit": f["unit"],
            }
            if include_links:
                url = _get_production_scratch_url(f["function"], slug_map)
                if url:
                    entry["scratch_url"] = url
            output_data.append(entry)
        print(json.dumps(output_data, indent=2))
        return

    if output_format == "markdown":
        console.print("## Matched Functions\n")
        console.print("| Function | Match | Unit | Scratch |")
        console.print("|----------|-------|------|---------|")
        for f in improved:
            url = _get_production_scratch_url(f["function"], slug_map) if include_links else None
            link = f"[link]({url})" if url else "-"
            pct_str = f"{f['from_pct']:.0f}% → {f['to_pct']:.0f}%" if f['from_pct'] > 0 else f"{f['to_pct']:.0f}%"
            console.print(f"| `{f['function']}` | {pct_str} | {f['unit']} | {link} |")
        return

    # Table format (default)
    table = Table(title=f"Matched Functions ({len(improved)} total)")
    table.add_column("Function", style="cyan")
    table.add_column("From", justify="right")
    table.add_column("To", justify="right")
    table.add_column("Unit", style="dim")
    if include_links:
        table.add_column("Scratch", style="dim")

    for f in improved:
        from_str = f"{f['from_pct']:.0f}%" if f['from_pct'] > 0 else "-"
        to_str = f"[green]{f['to_pct']:.0f}%[/green]" if f['to_pct'] >= 100 else f"{f['to_pct']:.0f}%"
        
        if include_links:
            url = _get_production_scratch_url(f["function"], slug_map)
            link = url.split("/")[-1] if url else "-"
            table.add_row(f["function"], from_str, to_str, f["unit"], link)
        else:
            table.add_row(f["function"], from_str, to_str, f["unit"])

    console.print(table)

    # Summary
    new_matches = len([f for f in improved if f["from_pct"] == 0 and f["to_pct"] >= 100])
    improved_matches = len([f for f in improved if f["from_pct"] > 0 and f["to_pct"] >= 100])
    partial = len([f for f in improved if f["to_pct"] < 100])

    console.print(f"\n[bold]Summary:[/bold]")
    if new_matches:
        console.print(f"  [green]New 100% matches: {new_matches}[/green]")
    if improved_matches:
        console.print(f"  [green]Improved to 100%: {improved_matches}[/green]")
    if partial:
        console.print(f"  [yellow]Partial improvements: {partial}[/yellow]")
