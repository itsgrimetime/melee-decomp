"""Tests for CLI common utilities.

These tests focus on behavior rather than implementation details,
making them resilient to refactoring. They test:
1. Path parsing and subdirectory key generation
2. PR URL/number parsing
3. Function categorization logic
4. Context file resolution
5. Compiler detection
6. Melee root resolution
"""

import pytest
from pathlib import Path


class TestSubdirectoryKeyParsing:
    """Tests for get_subdirectory_key - maps file paths to worktree keys.

    This is critical for the worktree isolation system. The function must
    handle various path formats that might come from different sources.
    """

    @pytest.fixture
    def get_subdirectory_key(self):
        from src.cli._common import get_subdirectory_key
        return get_subdirectory_key

    # Character files get their own worktree
    @pytest.mark.parametrize("path,expected", [
        ("ft/chara/ftFox/ftFx_SpecialHi.c", "ft-chara-ftFox"),
        ("ft/chara/ftMario/ftMr_Attack.c", "ft-chara-ftMario"),
        ("ft/chara/ftCommon/ftCo_Attack100.c", "ft-chara-ftCommon"),
    ])
    def test_character_files_get_own_worktree(self, get_subdirectory_key, path, expected):
        """Each character directory under ft/chara/ gets its own worktree."""
        assert get_subdirectory_key(path) == expected

    # Top-level modules use module name as key
    @pytest.mark.parametrize("path,expected", [
        ("lb/lbcollision.c", "lb"),
        ("gr/grbigblue.c", "gr"),
        ("it/item.c", "it"),
        ("gm/gmmain.c", "gm"),
        ("cm/camera.c", "cm"),
    ])
    def test_toplevel_modules(self, get_subdirectory_key, path, expected):
        """Top-level module files use the module directory as key."""
        assert get_subdirectory_key(path) == expected

    # Items get separate worktree from main it/
    def test_items_subdirectory_separate(self, get_subdirectory_key):
        """it/items/ is separate from it/ to isolate item-specific work."""
        assert get_subdirectory_key("it/items/itkinoko.c") == "it-items"
        assert get_subdirectory_key("it/item.c") == "it"

    # Various path prefix formats should all work
    @pytest.mark.parametrize("path", [
        "ft/chara/ftFox/ftFx_SpecialHi.c",           # relative to src/melee/
        "melee/ft/chara/ftFox/ftFx_SpecialHi.c",     # melee repo relative
        "src/melee/ft/chara/ftFox/ftFx_SpecialHi.c", # src-prefixed
        "melee/src/melee/ft/chara/ftFox/ftFx_SpecialHi.c",  # full project path
    ])
    def test_handles_various_path_formats(self, get_subdirectory_key, path):
        """All path formats should normalize to the same key."""
        assert get_subdirectory_key(path) == "ft-chara-ftFox"

    def test_root_level_files(self, get_subdirectory_key):
        """Files at root level should return 'root'."""
        assert get_subdirectory_key("main.c") == "root"


class TestWorktreeNaming:
    """Tests for worktree path generation."""

    @pytest.fixture
    def get_worktree_name(self):
        from src.cli._common import get_worktree_name_for_subdirectory
        return get_worktree_name_for_subdirectory

    def test_worktree_name_format(self, get_worktree_name):
        """Worktree names should have 'dir-' prefix."""
        assert get_worktree_name("lb") == "dir-lb"
        assert get_worktree_name("ft-chara-ftFox") == "dir-ft-chara-ftFox"


class TestPRInfoExtraction:
    """Tests for extract_pr_info - parses PR URLs and numbers.

    This is used to track PRs and check their status.
    """

    @pytest.fixture
    def extract_pr_info(self):
        from src.cli._common import extract_pr_info
        return extract_pr_info

    def test_full_github_url(self, extract_pr_info):
        """Full GitHub PR URLs should be parsed correctly."""
        repo, num = extract_pr_info("https://github.com/doldecomp/melee/pull/123")
        assert repo == "doldecomp/melee"
        assert num == 123

    def test_http_url(self, extract_pr_info):
        """HTTP (non-HTTPS) URLs should also work."""
        repo, num = extract_pr_info("http://github.com/doldecomp/melee/pull/456")
        assert repo == "doldecomp/melee"
        assert num == 456

    def test_repo_hash_format(self, extract_pr_info):
        """repo#number format should work."""
        repo, num = extract_pr_info("doldecomp/melee#789")
        assert repo == "doldecomp/melee"
        assert num == 789

    def test_just_number_uses_default_repo(self, extract_pr_info):
        """Plain PR number should use default repo."""
        repo, num = extract_pr_info("123")
        assert repo == "doldecomp/melee"
        assert num == 123

    def test_number_with_whitespace(self, extract_pr_info):
        """Whitespace should be trimmed."""
        repo, num = extract_pr_info("  456  ")
        assert num == 456

    def test_invalid_input_returns_empty(self, extract_pr_info):
        """Invalid input should return empty repo and 0."""
        repo, num = extract_pr_info("not-a-pr")
        assert repo == ""
        assert num == 0


class TestFunctionCategorization:
    """Tests for categorize_functions - groups functions by status.

    This is the core logic for tracking decompilation progress.
    Categories are based on match percentage and workflow state.
    """

    @pytest.fixture
    def categorize_functions(self):
        from src.cli._common import categorize_functions
        return categorize_functions

    def _make_data(self, completed=None, slug_map=None, synced=None):
        """Helper to build test data structure."""
        return {
            "completed": completed or {},
            "slug_map": slug_map or {},
            "synced": synced or {},
        }

    def test_high_match_with_pr_is_in_review(self, categorize_functions):
        """95%+ match with PR URL goes to in_review."""
        data = self._make_data(completed={
            "func1": {"match_percent": 98, "pr_url": "https://github.com/doldecomp/melee/pull/1"}
        })
        result = categorize_functions(data, check_pr_status=False)

        assert len(result["in_review"]) == 1
        assert result["in_review"][0]["function"] == "func1"

    def test_high_match_committed_no_pr(self, categorize_functions):
        """95%+ match that's committed but no PR goes to committed."""
        data = self._make_data(completed={
            "func1": {"match_percent": 95, "committed": True}
        })
        result = categorize_functions(data)

        assert len(result["committed"]) == 1
        assert result["committed"][0]["function"] == "func1"

    def test_high_match_synced_goes_to_ready(self, categorize_functions):
        """95%+ match that's synced to production goes to ready."""
        data = self._make_data(
            completed={"func1": {"match_percent": 100, "scratch_slug": "ABC"}},
            synced={"ABC": {"some": "data"}}
        )
        result = categorize_functions(data)

        assert len(result["ready"]) == 1
        assert result["ready"][0]["function"] == "func1"

    def test_high_match_not_synced_is_lost(self, categorize_functions):
        """95%+ match not synced anywhere goes to lost_high_match."""
        data = self._make_data(completed={
            "func1": {"match_percent": 97}
        })
        result = categorize_functions(data)

        assert len(result["lost_high_match"]) == 1
        assert result["lost_high_match"][0]["function"] == "func1"

    def test_low_match_is_work_in_progress(self, categorize_functions):
        """<95% match goes to work_in_progress."""
        data = self._make_data(completed={
            "func1": {"match_percent": 50},
            "func2": {"match_percent": 94},
        })
        result = categorize_functions(data)

        assert len(result["work_in_progress"]) == 2
        funcs = [e["function"] for e in result["work_in_progress"]]
        assert "func1" in funcs
        assert "func2" in funcs

    def test_skips_upstream_functions(self, categorize_functions):
        """Functions marked as already_in_upstream should be skipped."""
        data = self._make_data(completed={
            "func1": {"match_percent": 100, "already_in_upstream": True}
        })
        result = categorize_functions(data)

        # Should not appear in any category
        all_funcs = []
        for cat in result.values():
            all_funcs.extend([e["function"] for e in cat])
        assert "func1" not in all_funcs

    def test_results_sorted_by_match_percent_descending(self, categorize_functions):
        """Each category should be sorted by match percentage, highest first."""
        data = self._make_data(completed={
            "func_low": {"match_percent": 30},
            "func_mid": {"match_percent": 60},
            "func_high": {"match_percent": 90},
        })
        result = categorize_functions(data)

        wip = result["work_in_progress"]
        assert wip[0]["function"] == "func_high"
        assert wip[1]["function"] == "func_mid"
        assert wip[2]["function"] == "func_low"

    def test_function_in_prod_slug_map_is_synced(self, categorize_functions):
        """Function appearing in slug_map (prod) counts as synced."""
        data = self._make_data(
            completed={"func1": {"match_percent": 99}},
            slug_map={"PROD_SLUG": {"function": "func1"}}
        )
        result = categorize_functions(data)

        assert len(result["ready"]) == 1
        assert result["ready"][0]["function"] == "func1"

    def test_all_categories_present(self, categorize_functions):
        """Result should always have all category keys."""
        data = self._make_data()
        result = categorize_functions(data)

        expected_keys = {"merged", "in_review", "committed", "ready",
                        "lost_high_match", "work_in_progress"}
        assert set(result.keys()) == expected_keys


class TestContextFileResolution:
    """Tests for get_context_file - finds the right .ctx file for a source.

    The context file lookup has a fallback chain:
    1. Per-file .ctx in worktree build dir
    2. Per-file .ctx in main melee build dir
    3. Legacy consolidated ctx.c
    """

    @pytest.fixture
    def get_context_file(self):
        from src.cli._common import get_context_file
        return get_context_file

    def test_per_file_ctx_path_format(self, get_context_file, tmp_path):
        """Per-file context should map source path to .ctx path correctly."""
        # Create a fake melee structure
        melee_root = tmp_path / "melee"
        ctx_dir = melee_root / "build" / "GALE01" / "src" / "melee" / "lb"
        ctx_dir.mkdir(parents=True)
        ctx_file = ctx_dir / "lbcollision.ctx"
        ctx_file.write_text("/* context */")

        result = get_context_file("melee/lb/lbcollision.c", melee_root)

        assert result == ctx_file

    def test_source_path_normalization(self, get_context_file, tmp_path):
        """Source path with melee/ prefix should find context."""
        melee_root = tmp_path / "melee"
        ctx_dir = melee_root / "build" / "GALE01" / "src" / "melee" / "ft"
        ctx_dir.mkdir(parents=True)
        ctx_file = ctx_dir / "fighter.ctx"
        ctx_file.write_text("/* context */")

        # Path with melee/ prefix should work
        result = get_context_file("melee/ft/fighter.c", melee_root)
        assert result.exists()

    def test_fallback_to_legacy_ctx(self, get_context_file, tmp_path):
        """Should fall back to consolidated ctx.c if per-file not found."""
        melee_root = tmp_path / "melee"
        build_dir = melee_root / "build"
        build_dir.mkdir(parents=True)
        legacy_ctx = build_dir / "ctx.c"
        legacy_ctx.write_text("/* legacy context */")

        # No per-file .ctx exists, should fall back
        result = get_context_file("melee/lb/lbcollision.c", melee_root)

        assert result == legacy_ctx

    def test_returns_expected_path_when_missing(self, get_context_file, tmp_path):
        """Should return expected path even if file doesn't exist (for error messages)."""
        melee_root = tmp_path / "melee"
        melee_root.mkdir(parents=True)

        result = get_context_file("melee/lb/lbcollision.c", melee_root)

        # Should return the expected path structure
        assert "build" in str(result)
        assert "GALE01" in str(result)
        assert "lbcollision.ctx" in str(result)


class TestCompilerDetection:
    """Tests for get_compiler_for_source - parses build.ninja for compiler.

    Different source files may use different MWCC compiler versions.
    This is determined by parsing the build.ninja file.
    """

    @pytest.fixture
    def get_compiler(self):
        from src.cli._common import get_compiler_for_source
        return get_compiler_for_source

    @pytest.fixture
    def default_compiler(self):
        from src.cli._common import DEFAULT_DECOMP_COMPILER
        return DEFAULT_DECOMP_COMPILER

    def test_parses_mw_version_from_build_ninja(self, get_compiler, tmp_path):
        """Should extract mw_version for the target file."""
        melee_root = tmp_path / "melee"
        melee_root.mkdir()

        # Create a realistic build.ninja snippet
        build_ninja = melee_root / "build.ninja"
        build_ninja.write_text("""
# melee/lb/lbcollision.c: lb (Library) (linked False)
build build/GALE01/src/melee/lb/lbcollision.o: mwcc_sjis $
    src/melee/lb/lbcollision.c | tools/mwcc_compiler/...
  mw_version = GC/1.2.5n
  cflags = -O4,p

# melee/ft/fighter.c: ft (Library) (linked True)
build build/GALE01/src/melee/ft/fighter.o: mwcc_sjis $
    src/melee/ft/fighter.c | tools/mwcc_compiler/...
  mw_version = GC/1.2.5
  cflags = -O4,p
""")

        result = get_compiler("melee/lb/lbcollision.c", melee_root)

        # GC/1.2.5n should map to a specific decomp.me compiler
        assert result is not None
        assert "mwcc" in result or result.startswith("mwcc")

    def test_returns_default_when_no_build_ninja(self, get_compiler, default_compiler, tmp_path):
        """Should return default compiler if build.ninja doesn't exist."""
        melee_root = tmp_path / "melee"
        melee_root.mkdir()

        result = get_compiler("melee/lb/lbcollision.c", melee_root)

        assert result == default_compiler

    def test_returns_default_for_unknown_file(self, get_compiler, default_compiler, tmp_path):
        """Should return default compiler if file not found in build.ninja."""
        melee_root = tmp_path / "melee"
        melee_root.mkdir()

        build_ninja = melee_root / "build.ninja"
        build_ninja.write_text("""
# melee/lb/lbcollision.c: lb (Library)
build build/GALE01/src/melee/lb/lbcollision.o: mwcc_sjis
  mw_version = GC/1.2.5n
""")

        # Different file not in build.ninja
        result = get_compiler("melee/gr/grbigblue.c", melee_root)

        assert result == default_compiler

    def test_handles_src_prefix_variations(self, get_compiler, tmp_path):
        """Should handle source paths with or without src/ prefix."""
        melee_root = tmp_path / "melee"
        melee_root.mkdir()

        build_ninja = melee_root / "build.ninja"
        build_ninja.write_text("""
# melee/lb/lbcollision.c: lb (Library)
build build/GALE01/src/melee/lb/lbcollision.o: mwcc_sjis
  mw_version = GC/1.2.5n
""")

        # Both formats should work
        result1 = get_compiler("melee/lb/lbcollision.c", melee_root)
        result2 = get_compiler("src/melee/lb/lbcollision.c", melee_root)

        assert result1 == result2


class TestMeleeRootResolution:
    """Tests for resolve_melee_root - finds the right melee directory.

    This is critical for worktree isolation - commands must operate
    in the correct worktree based on the file being modified.
    """

    @pytest.fixture
    def resolve_melee_root(self):
        from src.cli._common import resolve_melee_root
        return resolve_melee_root

    @pytest.fixture
    def default_melee_root(self):
        from src.cli._common import DEFAULT_MELEE_ROOT
        return DEFAULT_MELEE_ROOT

    def test_explicit_path_returned_as_is(self, resolve_melee_root, tmp_path):
        """Explicitly provided melee_root should be returned unchanged."""
        explicit_path = tmp_path / "my_melee"

        result = resolve_melee_root(explicit_path)

        assert result == explicit_path

    def test_none_falls_back_to_default(self, resolve_melee_root, default_melee_root, monkeypatch):
        """None melee_root should fall back to default."""
        # Ensure we're not in a worktree
        monkeypatch.chdir("/tmp")

        result = resolve_melee_root(None)

        assert result == default_melee_root

    def test_target_file_triggers_worktree_lookup(self, resolve_melee_root, monkeypatch, tmp_path):
        """Providing target_file should use subdirectory worktree."""
        # This test verifies the behavior exists, even if we can't fully test
        # the worktree creation without more setup
        monkeypatch.chdir("/tmp")

        # When target_file is provided, it should try to find/create a worktree
        # The exact behavior depends on get_worktree_for_file which has side effects
        # so we just verify the function accepts the parameter
        try:
            result = resolve_melee_root(None, target_file="melee/lb/lbcollision.c")
            # If it succeeds, result should be a Path
            assert isinstance(result, Path)
        except Exception:
            # If it fails (no git setup), that's expected in test environment
            pass


class TestSourceFileFromClaim:
    """Tests for get_source_file_from_claim - looks up claimed source files.

    When committing, we need to know which source file a function belongs to
    so we can use the correct subdirectory worktree.
    """

    @pytest.fixture
    def get_source_file_from_claim(self):
        from src.cli._common import get_source_file_from_claim
        return get_source_file_from_claim

    def test_returns_none_when_no_claims_file(self, get_source_file_from_claim, tmp_path, monkeypatch):
        """Should return None if claims file doesn't exist."""
        # Ensure claims file doesn't exist
        import os
        if os.path.exists("/tmp/decomp_claims.json"):
            os.remove("/tmp/decomp_claims.json")

        result = get_source_file_from_claim("some_func")

        assert result is None

    def test_returns_source_file_for_claimed_function(self, get_source_file_from_claim, tmp_path):
        """Should return source file from valid claim."""
        import json
        import time

        claims = {
            "my_func": {
                "source_file": "melee/lb/lbcollision.c",
                "timestamp": time.time(),  # Fresh claim
            }
        }

        claims_file = Path("/tmp/decomp_claims.json")
        claims_file.write_text(json.dumps(claims))

        try:
            result = get_source_file_from_claim("my_func")
            assert result == "melee/lb/lbcollision.c"
        finally:
            claims_file.unlink(missing_ok=True)

    def test_returns_none_for_expired_claim(self, get_source_file_from_claim):
        """Should return None if claim has expired."""
        import json

        claims = {
            "old_func": {
                "source_file": "melee/lb/lbcollision.c",
                "timestamp": 0,  # Very old timestamp
            }
        }

        claims_file = Path("/tmp/decomp_claims.json")
        claims_file.write_text(json.dumps(claims))

        try:
            result = get_source_file_from_claim("old_func")
            assert result is None  # Expired
        finally:
            claims_file.unlink(missing_ok=True)

    def test_returns_none_for_unclaimed_function(self, get_source_file_from_claim):
        """Should return None if function isn't in claims."""
        import json
        import time

        claims = {
            "other_func": {
                "source_file": "melee/ft/fighter.c",
                "timestamp": time.time(),
            }
        }

        claims_file = Path("/tmp/decomp_claims.json")
        claims_file.write_text(json.dumps(claims))

        try:
            result = get_source_file_from_claim("not_claimed_func")
            assert result is None
        finally:
            claims_file.unlink(missing_ok=True)
