"""Example usage of the Commit Manager module.

This script demonstrates how to use the commit module to integrate
matched functions from decomp.me into the melee decompilation project.
"""

import asyncio
from pathlib import Path

# Import from the commit module
from . import (
    CommitWorkflow,
    auto_detect_and_commit,
    update_source_file,
    update_configure_py,
    update_scratches_txt,
    format_files,
    create_pr,
)


async def example_auto_detect():
    """Example: Auto-detect file and commit a matched function."""
    print("Example 1: Auto-detect and commit")
    print("-" * 60)

    # The matched code from decomp.me
    matched_code = """bool Command_Execute(CommandInfo* info, u32 command)
{
    if (command < 10) {
        lbCommand_803B9840[command](info);
        return true;
    }
    return false;
}"""

    # Execute the auto-detect workflow
    pr_url = await auto_detect_and_commit(
        function_name="Command_Execute",
        new_code=matched_code,
        scratch_id="abc123def",
        scratch_url="https://decomp.me/scratch/abc123def",
        melee_root=Path("/Users/mike/code/melee-decomp/melee"),
        author="agent",
        create_pull_request=True
    )

    if pr_url:
        print(f"\n✓ Success! PR created: {pr_url}")
    else:
        print("\n✗ Failed to create PR")


async def example_manual_workflow():
    """Example: Manual workflow with explicit file path."""
    print("\nExample 2: Manual workflow")
    print("-" * 60)

    # Initialize the workflow
    workflow = CommitWorkflow(
        melee_root=Path("/Users/mike/code/melee-decomp/melee")
    )

    # The matched code from decomp.me
    matched_code = """void Command_00(CommandInfo* info)
{
    info->u = NULL;
}"""

    # Execute the workflow with explicit file path
    pr_url = await workflow.execute(
        function_name="Command_00",
        file_path="melee/lb/lbcommand.c",
        new_code=matched_code,
        scratch_id="xyz789abc",
        scratch_url="https://decomp.me/scratch/xyz789abc",
        author="agent",
        create_pull_request=True
    )

    if pr_url:
        print(f"\n✓ Success! PR created: {pr_url}")
    else:
        print("\n✗ Failed to create PR")


async def example_individual_steps():
    """Example: Using individual functions for each step."""
    print("\nExample 3: Individual steps")
    print("-" * 60)

    melee_root = Path("/Users/mike/code/melee-decomp/melee")
    function_name = "Command_01"
    file_path = "melee/lb/lbcommand.c"

    matched_code = """void Command_01(CommandInfo* info)
{
    info->timer += info->u->Command_00.value;
    NEXT_CMD(info);
}"""

    # Step 1: Update source file
    print("\n[1/5] Updating source file...")
    if await update_source_file(file_path, function_name, matched_code, melee_root):
        print("✓ Source file updated")
    else:
        print("✗ Failed to update source file")
        return

    # Step 2: Update configure.py
    print("\n[2/5] Updating configure.py...")
    if await update_configure_py(file_path, melee_root):
        print("✓ configure.py updated")
    else:
        print("✗ Failed to update configure.py")
        return

    # Step 3: Update scratches.txt
    print("\n[3/5] Updating scratches.txt...")
    if await update_scratches_txt(function_name, "scratch123", melee_root, "agent"):
        print("✓ scratches.txt updated")
    else:
        print("✗ Failed to update scratches.txt")
        return

    # Step 4: Format files
    print("\n[4/5] Formatting files...")
    if await format_files([f"src/{file_path}"], melee_root):
        print("✓ Files formatted")
    else:
        print("⚠ Formatting skipped or failed")

    # Step 5: Create PR
    print("\n[5/5] Creating pull request...")
    pr_url = await create_pr(
        function_name,
        "https://decomp.me/scratch/scratch123",
        [f"src/{file_path}", "configure.py", "config/GALE01/scratches.txt"],
        melee_root
    )

    if pr_url:
        print(f"✓ PR created: {pr_url}")
    else:
        print("✗ Failed to create PR")


async def example_commit_without_pr():
    """Example: Commit changes without creating a PR."""
    print("\nExample 4: Commit without PR")
    print("-" * 60)

    workflow = CommitWorkflow(
        melee_root=Path("/Users/mike/code/melee-decomp/melee")
    )

    matched_code = """void Command_02(CommandInfo* info)
{
    info->timer = info->u->Command_02.value - info->frame_count;
    NEXT_CMD(info);
}"""

    # Execute without creating a PR
    result = await workflow.execute(
        function_name="Command_02",
        file_path="melee/lb/lbcommand.c",
        new_code=matched_code,
        scratch_id="nopr123",
        scratch_url="https://decomp.me/scratch/nopr123",
        author="agent",
        create_pull_request=False  # Don't create PR
    )

    print(f"\n✓ Changes committed locally. Files changed: {workflow.files_changed}")
    print("You can manually create a PR later using git/gh CLI")


async def main():
    """Run all examples."""
    print("\n" + "=" * 60)
    print("Commit Manager Module - Usage Examples")
    print("=" * 60)

    # Note: These are examples and won't actually run unless you have:
    # 1. The melee repository set up
    # 2. Git and gh CLI installed
    # 3. Proper permissions

    print("\n⚠ Note: These are demonstration examples.")
    print("Update the paths and ensure prerequisites before running.\n")

    # Uncomment to run specific examples:
    # await example_auto_detect()
    # await example_manual_workflow()
    # await example_individual_steps()
    # await example_commit_without_pr()


if __name__ == "__main__":
    asyncio.run(main())
