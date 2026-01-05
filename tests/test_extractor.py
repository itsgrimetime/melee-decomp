"""Integration tests for the extractor module.

These tests use real files from the melee submodule to test parsing and extraction.
Run with: pytest tests/test_extractor.py -v
"""

import pytest
from pathlib import Path

from src.extractor import (
    ConfigureParser,
    SymbolParser,
    SplitsParser,
    FunctionExtractor,
    parse_configure,
    parse_symbols,
    parse_splits,
    extract_unmatched_functions,
    extract_function,
)
from src.extractor.models import ObjectStatus, FunctionSymbol


# Path to the melee submodule
MELEE_ROOT = Path(__file__).parent.parent / "melee"


@pytest.fixture
def melee_root():
    """Fixture providing the melee root directory."""
    if not MELEE_ROOT.exists():
        pytest.skip(f"Melee submodule not found at {MELEE_ROOT}")
    return MELEE_ROOT


class TestConfigureParser:
    """Test the ConfigureParser class."""

    def test_init(self, melee_root):
        """Test parser initialization."""
        parser = ConfigureParser(melee_root)
        assert parser.melee_root == melee_root
        assert parser.configure_path == melee_root / "configure.py"

    def test_parse_objects(self, melee_root):
        """Test parsing all objects from configure.py."""
        parser = ConfigureParser(melee_root)
        objects = parser.parse_objects()

        # Should find objects
        assert len(objects) > 0

        # Check structure of objects
        for obj in objects:
            assert isinstance(obj, ObjectStatus)
            assert obj.file_path
            assert obj.status in ["Matching", "NonMatching", "Equivalent"]

    def test_get_non_matching_objects(self, melee_root):
        """Test getting only non-matching objects."""
        parser = ConfigureParser(melee_root)
        non_matching = parser.get_non_matching_objects()

        # Should have some non-matching objects
        assert len(non_matching) > 0

        # All should be NonMatching
        for obj in non_matching:
            assert obj.status == "NonMatching"

    def test_get_matching_objects(self, melee_root):
        """Test getting only matching objects."""
        parser = ConfigureParser(melee_root)
        matching = parser.get_matching_objects()

        # Should have some matching objects
        assert len(matching) > 0

        # All should be Matching
        for obj in matching:
            assert obj.status == "Matching"

    def test_get_object_status(self, melee_root):
        """Test getting status of a specific object."""
        parser = ConfigureParser(melee_root)
        all_objects = parser.parse_objects()

        # Get first object and test retrieval
        if all_objects:
            first_obj = all_objects[0]
            retrieved = parser.get_object_status(first_obj.file_path)
            assert retrieved is not None
            assert retrieved.file_path == first_obj.file_path
            assert retrieved.status == first_obj.status

        # Test non-existent object
        non_existent = parser.get_object_status("nonexistent/file.c")
        assert non_existent is None

    def test_get_all_libs(self, melee_root):
        """Test getting all library names."""
        parser = ConfigureParser(melee_root)
        libs = parser.get_all_libs()

        # Should return a list (may be empty if parser doesn't capture libs)
        assert isinstance(libs, list)

    def test_get_objects_by_lib(self, melee_root):
        """Test getting objects by library."""
        parser = ConfigureParser(melee_root)
        libs = parser.get_all_libs()

        # Only test if libs were captured
        if libs:
            lib_name = libs[0]
            objects = parser.get_objects_by_lib(lib_name)
            assert len(objects) > 0
            for obj in objects:
                assert obj.lib == lib_name
        else:
            # If no libs found, just verify method works
            objects = parser.get_objects_by_lib("nonexistent")
            assert isinstance(objects, list)
            assert len(objects) == 0

    def test_parse_matching_for_status(self, melee_root):
        """Test parsing MatchingFor status (treated as Matching)."""
        parser = ConfigureParser(melee_root)
        content = '''
MeleeLib("test")
Object(MatchingFor(version="GALE01"), "test/file.c")
'''
        objects = parser._extract_objects_from_content(content)
        assert len(objects) == 1
        assert objects[0].status == "Matching"

    @pytest.mark.asyncio
    async def test_async_parse_configure(self, melee_root):
        """Test async wrapper for parsing configure.py."""
        objects = await parse_configure(melee_root)
        assert len(objects) > 0
        assert all(isinstance(obj, ObjectStatus) for obj in objects)


class TestSymbolParser:
    """Test the SymbolParser class."""

    def test_init(self, melee_root):
        """Test parser initialization."""
        parser = SymbolParser(melee_root)
        assert parser.melee_root == melee_root
        assert parser.symbols_path == melee_root / "config" / "GALE01" / "symbols.txt"

    def test_parse_symbols(self, melee_root):
        """Test parsing all symbols from symbols.txt."""
        parser = SymbolParser(melee_root)
        symbols = parser.parse_symbols()

        # Should find many symbols
        assert len(symbols) > 0

        # Check structure
        for name, symbol in symbols.items():
            assert isinstance(symbol, FunctionSymbol)
            assert symbol.name == name
            assert symbol.address.startswith("0x")
            assert symbol.section

    def test_get_function_symbol(self, melee_root):
        """Test getting a specific function symbol."""
        parser = SymbolParser(melee_root)
        symbols = parser.parse_symbols()

        # Get first symbol and test retrieval
        if symbols:
            first_name = next(iter(symbols.keys()))
            symbol = parser.get_function_symbol(first_name)
            assert symbol is not None
            assert symbol.name == first_name

        # Test non-existent symbol
        non_existent = parser.get_function_symbol("NonExistentFunction")
        assert non_existent is None

    def test_get_functions_in_range(self, melee_root):
        """Test getting functions in an address range."""
        parser = SymbolParser(melee_root)

        # Get functions in init section range
        functions = parser.get_functions_in_range(0x80003100, 0x80006000)

        # Should find some functions
        assert len(functions) > 0

        # All should be in range
        for func in functions:
            addr = int(func.address, 16)
            assert 0x80003100 <= addr < 0x80006000

        # Should be sorted by address
        addresses = [int(f.address, 16) for f in functions]
        assert addresses == sorted(addresses)

    def test_get_functions_by_section(self, melee_root):
        """Test getting functions by section."""
        parser = SymbolParser(melee_root)

        # Get init section functions
        init_functions = parser.get_functions_by_section("init")

        # Should find some
        assert len(init_functions) > 0

        # All should be in init section
        for func in init_functions:
            assert func.section == "init"

        # Should be sorted by address
        addresses = [int(f.address, 16) for f in init_functions]
        assert addresses == sorted(addresses)

    def test_parse_symbol_with_size(self, melee_root):
        """Test parsing symbol with size information."""
        parser = SymbolParser(melee_root)
        symbols = parser.parse_symbols()

        # memset should have size 0x30
        if "memset" in symbols:
            memset = symbols["memset"]
            assert memset.size_bytes == 0x30
            assert memset.section == "init"

    @pytest.mark.asyncio
    async def test_async_parse_symbols(self, melee_root):
        """Test async wrapper for parsing symbols.txt."""
        symbols = await parse_symbols(melee_root)
        assert len(symbols) > 0
        assert all(isinstance(s, FunctionSymbol) for s in symbols.values())


class TestSplitsParser:
    """Test the SplitsParser class."""

    def test_init(self, melee_root):
        """Test parser initialization."""
        parser = SplitsParser(melee_root)
        assert parser.melee_root == melee_root
        assert parser.splits_path == melee_root / "config" / "GALE01" / "splits.txt"

    def test_parse_splits(self, melee_root):
        """Test parsing splits.txt."""
        parser = SplitsParser(melee_root)
        file_ranges = parser.parse_splits()

        # Should find many files
        assert len(file_ranges) > 0

        # Check structure
        for file_path, ranges in file_ranges.items():
            assert file_path.endswith(".c")
            assert len(ranges) > 0

            for range_info in ranges:
                assert "section" in range_info
                assert "start" in range_info
                assert "end" in range_info
                assert range_info["start"] < range_info["end"]

    def test_get_all_source_files(self, melee_root):
        """Test getting all source files."""
        parser = SplitsParser(melee_root)
        files = parser.get_all_source_files()

        # Should find many files
        assert len(files) > 0

        # All should end in .c
        for file_path in files:
            assert file_path.endswith(".c")

        # Should be sorted
        assert files == sorted(files)

    def test_get_file_for_address(self, melee_root):
        """Test getting file for a specific address."""
        parser = SplitsParser(melee_root)

        # Address from splits.txt: lbcommand.c contains 0x80005940 - 0x80005BB0
        file_path = parser.get_file_for_address(0x80005940)
        assert file_path is not None
        assert "lbcommand.c" in file_path

        # Address outside any range
        file_path = parser.get_file_for_address(0xFFFFFFFF)
        assert file_path is None

    def test_get_file_for_function(self, melee_root):
        """Test getting file for a function address."""
        parser = SplitsParser(melee_root)

        # Function at 0x80005940 should be in lbcommand.c
        file_path = parser.get_file_for_function("0x80005940", "text")
        assert file_path is not None
        assert "lbcommand.c" in file_path

        # Invalid address format
        file_path = parser.get_file_for_function("invalid", "text")
        assert file_path is None

    def test_get_functions_in_file(self, melee_root):
        """Test getting all functions in a specific file."""
        parser = SplitsParser(melee_root)
        symbol_parser = SymbolParser(melee_root)
        symbols = symbol_parser.parse_symbols()

        # Get a file from splits
        all_files = parser.get_all_source_files()
        if all_files:
            test_file = all_files[0]
            functions = parser.get_functions_in_file(test_file, symbols)

            # May or may not have functions, just check it doesn't error
            assert isinstance(functions, list)

    def test_caching(self, melee_root):
        """Test that parse results are cached."""
        parser = SplitsParser(melee_root)

        # First call should parse
        result1 = parser.parse_splits()

        # Second call should return cached result
        result2 = parser.parse_splits()

        assert result1 is result2

    @pytest.mark.asyncio
    async def test_async_parse_splits(self, melee_root):
        """Test async wrapper for parsing splits.txt."""
        file_ranges = await parse_splits(melee_root)
        assert len(file_ranges) > 0


class TestFunctionExtractor:
    """Test the FunctionExtractor class integration."""

    def test_init(self, melee_root):
        """Test extractor initialization."""
        extractor = FunctionExtractor(melee_root)
        assert extractor.melee_root == melee_root
        assert extractor.configure_parser is not None
        assert extractor.symbol_parser is not None
        assert extractor.splits_parser is not None

    def test_extract_function_basic(self, melee_root):
        """Test extracting a specific function without ASM or context."""
        extractor = FunctionExtractor(melee_root)
        symbol_parser = SymbolParser(melee_root)
        symbols = symbol_parser.parse_symbols()

        if symbols:
            # Get first function
            func_name = next(iter(symbols.keys()))

            # Extract without ASM or context for speed
            func_info = extractor.extract_function(
                func_name,
                include_asm=False,
                include_context=False
            )

            if func_info:  # May not find if function not in a configured object
                assert func_info.name == func_name
                assert func_info.address
                assert func_info.file_path
                assert func_info.object_status in ["Matching", "NonMatching", "Equivalent"]
                assert func_info.asm is None  # Not requested
                assert func_info.context is None  # Not requested

    def test_extract_non_existent_function(self, melee_root):
        """Test extracting a function that doesn't exist."""
        extractor = FunctionExtractor(melee_root)
        func_info = extractor.extract_function("NonExistentFunction")
        assert func_info is None

    def test_find_source_file_for_function(self, melee_root):
        """Test finding source file using splits.txt."""
        extractor = FunctionExtractor(melee_root)
        symbol_parser = SymbolParser(melee_root)
        symbols = symbol_parser.parse_symbols()

        configure_parser = ConfigureParser(melee_root)
        objects = configure_parser.parse_objects()
        object_map = {obj.file_path: obj for obj in objects}

        if symbols:
            func_name = next(iter(symbols.keys()))
            source_file = extractor._find_source_file_for_function(func_name, object_map)

            # May or may not find depending on configuration
            if source_file:
                assert source_file.endswith(".c")
                assert source_file in object_map

    @pytest.mark.asyncio
    async def test_async_extract_unmatched_functions(self, melee_root):
        """Test async extraction of unmatched functions."""
        # Extract without ASM/context for speed
        result = await extract_unmatched_functions(
            melee_root,
            include_asm=False,
            include_context=False
        )

        # Should have some statistics
        assert result.total_functions >= 0
        assert result.matched_functions >= 0
        assert result.unmatched_functions >= 0

        # All functions should be unmatched
        for func in result.functions:
            assert not func.is_matched

    @pytest.mark.asyncio
    async def test_async_extract_function(self, melee_root):
        """Test async extraction of a specific function."""
        symbol_parser = SymbolParser(melee_root)
        symbols = symbol_parser.parse_symbols()

        if symbols:
            func_name = next(iter(symbols.keys()))
            func_info = await extract_function(
                melee_root,
                func_name,
                include_asm=False,
                include_context=False
            )

            # May or may not find depending on configuration
            if func_info:
                assert func_info.name == func_name


class TestReportParser:
    """Test the ReportParser class for report.json parsing."""

    def test_init(self, melee_root):
        """Test parser initialization."""
        from src.extractor.report import ReportParser
        parser = ReportParser(melee_root)
        assert parser.melee_root == melee_root
        assert parser.report_path == melee_root / "build" / "GALE01" / "report.json"

    def test_get_function_matches(self, melee_root):
        """Test getting function match data."""
        from src.extractor.report import ReportParser
        parser = ReportParser(melee_root)

        try:
            matches = parser.get_function_matches()
        except FileNotFoundError:
            pytest.skip("report.json not found - run ninja first")

        # Should have functions
        assert len(matches) > 0

        # Check structure
        for name, match in list(matches.items())[:5]:
            assert match.name == name
            assert 0 <= match.fuzzy_match_percent <= 100

    def test_function_match_has_address(self, melee_root):
        """Test that FunctionMatch includes address from virtual_address."""
        from src.extractor.report import ReportParser
        parser = ReportParser(melee_root)

        try:
            matches = parser.get_function_matches()
        except FileNotFoundError:
            pytest.skip("report.json not found - run ninja first")

        # Find a function with an address
        funcs_with_addr = [m for m in matches.values() if m.address]
        assert len(funcs_with_addr) > 0, "No functions have addresses"

        # Check address format
        for match in funcs_with_addr[:5]:
            assert match.address.startswith("0x"), f"Address should be hex: {match.address}"
            assert len(match.address) == 10, f"Address should be 0xXXXXXXXX: {match.address}"

    def test_get_function_match_single(self, melee_root):
        """Test getting match data for a single function."""
        from src.extractor.report import ReportParser
        parser = ReportParser(melee_root)

        try:
            matches = parser.get_function_matches()
        except FileNotFoundError:
            pytest.skip("report.json not found - run ninja first")

        if matches:
            func_name = next(iter(matches.keys()))
            match = parser.get_function_match(func_name)
            assert match is not None
            assert match.name == func_name

    def test_get_overall_stats(self, melee_root):
        """Test getting overall match statistics."""
        from src.extractor.report import ReportParser
        parser = ReportParser(melee_root)

        try:
            stats = parser.get_overall_stats()
        except FileNotFoundError:
            pytest.skip("report.json not found - run ninja first")

        assert "total_functions" in stats
        assert "matched_functions" in stats
        assert "average_match" in stats
        assert stats["total_functions"] >= 0


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_complete_pipeline(self, melee_root):
        """Test the complete extraction pipeline."""
        # Parse all components
        configure_parser = ConfigureParser(melee_root)
        symbol_parser = SymbolParser(melee_root)
        splits_parser = SplitsParser(melee_root)

        objects = configure_parser.parse_objects()
        symbols = symbol_parser.parse_symbols()
        file_ranges = splits_parser.parse_splits()

        # Verify we got data from all parsers
        assert len(objects) > 0
        assert len(symbols) > 0
        assert len(file_ranges) > 0

    def test_function_to_file_mapping(self, melee_root):
        """Test mapping functions to their source files."""
        symbol_parser = SymbolParser(melee_root)
        splits_parser = SplitsParser(melee_root)

        symbols = symbol_parser.parse_symbols()

        # Try to map some functions to files
        mapped_count = 0
        for func_name, symbol in list(symbols.items())[:10]:  # Test first 10
            file_path = splits_parser.get_file_for_function(symbol.address, symbol.section)
            if file_path:
                mapped_count += 1

        # At least some functions should be mappable
        assert mapped_count > 0

    def test_object_status_consistency(self, melee_root):
        """Test that object statuses are consistent."""
        configure_parser = ConfigureParser(melee_root)
        objects = configure_parser.parse_objects()

        # Count statuses
        matching = sum(1 for obj in objects if obj.status == "Matching")
        non_matching = sum(1 for obj in objects if obj.status == "NonMatching")
        equivalent = sum(1 for obj in objects if obj.status == "Equivalent")

        # Total should equal all objects
        assert matching + non_matching + equivalent == len(objects)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
