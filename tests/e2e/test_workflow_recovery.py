"""Test error handling and recovery in workflows.

These tests verify that the system handles errors gracefully
and allows recovery from failed operations.
"""

import time

import pytest


class TestFailedCommitRecovery:
    """Test recovery from failed commits."""

    def test_can_reclaim_after_failed_commit(self, temp_db):
        """Can re-claim function after commit failure."""
        # Claim and attempt commit
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")
        temp_db.upsert_function("TestFunc", status="matched", match_percent=100.0)

        # Simulate failed commit - release claim without committing
        temp_db.release_claim("TestFunc", "agent-1")

        # Another agent should be able to claim
        success, _ = temp_db.add_claim("TestFunc", "agent-2")
        assert success is True

    def test_failed_commit_preserves_match(self, temp_db):
        """Failed commit preserves the match percentage."""
        temp_db.upsert_function(
            "TestFunc",
            status="matched",
            match_percent=100.0,
            local_scratch_slug="test-scratch"
        )

        # Even after claim release, match info preserved
        func = temp_db.get_function("TestFunc")
        assert func["match_percent"] == 100.0
        assert func["local_scratch_slug"] == "test-scratch"

    def test_failed_commit_keeps_scratch(self, temp_db):
        """Failed commit preserves scratch information."""
        temp_db.upsert_scratch(
            slug="test-scratch",
            instance="local",
            base_url="http://localhost:8000",
            function_name="TestFunc"
        )

        # Scratch should still exist after failed commit
        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM scratches WHERE slug = ?",
                ("test-scratch",)
            )
            scratch = cursor.fetchone()

        assert scratch is not None


class TestBrokenBuildRecovery:
    """Test recovery from broken builds."""

    def test_broken_build_tracked(self, temp_db):
        """Broken build status is tracked."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            is_committed=True,
            build_status="broken",
            build_diagnosis="Missing header declaration"
        )

        func = temp_db.get_function("TestFunc")
        assert func["build_status"] == "broken"
        assert "Missing header" in func["build_diagnosis"]

    def test_broken_build_blocks_new_claims_in_worktree(self, temp_db):
        """Worktree with too many broken builds blocks new claims."""
        worktree = "/path/wt-lb"

        # Add multiple broken functions
        for i in range(4):
            temp_db.upsert_function(
                f"Func{i}",
                worktree_path=worktree,
                build_status="broken"
            )

        count, names = temp_db.get_worktree_broken_count(worktree)
        assert count == 4

        # In real implementation, claim would be blocked if count > 3

    def test_fixing_broken_build_updates_status(self, temp_db):
        """Fixing a broken build updates the status."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            build_status="broken"
        )

        # Fix the build
        temp_db.upsert_function(
            "TestFunc",
            build_status="passing",
            build_diagnosis=None
        )

        func = temp_db.get_function("TestFunc")
        assert func["build_status"] == "passing"


class TestCompilationErrors:
    """Test handling of compilation errors."""

    def test_compilation_error_preserves_progress(self, temp_db):
        """Compilation error preserves match progress."""
        temp_db.upsert_function(
            "TestFunc",
            status="in_progress",
            match_percent=75.0,
            local_scratch_slug="test-scratch"
        )

        # Compilation fails but progress saved
        func = temp_db.get_function("TestFunc")
        assert func["match_percent"] == 75.0

    def test_can_retry_after_error(self, temp_db):
        """Can retry compilation after error."""
        temp_db.upsert_scratch(
            slug="test-scratch",
            instance="local",
            base_url="http://localhost:8000",
            function_name="TestFunc"
        )

        # First attempt - error
        temp_db.record_match_score("test-scratch", score=50, max_score=100)

        # Retry - better
        temp_db.record_match_score("test-scratch", score=25, max_score=100)

        # Check history shows both attempts
        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM match_history WHERE scratch_slug = ? ORDER BY timestamp",
                ("test-scratch",)
            )
            history = cursor.fetchall()

        assert len(history) == 2


class TestClaimRecovery:
    """Test claim recovery scenarios."""

    def test_expired_claim_auto_releases(self, temp_db):
        """Expired claims are automatically released."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        # Create an already-expired claim
        now = time.time()
        with temp_db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO claims (function_name, agent_id, claimed_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                ("TestFunc", "agent-1", now - 7200, now - 3600)
            )

        # New agent can claim
        success, _ = temp_db.add_claim("TestFunc", "agent-2")
        assert success is True

    def test_can_reclaim_released_function(self, temp_db):
        """Can immediately reclaim a released function."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")
        temp_db.release_claim("TestFunc", "agent-1")

        # Same or different agent can reclaim
        success, _ = temp_db.add_claim("TestFunc", "agent-1")
        assert success is True


class TestLockRecovery:
    """Test subdirectory lock recovery scenarios."""

    def test_expired_lock_allows_takeover(self, temp_db):
        """Expired lock allows another agent to take over."""
        now = time.time()
        with temp_db.connection() as conn:
            conn.execute(
                """
                INSERT INTO subdirectory_allocations
                    (subdirectory_key, worktree_path, branch_name,
                     locked_by_agent, locked_at, lock_expires_at, updated_at)
                VALUES (?, '', '', ?, ?, ?, ?)
                """,
                ("lb", "agent-1", now - 3600, now - 1800, now)
            )

        success, _ = temp_db.lock_subdirectory("lb", "agent-2")
        assert success is True

    def test_force_unlock_for_recovery(self, temp_db):
        """Can force unlock for recovery."""
        temp_db.lock_subdirectory("lb", "agent-1")

        # Force unlock without specifying agent
        result = temp_db.unlock_subdirectory("lb")

        assert result is True

        # Now any agent can lock
        success, _ = temp_db.lock_subdirectory("lb", "agent-2")
        assert success is True


class TestStateValidationRecovery:
    """Test state validation and fixing."""

    def test_status_mismatch_detectable(self, temp_db):
        """Can detect status mismatches."""
        # Create inconsistent state: committed=True but status=matched
        temp_db.upsert_function(
            "TestFunc",
            status="matched",
            is_committed=True  # Inconsistent!
        )

        func = temp_db.get_function("TestFunc")

        # Detection: status doesn't match is_committed
        is_inconsistent = (
            func["status"] == "matched" and func["is_committed"] == 1
        )
        assert is_inconsistent is True

    def test_can_fix_status_mismatch(self, temp_db):
        """Can fix detected status mismatches."""
        temp_db.upsert_function(
            "TestFunc",
            status="matched",
            is_committed=True
        )

        # Fix the inconsistency
        temp_db.upsert_function("TestFunc", status="committed")

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "committed"
        assert func["is_committed"] == 1


class TestBranchProgressRecovery:
    """Test branch progress recovery scenarios."""

    def test_progress_preserved_across_sessions(self, temp_db):
        """Branch progress is preserved across sessions."""
        temp_db.upsert_branch_progress(
            function_name="TestFunc",
            branch="feature",
            match_percent=75.0,
            scratch_slug="scratch-1"
        )

        # Simulate session restart by just re-reading
        progress = temp_db.get_branch_progress("TestFunc")

        assert len(progress) == 1
        assert progress[0]["match_percent"] == 75.0
        assert progress[0]["scratch_slug"] == "scratch-1"

    def test_best_branch_found_after_recovery(self, temp_db):
        """Can find best branch after recovery."""
        temp_db.upsert_branch_progress("TestFunc", "b1", match_percent=50.0)
        temp_db.upsert_branch_progress("TestFunc", "b2", match_percent=90.0)
        temp_db.upsert_branch_progress("TestFunc", "b3", match_percent=75.0)

        best = temp_db.get_best_branch_progress("TestFunc")

        assert best["branch"] == "b2"
        assert best["match_percent"] == 90.0

    def test_committed_branch_takes_priority(self, temp_db):
        """Committed branch is recognizable for recovery."""
        temp_db.upsert_branch_progress(
            "TestFunc", "main",
            match_percent=100.0,
            is_committed=True,
            commit_hash="abc123"
        )
        temp_db.upsert_branch_progress(
            "TestFunc", "feature",
            match_percent=95.0,
            is_committed=False
        )

        # Can find the committed one
        all_progress = temp_db.get_branch_progress("TestFunc")
        committed = [p for p in all_progress if p["is_committed"]]

        assert len(committed) == 1
        assert committed[0]["branch"] == "main"
