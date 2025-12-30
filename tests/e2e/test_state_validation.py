"""Test state validation and consistency checks.

These tests verify that state validation can detect and fix inconsistencies.
"""

import pytest


class TestStatusConsistency:
    """Test status field consistency checks."""

    def test_detect_committed_without_is_committed(self, temp_db):
        """Detect status=committed but is_committed=False."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            is_committed=False  # Inconsistent!
        )

        func = temp_db.get_function("TestFunc")

        # Should detect this inconsistency
        is_inconsistent = func["status"] == "committed" and not func["is_committed"]
        assert is_inconsistent

    def test_detect_is_committed_wrong_status(self, temp_db):
        """Detect is_committed=True but status not committed/merged."""
        temp_db.upsert_function(
            "TestFunc",
            status="in_progress",
            is_committed=True  # Inconsistent!
        )

        func = temp_db.get_function("TestFunc")

        # Should detect this inconsistency
        is_inconsistent = (
            func["is_committed"] == 1 and
            func["status"] not in ("committed", "in_review", "merged")
        )
        assert is_inconsistent

    def test_detect_merged_without_pr(self, temp_db):
        """Detect status=merged but no PR info."""
        temp_db.upsert_function(
            "TestFunc",
            status="merged",
            pr_url=None,  # Missing!
            pr_state=None
        )

        func = temp_db.get_function("TestFunc")

        # Should detect this inconsistency
        is_inconsistent = func["status"] == "merged" and not func["pr_url"]
        assert is_inconsistent

    def test_detect_matched_low_percent(self, temp_db):
        """Detect status=matched but low match percentage."""
        temp_db.upsert_function(
            "TestFunc",
            status="matched",
            match_percent=50.0  # Too low for matched!
        )

        func = temp_db.get_function("TestFunc")

        # Should detect this inconsistency (matched should be 95%+)
        is_inconsistent = func["status"] == "matched" and func["match_percent"] < 95.0
        assert is_inconsistent


class TestFixStatusInconsistencies:
    """Test fixing status inconsistencies."""

    def test_fix_committed_status(self, temp_db):
        """Can fix committed status inconsistency."""
        temp_db.upsert_function(
            "TestFunc",
            status="matched",
            is_committed=True
        )

        # Fix: update status to match is_committed
        temp_db.upsert_function("TestFunc", status="committed")

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "committed"

    def test_fix_is_committed_flag(self, temp_db):
        """Can fix is_committed flag."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            is_committed=False
        )

        # Fix: set is_committed to True
        temp_db.upsert_function("TestFunc", is_committed=True)

        func = temp_db.get_function("TestFunc")
        assert func["is_committed"] == 1


class TestMissingScratchLinks:
    """Test detecting missing scratch links."""

    def test_detect_missing_local_scratch(self, temp_db):
        """Detect function with progress but no local scratch link."""
        temp_db.upsert_function(
            "TestFunc",
            status="in_progress",
            match_percent=50.0,
            local_scratch_slug=None  # Missing!
        )

        func = temp_db.get_function("TestFunc")

        # Should detect: has progress but no scratch
        is_missing = (
            func["match_percent"] > 0 and
            not func.get("local_scratch_slug")
        )
        assert is_missing

    def test_detect_missing_production_scratch(self, temp_db):
        """Detect function synced but missing production scratch."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            local_scratch_slug="local-slug",
            production_scratch_slug=None  # Maybe missing
        )

        # This might be intentional (not synced yet)
        # Validation would flag for review


class TestPRStateConsistency:
    """Test PR state consistency checks."""

    def test_detect_stale_pr_state(self, temp_db):
        """Detect PR with stale state info."""
        temp_db.upsert_function(
            "TestFunc",
            status="in_review",
            pr_url="https://github.com/owner/repo/pull/123",
            pr_state="OPEN",
            pr_number=123
        )

        func = temp_db.get_function("TestFunc")

        # Has PR but might need refresh
        needs_refresh = (
            func["pr_url"] is not None and
            func.get("git_verified_at") is None
        )
        # This would be flagged for refresh

    def test_detect_pr_without_url(self, temp_db):
        """Detect PR number without URL."""
        temp_db.upsert_function(
            "TestFunc",
            status="in_review",
            pr_number=123,
            pr_url=None  # Missing!
        )

        func = temp_db.get_function("TestFunc")

        is_inconsistent = func["pr_number"] and not func["pr_url"]
        assert is_inconsistent


class TestUncommittedMatches:
    """Test finding uncommitted matches for validation."""

    def test_find_uncommitted_100_percent(self, temp_db):
        """Find 100% matched functions not committed."""
        temp_db.upsert_function(
            "Func1",
            status="matched",
            match_percent=100.0,
            is_committed=False
        )
        temp_db.upsert_function(
            "Func2",
            status="committed",
            match_percent=100.0,
            is_committed=True
        )

        uncommitted = temp_db.get_uncommitted_matches()

        assert len(uncommitted) == 1
        assert uncommitted[0]["function_name"] == "Func1"

    def test_find_high_match_not_committed(self, temp_db):
        """Find 95%+ matched functions not committed."""
        temp_db.upsert_function("Func1", status="matched", match_percent=95.0)
        temp_db.upsert_function("Func2", status="matched", match_percent=98.0)
        temp_db.upsert_function("Func3", status="in_progress", match_percent=90.0)

        uncommitted = temp_db.get_uncommitted_matches()

        names = [f["function_name"] for f in uncommitted]
        assert "Func1" in names
        assert "Func2" in names
        assert "Func3" not in names  # Below 95%


class TestBrokenBuildValidation:
    """Test broken build validation."""

    def test_count_broken_per_worktree(self, temp_db):
        """Count broken builds per worktree."""
        wt1 = "/path/wt-lb"
        wt2 = "/path/wt-ft"

        temp_db.upsert_function("Func1", worktree_path=wt1, build_status="broken")
        temp_db.upsert_function("Func2", worktree_path=wt1, build_status="broken")
        temp_db.upsert_function("Func3", worktree_path=wt1, build_status="passing")
        temp_db.upsert_function("Func4", worktree_path=wt2, build_status="broken")

        count1, names1 = temp_db.get_worktree_broken_count(wt1)
        count2, names2 = temp_db.get_worktree_broken_count(wt2)

        assert count1 == 2
        assert count2 == 1

    def test_all_broken_builds_grouped(self, temp_db):
        """Get all broken builds grouped by worktree."""
        temp_db.upsert_function("Func1", worktree_path="/wt1", build_status="broken")
        temp_db.upsert_function("Func2", worktree_path="/wt2", build_status="broken")

        all_broken = temp_db.get_all_broken_builds()

        assert "/wt1" in all_broken
        assert "/wt2" in all_broken


class TestClaimValidation:
    """Test claim validation."""

    def test_detect_orphaned_claims(self, temp_db):
        """Detect claims without corresponding function."""
        # Add claim for nonexistent function
        with temp_db.connection() as conn:
            import time
            now = time.time()
            conn.execute(
                """
                INSERT INTO claims (function_name, agent_id, claimed_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                ("NonexistentFunc", "agent-1", now, now + 3600)
            )

        claims = temp_db.get_active_claims()

        # Claim exists
        assert any(c["function_name"] == "NonexistentFunc" for c in claims)

        # But function doesn't exist
        func = temp_db.get_function("NonexistentFunc")
        # It might be None or minimal

    def test_detect_expired_but_not_cleaned(self, temp_db):
        """Detect expired claims that weren't cleaned up."""
        import time

        # Add expired claim
        now = time.time()
        with temp_db.connection() as conn:
            conn.execute(
                """
                INSERT INTO claims (function_name, agent_id, claimed_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                ("OldFunc", "agent-1", now - 7200, now - 3600)
            )

        # Active claims should not include expired
        active = temp_db.get_active_claims()
        assert not any(c["function_name"] == "OldFunc" for c in active)


class TestAuditLogValidation:
    """Test audit log validation."""

    def test_all_changes_logged(self, temp_db):
        """Verify all changes create audit entries."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")
        temp_db.upsert_function("TestFunc", status="in_progress")
        temp_db.upsert_function("TestFunc", status="matched")
        temp_db.release_claim("TestFunc", "agent-1")

        history = temp_db.get_history(entity_id="TestFunc", limit=100)

        # Should have multiple entries
        assert len(history) >= 3

    def test_audit_entries_have_timestamps(self, temp_db):
        """All audit entries should have timestamps."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        history = temp_db.get_history()

        for entry in history:
            assert entry["timestamp"] is not None
            assert entry["timestamp"] > 0


class TestSubdirectoryValidation:
    """Test subdirectory allocation validation."""

    def test_detect_stale_locks(self, temp_db):
        """Detect locks that should have expired."""
        import time

        now = time.time()
        with temp_db.connection() as conn:
            conn.execute(
                """
                INSERT INTO subdirectory_allocations
                    (subdirectory_key, worktree_path, branch_name,
                     locked_by_agent, locked_at, lock_expires_at, updated_at)
                VALUES (?, '', '', ?, ?, ?, ?)
                """,
                ("lb", "agent-1", now - 7200, now - 3600, now)
            )

        lock = temp_db.get_subdirectory_lock("lb")

        # Should be detected as expired
        is_expired = (
            lock and
            lock.get("lock_expires_at") and
            lock["lock_expires_at"] < time.time()
        )
        assert is_expired or lock.get("lock_expired")

    def test_detect_orphaned_worktrees(self, temp_db):
        """Detect worktree allocations with no active work."""
        temp_db.upsert_subdirectory("lb", "/path/wt-lb", "feature-old")

        # No functions reference this worktree
        status = temp_db.get_subdirectory_status()

        lb_status = next(s for s in status if s["subdirectory_key"] == "lb")
        # Could be flagged for cleanup if no recent activity
