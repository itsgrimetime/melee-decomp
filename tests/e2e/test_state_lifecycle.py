"""Test state transitions through the full lifecycle.

These tests verify correct state transitions:
UNCLAIMED -> CLAIMED -> IN_PROGRESS -> MATCHED -> COMMITTED -> IN_REVIEW -> MERGED
"""

import time

import pytest


class TestStateTransitions:
    """Test individual state transitions."""

    def test_unclaimed_is_default_state(self, temp_db):
        """New functions start in unclaimed state."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        func = temp_db.get_function("TestFunc")

        assert func is not None
        assert func["status"] == "unclaimed"

    def test_claim_transitions_to_claimed(self, temp_db):
        """Claiming a function transitions it to claimed status."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        success, error = temp_db.add_claim("TestFunc", "agent-1")

        assert success is True
        assert error is None

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "claimed"
        assert func["claimed_by_agent"] == "agent-1"

    def test_update_to_in_progress(self, temp_db):
        """Working on a function transitions to in_progress."""
        temp_db.upsert_function("TestFunc", status="claimed")
        temp_db.add_claim("TestFunc", "agent-1")

        # Simulate starting work (match < 95%)
        temp_db.upsert_function(
            "TestFunc",
            agent_id="agent-1",
            status="in_progress",
            match_percent=50.0
        )

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "in_progress"
        assert func["match_percent"] == 50.0

    def test_high_match_transitions_to_matched(self, temp_db):
        """Achieving 95%+ match transitions to matched status."""
        temp_db.upsert_function("TestFunc", status="in_progress", match_percent=50.0)
        temp_db.add_claim("TestFunc", "agent-1")

        # Simulate achieving match
        temp_db.upsert_function(
            "TestFunc",
            agent_id="agent-1",
            status="matched",
            match_percent=98.5
        )

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "matched"
        assert func["match_percent"] == 98.5

    def test_commit_transitions_to_committed(self, temp_db):
        """Committing code transitions to committed status."""
        temp_db.upsert_function("TestFunc", status="matched", match_percent=100.0)

        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            is_committed=True,
            commit_hash="abc123",
            branch="main"
        )

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "committed"
        assert func["is_committed"] == 1
        assert func["commit_hash"] == "abc123"

    def test_pr_link_transitions_to_in_review(self, temp_db):
        """Linking a PR transitions to in_review status."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            is_committed=True
        )

        temp_db.upsert_function(
            "TestFunc",
            status="in_review",
            pr_url="https://github.com/example/repo/pull/123",
            pr_number=123,
            pr_state="OPEN"
        )

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "in_review"
        assert func["pr_number"] == 123
        assert func["pr_state"] == "OPEN"

    def test_pr_merge_transitions_to_merged(self, temp_db):
        """PR merge transitions to merged status."""
        temp_db.upsert_function(
            "TestFunc",
            status="in_review",
            pr_url="https://github.com/example/repo/pull/123",
            pr_state="OPEN"
        )

        temp_db.upsert_function(
            "TestFunc",
            status="merged",
            pr_state="MERGED"
        )

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "merged"
        assert func["pr_state"] == "MERGED"


class TestFullLifecycle:
    """Test complete lifecycle from claim to merge."""

    def test_full_lifecycle_happy_path(self, temp_db):
        """Complete lifecycle: unclaimed -> claimed -> in_progress -> matched -> committed -> merged."""
        func_name = "TestFunction"

        # 1. Start unclaimed
        temp_db.upsert_function(func_name, status="unclaimed")
        assert temp_db.get_function(func_name)["status"] == "unclaimed"

        # 2. Claim
        success, _ = temp_db.add_claim(func_name, "agent-1")
        assert success
        assert temp_db.get_function(func_name)["status"] == "claimed"

        # 3. Start working (in_progress)
        temp_db.upsert_function(
            func_name,
            agent_id="agent-1",
            status="in_progress",
            match_percent=30.0,
            local_scratch_slug="test-scratch-1"
        )
        assert temp_db.get_function(func_name)["status"] == "in_progress"

        # 4. Achieve match
        temp_db.upsert_function(
            func_name,
            agent_id="agent-1",
            status="matched",
            match_percent=100.0
        )
        assert temp_db.get_function(func_name)["status"] == "matched"

        # 5. Commit
        temp_db.upsert_function(
            func_name,
            status="committed",
            is_committed=True,
            commit_hash="def456",
            build_status="passing"
        )
        func = temp_db.get_function(func_name)
        assert func["status"] == "committed"
        assert func["build_status"] == "passing"

        # 6. Create PR
        temp_db.upsert_function(
            func_name,
            status="in_review",
            pr_url="https://github.com/example/repo/pull/42",
            pr_number=42,
            pr_state="OPEN"
        )
        assert temp_db.get_function(func_name)["status"] == "in_review"

        # 7. Merge
        temp_db.upsert_function(
            func_name,
            status="merged",
            pr_state="MERGED"
        )
        final = temp_db.get_function(func_name)
        assert final["status"] == "merged"
        assert final["pr_state"] == "MERGED"
        assert final["is_committed"] == 1


class TestStatusConsistency:
    """Test that status is consistent with related fields."""

    def test_claimed_has_agent(self, temp_db):
        """Claimed status should have claimed_by_agent set."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "claimed"
        assert func["claimed_by_agent"] == "agent-1"
        assert func["claimed_at"] is not None

    def test_committed_has_hash(self, temp_db):
        """Committed status should have commit hash."""
        temp_db.upsert_function(
            "TestFunc",
            status="committed",
            is_committed=True,
            commit_hash="abc123"
        )

        func = temp_db.get_function("TestFunc")
        assert func["is_committed"] == 1
        assert func["commit_hash"] == "abc123"

    def test_in_review_has_pr_info(self, temp_db):
        """In review status should have PR info."""
        temp_db.upsert_function(
            "TestFunc",
            status="in_review",
            pr_url="https://github.com/example/repo/pull/1",
            pr_number=1,
            pr_state="OPEN"
        )

        func = temp_db.get_function("TestFunc")
        assert func["pr_url"] is not None
        assert func["pr_number"] == 1
        assert func["pr_state"] == "OPEN"

    def test_matched_has_high_percent(self, temp_db):
        """Matched status should have high match percentage."""
        temp_db.upsert_function(
            "TestFunc",
            status="matched",
            match_percent=98.5
        )

        func = temp_db.get_function("TestFunc")
        assert func["match_percent"] >= 95.0


class TestBranchProgress:
    """Test per-branch progress tracking."""

    def test_track_progress_on_branch(self, temp_db):
        """Can track match progress per branch."""
        temp_db.upsert_branch_progress(
            "TestFunc",
            branch="feature-1",
            scratch_slug="scratch-1",
            match_percent=75.0,
            agent_id="agent-1"
        )

        progress = temp_db.get_branch_progress("TestFunc")
        assert len(progress) == 1
        assert progress[0]["branch"] == "feature-1"
        assert progress[0]["match_percent"] == 75.0

    def test_multiple_branches_independent(self, temp_db):
        """Different branches can have different match states."""
        temp_db.upsert_branch_progress(
            "TestFunc",
            branch="feature-1",
            match_percent=50.0,
            agent_id="agent-1"
        )
        temp_db.upsert_branch_progress(
            "TestFunc",
            branch="feature-2",
            match_percent=80.0,
            agent_id="agent-2"
        )

        progress = temp_db.get_branch_progress("TestFunc")
        assert len(progress) == 2

        # Should be sorted by match_percent descending
        assert progress[0]["match_percent"] == 80.0
        assert progress[1]["match_percent"] == 50.0

    def test_get_best_branch(self, temp_db):
        """Can get the best match across branches."""
        temp_db.upsert_branch_progress("TestFunc", branch="b1", match_percent=50.0)
        temp_db.upsert_branch_progress("TestFunc", branch="b2", match_percent=95.0)
        temp_db.upsert_branch_progress("TestFunc", branch="b3", match_percent=75.0)

        best = temp_db.get_best_branch_progress("TestFunc")
        assert best is not None
        assert best["branch"] == "b2"
        assert best["match_percent"] == 95.0

    def test_committed_branch_tracked(self, temp_db):
        """Committed state is tracked per branch."""
        temp_db.upsert_branch_progress(
            "TestFunc",
            branch="main",
            match_percent=100.0,
            is_committed=True,
            commit_hash="abc123"
        )

        progress = temp_db.get_best_branch_progress("TestFunc")
        assert progress["is_committed"] == 1  # SQLite stores as integer
        assert progress["commit_hash"] == "abc123"
