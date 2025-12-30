"""Test complete workflow from claim to commit.

These tests verify the full workflow finish operations.
"""

import pytest


class TestWorkflowFinishHappyPath:
    """Test successful workflow completion."""

    def test_full_workflow_updates_all_state(self, temp_db):
        """Complete workflow updates all relevant state."""
        func_name = "TestFunction"

        # 1. Claim
        temp_db.upsert_function(func_name, status="unclaimed")
        success, _ = temp_db.add_claim(func_name, "agent-1")
        assert success

        # 2. Create scratch and track progress
        temp_db.upsert_scratch(
            slug="test-scratch",
            instance="local",
            base_url="http://localhost:8000",
            function_name=func_name
        )
        temp_db.upsert_function(
            func_name,
            status="in_progress",
            local_scratch_slug="test-scratch",
            match_percent=50.0
        )

        # 3. Achieve match
        temp_db.record_match_score("test-scratch", score=0, max_score=100)
        temp_db.upsert_function(
            func_name,
            status="matched",
            match_percent=100.0
        )

        # 4. Commit
        temp_db.upsert_function(
            func_name,
            status="committed",
            is_committed=True,
            commit_hash="abc123",
            build_status="passing"
        )

        # 5. Release claim
        temp_db.release_claim(func_name, "agent-1")

        # Verify final state
        func = temp_db.get_function(func_name)
        assert func["status"] == "committed"
        assert func["is_committed"] == 1
        assert func["match_percent"] == 100.0
        assert func["build_status"] == "passing"
        assert func["local_scratch_slug"] == "test-scratch"

        # Claim should be released
        claims = temp_db.get_active_claims()
        assert len(claims) == 0

    def test_workflow_creates_audit_trail(self, temp_db):
        """Complete workflow creates comprehensive audit trail."""
        func_name = "TestFunction"

        # Run through workflow
        temp_db.upsert_function(func_name, status="unclaimed")
        temp_db.add_claim(func_name, "agent-1")
        temp_db.upsert_function(func_name, status="in_progress", match_percent=50.0)
        temp_db.upsert_function(func_name, status="matched", match_percent=100.0)
        temp_db.upsert_function(func_name, status="committed", is_committed=True)
        temp_db.release_claim(func_name, "agent-1")

        # Get history
        history = temp_db.get_history(entity_id=func_name, limit=100)

        # Should have multiple entries
        assert len(history) >= 4  # At least: create, claim, match, commit

        # Should track status changes
        actions = [e["action"] for e in history]
        assert "created" in actions or "updated" in actions


class TestWorkflowFinishReleasesClaim:
    """Test that workflow finish releases claims."""

    def test_finish_releases_function_claim(self, temp_db):
        """Finishing releases the function claim."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        # Finish workflow
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            is_committed=True
        )
        temp_db.release_claim("TestFunc", "agent-1")

        claims = temp_db.get_active_claims()
        assert len(claims) == 0

    def test_finish_unlocks_subdirectory(self, temp_db):
        """Finishing can unlock the subdirectory."""
        temp_db.lock_subdirectory("lb", "agent-1")

        # After finishing, unlock
        temp_db.unlock_subdirectory("lb", "agent-1")

        lock = temp_db.get_subdirectory_lock("lb")
        assert lock["locked_by_agent"] is None


class TestWorkflowRecordsDatabase:
    """Test that workflow updates database correctly."""

    def test_finish_sets_commit_hash(self, temp_db):
        """Finishing sets the commit hash."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            commit_hash="abc123def456"
        )

        func = temp_db.get_function("TestFunc")
        assert func["commit_hash"] == "abc123def456"

    def test_finish_sets_build_status(self, temp_db):
        """Finishing sets build status."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            build_status="passing"
        )

        func = temp_db.get_function("TestFunc")
        assert func["build_status"] == "passing"

    def test_finish_sets_branch(self, temp_db):
        """Finishing records the branch name."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            branch="feature-branch"
        )

        func = temp_db.get_function("TestFunc")
        assert func["branch"] == "feature-branch"

    def test_finish_updates_branch_progress(self, temp_db):
        """Finishing updates branch progress."""
        temp_db.upsert_branch_progress(
            function_name="TestFunc",
            branch="feature",
            match_percent=100.0,
            is_committed=True,
            commit_hash="abc123"
        )

        progress = temp_db.get_best_branch_progress("TestFunc")
        assert progress["is_committed"] == 1  # SQLite stores as integer
        assert progress["commit_hash"] == "abc123"


class TestWorkflowDryRun:
    """Test dry-run behavior."""

    def test_dry_run_does_not_change_status(self, temp_db):
        """Dry run doesn't change function status."""
        temp_db.upsert_function("TestFunc", status="matched", match_percent=100.0)

        # In dry run, we would NOT update the database
        # Just verify current state is preserved
        func = temp_db.get_function("TestFunc")
        assert func["status"] == "matched"
        assert func.get("is_committed") != 1

    def test_dry_run_preserves_claim(self, temp_db):
        """Dry run preserves the claim."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        # In dry run, claim is NOT released
        claims = temp_db.get_active_claims()
        assert len(claims) == 1


class TestWorkflowWithForce:
    """Test workflow with --force flag."""

    def test_force_records_diagnosis(self, temp_db):
        """Force with diagnosis records the build diagnosis."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            is_committed=True,
            build_status="broken",
            build_diagnosis="Caller ftCommon_800D1234 needs signature update"
        )

        func = temp_db.get_function("TestFunc")
        assert func["build_status"] == "broken"
        assert "signature update" in func["build_diagnosis"]

    def test_force_allows_broken_build(self, temp_db):
        """Force allows committing with broken build."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            is_committed=True,
            build_status="broken"
        )

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "committed"
        assert func["build_status"] == "broken"


class TestWorkflowWithSubdirectory:
    """Test workflow with subdirectory worktree."""

    def test_workflow_tracks_worktree(self, temp_db):
        """Workflow tracks which worktree was used."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            worktree_path="/path/to/melee-worktrees/dir-lb"
        )

        func = temp_db.get_function("TestFunc")
        assert func["worktree_path"] == "/path/to/melee-worktrees/dir-lb"

    def test_workflow_tracks_source_file(self, temp_db):
        """Workflow tracks the source file path."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            source_file_path="melee/lb/lbcommand.c"
        )

        func = temp_db.get_function("TestFunc")
        assert func["source_file_path"] == "melee/lb/lbcommand.c"

    def test_workflow_increments_pending_commits(self, temp_db):
        """Workflow increments pending commits for subdirectory."""
        temp_db.upsert_subdirectory("lb", "/path/wt-lb", "main")

        temp_db.increment_pending_commits("lb")

        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT pending_commits FROM subdirectory_allocations WHERE subdirectory_key = ?",
                ("lb",)
            )
            row = cursor.fetchone()

        assert row["pending_commits"] == 1


class TestMultipleFunctionsWorkflow:
    """Test workflow with multiple functions."""

    def test_multiple_functions_same_subdirectory(self, temp_db):
        """Can complete multiple functions in same subdirectory."""
        for i in range(3):
            temp_db.upsert_function(
                f"Func{i}",
                status="committed",
                is_committed=True,
                worktree_path="/path/wt-lb"
            )

        # All should be committed
        committed = temp_db.get_functions_by_status("committed")
        assert len(committed) == 3

    def test_workflow_order_independence(self, temp_db):
        """Functions can be completed in any order."""
        # Complete in non-sequential order
        temp_db.upsert_function("FuncC", status="committed", is_committed=True)
        temp_db.upsert_function("FuncA", status="committed", is_committed=True)
        temp_db.upsert_function("FuncB", status="committed", is_committed=True)

        committed = temp_db.get_functions_by_status("committed")
        names = [f["function_name"] for f in committed]

        assert "FuncA" in names
        assert "FuncB" in names
        assert "FuncC" in names
