"""Common utilities and constants for CLI commands."""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

# Console for rich output
console = Console()

# Paths
DECOMP_CONFIG_DIR = Path.home() / ".config" / "decomp-me"
DECOMP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# Get agent ID for session isolation
AGENT_ID = os.environ.get("DECOMP_AGENT_ID", "")
_cookies_suffix = f"_{AGENT_ID}" if AGENT_ID else ""

DECOMP_COOKIES_FILE = os.environ.get(
    "DECOMP_COOKIES_FILE",
    str(DECOMP_CONFIG_DIR / f"cookies{_cookies_suffix}.json")
)
DECOMP_COMPLETED_FILE = os.environ.get(
    "DECOMP_COMPLETED_FILE",
    str(DECOMP_CONFIG_DIR / "completed_functions.json")
)
PRODUCTION_COOKIES_FILE = DECOMP_CONFIG_DIR / "production_cookies.json"

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_MELEE_ROOT = PROJECT_ROOT / "melee"
SLUG_MAP_FILE = PROJECT_ROOT / "config" / "scratches_slug_map.json"

# decomp.me instances
LOCAL_DECOMP_ME = os.environ.get("DECOMP_ME_URL", "http://10.200.0.1")
PRODUCTION_DECOMP_ME = "https://decomp.me"


def load_completed_functions() -> dict:
    """Load completed functions tracking file."""
    completed_path = Path(DECOMP_COMPLETED_FILE)
    if completed_path.exists():
        try:
            with open(completed_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_completed_functions(data: dict) -> None:
    """Save completed functions tracking file."""
    completed_path = Path(DECOMP_COMPLETED_FILE)
    completed_path.parent.mkdir(parents=True, exist_ok=True)
    with open(completed_path, 'w') as f:
        json.dump(data, f, indent=2)


def load_slug_map() -> dict:
    """Load local->production slug mapping."""
    if SLUG_MAP_FILE.exists():
        try:
            with open(SLUG_MAP_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_slug_map(data: dict) -> None:
    """Save local->production slug mapping."""
    SLUG_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SLUG_MAP_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def load_all_tracking_data(melee_root: Path) -> dict:
    """Load all tracking data sources into a unified view."""
    data = {
        "completed": {},
        "slug_map": {},
        "synced": {},
        "scratches_txt_funcs": set(),
        "scratches_txt_slugs": set(),
    }

    # Completed functions
    data["completed"] = load_completed_functions()

    # Slug map (production mappings)
    data["slug_map"] = load_slug_map()

    # Synced scratches
    synced_file = PRODUCTION_COOKIES_FILE.parent / "synced_scratches.json"
    if synced_file.exists():
        try:
            with open(synced_file, 'r') as f:
                data["synced"] = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # Parse scratches.txt
    scratches_file = melee_root / "config" / "GALE01" / "scratches.txt"
    if scratches_file.exists():
        content = scratches_file.read_text()
        # Extract function names and slugs
        pattern = re.compile(r'^(\w+)\s*=.*?id:(\w+)', re.MULTILINE)
        for match in pattern.finditer(content):
            data["scratches_txt_funcs"].add(match.group(1))
            data["scratches_txt_slugs"].add(match.group(2))

    return data


def categorize_functions(data: dict, check_pr_status: bool = False) -> dict:
    """Categorize all tracked functions by their status.

    Categories (for 95%+ matches):
    - merged: PR merged (done!)
    - in_review: Has PR that's still open
    - committed: Committed locally but no PR
    - ready: Synced + in scratches.txt, ready for PR
    - synced_not_in_file: Synced but not in scratches.txt
    - in_file_not_synced: In file but local slug
    - lost_high_match: 95%+ but not synced or in file

    For <95% matches:
    - work_in_progress: Still being worked on
    """
    categories = {
        "merged": [],             # PR merged
        "in_review": [],          # PR open
        "committed": [],          # Committed but no PR
        "ready": [],              # Synced + in file, ready for PR
        "synced_not_in_file": [], # Synced but not in scratches.txt
        "in_file_not_synced": [], # In file but local slug
        "lost_high_match": [],    # 95%+ but not synced or in file
        "work_in_progress": [],   # <95% match
    }

    # Build indexes
    prod_funcs = {v.get("function"): k for k, v in data["slug_map"].items()}
    synced_local_slugs = set(data["synced"].keys())

    # Cache PR statuses if checking
    pr_status_cache = {}

    for func, info in data["completed"].items():
        pct = info.get("match_percent", 0)
        slug = info.get("scratch_slug", "")
        pr_url = info.get("pr_url", "")
        is_committed = info.get("committed", False)
        branch = info.get("branch", "")

        # Determine status
        in_scratches = func in data["scratches_txt_funcs"] or slug in data["scratches_txt_slugs"]
        synced_to_prod = func in prod_funcs or slug in synced_local_slugs

        entry = {
            "function": func,
            "match_percent": pct,
            "local_slug": slug,
            "production_slug": prod_funcs.get(func, ""),
            "committed": is_committed,
            "branch": branch,
            "pr_url": pr_url,
            "notes": info.get("notes", ""),
        }

        if pct >= 95:
            # Check PR status if we have a PR URL
            if pr_url:
                pr_state = None
                if check_pr_status:
                    if pr_url not in pr_status_cache:
                        repo, pr_num = extract_pr_info(pr_url)
                        if repo and pr_num:
                            pr_status_cache[pr_url] = get_pr_status_from_gh(repo, pr_num)
                        else:
                            pr_status_cache[pr_url] = {}
                    pr_state = pr_status_cache.get(pr_url, {}).get("state")

                entry["pr_state"] = pr_state

                if pr_state == "MERGED":
                    categories["merged"].append(entry)
                elif pr_state == "CLOSED":
                    # PR was closed without merge, treat as committed
                    categories["committed"].append(entry)
                else:
                    # PR is open or we didn't check
                    categories["in_review"].append(entry)
            elif is_committed:
                categories["committed"].append(entry)
            elif synced_to_prod and in_scratches:
                categories["ready"].append(entry)
            elif synced_to_prod:
                categories["synced_not_in_file"].append(entry)
            elif in_scratches:
                categories["in_file_not_synced"].append(entry)
            else:
                categories["lost_high_match"].append(entry)
        else:
            categories["work_in_progress"].append(entry)

    # Sort each category by match percentage
    for cat in categories:
        categories[cat].sort(key=lambda x: -x["match_percent"])

    return categories


def extract_pr_info(pr_url: str) -> tuple[str, int]:
    """Extract repo and PR number from URL.

    Returns: (repo, pr_number) e.g. ("doldecomp/melee", 123)
    """
    match = re.match(r'https?://github\.com/([^/]+/[^/]+)/pull/(\d+)', pr_url)
    if match:
        return match.group(1), int(match.group(2))
    return "", 0


def get_pr_status_from_gh(repo: str, pr_number: int) -> dict:
    """Get PR status using gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json",
             "state,isDraft,title,mergeable,reviewDecision"],
            capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return {}
