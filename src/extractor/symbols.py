"""Parse symbols.txt to extract function information."""

import re
from pathlib import Path
from typing import Optional
from .models import FunctionSymbol


class SymbolParser:
    """Parser for symbols.txt to extract function symbols."""

    def __init__(self, melee_root: Path):
        """
        Initialize symbol parser.

        Args:
            melee_root: Path to the melee project root directory
        """
        self.melee_root = Path(melee_root)
        self.symbols_path = self.melee_root / "config" / "GALE01" / "symbols.txt"

        # Pattern to match function symbols
        # Example: memset = .init:0x80003100; // type:function size:0x30 scope:global
        self.function_pattern = re.compile(
            r'^(\w+)\s*=\s*\.?(\w+):0x([0-9A-Fa-f]+);\s*//.*type:function(?:\s+size:0x([0-9A-Fa-f]+))?(?:\s+scope:(\w+))?'
        )

    def parse_symbols(self) -> dict[str, FunctionSymbol]:
        """
        Parse symbols.txt and extract all function symbols.

        Returns:
            Dictionary mapping function names to FunctionSymbol objects
        """
        if not self.symbols_path.exists():
            raise FileNotFoundError(f"symbols.txt not found at {self.symbols_path}")

        symbols = {}

        with open(self.symbols_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                match = self.function_pattern.match(line)
                if match:
                    name = match.group(1)
                    section = match.group(2)
                    address = match.group(3)
                    size_hex = match.group(4)
                    scope = match.group(5)

                    # Parse size
                    size_bytes = 0
                    if size_hex:
                        try:
                            size_bytes = int(size_hex, 16)
                        except ValueError:
                            pass

                    # Create FunctionSymbol
                    symbol = FunctionSymbol(
                        name=name,
                        address=f"0x{address}",
                        size_bytes=size_bytes,
                        section=section,
                        scope=scope,
                    )
                    symbols[name] = symbol

        return symbols

    def get_function_symbol(self, function_name: str) -> Optional[FunctionSymbol]:
        """
        Get symbol information for a specific function.

        Args:
            function_name: Name of the function

        Returns:
            FunctionSymbol if found, None otherwise
        """
        symbols = self.parse_symbols()
        return symbols.get(function_name)

    def get_functions_in_range(
        self, start_addr: int, end_addr: int
    ) -> list[FunctionSymbol]:
        """
        Get all functions within an address range.

        Args:
            start_addr: Start address (inclusive)
            end_addr: End address (exclusive)

        Returns:
            List of FunctionSymbol objects
        """
        symbols = self.parse_symbols()
        result = []

        for symbol in symbols.values():
            try:
                addr = int(symbol.address, 16)
                if start_addr <= addr < end_addr:
                    result.append(symbol)
            except ValueError:
                continue

        # Sort by address
        result.sort(key=lambda s: int(s.address, 16))
        return result

    def get_functions_by_section(self, section: str) -> list[FunctionSymbol]:
        """
        Get all functions in a specific section.

        Args:
            section: Section name (e.g., "text", "init")

        Returns:
            List of FunctionSymbol objects
        """
        symbols = self.parse_symbols()
        result = [s for s in symbols.values() if s.section == section]

        # Sort by address
        result.sort(key=lambda s: int(s.address, 16))
        return result


async def parse_symbols(melee_root: Path) -> dict[str, FunctionSymbol]:
    """
    Async wrapper for parsing symbols.txt.

    Args:
        melee_root: Path to the melee project root directory

    Returns:
        Dictionary mapping function names to FunctionSymbol objects
    """
    parser = SymbolParser(melee_root)
    return parser.parse_symbols()
