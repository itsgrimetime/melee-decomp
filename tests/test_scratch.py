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


class TestPreprocessContext:
    """Tests for _preprocess_context - removes preprocessor directives for m2c.

    The m2c decompiler can't handle preprocessor directives like #include,
    #define, #ifdef, etc. This function uses gcc -E to preprocess the context.
    """

    @pytest.fixture
    def preprocess_context(self):
        from src.cli.scratch import _preprocess_context
        return _preprocess_context

    def test_empty_context_passthrough(self, preprocess_context):
        """Empty context should pass through unchanged."""
        result, success = preprocess_context("")
        assert result == ""
        assert success is True

    def test_whitespace_only_passthrough(self, preprocess_context):
        """Whitespace-only context should pass through."""
        result, success = preprocess_context("   \n\t\n  ")
        assert result == "   \n\t\n  "
        assert success is True

    def test_no_directives_passthrough(self, preprocess_context):
        """Context without preprocessor directives should pass through."""
        context = """
typedef struct {
    int x;
    int y;
} Point;

extern void foo(Point* p);
"""
        result, success = preprocess_context(context)
        assert result == context
        assert success is True

    def test_detects_hash_directives(self, preprocess_context):
        """Context with # directives should be detected for preprocessing."""
        context = """#ifndef HEADER_H
#define HEADER_H
typedef int MyInt;
#endif
"""
        # This will try to preprocess - success depends on gcc availability
        result, success = preprocess_context(context)
        # Either it succeeds (gcc available) or fails gracefully
        assert isinstance(result, str)
        assert isinstance(success, bool)

    def test_preserves_non_directive_hashes(self, preprocess_context):
        """# in strings or comments shouldn't trigger preprocessing (line starts with #)."""
        context = """
// This is a comment with # in it
char* str = "string with # symbol";
int x = 5;
"""
        # No lines start with #, so should pass through
        result, success = preprocess_context(context)
        assert result == context
        assert success is True

    def test_static_assert_removed(self, preprocess_context):
        """_Static_assert statements should be removed for m2c compatibility.

        m2c decompiler can't parse _Static_assert which is a C11 feature.
        This must be handled even when there are no preprocessor directives.

        Regression test for: Syntax error when parsing C context at _Static_assert
        """
        context = """typedef struct {
    int x;
    int y;
} Point;

_Static_assert((sizeof(Point) == 8), "Point size mismatch");

extern void foo(Point* p);
"""
        result, success = preprocess_context(context)
        assert success is True
        # _Static_assert should be removed or commented out
        # Check that it's either not present, or inside a comment
        if "_Static_assert" in result:
            comment_start = result.find("/*")
            static_assert_pos = result.find("_Static_assert")
            assert comment_start != -1 and comment_start < static_assert_pos, \
                f"_Static_assert should be removed or commented out, but found at position {static_assert_pos}"
        # But the rest of the code should remain
        assert "typedef struct" in result
        assert "extern void foo" in result

    def test_static_assert_multiline_removed(self, preprocess_context):
        """Multi-line _Static_assert should also be removed."""
        context = """typedef struct mnGallery_804A0B90_t {
    char data[0x96000];
} mnGallery_804A0B90_t;

_Static_assert((sizeof(struct mnGallery_804A0B90_t) == 0x96000), "("
"sizeof(struct mnGallery_804A0B90_t) == 0x96000" ") failed");

void other_func(void);
"""
        result, success = preprocess_context(context)
        assert success is True
        # _Static_assert should be removed or commented out
        if "_Static_assert" in result:
            comment_start = result.find("/*")
            static_assert_pos = result.find("_Static_assert")
            assert comment_start != -1 and comment_start < static_assert_pos, \
                f"_Static_assert should be removed or commented out, but found at position {static_assert_pos}"
        assert "void other_func" in result


class TestSlugExtraction:
    """Tests for slug extraction from URLs.

    Several commands accept either a raw slug or a full URL.
    This tests the extraction logic.
    """

    def test_extract_slug_from_url(self):
        """Should extract slug from decomp.me URL."""
        # Simulating the extraction logic used in scratch commands
        url = "http://10.200.0.1/scratch/abc123"
        slug = url
        if slug.startswith("http"):
            parts = slug.strip("/").split("/")
            if "scratch" in parts:
                idx = parts.index("scratch")
                if idx + 1 < len(parts):
                    slug = parts[idx + 1]
        assert slug == "abc123"

    def test_extract_slug_from_url_with_trailing_slash(self):
        """Should handle trailing slash in URL."""
        url = "http://decomp.me/scratch/xyz789/"
        slug = url
        if slug.startswith("http"):
            parts = slug.strip("/").split("/")
            if "scratch" in parts:
                idx = parts.index("scratch")
                if idx + 1 < len(parts):
                    slug = parts[idx + 1]
        assert slug == "xyz789"

    def test_raw_slug_passthrough(self):
        """Raw slug without http should pass through."""
        slug = "abc123"
        if slug.startswith("http"):
            # This branch won't be taken
            parts = slug.strip("/").split("/")
            if "scratch" in parts:
                idx = parts.index("scratch")
                if idx + 1 < len(parts):
                    slug = parts[idx + 1]
        assert slug == "abc123"

    def test_https_url_extraction(self):
        """Should handle HTTPS URLs."""
        url = "https://decomp.me/scratch/def456"
        slug = url
        if slug.startswith("http"):
            parts = slug.strip("/").split("/")
            if "scratch" in parts:
                idx = parts.index("scratch")
                if idx + 1 < len(parts):
                    slug = parts[idx + 1]
        assert slug == "def456"


class TestBuildFreshContext:
    """Tests for _build_fresh_context helper function.

    This function builds context from the repo using ninja and strips
    the target function definition.
    """

    def test_context_path_from_melee_root(self):
        """Test that context path calculation works for normal melee root."""
        from pathlib import Path

        # Simulate the path calculation logic
        melee_root = Path("/Users/mike/code/melee-decomp/melee")
        ctx_path = melee_root / "build" / "GALE01" / "src" / "melee" / "ft" / "ftcoll.ctx"

        try:
            ctx_relative = ctx_path.relative_to(melee_root)
            ninja_cwd = melee_root
            assert str(ctx_relative) == "build/GALE01/src/melee/ft/ftcoll.ctx"
            assert ninja_cwd == melee_root
        except ValueError:
            pytest.fail("Should not raise ValueError for path within melee_root")

    def test_context_path_from_worktree(self):
        """Test that context path calculation works for worktrees."""
        from pathlib import Path

        # Worktree paths have a different structure
        ctx_path = Path("/Users/mike/code/melee-decomp/melee-worktrees/dir-ft/build/GALE01/src/melee/ft/ftcoll.ctx")
        melee_root = Path("/Users/mike/code/melee-decomp/melee")

        # This should fail relative_to and fall back to worktree detection
        try:
            ctx_relative = ctx_path.relative_to(melee_root)
            # If this succeeds, we're in the normal case
            ninja_cwd = melee_root
        except ValueError:
            # Worktree case - find the build directory
            parts = ctx_path.parts
            ninja_cwd = None
            for i, part in enumerate(parts):
                if part == "build" and i > 0:
                    ninja_cwd = Path(*parts[:i])
                    ctx_relative = Path(*parts[i:])
                    break

            assert ninja_cwd is not None
            assert str(ninja_cwd) == "/Users/mike/code/melee-decomp/melee-worktrees/dir-ft"
            assert str(ctx_relative) == "build/GALE01/src/melee/ft/ftcoll.ctx"


class TestContextStrippingIntegration:
    """Tests verifying context stripping is called with correct params.

    The update-context and refresh-context features both need to strip
    the target function definition from context to avoid redefinition errors.
    """

    def test_strip_target_function_import(self):
        """Verify _strip_target_function can be imported from extract module."""
        from src.cli.extract import _strip_target_function
        assert callable(_strip_target_function)

    def test_strip_function_basic(self):
        """Basic test that stripping removes function definition but keeps declaration."""
        from src.cli.extract import _strip_target_function

        context = """
extern void foo(void);

void foo(void) {
    // implementation
    int x = 1;
}

void bar(void) {
    foo();
}
"""
        result = _strip_target_function(context, "foo")

        # Declaration should remain
        assert "extern void foo(void);" in result
        # Definition body should be stripped
        assert "int x = 1" not in result
        # Other functions should remain
        assert "void bar(void)" in result
        assert "foo();" in result  # Call to foo should remain
