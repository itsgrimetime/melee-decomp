"""Example usage of the decomp.me API client.

This demonstrates common workflows for working with decomp.me scratches.
"""

import asyncio

from src.client import DecompMeAPIClient, ScratchCreate, ScratchManager


async def example_basic_usage():
    """Example: Basic scratch creation and compilation."""
    print("=" * 60)
    print("Example 1: Basic Usage")
    print("=" * 60)

    async with DecompMeAPIClient("http://localhost:8000") as client:
        # Create a scratch
        scratch = await client.create_scratch(
            ScratchCreate(
                name="Test Function",
                compiler="mwcc_247_92",
                compiler_flags="-O4,p -inline auto -nodefaults",
                target_asm="""
glabel test_func
/* 00000000 00000000  7C 08 02 A6 */  mflr r0
/* 00000004 00000004  90 01 00 04 */  stw r0, 4(r1)
/* 00000008 00000008  94 21 FF E8 */  stwu r1, -0x18(r1)
/* 0000000C 0000000C  48 00 00 01 */  bl dummy
/* 00000010 00000010  80 01 00 1C */  lwz r0, 0x1c(r1)
/* 00000014 00000014  38 21 00 18 */  addi r1, r1, 0x18
/* 00000018 00000018  7C 08 03 A6 */  mtlr r0
/* 0000001C 0000001C  4E 80 00 20 */  blr
                """.strip(),
                diff_label="test_func",
                context="void dummy(void);",
                source_code="""
void test_func(void) {
    dummy();
}
                """.strip(),
            )
        )

        print(f"Created scratch: {scratch.slug}")
        print(f"Score: {scratch.score}/{scratch.max_score}")
        print(f"Claim token: {scratch.claim_token}")

        # Compile it
        result = await client.compile_scratch(scratch.slug, save_score=True)
        print(f"\nCompilation success: {result.success}")
        if result.diff_output:
            print(f"Score: {result.diff_output.current_score}/{result.diff_output.max_score}")
            if result.is_perfect:
                print("Perfect match!")

        return scratch.slug


async def example_scratch_manager():
    """Example: Using ScratchManager for high-level operations."""
    print("\n" + "=" * 60)
    print("Example 2: ScratchManager")
    print("=" * 60)

    async with DecompMeAPIClient("http://localhost:8000") as client:
        manager = ScratchManager(client)

        # Create from assembly with auto-decompilation
        scratch = await manager.create_from_asm(
            target_asm="""
glabel add_numbers
/* 00000000 00000000  7C 63 22 14 */  add r3, r3, r4
/* 00000004 00000004  4E 80 00 20 */  blr
            """.strip(),
            function_name="add_numbers",
            name="Simple Addition",
        )

        print(f"Created scratch: {scratch.slug}")
        print(f"Initial source:\n{scratch.source_code}")

        # Try different implementations
        variants = [
            "int add_numbers(int a, int b) { return a + b; }",
            "int add_numbers(int a, int b) { int result = a + b; return result; }",
        ]

        results = await manager.batch_compile(scratch, variants)
        for i, (source, result) in enumerate(results, 1):
            print(f"\nVariant {i}: Score = {result.score}")

        return scratch.slug


async def example_iteration_workflow():
    """Example: Iterative development workflow."""
    print("\n" + "=" * 60)
    print("Example 3: Iterative Development")
    print("=" * 60)

    async with DecompMeAPIClient("http://localhost:8000") as client:
        manager = ScratchManager(client)

        # Create a scratch
        scratch = await manager.create_from_asm(
            target_asm="""
glabel multiply_by_two
/* 00000000 00000000  54 63 08 3C */  rlwinm r3, r3, 1, 0, 0x1e
/* 00000004 00000004  4E 80 00 20 */  blr
            """.strip(),
            function_name="multiply_by_two",
            name="Multiply by Two",
        )

        print(f"Created scratch: {scratch.slug}")

        # Iteration 1: Try basic multiplication
        print("\nIteration 1: Basic multiplication")
        result1 = await manager.iterate(
            scratch,
            "int multiply_by_two(int x) { return x * 2; }",
            save=False,  # Don't save yet, just test
        )
        print(f"Score: {result1.score if result1.success else 'failed'}")

        # Iteration 2: Try left shift
        print("\nIteration 2: Left shift")
        result2 = await manager.iterate(
            scratch,
            "int multiply_by_two(int x) { return x << 1; }",
            save=False,
        )
        print(f"Score: {result2.score if result2.success else 'failed'}")

        # If we found a match, save it
        if result2.is_perfect:
            print("\nFound perfect match! Saving...")
            await manager.iterate(
                scratch,
                "int multiply_by_two(int x) { return x << 1; }",
                save=True,
            )

        return scratch.slug


async def example_flag_optimization():
    """Example: Finding optimal compiler flags."""
    print("\n" + "=" * 60)
    print("Example 4: Compiler Flag Optimization")
    print("=" * 60)

    async with DecompMeAPIClient("http://localhost:8000") as client:
        manager = ScratchManager(client)

        scratch = await manager.create_from_asm(
            target_asm="""
glabel test_optimization
/* 00000000 00000000  38 60 00 00 */  li r3, 0
/* 00000004 00000004  4E 80 00 20 */  blr
            """.strip(),
            function_name="test_optimization",
            source_code="int test_optimization(void) { return 0; }",
        )

        print(f"Created scratch: {scratch.slug}")

        # Try different optimization levels
        flag_variants = [
            "-O0 -nodefaults",
            "-O1 -nodefaults",
            "-O2 -nodefaults",
            "-O3 -nodefaults",
            "-O4,p -nodefaults",
            "-O4,p -inline auto -nodefaults",
        ]

        best_flags, best_score = await manager.find_best_flags(scratch, flag_variants)
        print(f"\nBest flags: {best_flags}")
        print(f"Best score: {best_score}")

        return scratch.slug


async def example_family_and_fork():
    """Example: Working with scratch families and forks."""
    print("\n" + "=" * 60)
    print("Example 5: Families and Forks")
    print("=" * 60)

    async with DecompMeAPIClient("http://localhost:8000") as client:
        manager = ScratchManager(client)

        # Create original scratch
        original = await manager.create_from_asm(
            target_asm="""
glabel original_func
/* 00000000 00000000  38 60 00 01 */  li r3, 1
/* 00000004 00000004  4E 80 00 20 */  blr
            """.strip(),
            function_name="original_func",
        )

        print(f"Created original: {original.slug}")

        # Fork it to try a different approach
        fork = await manager.fork_and_modify(
            original.slug,
            new_name="Alternative approach",
            new_source="int original_func(void) { return 1; }",
        )

        print(f"Created fork: {fork.slug}")

        # Get family
        family = await manager.get_family(original)
        print(f"\nFamily members: {len(family)}")
        for member in family:
            print(f"  - {member.slug}: {member.name}")

        return original.slug


async def main():
    """Run all examples."""
    print("Decomp.me API Client Examples")
    print("=" * 60)
    print("Note: These examples require a running decomp.me backend at localhost:8000")
    print()

    try:
        # Run examples
        await example_basic_usage()
        await example_scratch_manager()
        await example_iteration_workflow()
        await example_flag_optimization()
        await example_family_and_fork()

        print("\n" + "=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"\nError running examples: {e}")
        print("Make sure decomp.me backend is running at http://localhost:8000")
        raise


if __name__ == "__main__":
    asyncio.run(main())
