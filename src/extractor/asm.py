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

    def get_asm_for_file(self, source_file: str) -> Optional[str]:
        """
        Get the complete assembly for a source file.

        Args:
            source_file: Relative path to source file (e.g., "melee/lb/lbfile.c")

        Returns:
            Assembly code or None if file not found
        """
        # Convert source path to asm path
        # src/melee/lb/lbfile.c -> build/GALE01/asm/melee/lb/lbfile.s
        asm_path = self.asm_dir / Path(source_file).with_suffix(".s")

        if not asm_path.exists():
            return None

        try:
            with open(asm_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
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
        indent_level = 0

        # Patterns for function start and end
        # Function typically starts with:
        # .global function_name
        # function_name:
        global_pattern = re.compile(rf"^\s*\.(?:global|globl)\s+{re.escape(function_name)}\s*$")
        label_pattern = re.compile(rf"^{re.escape(function_name)}:\s*$")

        # Function typically ends with another global function or section directive
        next_function_pattern = re.compile(r"^\s*\.(?:global|globl)\s+\w+\s*$")
        section_pattern = re.compile(r"^\s*\.(text|data|rodata|bss|section)")

        for i, line in enumerate(lines):
            # Check if we're starting the function
            if global_pattern.match(line):
                in_function = True
                function_lines.append(line)
                continue

            if in_function:
                # Check if this is the function label
                if label_pattern.match(line):
                    function_lines.append(line)
                    continue

                # Check if we've reached the end of the function
                if next_function_pattern.match(line) or (
                    section_pattern.match(line) and function_lines
                ):
                    # End of function
                    break

                # Check for function end markers
                if line.strip().startswith(".size") and function_name in line:
                    function_lines.append(line)
                    break

                # Check for blr (branch to link register - function return)
                # This is a heuristic and might not always work
                if line.strip() == "blr":
                    function_lines.append(line)
                    # Keep going in case there's more after blr
                    continue

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

        functions = []
        # Look for .global directives followed by function labels
        global_pattern = re.compile(r"^\s*\.(?:global|globl)\s+(\w+)\s*$")

        for line in asm_content.split("\n"):
            match = global_pattern.match(line)
            if match:
                func_name = match.group(1)
                # Filter out likely non-function symbols
                if not func_name.startswith("_") or func_name.startswith("__"):
                    functions.append(func_name)

        return functions


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
