"""Parse splits.txt to get accurate function-to-file mapping."""

import bisect
import re
from pathlib import Path
from typing import Optional


class SplitsParser:
    """Parser for splits.txt to map functions to source files."""

    def __init__(self, melee_root: Path):
        """
        Initialize splits parser.

        Args:
            melee_root: Path to the melee project root directory
        """
        self.melee_root = Path(melee_root)
        self.splits_path = self.melee_root / "config" / "GALE01" / "splits.txt"
        self._file_ranges = None
        # Sorted interval index for O(log n) lookups: {section: [(start, end, file_path), ...]}
        self._interval_index: Optional[dict[str, list[tuple[int, int, str]]]] = None

    def parse_splits(self) -> dict[str, list[dict]]:
        """
        Parse splits.txt and extract file address ranges.

        Returns:
            Dictionary mapping source files to their section ranges
            Format: {
                "melee/lb/lbcommand.c": [
                    {"section": ".text", "start": 0x80005940, "end": 0x80005BB0},
                    ...
                ]
            }
        """
        if self._file_ranges is not None:
            return self._file_ranges

        if not self.splits_path.exists():
            raise FileNotFoundError(f"splits.txt not found at {self.splits_path}")

        file_ranges = {}
        current_file = None

        # Pattern for file header: "path/to/file.c:"
        file_pattern = re.compile(r'^([^:]+\.c):$')

        # Pattern for section range: "	.section    start:0xADDRESS end:0xADDRESS"
        range_pattern = re.compile(
            r'^\s+\.?(\w+)\s+start:0x([0-9A-Fa-f]+)\s+end:0x([0-9A-Fa-f]+)'
        )

        with open(self.splits_path, "r", encoding="utf-8") as f:
            for line in f:
                # Check for file header
                file_match = file_pattern.match(line)
                if file_match:
                    current_file = file_match.group(1)
                    file_ranges[current_file] = []
                    continue

                # Check for section range
                if current_file:
                    range_match = range_pattern.match(line)
                    if range_match:
                        section = range_match.group(1)
                        start = int(range_match.group(2), 16)
                        end = int(range_match.group(3), 16)

                        file_ranges[current_file].append({
                            "section": section,
                            "start": start,
                            "end": end,
                        })

        self._file_ranges = file_ranges
        return file_ranges

    def _build_interval_index(self) -> dict[str, list[tuple[int, int, str]]]:
        """
        Build a sorted interval index for O(log n) address lookups.

        Returns:
            Dictionary mapping section names to sorted lists of (start, end, file_path)
        """
        if self._interval_index is not None:
            return self._interval_index

        file_ranges = self.parse_splits()
        index: dict[str, list[tuple[int, int, str]]] = {}

        for file_path, ranges in file_ranges.items():
            for range_info in ranges:
                section = range_info["section"]
                if section not in index:
                    index[section] = []
                index[section].append((
                    range_info["start"],
                    range_info["end"],
                    file_path
                ))

        # Sort each section's intervals by start address
        for section in index:
            index[section].sort(key=lambda x: x[0])

        self._interval_index = index
        return index

    def get_file_for_address_fast(self, address: int, section: str = "text") -> Optional[str]:
        """
        Get the source file for an address using O(log n) binary search.

        Args:
            address: Memory address (as integer)
            section: Section name (default: "text")

        Returns:
            Source file path or None if not found
        """
        index = self._build_interval_index()
        intervals = index.get(section, [])

        if not intervals:
            return None

        # Binary search for the interval containing this address
        # Find the rightmost interval where start <= address
        starts = [iv[0] for iv in intervals]
        pos = bisect.bisect_right(starts, address) - 1

        if pos < 0:
            return None

        start, end, file_path = intervals[pos]
        if start <= address < end:
            return file_path

        return None

    def get_file_for_address(self, address: int) -> Optional[str]:
        """
        Get the source file that contains a given address.

        Args:
            address: Memory address (as integer)

        Returns:
            Source file path or None if not found
        """
        file_ranges = self.parse_splits()

        for file_path, ranges in file_ranges.items():
            for range_info in ranges:
                if range_info["start"] <= address < range_info["end"]:
                    return file_path

        return None

    def get_file_for_function(
        self, function_address: str, section: str = "text"
    ) -> Optional[str]:
        """
        Get the source file that contains a function using O(log n) lookup.

        Args:
            function_address: Function address as hex string (e.g., "0x80005940")
            section: Section name (default: "text")

        Returns:
            Source file path or None if not found
        """
        try:
            addr = int(function_address, 16)
        except ValueError:
            return None

        return self.get_file_for_address_fast(addr, section)

    def get_functions_in_file(
        self, source_file: str, symbols: dict
    ) -> list[str]:
        """
        Get all functions in a specific source file.

        Args:
            source_file: Source file path (e.g., "melee/lb/lbcommand.c")
            symbols: Dictionary of function symbols (from SymbolParser)

        Returns:
            List of function names in the file
        """
        file_ranges = self.parse_splits()
        if source_file not in file_ranges:
            return []

        ranges = file_ranges[source_file]
        functions = []

        for func_name, symbol in symbols.items():
            try:
                addr = int(symbol.address, 16)
            except ValueError:
                continue

            # Check if function is in any of the file's ranges
            for range_info in ranges:
                if (range_info["section"] == symbol.section and
                    range_info["start"] <= addr < range_info["end"]):
                    functions.append(func_name)
                    break

        return functions

    def get_all_source_files(self) -> list[str]:
        """
        Get all source files listed in splits.txt.

        Returns:
            List of source file paths
        """
        file_ranges = self.parse_splits()
        return sorted(file_ranges.keys())


async def parse_splits(melee_root: Path) -> dict[str, list[dict]]:
    """
    Async wrapper for parsing splits.txt.

    Args:
        melee_root: Path to the melee project root directory

    Returns:
        Dictionary mapping source files to their section ranges
    """
    parser = SplitsParser(melee_root)
    return parser.parse_splits()
