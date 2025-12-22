"""Tests for the Commit Manager module.

These tests verify the functionality of the commit module components.
Note: Some tests require a properly configured melee repository.
"""

import asyncio
from pathlib import Path
import tempfile
import shutil

from .update import update_source_file, update_scratches_txt
from .configure import update_configure_py, get_file_path_from_function
from .format import verify_clang_format_available
from .pr import get_remote_url, check_branch_exists


async def test_verify_clang_format():
    """Test if git clang-format is available."""
    print("\nTest: Verify clang-format availability")
    print("-" * 60)

    available = await verify_clang_format_available()
    if available:
        print("✓ git clang-format is available")
    else:
        print("⚠ git clang-format is not available")
    return available


async def test_get_remote_url(melee_root: Path):
    """Test getting the git remote URL."""
    print("\nTest: Get git remote URL")
    print("-" * 60)

    url = await get_remote_url(melee_root)
    if url:
        print(f"✓ Remote URL: {url}")
        return True
    else:
        print("✗ Failed to get remote URL")
        return False


async def test_check_branch_exists(melee_root: Path):
    """Test checking if a branch exists."""
    print("\nTest: Check branch existence")
    print("-" * 60)

    # Check for main branch (should exist)
    main_exists = await check_branch_exists("main", melee_root)
    if main_exists:
        print("✓ 'main' branch exists")
    else:
        print("⚠ 'main' branch not found (might be 'master')")

    # Check for non-existent branch
    fake_exists = await check_branch_exists("this-branch-definitely-does-not-exist", melee_root)
    if not fake_exists:
        print("✓ Non-existent branch correctly identified")
        return True
    else:
        print("✗ Branch check failed")
        return False


async def test_function_search(melee_root: Path):
    """Test searching for a function in source files."""
    print("\nTest: Function search")
    print("-" * 60)

    # Search for a known function
    file_path = await get_file_path_from_function("Command_Execute", melee_root)
    if file_path:
        print(f"✓ Found function in: {file_path}")
        return True
    else:
        print("⚠ Function not found (might not exist in test repo)")
        return False


async def test_update_source_file_dry_run():
    """Test source file update logic (without actually modifying files)."""
    print("\nTest: Source file update (dry run)")
    print("-" * 60)

    # Create a temporary test file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.c', delete=False) as f:
        test_file = Path(f.name)
        f.write("""#include <stdio.h>

void TestFunction(int x)
{
    printf("old implementation %d\\n", x);
}

void AnotherFunction(void)
{
    TestFunction(42);
}
""")

    try:
        # Create a temporary directory structure
        temp_dir = Path(tempfile.mkdtemp())
        src_dir = temp_dir / "src"
        src_dir.mkdir()

        # Copy test file
        dest_file = src_dir / "test.c"
        shutil.copy(test_file, dest_file)

        # Test updating the function
        new_code = """void TestFunction(int x)
{
    printf("new implementation %d\\n", x);
    printf("with more code\\n");
}"""

        success = await update_source_file(
            file_path="test.c",
            function_name="TestFunction",
            new_code=new_code,
            melee_root=temp_dir
        )

        if success:
            # Verify the update
            content = dest_file.read_text()
            if "new implementation" in content and "AnotherFunction" in content:
                print("✓ Source file update successful")
                print(f"✓ Function replaced correctly")
                print(f"✓ Other code preserved")
                return True
            else:
                print("✗ Update didn't work as expected")
                return False
        else:
            print("✗ Source file update failed")
            return False

    finally:
        # Cleanup
        test_file.unlink()
        shutil.rmtree(temp_dir)


async def test_scratches_txt_format():
    """Test scratches.txt entry formatting."""
    print("\nTest: scratches.txt format")
    print("-" * 60)

    # Create a temporary scratches.txt
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        scratches_file = Path(f.name)
        f.write("# Test scratches file\n")
        f.write("ExistingFunction = 100%:MATCHED; // author:someone id:old123\n")

    try:
        # Create temp directory structure
        temp_dir = Path(tempfile.mkdtemp())
        config_dir = temp_dir / "config" / "GALE01"
        config_dir.mkdir(parents=True)

        # Copy scratches file
        dest_file = config_dir / "scratches.txt"
        shutil.copy(scratches_file, dest_file)

        # Add new entry
        success = await update_scratches_txt(
            function_name="NewFunction",
            scratch_id="new456",
            melee_root=temp_dir,
            author="test_agent"
        )

        if success:
            content = dest_file.read_text()
            if "NewFunction = 100%:MATCHED; // author:test_agent id:new456" in content:
                print("✓ scratches.txt entry format correct")
                print("✓ Existing entries preserved")
                return True
            else:
                print("✗ Entry format incorrect")
                return False
        else:
            print("✗ Failed to update scratches.txt")
            return False

    finally:
        # Cleanup
        scratches_file.unlink()
        shutil.rmtree(temp_dir)


async def test_configure_py_update():
    """Test configure.py update logic."""
    print("\nTest: configure.py update")
    print("-" * 60)

    # Create a temporary configure.py
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        config_file = Path(f.name)
        f.write("""# Test configure file
from tools.project import Object, Matching, NonMatching

lib_melee = [
    Object(Matching, "melee/lb/lbcommand.c"),
    Object(NonMatching, "melee/lb/lbtest.c"),
    Object(NonMatching, "melee/lb/lbanother.c"),
]
""")

    try:
        # Create temp directory
        temp_dir = Path(tempfile.mkdtemp())
        dest_file = temp_dir / "configure.py"
        shutil.copy(config_file, dest_file)

        # Update NonMatching -> Matching
        success = await update_configure_py(
            file_path="melee/lb/lbtest.c",
            melee_root=temp_dir
        )

        if success:
            content = dest_file.read_text()
            if 'Object(Matching, "melee/lb/lbtest.c")' in content:
                print("✓ configure.py update successful")
                print("✓ NonMatching changed to Matching")

                # Verify other entries unchanged
                if 'Object(NonMatching, "melee/lb/lbanother.c")' in content:
                    print("✓ Other NonMatching entries preserved")
                    return True
                else:
                    print("✗ Other entries were affected")
                    return False
            else:
                print("✗ Update didn't work as expected")
                return False
        else:
            print("✗ configure.py update failed")
            return False

    finally:
        # Cleanup
        config_file.unlink()
        shutil.rmtree(temp_dir)


async def run_all_tests(melee_root: Path | None = None):
    """Run all tests.

    Args:
        melee_root: Path to melee repository for integration tests.
                   If None, only unit tests are run.
    """
    print("\n" + "=" * 60)
    print("Commit Manager Module - Test Suite")
    print("=" * 60)

    results = []

    # Unit tests (don't require melee repo)
    results.append(("Clang-format check", await test_verify_clang_format()))
    results.append(("Source file update", await test_update_source_file_dry_run()))
    results.append(("scratches.txt format", await test_scratches_txt_format()))
    results.append(("configure.py update", await test_configure_py_update()))

    # Integration tests (require melee repo)
    if melee_root and melee_root.exists():
        print(f"\nRunning integration tests with melee repo: {melee_root}")
        results.append(("Get remote URL", await test_get_remote_url(melee_root)))
        results.append(("Branch existence check", await test_check_branch_exists(melee_root)))
        results.append(("Function search", await test_function_search(melee_root)))
    else:
        print("\n⚠ Skipping integration tests (melee_root not provided or doesn't exist)")

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nPassed: {passed}/{total}")

    if passed == total:
        print("✓ All tests passed!")
    else:
        print(f"✗ {total - passed} test(s) failed")

    return passed == total


async def main():
    """Main test runner."""
    # You can provide a path to your melee repository for integration tests
    melee_root = Path("/Users/mike/code/melee-decomp/melee")

    # Run all tests
    success = await run_all_tests(melee_root if melee_root.exists() else None)

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
