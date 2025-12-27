"""Integration tests for the commit module.

These tests use temporary files and mock git operations to test the commit workflow.
Run with: pytest tests/test_commit.py -v
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.commit import (
    CommitWorkflow,
    update_source_file,
    update_configure_py,
    format_files,
    verify_clang_format_available,
)


@pytest.fixture
def temp_melee_root(tmp_path):
    """Create a temporary melee root directory with test files."""
    melee_root = tmp_path / "melee"
    melee_root.mkdir()

    # Create directory structure
    src_dir = melee_root / "src" / "melee" / "lb"
    src_dir.mkdir(parents=True)

    config_dir = melee_root / "config" / "GALE01"
    config_dir.mkdir(parents=True)

    # Create a sample C file
    test_c_file = src_dir / "lbcommand.c"
    test_c_file.write_text("""
#include "lb/types.h"

void TestFunction(void) {
    // TODO: Implement this
    return;
}

void AnotherFunction(int x) {
    if (x > 0) {
        return;
    }
}
""")

    # Create configure.py
    configure_py = melee_root / "configure.py"
    configure_py.write_text("""
# Test configure file

MeleeLib("lb (Library)")
Object(NonMatching, "melee/lb/lbcommand.c")
Object(Matching, "melee/lb/lbcollision.c")
""")

    return melee_root


class TestUpdateSourceFile:
    """Test the update_source_file function."""

    @pytest.mark.asyncio
    async def test_update_function_basic(self, temp_melee_root):
        """Test updating a function in a source file."""
        file_path = "melee/lb/lbcommand.c"
        function_name = "TestFunction"
        new_code = """void TestFunction(void) {
    // New implementation
    int x = 5;
    return;
}"""

        result = await update_source_file(
            file_path,
            function_name,
            new_code,
            temp_melee_root
        )

        assert result is True

        # Verify the file was updated
        full_path = temp_melee_root / "src" / file_path
        content = full_path.read_text()
        assert "New implementation" in content
        assert "int x = 5" in content
        assert "TODO: Implement this" not in content

    @pytest.mark.asyncio
    async def test_update_function_with_braces(self, temp_melee_root):
        """Test updating a function with nested braces."""
        file_path = "melee/lb/lbcommand.c"
        function_name = "AnotherFunction"
        new_code = """void AnotherFunction(int x) {
    if (x > 0) {
        int y = x * 2;
        while (y > 0) {
            y--;
        }
    }
}"""

        result = await update_source_file(
            file_path,
            function_name,
            new_code,
            temp_melee_root
        )

        assert result is True

        # Verify the file was updated
        full_path = temp_melee_root / "src" / file_path
        content = full_path.read_text()
        assert "int y = x * 2" in content
        assert "while (y > 0)" in content

    @pytest.mark.asyncio
    async def test_update_nonexistent_function(self, temp_melee_root):
        """Test updating a function that doesn't exist."""
        file_path = "melee/lb/lbcommand.c"
        function_name = "NonExistentFunction"
        new_code = "void NonExistentFunction(void) {}"

        result = await update_source_file(
            file_path,
            function_name,
            new_code,
            temp_melee_root
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_update_nonexistent_file(self, temp_melee_root):
        """Test updating a file that doesn't exist."""
        file_path = "melee/lb/nonexistent.c"
        function_name = "TestFunction"
        new_code = "void TestFunction(void) {}"

        result = await update_source_file(
            file_path,
            function_name,
            new_code,
            temp_melee_root
        )

        assert result is False


class TestUpdateConfigurePy:
    """Test the update_configure_py function."""

    @pytest.mark.asyncio
    async def test_change_nonmatching_to_matching(self, temp_melee_root):
        """Test changing a file from NonMatching to Matching."""
        file_path = "melee/lb/lbcommand.c"

        result = await update_configure_py(file_path, temp_melee_root)

        assert result is True

        # Verify configure.py was updated
        configure_path = temp_melee_root / "configure.py"
        content = configure_path.read_text()
        assert 'Object(Matching, "melee/lb/lbcommand.c")' in content
        assert 'Object(NonMatching, "melee/lb/lbcommand.c")' not in content

    @pytest.mark.asyncio
    async def test_already_matching(self, temp_melee_root):
        """Test updating a file that's already Matching."""
        file_path = "melee/lb/lbcollision.c"

        result = await update_configure_py(file_path, temp_melee_root)

        # Should succeed (already matching)
        assert result is True

    @pytest.mark.asyncio
    async def test_file_not_in_configure(self, temp_melee_root):
        """Test updating a file that's not in configure.py."""
        file_path = "melee/lb/nonexistent.c"

        result = await update_configure_py(file_path, temp_melee_root)

        assert result is False


class TestFormatFiles:
    """Test the format_files function."""

    @pytest.mark.asyncio
    async def test_verify_clang_format_unavailable(self):
        """Test verifying clang-format when it's not available."""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            # Simulate clang-format not found
            mock_exec.side_effect = FileNotFoundError()

            result = await verify_clang_format_available()
            assert result is False

    @pytest.mark.asyncio
    async def test_verify_clang_format_available(self):
        """Test verifying clang-format when it's available."""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            # Create mock process
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"git-clang-format version 14.0.0", b"")
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            result = await verify_clang_format_available()
            assert result is True

    @pytest.mark.asyncio
    async def test_format_files_empty_list(self, temp_melee_root):
        """Test formatting with empty file list."""
        result = await format_files([], temp_melee_root)
        assert result is True

    @pytest.mark.asyncio
    async def test_format_files_with_mocked_git(self, temp_melee_root):
        """Test formatting files with mocked git commands."""
        files = ["src/melee/lb/lbcommand.c"]

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            # Create mock process
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"", b"")
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            result = await format_files(files, temp_melee_root)
            assert result is True

            # Verify git commands were called
            assert mock_exec.call_count >= 2  # git add, git clang-format, git add again

    @pytest.mark.asyncio
    async def test_format_files_git_error(self, temp_melee_root):
        """Test formatting when git returns an error."""
        files = ["src/melee/lb/lbcommand.c"]

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            # Create mock process that fails
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"", b"Error: git failed")
            mock_process.returncode = 1
            mock_exec.return_value = mock_process

            result = await format_files(files, temp_melee_root)
            assert result is False


class TestCommitWorkflow:
    """Test the CommitWorkflow class."""

    def test_init(self, temp_melee_root):
        """Test workflow initialization."""
        workflow = CommitWorkflow(temp_melee_root)
        assert workflow.melee_root == temp_melee_root
        assert workflow.files_changed == []

    @pytest.mark.asyncio
    async def test_execute_without_pr(self, temp_melee_root):
        """Test executing workflow without creating a PR."""
        workflow = CommitWorkflow(temp_melee_root)

        # Mock the format verification and PR creation
        with patch('src.commit.workflow.verify_clang_format_available', return_value=False):
            result = await workflow.execute(
                function_name="TestFunction",
                file_path="melee/lb/lbcommand.c",
                new_code="void TestFunction(void) { return; }",
                scratch_id="test123",
                scratch_url="http://decomp.me/scratch/test123",
                create_pull_request=False
            )

        # Should succeed without PR
        assert result is None
        assert len(workflow.files_changed) > 0

        # Verify files were changed
        assert "src/melee/lb/lbcommand.c" in workflow.files_changed
        assert "configure.py" in workflow.files_changed

    @pytest.mark.asyncio
    async def test_execute_with_mocked_pr(self, temp_melee_root):
        """Test executing workflow with mocked PR creation."""
        workflow = CommitWorkflow(temp_melee_root)

        with patch('src.commit.workflow.verify_clang_format_available', return_value=False), \
             patch('src.commit.workflow.create_pr', return_value="https://github.com/test/pr/1"):

            result = await workflow.execute(
                function_name="TestFunction",
                file_path="melee/lb/lbcommand.c",
                new_code="void TestFunction(void) { return; }",
                scratch_id="test123",
                scratch_url="http://decomp.me/scratch/test123",
                create_pull_request=True
            )

        # Should return PR URL
        assert result == "https://github.com/test/pr/1"

    @pytest.mark.asyncio
    async def test_execute_update_failure(self, temp_melee_root):
        """Test workflow when source update fails."""
        workflow = CommitWorkflow(temp_melee_root)

        # Try to update non-existent function
        result = await workflow.execute(
            function_name="NonExistentFunction",
            file_path="melee/lb/lbcommand.c",
            new_code="void NonExistentFunction(void) {}",
            scratch_id="test123",
            scratch_url="http://decomp.me/scratch/test123",
            create_pull_request=False
        )

        # Should fail
        assert result is None
        assert len(workflow.files_changed) == 0


class TestIntegration:
    """Integration tests combining multiple commit operations."""

    @pytest.mark.asyncio
    async def test_complete_commit_pipeline(self, temp_melee_root):
        """Test the complete commit workflow pipeline."""
        # Step 1: Update source file
        result = await update_source_file(
            "melee/lb/lbcommand.c",
            "TestFunction",
            "void TestFunction(void) { /* matched */ }",
            temp_melee_root
        )
        assert result is True

        # Step 2: Update configure.py
        result = await update_configure_py(
            "melee/lb/lbcommand.c",
            temp_melee_root
        )
        assert result is True

        # Verify all changes
        src_file = temp_melee_root / "src" / "melee" / "lb" / "lbcommand.c"
        assert "/* matched */" in src_file.read_text()

        configure_file = temp_melee_root / "configure.py"
        assert 'Object(Matching, "melee/lb/lbcommand.c")' in configure_file.read_text()

    @pytest.mark.asyncio
    async def test_workflow_idempotency(self, temp_melee_root):
        """Test that running workflow twice is idempotent."""
        workflow1 = CommitWorkflow(temp_melee_root)

        with patch('src.commit.workflow.verify_clang_format_available', return_value=False):
            result1 = await workflow1.execute(
                function_name="TestFunction",
                file_path="melee/lb/lbcommand.c",
                new_code="void TestFunction(void) { return; }",
                scratch_id="test123",
                scratch_url="http://decomp.me/scratch/test123",
                create_pull_request=False
            )

        assert result1 is None

        # Run again
        workflow2 = CommitWorkflow(temp_melee_root)

        with patch('src.commit.workflow.verify_clang_format_available', return_value=False):
            result2 = await workflow2.execute(
                function_name="TestFunction",
                file_path="melee/lb/lbcommand.c",
                new_code="void TestFunction(void) { return; }",
                scratch_id="test123",
                scratch_url="http://decomp.me/scratch/test123",
                create_pull_request=False
            )

        # Should still succeed (idempotent)
        assert result2 is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
