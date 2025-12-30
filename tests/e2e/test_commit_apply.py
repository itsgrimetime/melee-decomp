"""Test commit apply with real git operations.

These tests verify that commit operations work correctly with real git
in temporary repositories.
"""

import os
import subprocess

import pytest


class TestGitRepoFixture:
    """Test that the temp_melee_repo fixture works correctly."""

    def test_repo_has_initial_commit(self, temp_melee_repo):
        """Temp repo has an initial commit."""
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=temp_melee_repo,
            capture_output=True,
            text=True
        )

        assert result.returncode == 0
        assert "Initial commit" in result.stdout

    def test_repo_has_sample_files(self, temp_melee_repo):
        """Temp repo has expected sample files."""
        lb_file = temp_melee_repo / "src" / "melee" / "lb" / "lbcommand.c"
        assert lb_file.exists()

        fox_file = temp_melee_repo / "src" / "melee" / "ft" / "chara" / "ftFox" / "ftFx_SpecialHi.c"
        assert fox_file.exists()

    def test_repo_has_configure(self, temp_melee_repo):
        """Temp repo has configure.py."""
        configure = temp_melee_repo / "configure.py"
        assert configure.exists()

        content = configure.read_text()
        assert "NonMatching" in content
        assert "lbcommand.c" in content


class TestGitOperations:
    """Test git operations in temp repo."""

    def test_can_create_branch(self, temp_melee_repo, git_env):
        """Can create a new branch in temp repo."""
        result = subprocess.run(
            ["git", "checkout", "-b", "test-branch"],
            cwd=temp_melee_repo,
            capture_output=True,
            env={**os.environ, **git_env}
        )

        assert result.returncode == 0

        # Verify we're on new branch
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=temp_melee_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout.strip() == "test-branch"

    def test_can_modify_and_commit(self, temp_melee_repo, git_env):
        """Can modify files and create commits."""
        # Modify a file
        lb_file = temp_melee_repo / "src" / "melee" / "lb" / "lbcommand.c"
        original = lb_file.read_text()
        lb_file.write_text(original + "\n// Modified\n")

        # Stage and commit
        subprocess.run(["git", "add", "."], cwd=temp_melee_repo, check=True)
        result = subprocess.run(
            ["git", "commit", "-m", "Test commit"],
            cwd=temp_melee_repo,
            capture_output=True,
            env={**os.environ, **git_env}
        )

        assert result.returncode == 0

        # Verify commit exists
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=temp_melee_repo,
            capture_output=True,
            text=True
        )
        assert "Test commit" in result.stdout

    def test_changes_are_isolated(self, temp_melee_repo, git_env):
        """Changes in one test don't affect others."""
        # Verify file is in original state
        lb_file = temp_melee_repo / "src" / "melee" / "lb" / "lbcommand.c"
        content = lb_file.read_text()

        # Should not have modifications from other tests
        assert "// Modified" not in content or content.count("// Modified") == 0


class TestSourceFileUpdates:
    """Test source file modification operations."""

    def test_update_function_in_file(self, temp_melee_repo):
        """Can update a function's implementation in a source file."""
        lb_file = temp_melee_repo / "src" / "melee" / "lb" / "lbcommand.c"
        original = lb_file.read_text()

        # Replace function implementation
        new_impl = """void TestFunction(void) {
    // New matched implementation
    int x = 42;
}"""

        # Simple replacement (in real code this would be smarter)
        old_impl_start = original.find("void TestFunction(void)")
        old_impl_end = original.find("}", old_impl_start) + 1

        new_content = original[:old_impl_start] + new_impl + original[old_impl_end:]
        lb_file.write_text(new_content)

        # Verify update
        updated = lb_file.read_text()
        assert "New matched implementation" in updated
        assert "int x = 42" in updated

    def test_update_configure_py(self, temp_melee_repo):
        """Can update configure.py to mark function as Matching."""
        configure = temp_melee_repo / "configure.py"
        content = configure.read_text()

        # Replace NonMatching with Matching for lbcommand.c
        new_content = content.replace(
            'Object(NonMatching, "melee/lb/lbcommand.c")',
            'Object(Matching, "melee/lb/lbcommand.c")'
        )
        configure.write_text(new_content)

        # Verify update
        updated = configure.read_text()
        assert 'Object(Matching, "melee/lb/lbcommand.c")' in updated


class TestCommitCreation:
    """Test creating commits for matched functions."""

    def test_create_match_commit(self, temp_melee_repo, git_env):
        """Can create a commit for a matched function."""
        # Modify the function
        lb_file = temp_melee_repo / "src" / "melee" / "lb" / "lbcommand.c"
        content = lb_file.read_text()
        content = content.replace("// Stub implementation", "// Matched implementation")
        lb_file.write_text(content)

        # Update configure.py
        configure = temp_melee_repo / "configure.py"
        conf_content = configure.read_text()
        conf_content = conf_content.replace("NonMatching", "Matching")
        configure.write_text(conf_content)

        # Create commit
        subprocess.run(["git", "add", "."], cwd=temp_melee_repo, check=True)
        result = subprocess.run(
            ["git", "commit", "-m", "Match TestFunction"],
            cwd=temp_melee_repo,
            capture_output=True,
            env={**os.environ, **git_env}
        )

        assert result.returncode == 0

        # Verify commit
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=temp_melee_repo,
            capture_output=True,
            text=True
        )
        assert "Match TestFunction" in log_result.stdout

    def test_commit_includes_both_files(self, temp_melee_repo, git_env):
        """Commit includes both source and configure.py changes."""
        # Modify both files
        lb_file = temp_melee_repo / "src" / "melee" / "lb" / "lbcommand.c"
        lb_file.write_text(lb_file.read_text() + "\n// Changed\n")

        configure = temp_melee_repo / "configure.py"
        configure.write_text(configure.read_text().replace("NonMatching", "Matching"))

        # Commit
        subprocess.run(["git", "add", "."], cwd=temp_melee_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Match function"],
            cwd=temp_melee_repo,
            env={**os.environ, **git_env},
            check=True
        )

        # Check what files were in the commit
        result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            cwd=temp_melee_repo,
            capture_output=True,
            text=True
        )

        files = result.stdout.strip().split("\n")
        assert any("lbcommand.c" in f for f in files)
        assert any("configure.py" in f for f in files)


class TestDryRun:
    """Test dry-run functionality."""

    def test_dry_run_shows_changes(self, temp_melee_repo):
        """Dry run can show what changes would be made."""
        lb_file = temp_melee_repo / "src" / "melee" / "lb" / "lbcommand.c"
        lb_file.write_text(lb_file.read_text() + "\n// Modified\n")

        # Check status (simulating dry run)
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=temp_melee_repo,
            capture_output=True,
            text=True
        )

        assert "lbcommand.c" in result.stdout

    def test_dry_run_does_not_commit(self, temp_melee_repo):
        """Dry run doesn't create any commits."""
        # Get current commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=temp_melee_repo,
            capture_output=True,
            text=True
        )
        original_hash = result.stdout.strip()

        # Modify file but don't commit (simulating dry run)
        lb_file = temp_melee_repo / "src" / "melee" / "lb" / "lbcommand.c"
        lb_file.write_text(lb_file.read_text() + "\n// Modified\n")

        # Verify no new commits
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=temp_melee_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout.strip() == original_hash


class TestDatabaseIntegration:
    """Test database updates during commit operations."""

    def test_commit_updates_function_status(self, temp_db):
        """Committing updates function status in database."""
        temp_db.upsert_function(
            "TestFunction",
            status="matched",
            match_percent=100.0
        )

        # Simulate commit
        temp_db.upsert_function(
            "TestFunction",
            status="committed",
            is_committed=True,
            commit_hash="abc123def",
            build_status="passing"
        )

        func = temp_db.get_function("TestFunction")
        assert func["status"] == "committed"
        assert func["is_committed"] == 1
        assert func["commit_hash"] == "abc123def"
        assert func["build_status"] == "passing"

    def test_commit_releases_claim(self, temp_db):
        """Successful commit releases the claim."""
        temp_db.upsert_function("TestFunction", status="unclaimed")
        temp_db.add_claim("TestFunction", "agent-1")

        # Before commit, claim exists
        claims = temp_db.get_active_claims()
        assert len(claims) == 1

        # Simulate successful commit
        temp_db.upsert_function(
            "TestFunction",
            status="committed",
            is_committed=True
        )
        temp_db.release_claim("TestFunction", "agent-1")

        # After commit, claim released
        claims = temp_db.get_active_claims()
        assert len(claims) == 0
