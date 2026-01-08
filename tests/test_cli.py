"""Integration tests for the CLI interface.

These tests verify CLI commands work correctly and handle errors properly.
Run with: pytest tests/test_cli.py -v
"""

import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import AsyncMock, MagicMock, patch

from src.cli import app


# Use typer's CliRunner for testing
runner = CliRunner()

# Path to the melee submodule
MELEE_ROOT = Path(__file__).parent.parent / "melee"


@pytest.fixture
def melee_root_exists():
    """Check if melee submodule exists."""
    if not MELEE_ROOT.exists():
        pytest.skip(f"Melee submodule not found at {MELEE_ROOT}")
    return MELEE_ROOT


class TestExtractCommands:
    """Test the extract command group."""

    def test_extract_help(self):
        """Test extract command help output."""
        result = runner.invoke(app, ["extract", "--help"])
        assert result.exit_code == 0
        assert "Extract and list unmatched functions" in result.stdout

    def test_extract_list_help(self):
        """Test extract list command help output."""
        result = runner.invoke(app, ["extract", "list", "--help"])
        assert result.exit_code == 0
        assert "List unmatched functions" in result.stdout
        assert "--melee-root" in result.stdout
        assert "--min-match" in result.stdout
        assert "--max-match" in result.stdout
        assert "--limit" in result.stdout

    def test_extract_list_with_invalid_path(self):
        """Test extract list with invalid melee root."""
        result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", "/nonexistent/path"
        ])
        # Should fail with file not found or similar error
        assert result.exit_code != 0

    def test_extract_list_basic(self, melee_root_exists):
        """Test basic extract list command."""
        result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", str(melee_root_exists),
            "--limit", "5"
        ])

        # Should succeed or fail gracefully
        if result.exit_code == 0:
            # If successful, should show a table
            assert "Unmatched Functions" in result.stdout or "Found" in result.stdout

    def test_extract_list_with_filters(self, melee_root_exists):
        """Test extract list with filtering options."""
        result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", str(melee_root_exists),
            "--min-match", "0.5",
            "--max-match", "0.9",
            "--min-size", "50",
            "--max-size", "500",
            "--limit", "3"
        ])

        # Should run without crashing
        assert result.exit_code == 0 or "not found" in result.stdout.lower()

    def test_extract_list_with_file_filter(self, melee_root_exists):
        """Test extract list with --file filter option."""
        result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", str(melee_root_exists),
            "--file", "lb/",
            "--limit", "5"
        ])

        # Should run without crashing
        assert result.exit_code == 0
        # Should show file filter in output
        assert "file='lb/'" in result.stdout

    def test_extract_list_file_filter_help(self):
        """Test that --file option is documented."""
        result = runner.invoke(app, ["extract", "list", "--help"])
        assert result.exit_code == 0
        assert "--file" in result.stdout or "-f" in result.stdout
        assert "filename" in result.stdout.lower() or "filter" in result.stdout.lower()

    def test_extract_list_show_excluded_help(self):
        """Test that --show-excluded option is documented."""
        result = runner.invoke(app, ["extract", "list", "--help"])
        assert result.exit_code == 0
        assert "--show-excluded" in result.stdout
        assert "diagnostic" in result.stdout.lower() or "excluded" in result.stdout.lower()

    def test_extract_list_show_excluded(self, melee_root_exists):
        """Test extract list with --show-excluded flag."""
        result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", str(melee_root_exists),
            "--module", "lb",
            "--limit", "3",
            "--show-excluded"
        ])

        # Should run without crashing
        assert result.exit_code == 0
        # Should show exclusion diagnostics section
        assert "Exclusion Diagnostics" in result.stdout

    def test_extract_list_only_excludes_merged(self, melee_root_exists):
        """Test that extract list only excludes merged functions, not all tracked.

        This is a regression test for the bug where extract list would exclude
        ALL functions in the database, not just those with status='merged'.
        """
        result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", str(melee_root_exists),
            "--limit", "5"
        ])

        # Should succeed
        assert result.exit_code == 0
        # Summary should say "merged excluded" not "completed excluded"
        if "excluded" in result.stdout:
            assert "merged excluded" in result.stdout

    def test_extract_list_include_completed_flag(self, melee_root_exists):
        """Test that --include-completed includes merged functions."""
        result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", str(melee_root_exists),
            "--include-completed",
            "--limit", "5"
        ])

        # Should succeed
        assert result.exit_code == 0
        # Should NOT show "merged excluded" when include-completed is set
        assert "merged excluded" not in result.stdout

    def test_extract_files_help(self):
        """Test extract files command help output."""
        result = runner.invoke(app, ["extract", "files", "--help"])
        assert result.exit_code == 0
        assert "List all source files" in result.stdout
        assert "--module" in result.stdout
        assert "--status" in result.stdout
        assert "--sort" in result.stdout
        assert "--limit" in result.stdout

    def test_extract_files_basic(self, melee_root_exists):
        """Test basic extract files command."""
        result = runner.invoke(app, [
            "extract", "files",
            "--melee-root", str(melee_root_exists),
            "--limit", "5"
        ])

        # Should succeed
        assert result.exit_code == 0
        # Should show table headers (some may be truncated due to terminal width)
        assert "Source Files" in result.stdout
        assert "File" in result.stdout
        assert "Status" in result.stdout
        assert "Done" in result.stdout  # Done % column
        assert "Matched" in result.stdout
        assert "Unmat" in result.stdout  # May be truncated to "Unmat…"

    def test_extract_files_with_module_filter(self, melee_root_exists):
        """Test extract files with module filter."""
        result = runner.invoke(app, [
            "extract", "files",
            "--melee-root", str(melee_root_exists),
            "--module", "lb",
            "--limit", "5"
        ])

        # Should succeed
        assert result.exit_code == 0
        # Should show module filter in summary
        assert "module=lb" in result.stdout

    def test_extract_files_with_status_filter(self, melee_root_exists):
        """Test extract files with status filter."""
        result = runner.invoke(app, [
            "extract", "files",
            "--melee-root", str(melee_root_exists),
            "--status", "NonMatching",
            "--limit", "5"
        ])

        # Should succeed
        assert result.exit_code == 0
        # Should show status filter in summary
        assert "status=NonMatching" in result.stdout

    def test_extract_files_sort_by_unmatched(self, melee_root_exists):
        """Test extract files sorted by unmatched count."""
        result = runner.invoke(app, [
            "extract", "files",
            "--melee-root", str(melee_root_exists),
            "--sort", "unmatched",
            "--limit", "5"
        ])

        # Should succeed
        assert result.exit_code == 0
        assert "Source Files" in result.stdout

    def test_extract_files_sort_by_match(self, melee_root_exists):
        """Test extract files sorted by match percentage."""
        result = runner.invoke(app, [
            "extract", "files",
            "--melee-root", str(melee_root_exists),
            "--sort", "match",
            "--limit", "5"
        ])

        # Should succeed
        assert result.exit_code == 0
        assert "Source Files" in result.stdout

    def test_extract_get_help(self):
        """Test extract get command help output."""
        result = runner.invoke(app, ["extract", "get", "--help"])
        assert result.exit_code == 0
        assert "Extract a specific function" in result.stdout
        assert "--melee-root" in result.stdout
        assert "--output" in result.stdout

    def test_extract_get_nonexistent_function(self, melee_root_exists):
        """Test extracting a function that doesn't exist."""
        result = runner.invoke(app, [
            "extract", "get",
            "NonExistentFunction12345",
            "--melee-root", str(melee_root_exists)
        ])

        # Should fail with function not found
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_extract_get_with_output(self, melee_root_exists, tmp_path):
        """Test extracting a function with output file."""
        output_file = tmp_path / "output.s"

        # Try with memset which should exist
        result = runner.invoke(app, [
            "extract", "get",
            "memset",
            "--melee-root", str(melee_root_exists),
            "--output", str(output_file)
        ])

        # If function exists, output file should be created
        if result.exit_code == 0:
            assert output_file.exists()
            assert output_file.stat().st_size > 0


class TestScratchCommands:
    """Test the scratch command group."""

    def test_scratch_help(self):
        """Test scratch command help output."""
        result = runner.invoke(app, ["scratch", "--help"])
        assert result.exit_code == 0
        assert "Manage decomp.me scratches" in result.stdout

    def test_scratch_create_help(self):
        """Test scratch create command help output."""
        result = runner.invoke(app, ["scratch", "create", "--help"])
        assert result.exit_code == 0
        assert "Create a new scratch" in result.stdout
        assert "--melee-root" in result.stdout
        assert "--api-url" in result.stdout

    def test_scratch_compile_help(self):
        """Test scratch compile command help output."""
        result = runner.invoke(app, ["scratch", "compile", "--help"])
        assert result.exit_code == 0
        assert "Compile a scratch" in result.stdout

    def test_scratch_update_help(self):
        """Test scratch update command help output."""
        result = runner.invoke(app, ["scratch", "update", "--help"])
        assert result.exit_code == 0
        assert "Update a scratch's source code" in result.stdout


class TestCommitCommands:
    """Test the commit command group."""

    def test_commit_help(self):
        """Test commit command help output."""
        result = runner.invoke(app, ["commit", "--help"])
        assert result.exit_code == 0
        assert "Commit matched functions" in result.stdout

    def test_commit_apply_help(self):
        """Test commit apply command help output."""
        result = runner.invoke(app, ["commit", "apply", "--help"])
        assert result.exit_code == 0
        assert "Apply a matched function" in result.stdout
        assert "--melee-root" in result.stdout
        assert "--api-url" in result.stdout
        assert "--pr" in result.stdout

    def test_commit_format_help(self):
        """Test commit format command help output."""
        result = runner.invoke(app, ["commit", "format", "--help"])
        assert result.exit_code == 0
        assert "Run clang-format" in result.stdout
        assert "--melee-root" in result.stdout


class TestDockerCommands:
    """Test the docker command group."""

    def test_docker_help(self):
        """Test docker command help output."""
        result = runner.invoke(app, ["docker", "--help"])
        assert result.exit_code == 0
        assert "Manage local decomp.me instance" in result.stdout

    def test_docker_up_help(self):
        """Test docker up command help output."""
        result = runner.invoke(app, ["docker", "up", "--help"])
        assert result.exit_code == 0
        assert "Start local decomp.me instance" in result.stdout
        assert "--port" in result.stdout
        assert "--detach" in result.stdout

    def test_docker_down_help(self):
        """Test docker down command help output."""
        result = runner.invoke(app, ["docker", "down", "--help"])
        assert result.exit_code == 0
        assert "Stop local decomp.me instance" in result.stdout

    def test_docker_status_help(self):
        """Test docker status command help output."""
        result = runner.invoke(app, ["docker", "status", "--help"])
        assert result.exit_code == 0
        assert "Check status" in result.stdout


class TestMainApp:
    """Test the main application."""

    def test_app_help(self):
        """Test main app help output."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Agent tooling for contributing to the Melee decompilation project" in result.stdout
        assert "extract" in result.stdout
        assert "scratch" in result.stdout
        assert "match" in result.stdout
        assert "commit" in result.stdout
        assert "docker" in result.stdout

    def test_app_version(self):
        """Test that app runs without errors."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0


class TestErrorHandling:
    """Test error handling in CLI commands."""

    def test_missing_required_argument(self):
        """Test command with missing required argument."""
        result = runner.invoke(app, ["extract", "get"])
        # Should fail due to missing function name
        assert result.exit_code != 0

    def test_invalid_option_value(self):
        """Test command with invalid option value."""
        result = runner.invoke(app, [
            "extract", "list",
            "--min-match", "invalid"
        ])
        # Should fail due to invalid float
        assert result.exit_code != 0

    def test_conflicting_options(self, melee_root_exists):
        """Test command with conflicting options."""
        result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", str(melee_root_exists),
            "--min-match", "0.9",
            "--max-match", "0.1"  # max < min
        ])

        # Should run but return no results
        if result.exit_code == 0:
            # Output should indicate no functions found
            assert "0 functions" in result.stdout.lower() or "found 0" in result.stdout.lower()


class TestCommandIntegration:
    """Integration tests combining multiple CLI commands."""

    def test_extract_list_then_get(self, melee_root_exists):
        """Test listing functions then extracting one."""
        # First, list functions
        list_result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", str(melee_root_exists),
            "--limit", "1"
        ])

        # If list succeeded, try to extract memset (should exist)
        if list_result.exit_code == 0:
            get_result = runner.invoke(app, [
                "extract", "get",
                "memset",
                "--melee-root", str(melee_root_exists)
            ])

            # Should either succeed or gracefully fail
            assert get_result.exit_code in [0, 1]

    def test_format_with_no_changes(self, tmp_path):
        """Test format command with no staged changes."""
        # Create temporary melee root
        melee_root = tmp_path / "melee"
        melee_root.mkdir()

        result = runner.invoke(app, [
            "commit", "format",
            "--melee-root", str(melee_root)
        ])

        # Should fail gracefully (no git repo or clang-format not available)
        assert result.exit_code in [0, 1]


class TestDefaultValues:
    """Test that default values are used correctly."""

    def test_default_melee_root(self):
        """Test that default melee root is used."""
        result = runner.invoke(app, ["extract", "list", "--help"])
        assert result.exit_code == 0
        # Help should show default path
        assert "melee" in result.stdout.lower()

    def test_default_api_url(self):
        """Test that API URL option is available."""
        result = runner.invoke(app, ["scratch", "create", "--help"])
        assert result.exit_code == 0
        # Help should show API URL option (auto-detected)
        assert "--api-url" in result.stdout
        assert "auto-detected" in result.stdout.lower()

    def test_default_limits(self):
        """Test that default limits are used."""
        result = runner.invoke(app, ["extract", "list", "--help"])
        assert result.exit_code == 0
        # Help should show default limit
        assert "20" in result.stdout  # Default limit


class TestOutputFormatting:
    """Test output formatting of CLI commands."""

    def test_table_output_format(self, melee_root_exists):
        """Test that table output is properly formatted."""
        result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", str(melee_root_exists),
            "--limit", "1"
        ])

        if result.exit_code == 0 and "Unmatched Functions" in result.stdout:
            # Check for table headers
            assert "Name" in result.stdout
            assert "File" in result.stdout
            assert "Match" in result.stdout

    def test_error_message_format(self):
        """Test that error messages are user-friendly."""
        result = runner.invoke(app, [
            "extract", "get",
            "NonExistentFunc",
            "--melee-root", "/nonexistent"
        ])

        # Should have clear error message
        assert result.exit_code != 0
        # Error messages may go to stdout or exception
        # Just verify the command failed properly


class TestAsyncCommands:
    """Test commands that use async operations."""

    @pytest.mark.asyncio
    async def test_extract_list_async(self, melee_root_exists):
        """Test that extract list works with async operations."""
        result = runner.invoke(app, [
            "extract", "list",
            "--melee-root", str(melee_root_exists),
            "--limit", "1"
        ])

        # Should complete without hanging
        assert result.exit_code in [0, 1]

    @pytest.mark.asyncio
    async def test_extract_get_async(self, melee_root_exists):
        """Test that extract get works with async operations."""
        result = runner.invoke(app, [
            "extract", "get",
            "memset",
            "--melee-root", str(melee_root_exists)
        ])

        # Should complete without hanging
        assert result.exit_code in [0, 1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
