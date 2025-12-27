"""Complete workflow for committing matched functions.

This module provides a high-level workflow that orchestrates all the steps
needed to commit a matched function and create a PR.
"""

import asyncio
from pathlib import Path
from typing import Optional

from .update import update_source_file
from .configure import update_configure_py, get_file_path_from_function
from .format import format_files, verify_clang_format_available
from .pr import create_pr, switch_to_branch
from .diagnostics import analyze_commit_error


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
        create_pull_request: bool = True,
        extract_function_only: bool = False,
    ) -> Optional[str]:
        """Execute the complete workflow to commit a matched function.

        This performs all necessary steps:
        1. Update the source file with new code
        2. Verify the file compiles (revert if not)
        3. Update configure.py to mark as Matching
        4. Format the changed files
        5. Regenerate progress report
        6. Create a PR (if requested)

        Args:
            function_name: Name of the matched function
            file_path: Relative path to the source file (e.g., "melee/lb/lbcommand.c")
            new_code: The new function implementation
            scratch_id: decomp.me scratch ID
            scratch_url: Full URL to the decomp.me scratch
            create_pull_request: Whether to create a PR (default: True)
            extract_function_only: If True, extract just the function from new_code.
                If False, use new_code as-is (agent decides what to include).

        Returns:
            PR URL if successful and create_pull_request is True, None otherwise
        """
        print(f"\n{'='*60}")
        print(f"Starting commit workflow for function: {function_name}")
        print(f"{'='*60}\n")

        # Step 1: Update source file
        print("[1/6] Updating source file...")
        if not await update_source_file(
            file_path, function_name, new_code, self.melee_root,
            extract_function_only=extract_function_only
        ):
            print("❌ Failed to update source file")
            return None
        self.files_changed.append(f"src/{file_path}")
        print("✓ Source file updated\n")

        # Step 2: Verify file compiles
        print("[2/6] Verifying file compiles...")
        compiles, error_msg, full_output = await self._verify_file_compiles(file_path)
        if not compiles:
            print(f"❌ File does not compile after update:")
            # Show diagnostics with suggestions
            diagnostic = analyze_commit_error(full_output, file_path)
            print(diagnostic)
            print("\n  Reverting changes...")
            if await self._revert_file(f"src/{file_path}"):
                print("  ✓ File reverted to original state")
            else:
                print("  ⚠ Failed to revert - please run: git checkout HEAD -- src/{file_path}")
            return None
        print("✓ File compiles successfully\n")

        # Step 3: Update configure.py
        print("[3/6] Updating configure.py...")
        if not await update_configure_py(file_path, self.melee_root):
            print("❌ Failed to update configure.py")
            return None
        self.files_changed.append("configure.py")
        print("✓ configure.py updated\n")

        # Step 4: Format files
        print("[4/6] Formatting changed files...")
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

        # Step 5: Regenerate progress report
        print("[5/6] Regenerating progress report...")
        await self._regenerate_report()

        # Step 6: Create PR (if requested)
        if create_pull_request:
            print("[6/6] Creating pull request...")
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
            print("[6/6] Skipping pull request creation (as requested)")
            print(f"\n{'='*60}")
            print(f"✓ Workflow completed successfully!")
            print(f"Files changed: {', '.join(self.files_changed)}")
            print(f"{'='*60}\n")
            return None

    async def _verify_file_compiles(self, file_path: str) -> tuple[bool, str, str]:
        """Verify that a source file compiles successfully.

        Args:
            file_path: Relative path to the source file (e.g., "melee/lb/lbcommand.c")

        Returns:
            Tuple of (success, error_message, full_output).
            error_message is empty on success, full_output is for diagnostics.
        """
        # Convert .c to .o for the object file target
        obj_path = f"build/GALE01/src/{file_path}".replace('.c', '.o')

        try:
            # First run configure.py to ensure build files are up to date
            proc = await asyncio.create_subprocess_exec(
                "python", "configure.py",
                cwd=self.melee_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            # Now compile just the one object file
            proc = await asyncio.create_subprocess_exec(
                "ninja", obj_path,
                cwd=self.melee_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                return True, "", ""
            else:
                # Extract the actual error message from stderr
                error_output = stderr.decode() if stderr else stdout.decode() if stdout else "Unknown error"
                # Look for the actual compiler error
                lines = error_output.split('\n')
                error_lines = [l for l in lines if 'Error:' in l or 'error:' in l.lower()]
                if error_lines:
                    return False, '\n'.join(error_lines[:5]), error_output
                return False, error_output[:500], error_output

        except FileNotFoundError:
            return False, "ninja not found - cannot verify compilation", ""
        except Exception as e:
            return False, f"Compilation check failed: {e}", ""

    async def _revert_file(self, file_path: str) -> bool:
        """Revert a file to its state in git HEAD.

        Args:
            file_path: Path relative to melee_root (e.g., "src/melee/lb/lbcommand.c")

        Returns:
            True if successful, False otherwise
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "checkout", "HEAD", "--", file_path,
                cwd=self.melee_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception as e:
            print(f"  Failed to revert file: {e}")
            return False

    async def _regenerate_report(self) -> bool:
        """Regenerate the progress report (report.json) via ninja."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ninja", "build/GALE01/report.json",
                cwd=self.melee_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                print("✓ Progress report regenerated\n")
                return True
            else:
                print(f"⚠ Warning: Failed to regenerate report (exit {proc.returncode})")
                if stderr:
                    print(f"  {stderr.decode().strip()}")
                return False
        except FileNotFoundError:
            print("⚠ Warning: ninja not found, skipping report regeneration")
            return False
        except Exception as e:
            print(f"⚠ Warning: Could not regenerate report: {e}")
            return False


async def auto_detect_and_commit(
    function_name: str,
    new_code: str,
    scratch_id: str,
    scratch_url: str,
    melee_root: Path,
    create_pull_request: bool = True,
    extract_function_only: bool = False,
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
        create_pull_request: Whether to create a PR (default: True)
        extract_function_only: If True, extract just the function from new_code.
            If False (default), use new_code as-is - the caller is responsible
            for providing exactly what should be inserted. Use False for agent
            workflows where the agent has analyzed the target file.

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
        create_pull_request,
        extract_function_only,
    )
