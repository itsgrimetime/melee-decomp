"""Tests for scratch CLI module.

These tests focus on the data processing and formatting logic
that should survive refactoring. API integration is tested separately
in test_client.py.
"""

import pytest


class TestExtractText:
    """Tests for _extract_text - extracts plain text from diff data.

    The decomp.me API returns diff text in various formats:
    - Plain strings
    - List of dicts with 'text' keys
    - Mixed lists
    """

    @pytest.fixture
    def extract_text(self):
        from src.cli.scratch import _extract_text
        return _extract_text

    def test_string_passthrough(self, extract_text):
        """Plain strings are returned as-is."""
        assert extract_text("hello world") == "hello world"

    def test_list_of_dicts_with_text(self, extract_text):
        """List of dicts extracts and joins 'text' values."""
        data = [
            {"text": "li "},
            {"text": "r3, "},
            {"text": "0"},
        ]
        assert extract_text(data) == "li r3, 0"

    def test_empty_list(self, extract_text):
        """Empty list returns empty string."""
        assert extract_text([]) == ""

    def test_none_returns_empty(self, extract_text):
        """None returns empty string."""
        assert extract_text(None) == ""

    def test_mixed_list(self, extract_text):
        """List with non-dict items converts them to strings."""
        data = [
            {"text": "foo"},
            "bar",  # plain string in list
            {"text": "baz"},
        ]
        result = extract_text(data)
        assert "foo" in result
        assert "bar" in result
        assert "baz" in result

    def test_dict_missing_text_key(self, extract_text):
        """Dicts without 'text' key contribute empty string."""
        data = [
            {"text": "has text"},
            {"other": "no text key"},
            {"text": "also has text"},
        ]
        assert extract_text(data) == "has textalso has text"


class TestDiffOutputParsing:
    """Tests for diff row parsing logic.

    These test the data structures returned by the API to ensure
    our parsing handles edge cases correctly.
    """

    def test_normalize_whitespace_for_comparison(self):
        """Whitespace normalization should work for diff comparison."""
        # This mirrors the logic in _format_diff_output
        base = "  li   r3,   0  "
        normalized = " ".join(base.split())
        assert normalized == "li r3, 0"

    def test_empty_text_handling(self):
        """Empty/missing text should be handled gracefully."""
        base_text = ""
        curr_text = ""

        base_norm = " ".join(base_text.split())
        curr_norm = " ".join(curr_text.split())

        # Empty strings should be equal
        assert base_norm == curr_norm


class TestScratchTokenPaths:
    """Tests verifying scratch token file path construction.

    Token storage is critical for maintaining scratch ownership.
    """

    def test_tokens_file_in_config_dir(self):
        """Tokens should be stored in the decomp config directory."""
        from src.cli.scratch import DECOMP_SCRATCH_TOKENS_FILE

        # Should be in a config directory, not cwd
        assert "decomp" in str(DECOMP_SCRATCH_TOKENS_FILE).lower() or \
               ".config" in str(DECOMP_SCRATCH_TOKENS_FILE)


class TestScratchCreateValidation:
    """Tests for scratch creation parameter validation.

    These test the expected inputs/outputs without calling the API.
    """

    def test_scratch_create_model_defaults(self):
        """ScratchCreate model should have sensible defaults."""
        from src.client.models import ScratchCreate

        scratch = ScratchCreate(
            target_asm="li r3, 0\nblr",
        )

        # Should have default compiler
        assert scratch.compiler == "mwcc_247_92"
        # Context defaults to empty
        assert scratch.context == ""

    def test_scratch_create_with_context(self):
        """ScratchCreate should accept context."""
        from src.client.models import ScratchCreate

        scratch = ScratchCreate(
            target_asm="li r3, 0",
            context="extern int foo;",
            source_code="int bar() { return foo; }",
        )

        assert scratch.context == "extern int foo;"
        assert scratch.source_code == "int bar() { return foo; }"


class TestCompileRequestModel:
    """Tests for compile request model."""

    def test_compile_request_with_code(self):
        """CompileRequest should capture source code."""
        from src.client.models import CompileRequest

        req = CompileRequest(
            source_code="void foo() {}",
        )

        assert req.source_code == "void foo() {}"

    def test_compile_request_with_context(self):
        """CompileRequest can include updated context."""
        from src.client.models import CompileRequest

        req = CompileRequest(
            source_code="void foo() {}",
            context="extern int x;",
        )

        assert req.context == "extern int x;"

    def test_compile_request_optional_fields(self):
        """CompileRequest fields should be optional."""
        from src.client.models import CompileRequest

        # Minimal request - all fields optional
        req = CompileRequest()
        assert req.source_code is None
        assert req.context is None
        assert req.compiler is None
