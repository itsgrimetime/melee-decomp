"""Shared helpers for PR commands."""

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

from .._common import console, PRODUCTION_DECOMP_ME, load_slug_map


def get_extended_pr_info(repo: str, pr_number: int) -> dict | None:
    """Get extended PR info including body, commits, and base branch."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json",
             "state,isDraft,title,body,mergeable,mergeStateStatus,reviewDecision,baseRefName,headRefName,commits,url"],
            capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def extract_functions_from_commits(commits: list[dict]) -> list[dict]:
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


def validate_pr_description(body: str, functions: list[str], slug_map: dict) -> list[str]:
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


def get_production_scratch_url(func_name: str, slug_map: dict) -> str | None:
    """Get production scratch URL for a function from slug map."""
    for prod_slug, info in slug_map.items():
        if info.get('function') == func_name:
            return f"{PRODUCTION_DECOMP_ME}/scratch/{prod_slug}"
    return None


def get_pr_checks(repo: str, pr_number: int) -> list[dict]:
    """Get PR check runs and their status."""
    try:
        result = subprocess.run(
            ["gh", "pr", "checks", str(pr_number), "--repo", repo, "--json",
             "name,state,startedAt,completedAt,link,workflow"],
            capture_output=True, text=True, check=True
        )
        checks = json.loads(result.stdout)
        # Normalize field names to match expected format in consumers
        for check in checks:
            # Map 'state' to 'conclusion' for backward compatibility
            # gh uses state: SUCCESS, FAILURE, SKIPPED, PENDING, etc.
            state = check.get('state', '').upper()
            if state == 'SUCCESS':
                check['conclusion'] = 'success'
            elif state == 'FAILURE':
                check['conclusion'] = 'failure'
            elif state in ('PENDING', 'IN_PROGRESS', 'QUEUED'):
                check['conclusion'] = None  # Not finished
            else:
                check['conclusion'] = state.lower() if state else None
        return checks
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return []


def get_failed_check_logs(repo: str, run_id: str, max_lines: int = 100) -> str:
    """Get logs from a failed check run."""
    try:
        # Get workflow run logs
        result = subprocess.run(
            ["gh", "run", "view", run_id, "--repo", repo, "--log-failed"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().split('\n')
            # Return last N lines (most likely to show the error)
            if len(lines) > max_lines:
                return f"[truncated to last {max_lines} lines]\n" + '\n'.join(lines[-max_lines:])
            return '\n'.join(lines)
        return ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def parse_build_errors(log_output: str) -> list[dict]:
    """Parse build errors from log output."""
    errors = []

    # Common error patterns
    error_patterns = [
        # GCC/Clang style: file.c:123:45: error: message
        (r'([^:\s]+\.[ch]pp?):(\d+):(\d+):\s*error:\s*(.+)', 'compile'),
        # PowerPC assembler: file.s:123: Error: message
        (r'([^:\s]+\.[sS]):(\d+):\s*Error:\s*(.+)', 'assemble'),
        # Linker: undefined reference to `symbol'
        (r"undefined reference to [`']([^'`]+)[`']", 'link'),
        # Multiple definition
        (r'multiple definition of [`\']([^\'`]+)[`\']', 'link'),
        # objdiff-cli errors
        (r'error:\s*(.+)', 'general'),
    ]

    for pattern, error_type in error_patterns:
        for match in re.finditer(pattern, log_output, re.MULTILINE):
            groups = match.groups()
            if error_type == 'compile':
                errors.append({
                    'type': 'compile',
                    'file': groups[0],
                    'line': int(groups[1]),
                    'column': int(groups[2]),
                    'message': groups[3].strip(),
                })
            elif error_type == 'assemble':
                errors.append({
                    'type': 'assemble',
                    'file': groups[0],
                    'line': int(groups[1]),
                    'message': groups[2].strip(),
                })
            elif error_type == 'link':
                errors.append({
                    'type': 'link',
                    'symbol': groups[0],
                    'message': f"undefined reference to `{groups[0]}`",
                })
            else:
                errors.append({
                    'type': 'general',
                    'message': groups[0].strip(),
                })

    return errors


def get_pr_review_comments(repo: str, pr_number: int) -> list[dict]:
    """Get review comments on a PR."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls/{pr_number}/comments",
             "--jq", "[.[] | {body, path, line, user: .user.login, created_at}]"],
            capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return []


def parse_decomp_dev_report(body: str) -> dict | None:
    """Parse decomp.dev report from PR body or comment.

    Supports both the old "## decomp.dev Report" format in PR bodies
    and the new bot comment format with "### Report for GALE01".
    """
    if not body:
        return None

    result = {
        'matching_functions': 0,
        'matching_bytes': 0,
        'total_bytes': 0,
        'completion_percent': 0.0,
        'delta_percent': 0.0,
        'delta_bytes': 0,
        'broken_matches': [],
        'regressions': [],
        'improvements': [],
        'new_matches': [],
        'raw_text': '',
    }

    # Try new bot comment format first: "### Report for GALE01"
    bot_report_match = re.search(r'### Report for (\w+)', body)
    if bot_report_match:
        result['raw_text'] = body

        # Parse: "📈 **Matched code**: 48.62% (+0.01%, +512 bytes)"
        # or: "📉 **Matched code**: 48.54% (-0.06%, -2268 bytes)"
        matched_pattern = r'\*\*Matched code\*\*:\s*([\d.]+)%\s*\(([+-][\d.]+)%,\s*([+-][\d,]+)\s*bytes\)'
        matched = re.search(matched_pattern, body)
        if matched:
            result['completion_percent'] = float(matched.group(1))
            result['delta_percent'] = float(matched.group(2))
            result['delta_bytes'] = int(matched.group(3).replace(',', ''))

        # Count broken matches (100% -> 0%)
        broken_section = re.search(r'💔\s*(\d+)\s*broken match', body)
        if broken_section:
            result['broken_matches_count'] = int(broken_section.group(1))
            # Extract function names from the broken matches table
            broken_funcs = re.findall(r'\|\s*`[^`]+`\s*\|\s*`([^`]+)`\s*\|[^|]+\|\s*100\.00%\s*\|\s*0\.00%', body)
            result['broken_matches'] = broken_funcs

        # Count regressions
        regression_section = re.search(r'📉\s*(\d+)\s*regression', body)
        if regression_section:
            result['regressions_count'] = int(regression_section.group(1))
            # Extract function names
            regression_funcs = re.findall(r'\|\s*`[^`]+`\s*\|\s*`([^`]+)`\s*\|[^|]+\|[^|]+\|\s*0\.00%', body)
            result['regressions'] = regression_funcs

        # Count improvements
        improvement_section = re.search(r'📈\s*(\d+)\s*improvement', body)
        if improvement_section:
            result['improvements_count'] = int(improvement_section.group(1))

        # Count new matches
        new_match_section = re.search(r'✅\s*(\d+)\s*new match', body)
        if new_match_section:
            result['new_matches_count'] = int(new_match_section.group(1))

        return result

    # Fall back to old "## decomp.dev Report" format in PR bodies
    report_pattern = r'## decomp\.dev Report\s*\n(.*?)(?:\n##|\Z)'
    match = re.search(report_pattern, body, re.DOTALL | re.IGNORECASE)
    if not match:
        return None

    report_text = match.group(1)
    result['raw_text'] = report_text

    # Pattern: "Matching functions: 123"
    func_match = re.search(r'Matching functions?:\s*(\d+)', report_text, re.IGNORECASE)
    if func_match:
        result['matching_functions'] = int(func_match.group(1))

    # Pattern: "Matching bytes: 12,345 / 1,234,567 (12.34%)"
    bytes_match = re.search(
        r'Matching bytes?:\s*([\d,]+)\s*/\s*([\d,]+)\s*\(?([\d.]+)%?\)?',
        report_text, re.IGNORECASE
    )
    if bytes_match:
        result['matching_bytes'] = int(bytes_match.group(1).replace(',', ''))
        result['total_bytes'] = int(bytes_match.group(2).replace(',', ''))
        result['completion_percent'] = float(bytes_match.group(3))

    return result


def get_pr_issue_comments(repo: str, pr_number: int) -> list[dict]:
    """Get issue comments (including bot comments) on a PR."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{pr_number}/comments",
             "--jq", "[.[] | {body, user: .user.login, created_at, updated_at}]"],
            capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return []


def get_decomp_dev_report(repo: str, pr_number: int) -> dict | None:
    """Get decomp.dev report from PR comments or body.

    First checks issue comments for bot-generated reports, then falls
    back to checking the PR body for embedded reports.
    """
    # First check issue comments for bot reports
    comments = get_pr_issue_comments(repo, pr_number)
    for comment in reversed(comments):  # Most recent first
        body = comment.get('body', '')
        if '### Report for' in body or 'Matched code' in body:
            report = parse_decomp_dev_report(body)
            if report:
                report['source'] = 'comment'
                report['author'] = comment.get('user', 'unknown')
                return report

    # Fall back to PR body
    info = get_extended_pr_info(repo, pr_number)
    if not info:
        return None

    body = info.get('body', '')
    report = parse_decomp_dev_report(body)
    if report:
        report['source'] = 'body'
    return report


def get_pr_merge_status(repo: str, pr_number: int) -> dict:
    """Get detailed merge status for a PR."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json",
             "state,mergeable,mergeStateStatus,mergeCommit,mergedAt,mergedBy"],
            capture_output=True, text=True, check=True
        )
        data = json.loads(result.stdout)
        return {
            'state': data.get('state'),
            'mergeable': data.get('mergeable'),
            'merge_state': data.get('mergeStateStatus'),
            'merged_at': data.get('mergedAt'),
            'merged_by': data.get('mergedBy', {}).get('login') if data.get('mergedBy') else None,
        }
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return {}
