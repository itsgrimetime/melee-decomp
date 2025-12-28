"""Main extractor that combines all components to extract function information."""

from pathlib import Path
from typing import Optional
from .models import FunctionInfo, ExtractionResult
from .parser import ConfigureParser
from .report import ReportParser
from .symbols import SymbolParser
from .asm import AsmExtractor
from .context import ContextGenerator
from .splits import SplitsParser


class FunctionExtractor:
    """Main extractor for function information from the melee project."""

    def __init__(self, melee_root: Path):
        """
        Initialize the function extractor.

        Args:
            melee_root: Path to the melee project root directory
        """
        self.melee_root = Path(melee_root)
        self.configure_parser = ConfigureParser(melee_root)
        self.report_parser = ReportParser(melee_root)
        self.symbol_parser = SymbolParser(melee_root)
        self.asm_extractor = AsmExtractor(melee_root)
        self.context_generator = ContextGenerator(melee_root)
        self.splits_parser = SplitsParser(melee_root)

        # Cache for function name to source file mapping
        self._function_to_file_cache: Optional[dict[str, str]] = None

    def extract_all_functions(
        self,
        include_asm: bool = True,
        include_context: bool = False,
    ) -> ExtractionResult:
        """
        Extract all functions from the project.

        Args:
            include_asm: Whether to include assembly code
            include_context: Whether to include decompilation context

        Returns:
            ExtractionResult with all extracted functions
        """
        # Parse symbols to get all functions (once)
        symbols = self.symbol_parser.parse_symbols()

        # Parse report for match data (once)
        function_matches = self.report_parser.get_function_matches()

        # Parse configure.py for object status (once)
        objects = self.configure_parser.parse_objects()
        object_map = {obj.file_path: obj for obj in objects}

        # Build function-to-file lookup table (once)
        function_to_file = self._build_function_to_file_lookup(object_map)

        # Build function info list
        functions = []

        for func_name, symbol in symbols.items():
            # O(1) lookup for source file
            source_file = function_to_file.get(func_name)
            if not source_file:
                continue

            # Get object status
            obj_status = object_map.get(source_file)
            if not obj_status:
                continue

            # Get match percentage
            match_data = function_matches.get(func_name)
            if match_data:
                current_match = match_data.fuzzy_match_percent / 100.0
            else:
                # Default based on object status
                if obj_status.status == "Matching":
                    current_match = 1.0
                else:
                    current_match = 0.0

            # Get assembly if requested
            asm = None
            if include_asm:
                asm = self.asm_extractor.get_asm_for_function(source_file, func_name)

            # Get context if requested
            context = None
            if include_context:
                try:
                    context = self.context_generator.generate_context(source_file)
                except Exception:
                    # Context generation might fail, that's okay
                    pass

            # Create FunctionInfo
            func_info = FunctionInfo(
                name=func_name,
                file_path=source_file,
                address=symbol.address,
                size_bytes=symbol.size_bytes,
                current_match=current_match,
                asm=asm,
                context=context,
                object_status=obj_status.status,
                section=symbol.section,
                lib=obj_status.lib,
            )
            functions.append(func_info)

        # Create result
        total = len(functions)
        matched = sum(1 for f in functions if f.is_matched)

        return ExtractionResult(
            functions=functions,
            total_functions=total,
            matched_functions=matched,
            unmatched_functions=total - matched,
        )

    def extract_unmatched_functions(
        self,
        include_asm: bool = True,
        include_context: bool = False,
    ) -> ExtractionResult:
        """
        Extract only unmatched functions from the project.

        Args:
            include_asm: Whether to include assembly code
            include_context: Whether to include decompilation context

        Returns:
            ExtractionResult with unmatched functions
        """
        result = self.extract_all_functions(include_asm, include_context)

        # Filter to only unmatched
        unmatched = [f for f in result.functions if not f.is_matched]

        return ExtractionResult(
            functions=unmatched,
            total_functions=len(unmatched),
            matched_functions=0,
            unmatched_functions=len(unmatched),
        )

    def extract_function(
        self,
        function_name: str,
        include_asm: bool = True,
        include_context: bool = True,
    ) -> Optional[FunctionInfo]:
        """
        Extract information for a specific function.

        Args:
            function_name: Name of the function
            include_asm: Whether to include assembly code
            include_context: Whether to include decompilation context

        Returns:
            FunctionInfo or None if function not found
        """
        # Get symbol
        symbol = self.symbol_parser.get_function_symbol(function_name)
        if not symbol:
            return None

        # Find source file
        objects = self.configure_parser.parse_objects()
        object_map = {obj.file_path: obj for obj in objects}
        source_file = self._find_source_file_for_function(function_name, object_map)
        if not source_file:
            return None

        # Get object status
        obj_status = object_map.get(source_file)
        if not obj_status:
            return None

        # Get match percentage
        function_matches = self.report_parser.get_function_matches()
        match_data = function_matches.get(function_name)
        if match_data:
            current_match = match_data.fuzzy_match_percent / 100.0
        else:
            # Default based on object status
            if obj_status.status == "Matching":
                current_match = 1.0
            else:
                current_match = 0.0

        # Get assembly if requested
        asm = None
        if include_asm:
            asm = self.asm_extractor.get_asm_for_function(source_file, function_name)

        # Get context if requested
        context = None
        if include_context:
            try:
                context = self.context_generator.generate_context(source_file)
            except Exception:
                pass

        return FunctionInfo(
            name=function_name,
            file_path=source_file,
            address=symbol.address,
            size_bytes=symbol.size_bytes,
            current_match=current_match,
            asm=asm,
            context=context,
            object_status=obj_status.status,
            section=symbol.section,
            lib=obj_status.lib,
        )

    def _build_function_to_file_lookup(self, object_map: dict) -> dict[str, str]:
        """
        Build a lookup table mapping function names to source files.

        This is computed once and cached for O(1) lookups.
        Uses O(log n) binary search for address lookups.

        Args:
            object_map: Map of file paths to ObjectStatus

        Returns:
            Dictionary mapping function names to source file paths
        """
        if self._function_to_file_cache is not None:
            return self._function_to_file_cache

        function_to_file = {}
        unresolved_funcs = []

        # Parse all symbols once
        symbols = self.symbol_parser.parse_symbols()

        # Build interval index once for O(log n) lookups
        self.splits_parser._build_interval_index()

        # For each symbol, find its source file using O(log n) binary search
        for func_name, symbol in symbols.items():
            try:
                addr = int(symbol.address, 16)
            except ValueError:
                continue

            # O(log n) lookup using interval index
            file_path = self.splits_parser.get_file_for_address_fast(addr, symbol.section)

            if file_path and file_path in object_map:
                function_to_file[func_name] = file_path
            else:
                unresolved_funcs.append(func_name)

        # Fallback: Build ASM index once for all unresolved functions
        if unresolved_funcs:
            asm_index = self.asm_extractor.build_function_to_file_index(list(object_map.keys()))
            for func_name in unresolved_funcs:
                if func_name in asm_index:
                    function_to_file[func_name] = asm_index[func_name]

        # Cache the result
        self._function_to_file_cache = function_to_file
        return function_to_file

    def _find_source_file_for_function(
        self, function_name: str, object_map: dict
    ) -> Optional[str]:
        """
        Find the source file that contains a function.

        Uses cached lookup table for O(1) performance.

        Args:
            function_name: Name of the function
            object_map: Map of file paths to ObjectStatus

        Returns:
            Source file path or None
        """
        # Build or retrieve cached lookup table
        function_to_file = self._build_function_to_file_lookup(object_map)

        # O(1) lookup
        return function_to_file.get(function_name)


async def extract_unmatched_functions(
    melee_root: Path,
    include_asm: bool = True,
    include_context: bool = False,
) -> ExtractionResult:
    """
    Async wrapper for extracting unmatched functions.

    Args:
        melee_root: Path to the melee project root directory
        include_asm: Whether to include assembly code
        include_context: Whether to include decompilation context

    Returns:
        ExtractionResult with unmatched functions
    """
    extractor = FunctionExtractor(melee_root)
    return extractor.extract_unmatched_functions(include_asm, include_context)


async def extract_function(
    melee_root: Path,
    function_name: str,
    include_asm: bool = True,
    include_context: bool = True,
) -> Optional[FunctionInfo]:
    """
    Async wrapper for extracting a specific function.

    Args:
        melee_root: Path to the melee project root directory
        function_name: Name of the function
        include_asm: Whether to include assembly code
        include_context: Whether to include decompilation context

    Returns:
        FunctionInfo or None if function not found
    """
    extractor = FunctionExtractor(melee_root)
    return extractor.extract_function(function_name, include_asm, include_context)
