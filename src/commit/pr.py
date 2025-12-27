"""Create PRs via GitHub API."""

import asyncio
import json
from pathlib import Path
from typing import Optional


async def create_pr(
    function_name: str,
    scratch_url: str,
    files_changed: list[str],
    melee_root: Path,
    base_branch: str = "main"
) -> str | None:
    """Create branch, commit, and open PR. Returns PR URL.

    Args:
        function_name: Name of the matched function
        scratch_url: URL to the decomp.me scratch
        files_changed: List of files that were changed
        melee_root: Path to the melee project root
        base_branch: Base branch to create PR against (default: "main")

    Returns:
        PR URL if successful, None otherwise
    """
    try:
        # Create branch name
        branch_name = f"agent/match-{function_name}"

        # Check if we're in a git repository
        if not (melee_root / ".git").exists():
            print(f"Error: {melee_root} is not a git repository")
            return None

        # Create and checkout new branch
        print(f"Creating branch: {branch_name}")
        checkout_cmd = ["git", "checkout", "-b", branch_name]

        process = await asyncio.create_subprocess_exec(
            *checkout_cmd,
            cwd=str(melee_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            stderr_text = stderr.decode()
            # Check if branch already exists
            if "already exists" in stderr_text:
                print(f"Branch '{branch_name}' already exists, checking it out...")
                checkout_existing = ["git", "checkout", branch_name]
                process = await asyncio.create_subprocess_exec(
                    *checkout_existing,
                    cwd=str(melee_root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await process.communicate()
            else:
                print(f"Error creating branch: {stderr_text}")
                return None

        # Create commit message
        commit_message = f"""Match {function_name}

Matched function {function_name} from decomp.me.

decomp.me scratch: {scratch_url}

Files changed:
{chr(10).join(f'- {f}' for f in files_changed)}

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"""

        # Create commit
        print("Creating commit...")
        commit_cmd = ["git", "commit", "-m", commit_message]

        process = await asyncio.create_subprocess_exec(
            *commit_cmd,
            cwd=str(melee_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            stderr_text = stderr.decode()
            print(f"Error creating commit: {stderr_text}")
            return None

        print(f"Commit created: {stdout.decode()}")

        # Push the branch
        print(f"Pushing branch to remote...")
        push_cmd = ["git", "push", "-u", "origin", branch_name]

        process = await asyncio.create_subprocess_exec(
            *push_cmd,
            cwd=str(melee_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            stderr_text = stderr.decode()
            print(f"Error pushing branch: {stderr_text}")
            return None

        # Create PR body
        pr_body = f"""## Summary
- Matched function `{function_name}` from decomp.me
- Updated source file with matching code
- Changed NonMatching to Matching in configure.py

## decomp.me scratch
{scratch_url}

## Test plan
- [ ] Build succeeds
- [ ] Function matches original binary
- [ ] No regressions in other functions

🤖 Generated with [Claude Code](https://claude.com/claude-code)"""

        pr_title = f"Match {function_name}"

        # Create PR using gh CLI
        print("Creating pull request...")
        gh_cmd = [
            "gh", "pr", "create",
            "--title", pr_title,
            "--body", pr_body,
            "--base", base_branch
        ]

        process = await asyncio.create_subprocess_exec(
            *gh_cmd,
            cwd=str(melee_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            stderr_text = stderr.decode()
            print(f"Error creating PR: {stderr_text}")
            print("Note: Make sure 'gh' CLI is installed and authenticated")
            return None

        pr_url = stdout.decode().strip()
        print(f"Successfully created PR: {pr_url}")
        return pr_url

    except FileNotFoundError as e:
        print(f"Error: Required command not found: {e}")
        print("Make sure 'git' and 'gh' are installed and in PATH")
        return None
    except Exception as e:
        print(f"Error creating PR: {e}")
        return None


async def get_remote_url(melee_root: Path) -> str | None:
    """Get the GitHub repository URL.

    Args:
        melee_root: Path to the melee project root

    Returns:
        Repository URL if successful, None otherwise
    """
    try:
        cmd = ["git", "remote", "get-url", "origin"]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(melee_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            return stdout.decode().strip()
        else:
            return None

    except Exception:
        return None


async def check_branch_exists(
    branch_name: str,
    melee_root: Path
) -> bool:
    """Check if a branch exists locally.

    Args:
        branch_name: Name of the branch to check
        melee_root: Path to the melee project root

    Returns:
        True if branch exists, False otherwise
    """
    try:
        cmd = ["git", "rev-parse", "--verify", branch_name]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(melee_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        await process.communicate()

        return process.returncode == 0

    except Exception:
        return False


async def switch_to_branch(
    branch_name: str,
    melee_root: Path
) -> bool:
    """Switch to a specific branch.

    Args:
        branch_name: Name of the branch to switch to
        melee_root: Path to the melee project root

    Returns:
        True if successful, False otherwise
    """
    try:
        cmd = ["git", "checkout", branch_name]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(melee_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            print(f"Switched to branch: {branch_name}")
            return True
        else:
            print(f"Error switching to branch: {stderr.decode()}")
            return False

    except Exception as e:
        print(f"Error switching branch: {e}")
        return False
