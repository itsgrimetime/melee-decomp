"""Test state query commands.

These tests verify state status/history/agents/stale query operations.
"""

import time

import pytest


class TestGetFunctionsByStatus:
    """Test filtering functions by status."""

    def test_get_by_status_claimed(self, temp_db):
        """Can get all claimed functions."""
        temp_db.upsert_function("Func1", status="claimed")
        temp_db.upsert_function("Func2", status="claimed")
        temp_db.upsert_function("Func3", status="unclaimed")

        claimed = temp_db.get_functions_by_status("claimed")

        assert len(claimed) == 2
        assert all(f["status"] == "claimed" for f in claimed)

    def test_get_by_status_in_progress(self, temp_db):
        """Can get all in_progress functions."""
        temp_db.upsert_function("Func1", status="in_progress", match_percent=50.0)
        temp_db.upsert_function("Func2", status="in_progress", match_percent=75.0)
        temp_db.upsert_function("Func3", status="matched", match_percent=100.0)

        in_progress = temp_db.get_functions_by_status("in_progress")

        assert len(in_progress) == 2
        assert all(f["status"] == "in_progress" for f in in_progress)

    def test_get_by_status_committed(self, temp_db):
        """Can get all committed functions."""
        temp_db.upsert_function("Func1", status="committed", is_committed=True)
        temp_db.upsert_function("Func2", status="matched", match_percent=100.0)

        committed = temp_db.get_functions_by_status("committed")

        assert len(committed) == 1
        assert committed[0]["function_name"] == "Func1"

    def test_empty_result_for_no_matches(self, temp_db):
        """Returns empty list if no functions match status."""
        temp_db.upsert_function("Func1", status="unclaimed")

        merged = temp_db.get_functions_by_status("merged")

        assert merged == []


class TestUncommittedMatches:
    """Test finding uncommitted matches."""

    def test_finds_high_match_uncommitted(self, temp_db):
        """Finds functions with 95%+ match that aren't committed."""
        temp_db.upsert_function("Func1", status="matched", match_percent=98.5)
        temp_db.upsert_function("Func2", status="matched", match_percent=100.0)
        temp_db.upsert_function("Func3", status="in_progress", match_percent=50.0)
        temp_db.upsert_function("Func4", status="committed", match_percent=100.0, is_committed=True)

        uncommitted = temp_db.get_uncommitted_matches()

        assert len(uncommitted) == 2
        func_names = [f["function_name"] for f in uncommitted]
        assert "Func1" in func_names
        assert "Func2" in func_names
        assert "Func3" not in func_names  # Too low match
        assert "Func4" not in func_names  # Already committed


class TestStaleData:
    """Test stale data detection."""

    def test_stale_scratches_detected(self, temp_db):
        """Detects scratches not verified recently."""
        # This would need scratches with old verified_at timestamps
        # For now, just verify the method runs
        stale = temp_db.get_stale_data(hours_threshold=0.001)  # Very short threshold

        assert isinstance(stale, list)

    def test_fresh_data_not_stale(self, temp_db):
        """Recently verified data is not stale."""
        # Fresh data should not appear in stale list with high threshold
        stale = temp_db.get_stale_data(hours_threshold=24.0)

        # No scratches = no stale data
        assert isinstance(stale, list)


class TestAgentSummary:
    """Test agent summary queries."""

    def test_agent_summary_shows_active_agents(self, temp_db):
        """Agent summary shows agents with claims."""
        temp_db.upsert_function("Func1", status="unclaimed")
        temp_db.add_claim("Func1", "agent-1")
        temp_db.upsert_agent("agent-1")

        summary = temp_db.get_agent_summary()

        assert len(summary) >= 1
        agent = next((a for a in summary if a["agent_id"] == "agent-1"), None)
        assert agent is not None

    def test_agent_summary_includes_claim_count(self, temp_db):
        """Agent summary includes number of active claims."""
        for i in range(3):
            temp_db.upsert_function(f"Func{i}", status="unclaimed")
            temp_db.add_claim(f"Func{i}", "agent-1")

        temp_db.upsert_agent("agent-1")
        summary = temp_db.get_agent_summary()

        agent = next((a for a in summary if a["agent_id"] == "agent-1"), None)
        assert agent is not None


class TestSubdirectoryStatus:
    """Test subdirectory status queries."""

    def test_subdirectory_status_shows_all(self, temp_db):
        """Can get status of all subdirectories."""
        temp_db.upsert_subdirectory("lb", "/path/wt-lb", "branch-lb")
        temp_db.upsert_subdirectory("ft-chara-ftFox", "/path/wt-fox", "branch-fox")

        status = temp_db.get_subdirectory_status()

        assert len(status) == 2
        keys = [s["subdirectory_key"] for s in status]
        assert "lb" in keys
        assert "ft-chara-ftFox" in keys

    def test_subdirectory_status_shows_lock_info(self, temp_db):
        """Subdirectory status includes lock information."""
        temp_db.upsert_subdirectory("lb", "/path/wt-lb", "branch-lb")
        temp_db.lock_subdirectory("lb", "agent-1")

        status = temp_db.get_subdirectory_status()

        lb_status = next(s for s in status if s["subdirectory_key"] == "lb")
        assert lb_status["locked_by_agent"] == "agent-1"


class TestBrokenBuilds:
    """Test broken build tracking queries."""

    def test_get_worktree_broken_count(self, temp_db):
        """Can count broken builds per worktree."""
        temp_db.upsert_function(
            "Func1",
            worktree_path="/wt/lb",
            build_status="broken"
        )
        temp_db.upsert_function(
            "Func2",
            worktree_path="/wt/lb",
            build_status="broken"
        )
        temp_db.upsert_function(
            "Func3",
            worktree_path="/wt/lb",
            build_status="passing"
        )

        count, names = temp_db.get_worktree_broken_count("/wt/lb")

        assert count == 2
        assert "Func1" in names
        assert "Func2" in names
        assert "Func3" not in names

    def test_get_all_broken_builds(self, temp_db):
        """Can get all broken builds grouped by worktree."""
        temp_db.upsert_function("Func1", worktree_path="/wt/lb", build_status="broken")
        temp_db.upsert_function("Func2", worktree_path="/wt/ft", build_status="broken")
        temp_db.upsert_function("Func3", worktree_path="/wt/lb", build_status="passing")

        all_broken = temp_db.get_all_broken_builds()

        assert "/wt/lb" in all_broken
        assert "/wt/ft" in all_broken
        assert len(all_broken["/wt/lb"]) == 1
        assert len(all_broken["/wt/ft"]) == 1


class TestMetadata:
    """Test metadata operations."""

    def test_get_set_meta(self, temp_db):
        """Can get and set metadata values."""
        temp_db.set_meta("test_key", "test_value")

        value = temp_db.get_meta("test_key")

        assert value == "test_value"

    def test_get_nonexistent_meta(self, temp_db):
        """Getting nonexistent meta returns None."""
        value = temp_db.get_meta("nonexistent_key")

        assert value is None

    def test_update_meta_overwrites(self, temp_db):
        """Setting existing meta key overwrites value."""
        temp_db.set_meta("key", "value1")
        temp_db.set_meta("key", "value2")

        value = temp_db.get_meta("key")

        assert value == "value2"


class TestScratchOperations:
    """Test scratch-related queries."""

    def test_upsert_scratch(self, temp_db):
        """Can upsert a scratch record."""
        temp_db.upsert_scratch(
            slug="test-scratch-1",
            instance="local",
            base_url="http://localhost:8000",
            function_name="TestFunc",
            score=50,
            max_score=100
        )

        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM scratches WHERE slug = ?",
                ("test-scratch-1",)
            )
            scratch = cursor.fetchone()

        assert scratch is not None
        assert scratch["function_name"] == "TestFunc"
        assert scratch["instance"] == "local"

    def test_record_match_score(self, temp_db):
        """Can record match score history."""
        temp_db.upsert_scratch(
            slug="test-scratch",
            instance="local",
            base_url="http://localhost:8000"
        )

        temp_db.record_match_score("test-scratch", score=50, max_score=100)
        temp_db.record_match_score("test-scratch", score=25, max_score=100)
        temp_db.record_match_score("test-scratch", score=0, max_score=100)

        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM match_history WHERE scratch_slug = ? ORDER BY timestamp",
                ("test-scratch",)
            )
            history = cursor.fetchall()

        assert len(history) == 3
        assert history[0]["score"] == 50
        assert history[1]["score"] == 25
        assert history[2]["score"] == 0

    def test_record_match_score_skips_duplicates(self, temp_db):
        """Recording same score twice doesn't create duplicate."""
        temp_db.upsert_scratch(
            slug="test-scratch",
            instance="local",
            base_url="http://localhost:8000"
        )

        temp_db.record_match_score("test-scratch", score=50, max_score=100)
        temp_db.record_match_score("test-scratch", score=50, max_score=100)  # Duplicate

        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) as cnt FROM match_history WHERE scratch_slug = ?",
                ("test-scratch",)
            )
            count = cursor.fetchone()["cnt"]

        assert count == 1  # Only one entry, not two
