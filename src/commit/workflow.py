"""Complete workflow for committing matched functions.

This module provides a high-level workflow that orchestrates all the steps
needed to commit a matched function and create a PR.
"""

from pathlib import Path
from typing import Optional

from .update import update_source_file, update_scratches_txt
from .configure import update_configure_py, get_file_path_from_function
from .format import format_files, verify_clang_format_available
from .pr import create_pr, switch_to_branch


class CommitWorkflow:
    """Manages the complete workflow for committing matched functions."""

    def __init__(self, melee_root: Path):
        """Initialize the commit workflow.

        Args:
            melee_root: Path to the melee project root directory
        """
        self.melee_root = Path(melee_root)
        self.files_changed: list[str] = []

    async def execute(
        self,
        function_name: str,
        file_path: str,
        new_code: str,
        scratch_id: str,
        scratch_url: str,
        author: str = "agent",
        create_pull_request: bool = True
    ) -> Optional[str]:
        """Execute the complete workflow to commit a matched function.

        This performs all necessary steps:
        1. Update the source file with new code
        2. Update configure.py to mark as Matching
        3. Update scratches.txt with the match info
        4. Format the changed files
        5. Create a PR (if requested)

        Args:
            function_name: Name of the matched function
            file_path: Relative path to the source file (e.g., "melee/lb/lbcommand.c")
            new_code: The new function implementation
            scratch_id: decomp.me scratch ID
            scratch_url: Full URL to the decomp.me scratch
            author: Author name for the scratches.txt entry
            create_pull_request: Whether to create a PR (default: True)

        Returns:
            PR URL if successful and create_pull_request is True, None otherwise
        """
        print(f"\n{'='*60}")
        print(f"Starting commit workflow for function: {function_name}")
        print(f"{'='*60}\n")

        # Step 1: Update source file
        print("[1/5] Updating source file...")
        if not await update_source_file(file_path, function_name, new_code, self.melee_root):
            print("❌ Failed to update source file")
            return None
        self.files_changed.append(f"src/{file_path}")
        print("✓ Source file updated\n")

        # Step 2: Update configure.py
        print("[2/5] Updating configure.py...")
        if not await update_configure_py(file_path, self.melee_root):
            print("❌ Failed to update configure.py")
            return None
        self.files_changed.append("configure.py")
        print("✓ configure.py updated\n")

        # Step 3: Update scratches.txt
        print("[3/5] Updating scratches.txt...")
        if not await update_scratches_txt(function_name, scratch_id, self.melee_root, author):
            print("❌ Failed to update scratches.txt")
            return None
        self.files_changed.append("config/GALE01/scratches.txt")
        print("✓ scratches.txt updated\n")

        # Step 4: Format files
        print("[4/5] Formatting changed files...")
        if not await verify_clang_format_available():
            print("⚠ Warning: git clang-format not available, skipping formatting")
        else:
            # Only format the C source file
            c_files = [f for f in self.files_changed if f.endswith('.c')]
            if c_files:
                if not await format_files(c_files, self.melee_root):
                    print("⚠ Warning: Formatting failed, but continuing...")
                else:
                    print("✓ Files formatted\n")
            else:
                print("✓ No C files to format\n")

        # Step 5: Create PR (if requested)
        if create_pull_request:
            print("[5/5] Creating pull request...")
            pr_url = await create_pr(
                function_name,
                scratch_url,
                self.files_changed,
                self.melee_root
            )
            if pr_url:
                print(f"✓ Pull request created: {pr_url}\n")
                print(f"\n{'='*60}")
                print(f"✓ Workflow completed successfully!")
                print(f"{'='*60}\n")
                return pr_url
            else:
                print("❌ Failed to create pull request")
                return None
        else:
            print("[5/5] Skipping pull request creation (as requested)")
            print(f"\n{'='*60}")
            print(f"✓ Workflow completed successfully!")
            print(f"Files changed: {', '.join(self.files_changed)}")
            print(f"{'='*60}\n")
            return None


async def auto_detect_and_commit(
    function_name: str,
    new_code: str,
    scratch_id: str,
    scratch_url: str,
    melee_root: Path,
    author: str = "agent",
    create_pull_request: bool = True
) -> Optional[str]:
    """Auto-detect the file containing a function and commit it.

    This is a convenience function that automatically finds the file
    containing the function before running the workflow.

    Args:
        function_name: Name of the matched function
        new_code: The new function implementation
        scratch_id: decomp.me scratch ID
        scratch_url: Full URL to the decomp.me scratch
        melee_root: Path to the melee project root
        author: Author name for the scratches.txt entry
        create_pull_request: Whether to create a PR (default: True)

    Returns:
        PR URL if successful and create_pull_request is True, None otherwise
    """
    print(f"Auto-detecting file for function: {function_name}")

    file_path = await get_file_path_from_function(function_name, melee_root)
    if not file_path:
        print(f"❌ Could not find file containing function '{function_name}'")
        return None

    print(f"Found function in: {file_path}\n")

    workflow = CommitWorkflow(melee_root)
    return await workflow.execute(
        function_name,
        file_path,
        new_code,
        scratch_id,
        scratch_url,
        author,
        create_pull_request
    )
