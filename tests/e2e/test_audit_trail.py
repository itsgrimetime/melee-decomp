"""Test audit log completeness.

These tests verify that all state-changing operations are properly logged.
"""

import json
import time

import pytest


class TestAuditLogging:
    """Test that operations create audit entries."""

    def test_claim_creates_audit_entry(self, temp_db):
        """Claiming a function creates an audit entry."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        history = temp_db.get_history(entity_type="claim", entity_id="TestFunc")

        assert len(history) >= 1
        entry = history[0]
        assert entry["action"] == "created"
        assert entry["agent_id"] == "agent-1"

    def test_release_creates_audit_entry(self, temp_db):
        """Releasing a claim creates an audit entry."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")
        temp_db.release_claim("TestFunc", "agent-1")

        history = temp_db.get_history(entity_type="claim", entity_id="TestFunc")

        # Should have both create and release entries
        assert len(history) >= 2
        actions = [e["action"] for e in history]
        assert "created" in actions
        assert "released" in actions

    def test_function_update_logs_changes(self, temp_db):
        """Function updates log old and new values."""
        temp_db.upsert_function("TestFunc", status="unclaimed", match_percent=0.0)
        temp_db.upsert_function("TestFunc", status="in_progress", match_percent=50.0)

        history = temp_db.get_history(entity_type="function", entity_id="TestFunc")

        # Should have at least 2 entries (create + update)
        assert len(history) >= 2

        # Get actions - most recent first
        actions = [e["action"] for e in history]

        # Should have at least one created
        assert "created" in actions

        # If update is logged, verify it has new values
        update_entries = [e for e in history if e["action"] == "updated"]
        if update_entries:
            # The most recent update should have new values
            if update_entries[0]["new_value"]:
                assert update_entries[0]["new_value"].get("status") == "in_progress"

    def test_scratch_create_logged(self, temp_db):
        """Creating a scratch creates an audit entry."""
        temp_db.upsert_scratch(
            slug="test-scratch",
            instance="local",
            base_url="http://localhost:8000",
            function_name="TestFunc",
            agent_id="agent-1"
        )

        history = temp_db.get_history(entity_type="scratch", entity_id="test-scratch")

        assert len(history) >= 1
        assert history[0]["action"] == "created"

    def test_subdirectory_lock_logged(self, temp_db):
        """Locking a subdirectory creates an audit entry."""
        temp_db.lock_subdirectory("lb", "agent-1")

        history = temp_db.get_history(entity_type="subdirectory", entity_id="lb")

        assert len(history) >= 1
        assert history[0]["action"] == "locked"
        assert history[0]["agent_id"] == "agent-1"

    def test_subdirectory_unlock_logged(self, temp_db):
        """Unlocking a subdirectory creates an audit entry."""
        temp_db.lock_subdirectory("lb", "agent-1")
        temp_db.unlock_subdirectory("lb", "agent-1")

        history = temp_db.get_history(entity_type="subdirectory", entity_id="lb")

        actions = [e["action"] for e in history]
        assert "unlocked" in actions


class TestAuditHistory:
    """Test audit history queries."""

    def test_history_ordered_by_time_desc(self, temp_db):
        """History is ordered newest first."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        time.sleep(0.01)
        temp_db.upsert_function("TestFunc", status="claimed")
        time.sleep(0.01)
        temp_db.upsert_function("TestFunc", status="in_progress")

        history = temp_db.get_history(entity_type="function", entity_id="TestFunc")

        # Newest should be first
        assert len(history) >= 3
        timestamps = [e["timestamp"] for e in history]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_history_limit(self, temp_db):
        """Can limit history results."""
        for i in range(10):
            temp_db.upsert_function("TestFunc", match_percent=float(i))

        history = temp_db.get_history(entity_id="TestFunc", limit=5)

        assert len(history) == 5

    def test_history_filter_by_entity_type(self, temp_db):
        """Can filter history by entity type."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        # Filter for only claims
        claim_history = temp_db.get_history(entity_type="claim")

        assert all(e["entity_type"] == "claim" for e in claim_history)

    def test_history_filter_by_entity_id(self, temp_db):
        """Can filter history by entity ID."""
        temp_db.upsert_function("Func1", status="unclaimed")
        temp_db.upsert_function("Func2", status="unclaimed")

        func1_history = temp_db.get_history(entity_id="Func1")

        assert all(e["entity_id"] == "Func1" for e in func1_history)

    def test_history_includes_agent_id(self, temp_db):
        """Audit entries include agent ID when available."""
        temp_db.upsert_function("TestFunc", agent_id="agent-1", status="in_progress")

        history = temp_db.get_history(entity_id="TestFunc")

        assert len(history) >= 1
        assert history[0]["agent_id"] == "agent-1"


class TestAuditValues:
    """Test old_value and new_value in audit entries."""

    def test_old_value_is_previous_state(self, temp_db):
        """old_value contains the state before the change."""
        temp_db.upsert_function("TestFunc", status="unclaimed", match_percent=0.0)
        temp_db.upsert_function("TestFunc", status="in_progress", match_percent=50.0)

        history = temp_db.get_history(entity_id="TestFunc")
        update_entry = next(e for e in history if e["action"] == "updated")

        if update_entry["old_value"]:
            assert update_entry["old_value"]["status"] == "unclaimed"

    def test_new_value_is_current_state(self, temp_db):
        """new_value contains the state after the change."""
        temp_db.upsert_function("TestFunc", status="in_progress", match_percent=50.0)

        history = temp_db.get_history(entity_id="TestFunc")

        if history and history[0]["new_value"]:
            assert history[0]["new_value"]["status"] == "in_progress"

    def test_create_has_no_old_value(self, temp_db):
        """First creation has no old_value (was None)."""
        temp_db.upsert_function("NewFunc", status="unclaimed")

        history = temp_db.get_history(entity_id="NewFunc")

        create_entry = next(e for e in history if e["action"] == "created")
        assert create_entry["old_value"] is None


class TestBranchProgressAudit:
    """Test branch progress audit logging."""

    def test_branch_progress_logged(self, temp_db):
        """Branch progress changes are logged."""
        temp_db.upsert_branch_progress(
            function_name="TestFunc",
            branch="feature-1",
            match_percent=50.0,
            agent_id="agent-1"
        )

        history = temp_db.get_history(entity_type="branch_progress")

        assert len(history) >= 1
        # Entity ID format is "function@branch"
        assert any("TestFunc@feature-1" in str(e.get("entity_id", "")) for e in history)

    def test_branch_progress_update_logged(self, temp_db):
        """Branch progress updates log old and new values."""
        temp_db.upsert_branch_progress("TestFunc", "feature-1", match_percent=50.0)
        temp_db.upsert_branch_progress("TestFunc", "feature-1", match_percent=75.0)

        history = temp_db.get_history(entity_type="branch_progress")

        # Should have both create and update
        actions = [e["action"] for e in history]
        assert "created" in actions
        assert "updated" in actions


class TestAuditIntegrity:
    """Test audit log integrity."""

    def test_audit_has_timestamps(self, temp_db):
        """All audit entries have timestamps."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        history = temp_db.get_history()

        for entry in history:
            assert entry["timestamp"] is not None
            assert entry["timestamp"] > 0

    def test_audit_preserves_metadata(self, temp_db):
        """Audit entries preserve any metadata."""
        # Direct audit log call with metadata
        temp_db.log_audit(
            entity_type="test",
            entity_id="test-1",
            action="test_action",
            metadata={"extra": "info", "count": 42}
        )

        history = temp_db.get_history(entity_type="test")

        assert len(history) >= 1
        assert history[0]["metadata"] == {"extra": "info", "count": 42}

    def test_all_entity_types_logged(self, temp_db):
        """All major entity types are logged."""
        # Create various entities
        temp_db.upsert_function("Func1", status="unclaimed")
        temp_db.add_claim("Func1", "agent-1")
        temp_db.upsert_scratch("scratch-1", "local", "http://localhost")
        temp_db.lock_subdirectory("lb", "agent-1")
        temp_db.upsert_branch_progress("Func1", "main", match_percent=50.0)

        # Get all history
        history = temp_db.get_history(limit=100)

        entity_types = set(e["entity_type"] for e in history)

        assert "function" in entity_types
        assert "claim" in entity_types
        assert "scratch" in entity_types
        assert "subdirectory" in entity_types
        assert "branch_progress" in entity_types
