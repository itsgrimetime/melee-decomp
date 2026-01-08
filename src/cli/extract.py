"""Extract commands - list and extract unmatched functions."""

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
    get_context_file,
    resolve_melee_root,
    detect_local_api_url,
    AGENT_ID,
    db_upsert_function,
    db_upsert_scratch,
    get_compiler_for_source,
)

# Try to import tree-sitter based functions for better accuracy
try:
    from src.hooks.c_analyzer import (
        strip_function_bodies as _ts_strip_function_bodies,
        strip_target_function as _ts_strip_target_function,
        TREE_SITTER_AVAILABLE,
    )
except ImportError:
    TREE_SITTER_AVAILABLE = False
    _ts_strip_function_bodies = None
    _ts_strip_target_function = None

# Context file override from environment
_context_env = os.environ.get("DECOMP_CONTEXT_FILE", "")


def _get_context_file(source_file: str | None = None, melee_root: Path | None = None) -> Path:
    """Get context file path.

    Args:
        source_file: Optional source file path to find per-file .ctx context.
        melee_root: Optional melee root path (for worktree support).
    """
    if _context_env:
        return Path(_context_env)
    return get_context_file(source_file=source_file, melee_root=melee_root)


def _count_braces(line: str) -> tuple[int, int]:
    """Count opening and closing braces, ignoring those in comments and strings.

    Returns:
        Tuple of (open_count, close_count)
    """
    # Remove // comments
    comment_pos = line.find('//')
    if comment_pos != -1:
        line = line[:comment_pos]

    # Remove string literals (handle escaped quotes)
    result = []
    in_string = False
    string_char = None
    i = 0
    while i < len(line):
        c = line[i]
        if in_string:
            if c == '\\' and i + 1 < len(line):
                i += 2  # Skip escaped character
                continue
            if c == string_char:
                in_string = False
        else:
            if c in '"\'':
                in_string = True
                string_char = c
            else:
                result.append(c)
        i += 1

    cleaned = ''.join(result)
    return cleaned.count('{'), cleaned.count('}')


def _strip_inline_functions(context: str) -> tuple[str, int]:
    """Strip inline function bodies from context, keeping declarations.

    This reduces context pollution where inline functions get compiled
    and appear in the diff output, making it harder to match the target function.

    Returns:
        Tuple of (filtered context, number of functions stripped)
    """
    import re

    lines = context.split('\n')
    filtered = []
    in_inline = False
    depth = 0
    stripped_count = 0
    signature_lines = []

    # Pattern to detect inline function definitions (not declarations ending with ;)
    inline_pattern = re.compile(r'^(?:static\s+)?inline\s+')

    for line in lines:
        stripped = line.strip()

        if not in_inline:
            # Check if this line starts an inline function definition
            if inline_pattern.match(stripped):
                # Skip if it's just a declaration (ends with ;)
                if stripped.endswith(';'):
                    filtered.append(line)
                    continue

                # This is a definition - collect the signature
                in_inline = True
                signature_lines = [stripped]
                open_braces, close_braces = _count_braces(line)
                depth = open_braces - close_braces
                stripped_count += 1

                # If opening brace is on this line, we have the full signature
                if open_braces > 0:
                    # Extract signature up to the brace
                    sig = stripped[:stripped.find('{')].rstrip()
                    # Remove 'inline' and 'static' keywords - static declarations without
                    # bodies cause MWCC to expect '{', and inline is invalid in C89 without body
                    sig = re.sub(r'\bstatic\s+', '', sig)
                    sig = re.sub(r'\binline\s+', '', sig)
                    filtered.append(sig + ';  // body stripped')
                    signature_lines = []

                    if depth <= 0:
                        in_inline = False
                continue

        if in_inline:
            # Still collecting signature or skipping body
            open_braces, close_braces = _count_braces(line)
            if depth == 0 and '{' not in ''.join(signature_lines):
                # Still in multi-line signature
                signature_lines.append(stripped)
                if open_braces > 0:
                    # Found the opening brace - emit declaration
                    full_sig = ' '.join(signature_lines)
                    sig = full_sig[:full_sig.find('{')].rstrip()
                    # Remove 'inline' and 'static' keywords - static declarations without
                    # bodies cause MWCC to expect '{', and inline is invalid in C89 without body
                    sig = re.sub(r'\bstatic\s+', '', sig)
                    sig = re.sub(r'\binline\s+', '', sig)
                    filtered.append(sig + ';  // body stripped')
                    signature_lines = []
                    depth = open_braces - close_braces
                    if depth <= 0:
                        in_inline = False
                continue

            depth += open_braces - close_braces
            if depth <= 0:
                in_inline = False
            continue

        filtered.append(line)

    return '\n'.join(filtered), stripped_count


def _strip_all_function_bodies(context: str, keep_functions: set[str] | None = None) -> tuple[str, int]:
    """Strip ALL function bodies from context, keeping only declarations.

    This is more aggressive than _strip_inline_functions - it strips any
    function definition, not just those marked inline. This prevents the
    compiler from auto-inlining functions with -inline auto.

    Uses tree-sitter for accurate parsing when available, falling back to
    regex-based heuristics otherwise. Tree-sitter properly distinguishes
    function bodies from struct/union bodies and typedefs.

    Args:
        context: The context string to process
        keep_functions: Optional set of function names to NOT strip (keep their bodies)

    Returns:
        Tuple of (filtered context, number of functions stripped)
    """
    # Use tree-sitter when available for accurate parsing
    if TREE_SITTER_AVAILABLE and _ts_strip_function_bodies is not None:
        return _ts_strip_function_bodies(context, keep_functions)

    # Fallback to regex-based approach
    return _strip_all_function_bodies_regex(context, keep_functions)


def _strip_all_function_bodies_regex(context: str, keep_functions: set[str] | None = None) -> tuple[str, int]:
    """Regex-based function body stripping (fallback when tree-sitter unavailable).

    WARNING: This can incorrectly strip struct/union bodies in some edge cases.
    Prefer tree-sitter based stripping when possible.
    """
    import re

    if keep_functions is None:
        keep_functions = set()

    lines = context.split('\n')
    filtered = []
    in_func = False
    in_signature = False
    depth = 0
    stripped_count = 0
    signature_lines = []
    current_func_name = None

    # Pattern to detect function definitions:
    # - Starts with optional storage class (static, extern, inline)
    # - Has a return type (void, int, s32, struct X*, etc.)
    # - Has a function name followed by (
    # - Does NOT end with ; (that would be a declaration)
    # Matches: "void func(", "static int foo(", "struct X* bar(", etc.
    func_def_pattern = re.compile(
        r'^(?:static\s+)?(?:inline\s+)?'  # optional static/inline
        r'(?:(?:const\s+)?'  # optional const
        r'(?:struct\s+\w+\s*\*?|union\s+\w+\s*\*?|enum\s+\w+|'  # struct/union/enum types
        r'unsigned\s+\w+|signed\s+\w+|'  # unsigned/signed types
        r'\w+)\s*\**\s+)'  # other types with optional pointer
        r'(\w+)\s*\('  # function name and opening paren
    )

    for line in lines:
        stripped = line.strip()

        if not in_func and not in_signature:
            # Check if this is a function definition
            match = func_def_pattern.match(stripped)
            if match and not stripped.endswith(';'):
                func_name = match.group(1)

                # Skip if this function should be kept
                if func_name in keep_functions:
                    filtered.append(line)
                    continue

                # Skip if line looks like a declaration or forward ref
                # (ends with ); after possible multi-line)
                if stripped.endswith(');'):
                    filtered.append(line)
                    continue

                # This is a function definition - start stripping
                in_signature = True
                signature_lines = [stripped]
                current_func_name = func_name
                stripped_count += 1

                open_braces, close_braces = _count_braces(line)
                if open_braces > 0:
                    # Found the opening brace - emit declaration and enter body
                    sig = stripped[:stripped.find('{')].rstrip()
                    # Remove 'inline' and 'static' keywords - static declarations without
                    # bodies cause MWCC to expect '{', and inline is invalid in C89 without body
                    sig = re.sub(r'\bstatic\s+', '', sig)
                    sig = re.sub(r'\binline\s+', '', sig)
                    filtered.append(sig + ';  /* body stripped: auto-inline prevention */')
                    in_signature = False
                    in_func = True
                    depth = open_braces - close_braces
                    signature_lines = []
                    if depth <= 0:
                        in_func = False
                        current_func_name = None
                continue

        if in_signature:
            # Collecting multi-line signature
            signature_lines.append(stripped)
            open_braces, close_braces = _count_braces(line)
            if open_braces > 0:
                # Found the opening brace - emit declaration
                full_sig = ' '.join(signature_lines)
                sig_end = full_sig.find('{')
                if sig_end > 0:
                    sig = full_sig[:sig_end].rstrip()
                else:
                    sig = full_sig.rstrip()
                # Remove 'inline' and 'static' keywords - static declarations without
                # bodies cause MWCC to expect '{', and inline is invalid in C89 without body
                sig = re.sub(r'\bstatic\s+', '', sig)
                sig = re.sub(r'\binline\s+', '', sig)
                filtered.append(sig + ';  /* body stripped: auto-inline prevention */')
                in_signature = False
                in_func = True
                depth = open_braces - close_braces
                signature_lines = []
                if depth <= 0:
                    in_func = False
                    current_func_name = None
            continue

        if in_func:
            # Inside function body - skip lines until we exit
            open_braces, close_braces = _count_braces(line)
            depth += open_braces - close_braces
            if depth <= 0:
                in_func = False
                current_func_name = None
            continue

        filtered.append(line)

    return '\n'.join(filtered), stripped_count


def _strip_target_function(context: str, func_name: str) -> str:
    """Strip the target function's definition from context, preserving calls.

    This removes the function definition to avoid redefinition errors when
    compiling the scratch, while keeping:
    - Function declarations (prototypes ending with ;)
    - Function calls (func_name appears without return type before it)
    - Comments mentioning the function

    Uses tree-sitter for accurate parsing when available.

    Args:
        context: The full context string
        func_name: Name of the function to strip

    Returns:
        Context with function definition removed
    """
    if func_name not in context:
        return context

    # Use tree-sitter when available for accurate parsing
    if TREE_SITTER_AVAILABLE and _ts_strip_target_function is not None:
        return _ts_strip_target_function(context, func_name)

    lines = context.split('\n')
    filtered = []
    in_func = False
    in_signature = False  # True when we've seen the function name but not yet the opening {
    depth = 0

    for line in lines:
        if not in_func and not in_signature and func_name in line and '(' in line:
            s = line.strip()
            # Skip comments, control flow
            if s.startswith('//') or s.startswith('if') or s.startswith('while'):
                filtered.append(line)
                continue
            # Keep declarations (prototypes) - they end with );
            if s.endswith(';'):
                filtered.append(line)
                continue
            # Check if this is a function CALL vs DEFINITION
            # A definition has a return type before the function name
            # A call starts with the function name (possibly with leading whitespace)
            # Also check for assignment (function pointer) or control flow
            func_pos = s.find(func_name)
            if func_pos > 0:
                before_func = s[:func_pos].rstrip()
                # If there's only whitespace before the function name, it's a call
                if not before_func:
                    filtered.append(line)
                    continue
                # If it's inside a block, after operator, or in a call, it's not a definition
                # { = inside function body, ( = inside call/condition, etc.
                if before_func.endswith(('=', ',', '(', '{', '!', '&', '|', '?', ':', ';')):
                    filtered.append(line)
                    continue
                # If it ends with a keyword/operator, it's likely a call context
                if before_func.endswith(('return', 'case')):
                    filtered.append(line)
                    continue
            elif func_pos == 0:
                # Function name at start of stripped line = likely a call
                filtered.append(line)
                continue
            # This is a function definition (has return type before name)
            filtered.append(f'// {func_name} definition stripped')
            open_b, close_b = _count_braces(line)
            if open_b > 0:
                # Opening brace on same line - we're in the body
                in_func = True
                depth = open_b - close_b
                if depth <= 0:
                    in_func = False
            else:
                # No brace yet - we're in a multi-line signature
                in_signature = True
            continue
        if in_signature:
            # Still looking for the opening brace
            open_b, close_b = _count_braces(line)
            if open_b > 0:
                # Found the opening brace - now we're in the body
                in_signature = False
                in_func = True
                depth = open_b - close_b
                if depth <= 0:
                    in_func = False
            # Either way, skip this line (it's part of the signature or opening brace)
            continue
        if in_func:
            open_b, close_b = _count_braces(line)
            depth += open_b - close_b
            if depth <= 0:
                in_func = False
            continue
        filtered.append(line)

    return '\n'.join(filtered)


extract_app = typer.Typer(help="Extract and list unmatched functions")


def _compute_recommendation_score(func) -> float:
    """Compute a recommendation score for function selection.

    Higher score = better candidate for matching.
    Factors:
    - Smaller functions are easier (50-300 bytes ideal)
    - Low match % means more room for improvement
    - Certain modules (ft/, lb/) are better documented
    """
    score = 100.0

    # Size scoring: prefer 50-300 bytes
    if func.size_bytes < 50:
        score -= 20  # Too small, likely trivial
    elif func.size_bytes <= 150:
        score += 20  # Ideal small
    elif func.size_bytes <= 300:
        score += 10  # Good medium
    elif func.size_bytes <= 500:
        score += 0   # Acceptable
    elif func.size_bytes <= 800:
        score -= 10  # Getting complex
    else:
        score -= 30  # Very complex

    # Match % scoring: prefer lower (more room to improve)
    if func.current_match < 0.10:
        score += 15  # Fresh start
    elif func.current_match < 0.30:
        score += 10  # Good candidate
    elif func.current_match < 0.50:
        score += 5   # Some work done
    elif func.current_match >= 0.95:
        score -= 40  # Already nearly done, likely stuck

    # Module scoring: prefer well-documented modules
    path = func.file_path.lower()
    if "/ft/" in path or "/lb/" in path:
        score += 15  # Fighter/Library - well documented
    elif "/gr/" in path:
        score += 10  # Ground - good patterns
    elif "/it/" in path:
        score += 5   # Item - reasonable
    elif "/mn/" in path or "/db/" in path:
        score -= 10  # Menu/Debug - less common

    return score


@extract_app.command("list")
def extract_list(
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-m", help="Path to melee submodule (auto-detects agent worktree)")
    ] = None,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage")
    ] = 0.0,
    max_match: Annotated[
        float, typer.Option("--max-match", help="Maximum match percentage")
    ] = 0.99,
    min_size: Annotated[
        int, typer.Option("--min-size", help="Minimum function size in bytes")
    ] = 0,
    max_size: Annotated[
        int, typer.Option("--max-size", help="Maximum function size in bytes")
    ] = 10000,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum number of results")
    ] = 20,
    include_completed: Annotated[
        bool, typer.Option("--include-completed", help="Include already-completed functions")
    ] = False,
    matching_only: Annotated[
        bool, typer.Option("--matching-only", "--committable", help="Only show functions in Matching files (can be committed)")
    ] = False,
    show_status: Annotated[
        bool, typer.Option("--show-status", help="Show object status column (Matching/NonMatching)")
    ] = False,
    module: Annotated[
        Optional[str], typer.Option("--module", help="Filter by module path (e.g., ft, lb, gr, it)")
    ] = None,
    sort_by: Annotated[
        str, typer.Option("--sort", help="Sort by: score (recommended), size, match")
    ] = "score",
    show_score: Annotated[
        bool, typer.Option("--show-score", help="Show recommendation score column")
    ] = False,
    exclude_subdir: Annotated[
        Optional[list[str]], typer.Option("--exclude-subdir", help="Exclude functions in these subdirectories (can be repeated)")
    ] = None,
    file_filter: Annotated[
        Optional[str], typer.Option("--file", "-f", help="Filter by filename (partial match, e.g., 'lbfile.c' or 'lb/')")
    ] = None,
    show_excluded: Annotated[
        bool, typer.Option("--show-excluded", help="Show diagnostic info about excluded functions")
    ] = False,
):
    """List unmatched functions from the melee project.

    Match percentages are read from the authoritative report.json which reflects
    the actual compiled state of decompiled code in the repository.

    By default, excludes only functions marked as 'merged' in the database.
    Use --include-completed to also include merged functions.

    Use --matching-only to only show functions in files already marked as Matching.
    These are the only functions that can be safely committed without linker errors
    from NonMatching file dependencies.

    Use --sort score to sort by recommendation score (best candidates first).
    Use --module ft to filter to fighter module only.

    To update match percentages after committing code:
        ninja build/GALE01/report.json
    """
    # Auto-detect agent worktree
    melee_root = resolve_melee_root(melee_root)

    from src.extractor import extract_unmatched_functions
    from src.extractor.report import ReportParser

    # Check if report.json exists and warn if stale
    report_parser = ReportParser(melee_root)
    if not (melee_root / "build" / "GALE01" / "report.json").exists():
        console.print("[yellow]Warning: report.json not found. Run 'ninja build/GALE01/report.json' to generate it.[/yellow]")
    elif report_parser.is_report_stale(max_age_hours=168):  # 1 week
        age_hours = report_parser.get_report_age_seconds() / 3600
        console.print(f"[dim]Note: report.json is {age_hours:.0f}h old. Run 'ninja build/GALE01/report.json' to refresh.[/dim]")

    # Don't load ASM for listing - it's not needed and adds significant overhead
    result = asyncio.run(extract_unmatched_functions(melee_root, include_asm=False))

    # Load merged functions to exclude (only truly completed ones)
    # Other statuses (in_progress, matched, committed) should still show
    # because they may need more work or verification
    merged = set()
    if not include_completed:
        from src.db import get_db
        db = get_db()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT function_name FROM functions
                WHERE status = 'merged'
            """)
            merged = {row[0] for row in cursor.fetchall()}

    # Build subdirectory exclusion check
    def _is_excluded_subdir(file_path: str) -> bool:
        if not exclude_subdir:
            return False
        path_lower = file_path.lower()
        for subdir in exclude_subdir:
            if f"/{subdir.lower()}/" in path_lower:
                return True
        return False

    # Build file filter check
    def _matches_file_filter(file_path: str) -> bool:
        if not file_filter:
            return True
        path_lower = file_path.lower()
        filter_lower = file_filter.lower()
        # Match if filter appears anywhere in path
        return filter_lower in path_lower

    # Filter functions
    functions = [
        f for f in result.functions
        if min_match <= f.current_match <= max_match
        and min_size <= f.size_bytes <= max_size
        and f.name not in merged
        and (not matching_only or f.object_status == "Matching")
        and (not module or f"/{module}/" in f.file_path.lower())
        and not _is_excluded_subdir(f.file_path)
        and _matches_file_filter(f.file_path)
    ]

    # Sort functions
    if sort_by == "score":
        functions = sorted(functions, key=lambda f: -_compute_recommendation_score(f))
    elif sort_by == "size":
        functions = sorted(functions, key=lambda f: f.size_bytes)
    elif sort_by == "match":
        functions = sorted(functions, key=lambda f: -f.current_match)
    else:
        functions = sorted(functions, key=lambda f: -f.current_match)

    functions = functions[:limit]

    # Build table
    title = "Unmatched Functions"
    if sort_by == "score":
        title += " (sorted by recommendation)"
    table = Table(title=title)
    table.add_column("Name", style="cyan")
    table.add_column("File", style="green")
    table.add_column("Match %", justify="right")
    table.add_column("Size", justify="right")
    if show_score:
        table.add_column("Score", justify="right", style="magenta")
    if show_status:
        table.add_column("Status", style="yellow")
    table.add_column("Address", style="dim")

    for func in functions:
        row = [
            func.name,
            func.file_path,
            f"{func.current_match * 100:.1f}%",
            f"{func.size_bytes}",
        ]
        if show_score:
            score = _compute_recommendation_score(func)
            row.append(f"{score:.0f}")
        if show_status:
            row.append(func.object_status)
        row.append(func.address)
        table.add_row(*row)

    console.print(table)
    excluded_msg = f", {len(merged)} merged excluded" if merged else ""
    matching_msg = ", Matching files only" if matching_only else ""
    module_msg = f", {module}/ only" if module else ""
    subdir_msg = f", excluding {', '.join(exclude_subdir)}" if exclude_subdir else ""
    file_msg = f", file='{file_filter}'" if file_filter else ""
    console.print(f"\n[dim]Found {len(functions)} functions (from {result.total_functions} total{excluded_msg}{matching_msg}{module_msg}{subdir_msg}{file_msg})[/dim]")

    # Warn if no results but filters might be hiding functions
    if len(functions) == 0 and result.total_functions > 0:
        # Check if there are non-merged functions in the DB that might be incorrectly hiding results
        from src.db import get_db
        db = get_db()
        with db.connection() as conn:
            # Check for functions in range that have DB status but aren't merged
            cursor = conn.execute("""
                SELECT COUNT(*) FROM functions
                WHERE status NOT IN ('merged', 'unclaimed')
            """)
            tracked_count = cursor.fetchone()[0]
            if tracked_count > 0:
                console.print(f"[yellow]Note: {tracked_count} functions are tracked in DB (not merged). Use 'melee-agent state status' to review.[/yellow]")

    # Show detailed exclusion diagnostics if requested
    if show_excluded:
        from src.db import get_db
        db = get_db()

        console.print("\n[bold]Exclusion Diagnostics:[/bold]")

        # Count reasons for exclusion
        excluded_by_match = 0
        excluded_by_size = 0
        excluded_by_merged = 0
        excluded_by_matching_only = 0
        excluded_by_module = 0
        excluded_by_subdir = 0
        excluded_by_file = 0

        for f in result.functions:
            # Check each filter in order
            if not (min_match <= f.current_match <= max_match):
                excluded_by_match += 1
                continue
            if not (min_size <= f.size_bytes <= max_size):
                excluded_by_size += 1
                continue
            if f.name in merged:
                excluded_by_merged += 1
                continue
            if matching_only and f.object_status != "Matching":
                excluded_by_matching_only += 1
                continue
            if module and f"/{module}/" not in f.file_path.lower():
                excluded_by_module += 1
                continue
            if _is_excluded_subdir(f.file_path):
                excluded_by_subdir += 1
                continue
            if not _matches_file_filter(f.file_path):
                excluded_by_file += 1
                continue

        if excluded_by_match > 0:
            console.print(f"  Match range ({min_match*100:.0f}%-{max_match*100:.0f}%): [yellow]{excluded_by_match}[/yellow] excluded")
        if excluded_by_size > 0:
            console.print(f"  Size range ({min_size}-{max_size}): [yellow]{excluded_by_size}[/yellow] excluded")
        if excluded_by_merged > 0:
            console.print(f"  Merged (status='merged'): [green]{excluded_by_merged}[/green] excluded")
        if excluded_by_matching_only > 0:
            console.print(f"  Matching files only: [yellow]{excluded_by_matching_only}[/yellow] excluded")
        if excluded_by_module > 0:
            console.print(f"  Module filter ({module}/): [yellow]{excluded_by_module}[/yellow] excluded")
        if excluded_by_subdir > 0:
            console.print(f"  Subdirectory exclusion: [yellow]{excluded_by_subdir}[/yellow] excluded")
        if excluded_by_file > 0:
            console.print(f"  File filter: [yellow]{excluded_by_file}[/yellow] excluded")

        # Show functions in DB that match the module but aren't merged
        if module:
            with db.connection() as conn:
                cursor = conn.execute("""
                    SELECT function_name, match_percent, status
                    FROM functions
                    WHERE source_file_path LIKE ?
                    AND status != 'merged'
                    ORDER BY match_percent DESC
                    LIMIT 10
                """, (f"%/{module}/%",))
                rows = cursor.fetchall()
                if rows:
                    console.print(f"\n  [bold]DB-tracked functions in {module}/ (not merged):[/bold]")
                    for row in rows:
                        console.print(f"    {row[0]}: {row[1]:.1f}% ({row[2]})")


def _get_db_file_stats(func_to_file: dict[str, str]) -> dict[str, dict]:
    """Query state DB for function status grouped by source file.

    Args:
        func_to_file: Mapping from function name to source file path

    Returns dict mapping file_path -> {pending: int, committed: int, total: int}
    - pending = in_progress or matched (has scratch work, not committed)
    - committed = committed in some worktree (not yet in main)
    """
    from src.db import get_db

    db = get_db()
    stats: dict[str, dict] = {}

    with db.connection() as conn:
        # Query all tracked functions with their status
        cursor = conn.execute("""
            SELECT function_name, status
            FROM functions
            WHERE status NOT IN ('unclaimed', 'merged')
        """)

        for row in cursor.fetchall():
            func_name = row[0]
            status = row[1]

            # Look up file path from function name
            file_path = func_to_file.get(func_name)
            if not file_path:
                continue

            if file_path not in stats:
                stats[file_path] = {"pending": 0, "committed": 0, "total": 0}

            stats[file_path]["total"] += 1

            # Categorize by status
            if status in ('in_progress', 'matched'):
                stats[file_path]["pending"] += 1
            elif status in ('committed', 'committed_needs_fix', 'in_review'):
                stats[file_path]["committed"] += 1

    return stats


@extract_app.command("files")
def extract_files(
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-m", help="Path to melee submodule (auto-detects agent worktree)")
    ] = None,
    module: Annotated[
        Optional[str], typer.Option("--module", help="Filter by module path (e.g., ft, lb, gr, it)")
    ] = None,
    status_filter: Annotated[
        Optional[str], typer.Option("--status", "-s", help="Filter by status: Matching, NonMatching, Equivalent")
    ] = None,
    sort_by: Annotated[
        str, typer.Option("--sort", help="Sort by: name, match, unmatched, total, pending")
    ] = "name",
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum number of results (0 for all)")
    ] = 0,
    show_complete: Annotated[
        bool, typer.Option("--show-complete", help="Show files that are 100% matched")
    ] = False,
    show_db: Annotated[
        bool, typer.Option("--show-db/--no-db", help="Show state DB columns (Pending/Committed work)")
    ] = True,
):
    """List all source files with matching statistics.

    Shows per-file breakdown of matched vs unmatched functions.
    Useful for identifying files that are close to completion or
    finding files with many unmatched functions to work on.

    The --show-db flag (default on) adds columns showing work tracked in the
    state database that may not yet be reflected in report.json:
    - Pending: functions with scratches (in_progress/matched status)
    - Committed: functions committed in worktrees (not yet in main)

    Examples:
        melee-agent extract files --module lb
        melee-agent extract files --sort unmatched --limit 20
        melee-agent extract files --sort pending  # files with most pending work
        melee-agent extract files --status NonMatching
    """
    from collections import defaultdict

    # Auto-detect agent worktree
    melee_root = resolve_melee_root(melee_root)

    from src.extractor import FunctionExtractor

    extractor = FunctionExtractor(melee_root)
    result = extractor.extract_all_functions(include_asm=False, include_context=False)

    # Build function -> file mapping for DB lookup
    func_to_file = {func.name: func.file_path for func in result.functions}

    # Get DB stats if requested
    db_stats = _get_db_file_stats(func_to_file) if show_db else {}

    # Group functions by file
    file_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0,
        "matched": 0,
        "unmatched": 0,
        "status": "Unknown",
        "lib": None,
        "match_sum": 0.0,
    })

    for func in result.functions:
        stats = file_stats[func.file_path]
        stats["total"] += 1
        stats["match_sum"] += func.current_match
        stats["status"] = func.object_status
        stats["lib"] = func.lib
        if func.is_matched:
            stats["matched"] += 1
        else:
            stats["unmatched"] += 1

    # Convert to list for filtering/sorting
    files = []
    for file_path, stats in file_stats.items():
        # Average match score across all functions (can be 99.9% even with unmatched)
        avg_score = (stats["match_sum"] / stats["total"] * 100) if stats["total"] > 0 else 0.0
        # Percentage of functions fully matched (100%)
        done_pct = (stats["matched"] / stats["total"] * 100) if stats["total"] > 0 else 0.0

        # Get DB stats for this file
        db_file_stats = db_stats.get(file_path, {})
        pending = db_file_stats.get("pending", 0)
        committed = db_file_stats.get("committed", 0)

        files.append({
            "file_path": file_path,
            "status": stats["status"],
            "lib": stats["lib"],
            "total": stats["total"],
            "matched": stats["matched"],
            "unmatched": stats["unmatched"],
            "avg_score": avg_score,
            "done_pct": done_pct,
            "pending": pending,
            "committed": committed,
        })

    # Apply filters
    if module:
        files = [f for f in files if f"/{module}/" in f["file_path"].lower()]

    if status_filter:
        files = [f for f in files if f["status"].lower() == status_filter.lower()]

    if not show_complete:
        files = [f for f in files if f["done_pct"] < 100.0]

    # Sort
    if sort_by == "match":
        files = sorted(files, key=lambda f: -f["done_pct"])
    elif sort_by == "unmatched":
        files = sorted(files, key=lambda f: -f["unmatched"])
    elif sort_by == "total":
        files = sorted(files, key=lambda f: -f["total"])
    elif sort_by == "pending":
        # Sort by pending + committed (total in-flight work)
        files = sorted(files, key=lambda f: -(f["pending"] + f["committed"]))
    else:  # name
        files = sorted(files, key=lambda f: f["file_path"])

    # Apply limit
    if limit > 0:
        files = files[:limit]

    # Build table
    table = Table(title="Source Files")
    table.add_column("File", style="cyan")
    table.add_column("Status", style="yellow")
    table.add_column("Done", justify="right")  # % of functions fully matched
    table.add_column("Avg", justify="right")   # Average match score
    table.add_column("Matched", justify="right", style="green")
    table.add_column("Unmatched", justify="right", style="red")
    table.add_column("Total", justify="right")
    if show_db:
        table.add_column("Pend", justify="right", style="magenta")  # Pending scratches
        table.add_column("Commit", justify="right", style="blue")   # Committed in worktrees

    for f in files:
        # Color done percentage based on progress
        done_pct = f["done_pct"]
        if done_pct >= 95:
            done_style = "green"
        elif done_pct >= 50:
            done_style = "yellow"
        else:
            done_style = "red"

        # Color avg score - dim if same as done (no partial matches)
        avg_score = f["avg_score"]
        if abs(avg_score - done_pct) < 0.1:
            avg_str = "[dim]-[/dim]"  # Same as done, no need to show
        else:
            avg_str = f"[dim]{avg_score:.1f}%[/dim]"

        row = [
            f["file_path"],
            f["status"],
            f"[{done_style}]{done_pct:.1f}%[/{done_style}]",
            avg_str,
            str(f["matched"]),
            str(f["unmatched"]),
            str(f["total"]),
        ]
        if show_db:
            # Show pending/committed counts, dim if zero
            pending = f["pending"]
            committed = f["committed"]
            row.append(str(pending) if pending > 0 else "[dim]-[/dim]")
            row.append(str(committed) if committed > 0 else "[dim]-[/dim]")

        table.add_row(*row)

    console.print(table)

    # Summary stats
    total_files = len(files)
    total_funcs = sum(f["total"] for f in files)
    total_matched = sum(f["matched"] for f in files)
    total_unmatched = sum(f["unmatched"] for f in files)
    overall_pct = (total_matched / total_funcs * 100) if total_funcs > 0 else 0

    filter_msgs = []
    if module:
        filter_msgs.append(f"module={module}")
    if status_filter:
        filter_msgs.append(f"status={status_filter}")
    filter_str = f" ({', '.join(filter_msgs)})" if filter_msgs else ""

    # DB stats summary
    db_msg = ""
    if show_db:
        total_pending = sum(f["pending"] for f in files)
        total_committed = sum(f["committed"] for f in files)
        if total_pending > 0 or total_committed > 0:
            db_msg = f" | DB: {total_pending} pending, {total_committed} committed"

    console.print(f"\n[dim]{total_files} files{filter_str}: {total_matched}/{total_funcs} functions matched ({overall_pct:.1f}%), {total_unmatched} remaining{db_msg}[/dim]")


@extract_app.command("get")
def extract_get(
    function_name: Annotated[str, typer.Argument(help="Name of the function to extract")],
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-m", help="Path to melee submodule (auto-detects agent worktree)")
    ] = None,
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Output file for ASM")
    ] = None,
    full: Annotated[
        bool, typer.Option("--full", "-f", help="Show full assembly (no truncation)")
    ] = False,
    create_scratch: Annotated[
        bool, typer.Option("--create-scratch", "-s", help="Create a decomp.me scratch")
    ] = False,
    api_url: Annotated[
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected if not provided)")
    ] = None,
    strip_inline: Annotated[
        bool, typer.Option("--strip-inline/--no-strip-inline", help="Strip inline function bodies from context (reduces pollution)")
    ] = True,
    strip_all_bodies: Annotated[
        bool, typer.Option("--strip-all-bodies/--no-strip-all-bodies", help="Strip ALL function bodies from context (prevents -inline auto issues)")
    ] = False,
    auto_decompile: Annotated[
        bool, typer.Option("--decompile", "-d", help="Run m2c decompiler for initial code when creating scratch")
    ] = True,
):
    """Extract a specific function's ASM and context.

    Match percentages are read from the authoritative report.json.
    Use --create-scratch to also create a decomp.me scratch in one step.
    By default, runs the m2c decompiler to generate initial C code.
    Use --no-decompile to skip auto-decompilation and start with an empty stub.
    """
    # First pass: use default melee root to look up function info
    initial_root = melee_root or DEFAULT_MELEE_ROOT

    # Auto-detect API URL if creating scratch
    if create_scratch and not api_url:
        api_url = detect_local_api_url()
        if not api_url:
            console.print("[red]Error: Could not find local decomp.me server[/red]")
            console.print("[dim]Tried: nzxt-discord.local, 10.200.0.1, localhost:8000[/dim]")
            console.print("")
            console.print("[yellow]STOP: The decomp.me server should always be available.[/yellow]")
            console.print("[yellow]Report this issue to the user - do NOT attempt local-only workarounds.[/yellow]")
            raise typer.Exit(1)
        console.print(f"[dim]Using decomp.me server: {api_url}[/dim]")

    from src.extractor import extract_function

    func = asyncio.run(extract_function(initial_root, function_name))

    if func is None:
        console.print(f"[red]Function '{function_name}' not found[/red]")
        raise typer.Exit(1)

    # Now that we know the source file, resolve to the correct worktree
    # This ensures context comes from the worktree with the agent's changes
    if melee_root is None and func.file_path:
        melee_root = resolve_melee_root(None, target_file=func.file_path)
    else:
        melee_root = initial_root

    console.print(f"[bold cyan]{func.name}[/bold cyan]")
    console.print(f"File: {func.file_path}")
    console.print(f"Address: {func.address}")
    console.print(f"Size: {func.size_bytes} bytes")
    console.print(f"Match: {func.current_match * 100:.1f}%")
    console.print("\n[bold]Assembly:[/bold]")
    if func.asm:
        if full or len(func.asm) <= 4000:
            console.print(func.asm)
        else:
            console.print(func.asm[:4000] + f"\n... ({len(func.asm) - 4000} more chars, use --full to see all)")
    else:
        console.print("[yellow]ASM not available (project needs to be built first)[/yellow]")

    if output:
        if func.asm:
            output.write_text(func.asm)
            console.print(f"\n[green]ASM written to {output}[/green]")
        else:
            console.print("[red]Cannot write output - ASM not available[/red]")

    # Create scratch if requested
    if create_scratch:
        if not api_url:
            console.print("[red]Error: DECOMP_API_BASE environment variable required for --create-scratch[/red]")
            raise typer.Exit(1)

        if not func.asm:
            console.print("[red]Cannot create scratch - ASM not available[/red]")
            raise typer.Exit(1)

        ctx_path = _get_context_file(source_file=func.file_path, melee_root=melee_root)

        # Always rebuild context to pick up header changes
        import subprocess
        # Build the context file - need relative path from melee_root
        try:
            ctx_relative = ctx_path.relative_to(melee_root)
            ninja_cwd = melee_root
        except ValueError:
            # ctx_path might be in a worktree, find the melee root for that worktree
            # The ctx_path looks like: .../melee-worktrees/<name>/build/GALE01/src/...
            # We need to run ninja from the worktree root
            parts = ctx_path.parts
            for i, part in enumerate(parts):
                if part == "build" and i > 0:
                    ninja_cwd = Path(*parts[:i])
                    ctx_relative = Path(*parts[i:])
                    break
            else:
                console.print(f"[red]Cannot determine ninja target for: {ctx_path}[/red]")
                raise typer.Exit(1)

        try:
            result = subprocess.run(
                ["ninja", str(ctx_relative)],
                cwd=ninja_cwd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                console.print(f"[red]Failed to build context file:[/red]")
                console.print(result.stderr or result.stdout)
                raise typer.Exit(1)
            # Only show message if ninja actually did something
            if "no work to do" not in result.stdout.lower():
                console.print(f"[green]Built context file[/green]")
        except subprocess.TimeoutExpired:
            console.print(f"[red]Timeout building context file[/red]")
            raise typer.Exit(1)
        except FileNotFoundError:
            console.print(f"[red]ninja not found - please install it[/red]")
            raise typer.Exit(1)

        if not ctx_path.exists():
            console.print(f"[red]Context file not found after build: {ctx_path}[/red]")
            raise typer.Exit(1)

        melee_context = ctx_path.read_text()
        console.print(f"\n[dim]Loaded {len(melee_context):,} bytes of context[/dim]")

        # Strip function bodies to reduce context pollution
        if strip_all_bodies:
            # Strip ALL function bodies - prevents -inline auto from inlining anything
            melee_context, body_count = _strip_all_function_bodies(melee_context)
            if body_count > 0:
                console.print(f"[dim]Stripped {body_count} function bodies (aggressive mode)[/dim]")
        elif strip_inline:
            # Only strip explicitly inline function bodies
            melee_context, inline_count = _strip_inline_functions(melee_context)
            if inline_count > 0:
                console.print(f"[dim]Stripped {inline_count} inline function bodies[/dim]")

        # Strip function definition (but keep declaration) to avoid redefinition errors
        if func.name in melee_context:
            melee_context = _strip_target_function(melee_context, func.name)
            console.print(f"[dim]Stripped {func.name} definition from context[/dim]")

        # Detect correct compiler for this source file
        compiler = get_compiler_for_source(func.file_path, melee_root)
        console.print(f"[dim]Using compiler: {compiler}[/dim]")

        async def find_or_create():
            from src.client import DecompMeAPIClient, ScratchCreate, ScratchUpdate
            async with DecompMeAPIClient(base_url=api_url) as client:
                # First, search for existing scratches with this function name
                console.print(f"[dim]Searching for existing scratches...[/dim]")
                existing = await client.list_scratches(search=func.name, page_size=20)

                # Filter to exact name matches and find the best one
                best_scratch = None
                best_match_pct = -1.0

                for s in existing:
                    if s.name == func.name and s.max_score > 0:
                        match_pct = (s.max_score - s.score) / s.max_score * 100
                        if match_pct > best_match_pct:
                            best_match_pct = match_pct
                            best_scratch = s

                # If we found an existing scratch, check its family for even better matches
                if best_scratch:
                    console.print(f"[dim]Found existing scratch at {best_match_pct:.1f}%, checking family...[/dim]")
                    try:
                        family = await client.get_scratch_family(best_scratch.slug)
                        for s in family:
                            if s.max_score > 0:
                                match_pct = (s.max_score - s.score) / s.max_score * 100
                                if match_pct > best_match_pct:
                                    best_match_pct = match_pct
                                    best_scratch = s
                    except Exception:
                        pass  # Family lookup failed, use what we have

                # If we found a good existing scratch, fork it to continue
                if best_scratch and best_match_pct > 0:
                    console.print(f"[green]Found existing scratch at {best_match_pct:.1f}% - forking to continue[/green]")
                    scratch = await client.fork_scratch(best_scratch.slug)
                    if scratch.claim_token:
                        from src.cli.scratch import _save_scratch_token
                        _save_scratch_token(scratch.slug, scratch.claim_token)
                        try:
                            await client.claim_scratch(scratch.slug, scratch.claim_token)
                        except Exception:
                            pass
                    # Update forked scratch with fresh context from local build
                    try:
                        await client.update_scratch(scratch.slug, ScratchUpdate(context=melee_context))
                        console.print(f"[dim]Updated forked scratch with fresh context[/dim]")
                    except Exception as e:
                        console.print(f"[yellow]Warning: Could not update context: {e}[/yellow]")
                    return scratch, best_match_pct

                # No existing scratch found - create new
                console.print(f"[dim]No existing scratches found, creating new...[/dim]")

                # If auto-decompiling, preprocess context to remove preprocessor directives
                # that m2c can't handle. Use preprocessed for decompilation, but restore
                # original context afterward for compilation (compiler handles directives fine)
                decompile_context = melee_context
                if auto_decompile and melee_context:
                    from src.cli.scratch import _preprocess_context
                    preprocessed, success = _preprocess_context(melee_context)
                    if success and preprocessed != melee_context:
                        decompile_context = preprocessed
                        console.print(f"[dim]Preprocessed context for m2c ({len(melee_context):,} → {len(preprocessed):,} bytes)[/dim]")

                # Build scratch params - omit source_code to trigger auto-decompilation
                scratch_params = ScratchCreate(
                    name=func.name,
                    target_asm=func.asm,
                    context=decompile_context if auto_decompile else melee_context,
                    compiler=compiler,
                    compiler_flags="-O4,p -nodefaults -fp hard -Cpp_exceptions off -enum int -fp_contract on -inline auto",
                    diff_label=func.name,
                )

                # Only set source_code if NOT auto-decompiling
                if not auto_decompile:
                    scratch_params.source_code = "// TODO: Decompile this function\n"
                else:
                    console.print(f"[dim]Running m2c decompiler for initial code...[/dim]")

                scratch = await client.create_scratch(scratch_params)

                # Claim ownership first (needed for subsequent updates)
                if scratch.claim_token:
                    from src.cli.scratch import _save_scratch_token
                    _save_scratch_token(scratch.slug, scratch.claim_token)
                    try:
                        await client.claim_scratch(scratch.slug, scratch.claim_token)
                        console.print(f"[dim]Claimed ownership of scratch[/dim]")
                    except Exception as e:
                        console.print(f"[yellow]Warning: Could not claim scratch: {e}[/yellow]")

                # Restore original context (with preprocessor directives) for MWCC compilation.
                # The preprocessed context was only needed for m2c decompilation.
                # MWCC needs the original because gcc -E introduces incompatible features:
                # - __attribute__((noreturn)) not supported by MWCC
                # - _Static_assert is C11, not supported by MWCC
                # - Assert macro expansions cause type mismatches
                if auto_decompile and decompile_context != melee_context:
                    try:
                        await client.update_scratch(scratch.slug, ScratchUpdate(context=melee_context))
                        console.print(f"[dim]Restored original context for MWCC[/dim]")
                    except Exception as e:
                        console.print(f"[yellow]Warning: Could not restore context: {e}[/yellow]")

                return scratch, 0.0

        scratch, starting_pct = asyncio.run(find_or_create())
        if starting_pct > 0:
            console.print(f"[green]Continuing from {starting_pct:.1f}% match:[/green] {api_url}/scratch/{scratch.slug}")
        else:
            console.print(f"[green]Created scratch:[/green] {api_url}/scratch/{scratch.slug}")

        # Write to state database (non-blocking)
        db_upsert_scratch(
            scratch.slug,
            instance='local',
            base_url=api_url,
            function_name=func.name,
            claim_token=scratch.claim_token,
            match_percent=starting_pct,
        )
        # Determine status based on match percentage
        if starting_pct >= 95:
            status = 'matched'
        elif starting_pct > 0:
            status = 'in_progress'
        else:
            status = 'in_progress'
        db_upsert_function(
            func.name,
            local_scratch_slug=scratch.slug,
            match_percent=starting_pct,
            status=status,
        )
