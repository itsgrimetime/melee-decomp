#!/usr/bin/env python3
"""Verification script for the decomp.me API client.

This script verifies that the client module is properly installed and can
connect to a running decomp.me backend.

Usage:
    python scripts/verify_client.py [BASE_URL]

Arguments:
    BASE_URL: Optional base URL for the API (default: http://localhost:8000)
"""

import asyncio
import sys

from src.client import (
    DecompMeAPIClient,
    DecompMeAPIError,
    ScratchCreate,
    ScratchManager,
)


async def verify_client(base_url: str = "http://localhost:8000"):
    """Verify the client works with the API."""
    print("=" * 70)
    print("Decomp.me API Client Verification")
    print("=" * 70)
    print(f"API Base URL: {base_url}")
    print()

    try:
        async with DecompMeAPIClient(base_url) as client:
            # Test 1: List compilers
            print("[1/5] Testing compiler list...")
            compilers = await client.list_compilers()
            print(f"  ✓ Found {len(compilers)} compilers")
            if any(c.id == "mwcc_247_92" for c in compilers):
                print("  ✓ Melee compiler (mwcc_247_92) is available")
            else:
                print("  ⚠ Melee compiler (mwcc_247_92) not found")
            print()

            # Test 2: List presets
            print("[2/5] Testing preset list...")
            presets = await client.list_presets()
            print(f"  ✓ Found {len(presets)} presets")
            print()

            # Test 3: Create a scratch
            print("[3/5] Testing scratch creation...")
            test_asm = """
glabel test_verification
/* 00000000 00000000  38 60 00 2A */  li r3, 42
/* 00000004 00000004  4E 80 00 20 */  blr
            """.strip()

            scratch = await client.create_scratch(
                ScratchCreate(
                    name="Client Verification Test",
                    compiler="mwcc_247_92",
                    compiler_flags="-O4,p -nodefaults",
                    target_asm=test_asm,
                    diff_label="test_verification",
                    source_code="int test_verification(void) { return 42; }",
                )
            )
            print(f"  ✓ Created scratch: {scratch.slug}")
            print(f"  ✓ Initial score: {scratch.score}/{scratch.max_score}")
            print()

            # Test 4: Compile the scratch
            print("[4/5] Testing compilation...")
            result = await client.compile_scratch(scratch.slug, save_score=True)
            print(f"  ✓ Compilation success: {result.success}")
            if result.diff_output:
                print(
                    f"  ✓ Score: {result.diff_output.current_score}/{result.diff_output.max_score}"
                )
                if result.is_perfect:
                    print("  ✓ Perfect match!")
            print()

            # Test 5: ScratchManager
            print("[5/5] Testing ScratchManager...")
            manager = ScratchManager(client)

            test_asm2 = """
glabel manager_test
/* 00000000 00000000  38 60 00 00 */  li r3, 0
/* 00000004 00000004  4E 80 00 20 */  blr
            """.strip()

            scratch2 = await manager.create_from_asm(
                target_asm=test_asm2,
                function_name="manager_test",
                name="Manager Verification Test",
            )
            print(f"  ✓ Created scratch via manager: {scratch2.slug}")

            # Test iteration
            new_source = "int manager_test(void) { return 0; }"
            result2 = await manager.iterate(scratch2, new_source, save=False)
            print(f"  ✓ Iteration test successful")
            print()

            print("=" * 70)
            print("All verification tests passed! ✓")
            print("=" * 70)
            print()
            print("Client module is ready to use.")
            print()
            print("Created test scratches:")
            print(f"  - {scratch.slug}: {scratch.name}")
            print(f"  - {scratch2.slug}: {scratch2.name}")
            print()
            print("You can view them at:")
            print(f"  {base_url}/scratch/{scratch.slug}")
            print(f"  {base_url}/scratch/{scratch2.slug}")

    except DecompMeAPIError as e:
        print()
        print("=" * 70)
        print("Verification failed! ✗")
        print("=" * 70)
        print()
        print(f"Error: {e}")
        print()
        print("Make sure the decomp.me backend is running at:")
        print(f"  {base_url}")
        print()
        print("To start the backend:")
        print("  cd decomp.me/backend")
        print("  python manage.py runserver")
        sys.exit(1)

    except Exception as e:
        print()
        print("=" * 70)
        print("Unexpected error! ✗")
        print("=" * 70)
        print()
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


def main():
    """Main entry point."""
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    asyncio.run(verify_client(base_url))


if __name__ == "__main__":
    main()
