"""Extract assembly code for functions from the build directory."""

import re
from pathlib import Path
from typing import Optional


class AsmExtractor:
    """Extractor for assembly code from .s files."""

    def __init__(self, melee_root: Path):
        """
        Initialize ASM extractor.

        Args:
            melee_root: Path to the melee project root directory
        """
        self.melee_root = Path(melee_root)
        self.asm_dir = self.melee_root / "build" / "GALE01" / "asm"
        # Cache for file contents to avoid repeated disk reads
        self._file_cache: dict[str, Optional[str]] = {}
        # Cache for functions-per-file index
        self._functions_index: Optional[dict[str, list[str]]] = None

    def get_asm_for_file(self, source_file: str) -> Optional[str]:
        """
        Get the complete assembly for a source file.

        Args:
            source_file: Relative path to source file (e.g., "melee/lb/lbfile.c")

        Returns:
            Assembly code or None if file not found
        """
        # Check cache first
        if source_file in self._file_cache:
            return self._file_cache[source_file]

        # Convert source path to asm path
        # src/melee/lb/lbfile.c -> build/GALE01/asm/melee/lb/lbfile.s
        asm_path = self.asm_dir / Path(source_file).with_suffix(".s")

        if not asm_path.exists():
            self._file_cache[source_file] = None
            return None

        try:
            with open(asm_path, "r", encoding="utf-8") as f:
                content = f.read()
                self._file_cache[source_file] = content
                return content
        except Exception:
            self._file_cache[source_file] = None
            return None

    def get_asm_for_function(
        self, source_file: str, function_name: str
    ) -> Optional[str]:
        """
        Extract assembly for a specific function from an ASM file.

        Args:
            source_file: Relative path to source file
            function_name: Name of the function

        Returns:
            Assembly code for the function or None if not found
        """
        asm_content = self.get_asm_for_file(source_file)
        if not asm_content:
            return None

        return self._extract_function_from_asm(asm_content, function_name)

    def _extract_function_from_asm(
        self, asm_content: str, function_name: str
    ) -> Optional[str]:
        """
        Extract a specific function from ASM content.

        Args:
            asm_content: Complete ASM file content
            function_name: Name of the function to extract

        Returns:
            Assembly code for the function or None if not found
        """
        lines = asm_content.split("\n")
        function_lines = []
        in_function = False

        # Patterns for function start and end
        # DTK format: .fn function_name, global
        # Traditional format: .global function_name / function_name:
        fn_pattern = re.compile(rf"^\s*\.fn\s+{re.escape(function_name)}(?:,\s*\w+)?\s*$")
        global_pattern = re.compile(rf"^\s*\.(?:global|globl)\s+{re.escape(function_name)}\s*$")
        label_pattern = re.compile(rf"^{re.escape(function_name)}:\s*$")

        # Function end patterns
        endfn_pattern = re.compile(rf"^\s*\.endfn\s+{re.escape(function_name)}\s*$")
        next_fn_pattern = re.compile(r"^\s*\.fn\s+\w+")
        next_global_pattern = re.compile(r"^\s*\.(?:global|globl)\s+\w+\s*$")
        section_pattern = re.compile(r"^\s*\.(text|data|rodata|bss|section)")

        for i, line in enumerate(lines):
            # Check if we're starting the function (DTK format)
            if fn_pattern.match(line):
                in_function = True
                function_lines.append(line)
                continue

            # Check if we're starting the function (traditional format)
            if global_pattern.match(line):
                in_function = True
                function_lines.append(line)
                continue

            if in_function:
                # Check if this is the function label
                if label_pattern.match(line):
                    function_lines.append(line)
                    continue

                # Check for .endfn marker (DTK format)
                if endfn_pattern.match(line):
                    function_lines.append(line)
                    break

                # Check if we've reached the next function
                if next_fn_pattern.match(line) or next_global_pattern.match(line):
                    break

                # Check for section change
                if section_pattern.match(line) and function_lines:
                    break

                # Check for .size marker (traditional format)
                if line.strip().startswith(".size") and function_name in line:
                    function_lines.append(line)
                    break

                # Add line to function
                function_lines.append(line)

        if not function_lines:
            return None

        return "\n".join(function_lines)

    def list_asm_files(self) -> list[Path]:
        """
        List all ASM files in the build directory.

        Returns:
            List of paths to .s files
        """
        if not self.asm_dir.exists():
            return []

        return sorted(self.asm_dir.rglob("*.s"))

    def get_functions_in_asm_file(self, source_file: str) -> list[str]:
        """
        Get a list of all function names in an ASM file.

        Args:
            source_file: Relative path to source file

        Returns:
            List of function names
        """
        asm_content = self.get_asm_for_file(source_file)
        if not asm_content:
            return []

        return self._parse_functions_from_asm(asm_content)

    def _parse_functions_from_asm(self, asm_content: str) -> list[str]:
        """Parse function names from ASM content."""
        functions = []
        # Look for .fn directives (DTK format) or .global directives (traditional)
        fn_pattern = re.compile(r"^\s*\.fn\s+(\w+)(?:,\s*\w+)?\s*$")
        global_pattern = re.compile(r"^\s*\.(?:global|globl)\s+(\w+)\s*$")

        for line in asm_content.split("\n"):
            # Try DTK format first
            match = fn_pattern.match(line)
            if match:
                functions.append(match.group(1))
                continue

            # Try traditional format
            match = global_pattern.match(line)
            if match:
                func_name = match.group(1)
                # Filter out likely non-function symbols
                if not func_name.startswith("_") or func_name.startswith("__"):
                    functions.append(func_name)

        return functions

    def build_function_to_file_index(self, source_files: list[str]) -> dict[str, str]:
        """
        Build a complete index mapping function names to source files.

        This scans all ASM files once and builds a lookup table.

        Args:
            source_files: List of source file paths to index

        Returns:
            Dictionary mapping function names to source file paths
        """
        if self._functions_index is not None:
            # Return flattened index
            result = {}
            for source_file, funcs in self._functions_index.items():
                for func in funcs:
                    result[func] = source_file
            return result

        self._functions_index = {}
        result = {}

        for source_file in source_files:
            funcs = self.get_functions_in_asm_file(source_file)
            self._functions_index[source_file] = funcs
            for func in funcs:
                result[func] = source_file

        return result


async def extract_asm_for_function(
    melee_root: Path, source_file: str, function_name: str
) -> Optional[str]:
    """
    Async wrapper for extracting function assembly.

    Args:
        melee_root: Path to the melee project root directory
        source_file: Relative path to source file
        function_name: Name of the function

    Returns:
        Assembly code or None if not found
    """
    extractor = AsmExtractor(melee_root)
    return extractor.get_asm_for_function(source_file, function_name)
