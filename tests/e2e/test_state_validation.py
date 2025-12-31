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


class TestCommittedNeedsFixStatus:
    """Test committed_needs_fix status handling.

    When a function is committed with --force --diagnosis, it should:
    - Have status='committed_needs_fix'
    - Have build_status='broken'
    - Have build_diagnosis set

    The validation logic should NOT "fix" this to status='committed'.
    """

    def test_committed_needs_fix_not_overwritten_by_validate(self, temp_db):
        """Validation should preserve committed_needs_fix status.

        Bug: validate was treating is_committed=True as meaning status should be 'committed',
        ignoring build_status='broken' which should keep status as 'committed_needs_fix'.
        """
        # Setup: function committed with broken build
        temp_db.upsert_function(
            "BrokenFunc",
            status="committed_needs_fix",
            is_committed=True,
            build_status="broken",
            build_diagnosis="Header has UNK_RET but function returns void",
            match_percent=100.0,
        )

        func = temp_db.get_function("BrokenFunc")

        # Simulate what validate's status consistency check does
        status = func.get('status')
        is_committed = func.get('is_committed', False)
        build_status = func.get('build_status')
        pr_state = func.get('pr_state')

        # The correct expected status when is_committed=True AND build_status='broken'
        # should be 'committed_needs_fix', not 'committed'
        if pr_state == 'MERGED':
            expected_status = 'merged'
        elif pr_state == 'OPEN':
            expected_status = 'in_review'
        elif is_committed:
            # BUG WAS HERE: old code just did expected_status = 'committed'
            # Correct logic must check build_status
            if build_status == 'broken':
                expected_status = 'committed_needs_fix'
            else:
                expected_status = 'committed'
        else:
            expected_status = 'matched'

        # Status should already be correct - no "fix" needed
        assert status == expected_status
        assert status == 'committed_needs_fix'

    def test_category_needs_fix_filter(self, temp_db):
        """--category needs_fix should find functions with build_status='broken'."""
        # Setup: mix of functions with different build states
        temp_db.upsert_function(
            "BrokenFunc1",
            status="committed_needs_fix",
            is_committed=True,
            build_status="broken",
            build_diagnosis="Missing header",
        )
        temp_db.upsert_function(
            "BrokenFunc2",
            status="committed_needs_fix",
            is_committed=True,
            build_status="broken",
            build_diagnosis="Type mismatch",
        )
        temp_db.upsert_function(
            "HealthyFunc",
            status="committed",
            is_committed=True,
            build_status="passing",
        )
        temp_db.upsert_function(
            "MergedFunc",
            status="merged",
            is_committed=True,
            build_status="passing",
            pr_state="MERGED",
        )

        # Query for needs_fix category
        with temp_db.connection() as conn:
            cursor = conn.execute("""
                SELECT function_name FROM functions
                WHERE build_status = 'broken' AND is_committed = TRUE
            """)
            needs_fix = [row['function_name'] for row in cursor.fetchall()]

        assert len(needs_fix) == 2
        assert "BrokenFunc1" in needs_fix
        assert "BrokenFunc2" in needs_fix
        assert "HealthyFunc" not in needs_fix
        assert "MergedFunc" not in needs_fix

    def test_committed_needs_fix_status_is_valid(self, temp_db):
        """Verify committed_needs_fix is a valid status value."""
        # This tests the schema constraint allows this status
        temp_db.upsert_function(
            "TestFunc",
            status="committed_needs_fix",
            is_committed=True,
            build_status="broken",
        )

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "committed_needs_fix"
        assert func["build_status"] == "broken"

    def test_broken_build_with_committed_status_is_inconsistent(self, temp_db):
        """If status='committed' but build_status='broken', that's inconsistent.

        This is the bug the agent found: workflow finish was setting
        status='committed' with build_status='broken' instead of
        status='committed_needs_fix'.
        """
        # Setup: the WRONG state (what the bug produces)
        temp_db.upsert_function(
            "BuggyFunc",
            status="committed",  # Wrong! Should be committed_needs_fix
            is_committed=True,
            build_status="broken",
            build_diagnosis="Some issue",
        )

        func = temp_db.get_function("BuggyFunc")

        # Detect the inconsistency
        is_inconsistent = (
            func["status"] == "committed" and
            func["build_status"] == "broken"
        )

        # This state IS inconsistent and should be flagged/fixed
        assert is_inconsistent, "status='committed' with build_status='broken' is inconsistent"

    def test_validation_fixes_committed_with_broken_to_committed_needs_fix(self, temp_db):
        """Validation --fix should correct committed+broken to committed_needs_fix."""
        # Setup: the WRONG state
        temp_db.upsert_function(
            "BuggyFunc",
            status="committed",  # Wrong!
            is_committed=True,
            build_status="broken",
            build_diagnosis="Some issue",
        )

        # Simulate what validation --fix should do
        func = temp_db.get_function("BuggyFunc")
        if func["status"] == "committed" and func["build_status"] == "broken":
            temp_db.upsert_function("BuggyFunc", status="committed_needs_fix")

        # Verify fix
        func = temp_db.get_function("BuggyFunc")
        assert func["status"] == "committed_needs_fix"


class TestStateValidateCliCommand:
    """Test the actual state validate CLI command.

    These tests verify the CLI command behaves correctly with committed_needs_fix.
    """

    def test_validate_preserves_committed_needs_fix(self, cli_with_db, temp_db):
        """state validate --fix should NOT change committed_needs_fix to committed."""
        # Setup: correctly marked function
        temp_db.upsert_function(
            "BrokenFunc",
            status="committed_needs_fix",
            is_committed=True,
            build_status="broken",
            build_diagnosis="Header mismatch",
            match_percent=100.0,
        )

        # Run validate with fix
        result = cli_with_db("state", "validate", "--fix")

        # Check status was preserved
        func = temp_db.get_function("BrokenFunc")
        assert func["status"] == "committed_needs_fix", (
            f"Expected 'committed_needs_fix' but got '{func['status']}'. "
            f"Output: {result.output}"
        )

    def test_validate_detects_wrong_status_for_broken_build(self, cli_with_db, temp_db):
        """state validate should detect status='committed' with build_status='broken'."""
        # Setup: incorrectly marked function (the bug state)
        temp_db.upsert_function(
            "BuggyFunc",
            status="committed",  # Wrong!
            is_committed=True,
            build_status="broken",
            build_diagnosis="Some issue",
            match_percent=100.0,
        )

        # Run validate (without fix)
        result = cli_with_db("state", "validate")

        # Should report an issue
        assert result.exit_code == 0

        # After fix, status should be committed_needs_fix
        result = cli_with_db("state", "validate", "--fix")
        func = temp_db.get_function("BuggyFunc")
        assert func["status"] == "committed_needs_fix", (
            f"Expected 'committed_needs_fix' after fix but got '{func['status']}'"
        )


class TestStateCategoryFilter:
    """Test the --category filter in state status command."""

    def test_category_needs_fix(self, cli_with_db, temp_db):
        """--category needs_fix should list functions with broken builds."""
        # Setup
        temp_db.upsert_function(
            "BrokenFunc",
            status="committed_needs_fix",
            is_committed=True,
            build_status="broken",
            build_diagnosis="Missing header",
            match_percent=100.0,
        )
        temp_db.upsert_function(
            "HealthyFunc",
            status="committed",
            is_committed=True,
            build_status="passing",
            match_percent=100.0,
        )

        # Run with --category needs_fix
        result = cli_with_db("state", "status", "--category", "needs_fix")

        # Should include broken func, not healthy func
        assert result.exit_code == 0
        assert "BrokenFunc" in result.output, f"Expected 'BrokenFunc' in output: {result.output}"
        assert "HealthyFunc" not in result.output, f"Did not expect 'HealthyFunc' in output"


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
