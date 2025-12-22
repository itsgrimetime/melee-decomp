#!/usr/bin/env python3
"""Example usage of the function extractor module."""

from pathlib import Path
from extractor import FunctionExtractor, ConfigureParser, SymbolParser


def main():
    """Run examples of the extractor module."""
    # Set the path to the melee project
    melee_root = Path(__file__).parent.parent.parent / "melee"

    print("=" * 80)
    print("Melee Decompilation Function Extractor - Examples")
    print("=" * 80)
    print()

    # Example 1: Parse configure.py
    print("Example 1: Parse configure.py")
    print("-" * 80)
    parser = ConfigureParser(melee_root)
    objects = parser.parse_objects()
    print(f"Total objects: {len(objects)}")

    non_matching = parser.get_non_matching_objects()
    matching = parser.get_matching_objects()
    equivalent = parser.get_equivalent_objects()

    print(f"Matching: {len(matching)}")
    print(f"NonMatching: {len(non_matching)}")
    print(f"Equivalent: {len(equivalent)}")

    print(f"\nFirst 5 NonMatching objects:")
    for obj in non_matching[:5]:
        print(f"  - {obj.file_path} (lib: {obj.lib})")

    print()

    # Example 2: Parse symbols.txt
    print("Example 2: Parse symbols.txt")
    print("-" * 80)
    symbol_parser = SymbolParser(melee_root)
    symbols = symbol_parser.parse_symbols()
    print(f"Total function symbols: {len(symbols)}")

    print(f"\nFirst 5 functions:")
    for name, symbol in list(symbols.items())[:5]:
        print(f"  - {symbol.name} @ {symbol.address} ({symbol.size_bytes} bytes)")

    # Get functions by section
    text_funcs = symbol_parser.get_functions_by_section("text")
    print(f"\nFunctions in .text section: {len(text_funcs)}")

    print()

    # Example 3: Extract function information
    print("Example 3: Extract function information")
    print("-" * 80)
    extractor = FunctionExtractor(melee_root)

    # Try to extract a specific function
    # Using a common function that likely exists
    test_functions = ["memset", "memcpy", "__start"]
    for func_name in test_functions:
        func_info = extractor.extract_function(
            func_name,
            include_asm=True,
            include_context=False  # Skip context for speed
        )
        if func_info:
            print(f"\nFunction: {func_info.name}")
            print(f"  Address: {func_info.address}")
            print(f"  Size: {func_info.size_bytes} bytes")
            print(f"  Match: {func_info.match_percent:.1f}%")
            print(f"  File: {func_info.file_path}")
            print(f"  Status: {func_info.object_status}")
            print(f"  Section: {func_info.section}")
            if func_info.asm:
                asm_lines = func_info.asm.split('\n')
                print(f"  Assembly (first 5 lines):")
                for line in asm_lines[:5]:
                    print(f"    {line}")
            break
    else:
        print("Could not find any of the test functions")

    print()

    # Example 4: Extract unmatched functions (limited to first 10)
    print("Example 4: Extract unmatched functions")
    print("-" * 80)
    print("Note: This may take a while depending on project size")
    print("Extracting first 10 unmatched functions...")

    result = extractor.extract_unmatched_functions(
        include_asm=False,  # Skip ASM for speed
        include_context=False  # Skip context for speed
    )

    print(f"\nTotal unmatched functions: {result.unmatched_functions}")
    print(f"\nFirst 10 unmatched functions:")
    for func in result.functions[:10]:
        print(f"  - {func.name:<30} @ {func.address} ({func.match_percent:.1f}% match)")
        print(f"    File: {func.file_path}")
        print(f"    Lib: {func.lib or 'N/A'}")

    print()
    print("=" * 80)
    print("Examples complete!")
    print("=" * 80)


if __name__ == "__main__":
    main()
