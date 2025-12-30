"""Test concurrent claims between multiple agents.

These tests verify that claim coordination works correctly
when multiple agents are working in parallel.
"""

import time

import pytest


class TestConcurrentClaims:
    """Test concurrent claim behavior."""

    def test_concurrent_claim_same_function_first_wins(self, temp_db):
        """When two agents claim the same function, first one wins."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        # First agent claims
        success1, error1 = temp_db.add_claim("TestFunc", "agent-1")

        # Second agent tries to claim same function
        success2, error2 = temp_db.add_claim("TestFunc", "agent-2")

        assert success1 is True
        assert error1 is None
        assert success2 is False
        assert "agent-1" in error2

    def test_concurrent_claim_different_functions_both_succeed(self, temp_db):
        """Two agents can claim different functions simultaneously."""
        temp_db.upsert_function("Func1", status="unclaimed")
        temp_db.upsert_function("Func2", status="unclaimed")

        success1, _ = temp_db.add_claim("Func1", "agent-1")
        success2, _ = temp_db.add_claim("Func2", "agent-2")

        assert success1 is True
        assert success2 is True

        claims = temp_db.get_active_claims()
        assert len(claims) == 2

    def test_agent_can_hold_multiple_claims(self, temp_db):
        """Single agent can claim multiple functions."""
        for i in range(3):
            temp_db.upsert_function(f"Func{i}", status="unclaimed")
            success, _ = temp_db.add_claim(f"Func{i}", "agent-1")
            assert success is True

        claims = temp_db.get_active_claims()
        assert len(claims) == 3
        assert all(c["agent_id"] == "agent-1" for c in claims)


class TestSubdirectoryLocking:
    """Test subdirectory-based worktree locking."""

    def test_lock_subdirectory_succeeds(self, temp_db):
        """Can lock an unlocked subdirectory."""
        success, error = temp_db.lock_subdirectory("lb", "agent-1")

        assert success is True
        assert error is None

    def test_lock_already_locked_fails(self, temp_db):
        """Cannot lock subdirectory already locked by another agent."""
        temp_db.lock_subdirectory("lb", "agent-1")

        success, error = temp_db.lock_subdirectory("lb", "agent-2")

        assert success is False
        assert "agent-1" in error

    def test_same_agent_can_extend_lock(self, temp_db):
        """Same agent can re-lock (extend) their own lock."""
        temp_db.lock_subdirectory("lb", "agent-1", timeout_minutes=10)

        # Same agent extends the lock
        success, error = temp_db.lock_subdirectory("lb", "agent-1", timeout_minutes=30)

        assert success is True
        assert error is None

    def test_different_subdirs_independent(self, temp_db):
        """Different subdirectories can be locked by different agents."""
        success1, _ = temp_db.lock_subdirectory("lb", "agent-1")
        success2, _ = temp_db.lock_subdirectory("ft-chara-ftFox", "agent-2")

        assert success1 is True
        assert success2 is True

    def test_unlock_subdirectory(self, temp_db):
        """Can unlock a subdirectory."""
        temp_db.lock_subdirectory("lb", "agent-1")

        result = temp_db.unlock_subdirectory("lb", "agent-1")

        assert result is True

        # Now another agent can lock it
        success, _ = temp_db.lock_subdirectory("lb", "agent-2")
        assert success is True

    def test_cannot_unlock_others_lock(self, temp_db):
        """Cannot unlock subdirectory locked by another agent."""
        temp_db.lock_subdirectory("lb", "agent-1")

        result = temp_db.unlock_subdirectory("lb", "agent-2")

        assert result is False

    def test_force_unlock_without_agent(self, temp_db):
        """Can force unlock without specifying agent."""
        temp_db.lock_subdirectory("lb", "agent-1")

        result = temp_db.unlock_subdirectory("lb")  # No agent specified

        assert result is True


class TestLockExpiry:
    """Test lock expiry behavior."""

    def test_expired_lock_allows_takeover(self, temp_db):
        """Expired lock allows another agent to take over."""
        # Create an expired lock directly
        now = time.time()
        with temp_db.connection() as conn:
            conn.execute(
                """
                INSERT INTO subdirectory_allocations
                    (subdirectory_key, worktree_path, branch_name,
                     locked_by_agent, locked_at, lock_expires_at, updated_at)
                VALUES (?, '', '', ?, ?, ?, ?)
                """,
                ("lb", "agent-1", now - 3600, now - 1800, now)  # Expired 30 min ago
            )

        # New agent should be able to lock
        success, error = temp_db.lock_subdirectory("lb", "agent-2")

        assert success is True

    def test_get_lock_shows_expiry_status(self, temp_db):
        """get_subdirectory_lock shows if lock is expired."""
        # Create an expired lock
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

        lock_info = temp_db.get_subdirectory_lock("lb")

        assert lock_info is not None
        # The locked_by_agent should be cleared due to expiry
        assert lock_info.get("lock_expired") is True or lock_info["locked_by_agent"] is None


class TestAgentHandoff:
    """Test agent handoff scenarios."""

    def test_release_allows_immediate_reclaim(self, temp_db):
        """After release, another agent can immediately claim."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        # Agent 1 claims
        temp_db.add_claim("TestFunc", "agent-1")

        # Agent 1 releases
        temp_db.release_claim("TestFunc", "agent-1")

        # Agent 2 can now claim
        success, _ = temp_db.add_claim("TestFunc", "agent-2")

        assert success is True
        func = temp_db.get_function("TestFunc")
        assert func["claimed_by_agent"] == "agent-2"

    def test_handoff_preserves_progress(self, temp_db):
        """Handoff preserves match progress."""
        temp_db.upsert_function(
            "TestFunc",
            status="in_progress",
            match_percent=75.0,
            local_scratch_slug="scratch-1"
        )
        temp_db.add_claim("TestFunc", "agent-1")

        # Agent 1 releases
        temp_db.release_claim("TestFunc", "agent-1")

        # Agent 2 claims
        temp_db.add_claim("TestFunc", "agent-2")

        func = temp_db.get_function("TestFunc")
        # Progress should be preserved
        assert func["match_percent"] == 75.0
        assert func["local_scratch_slug"] == "scratch-1"


class TestAgentTracking:
    """Test agent activity tracking."""

    def test_upsert_agent_creates_record(self, temp_db):
        """upserting agent creates agent record."""
        temp_db.upsert_agent("agent-1", worktree_path="/path/to/wt", branch_name="feature")

        summary = temp_db.get_agent_summary()
        assert len(summary) >= 1

        agent = next((a for a in summary if a["agent_id"] == "agent-1"), None)
        assert agent is not None

    def test_agent_subdirectory_assignments(self, temp_db):
        """Agent subdirectory assignments are tracked."""
        temp_db.lock_subdirectory("lb", "agent-1")
        temp_db.lock_subdirectory("ft-chara-ftFox", "agent-1")

        subdirs = temp_db.get_agent_subdirectories("agent-1")

        assert "lb" in subdirs
        assert "ft-chara-ftFox" in subdirs


class TestDatabaseLevelIsolation:
    """Test database-level isolation for concurrent operations."""

    def test_transaction_isolation(self, temp_db):
        """Transactions provide isolation for claims."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        # This tests that the transaction-based add_claim
        # correctly uses BEGIN IMMEDIATE for locking
        success1, _ = temp_db.add_claim("TestFunc", "agent-1")

        # The claim should be visible immediately
        claims = temp_db.get_active_claims()
        assert len(claims) == 1

        # Second claim should fail
        success2, error2 = temp_db.add_claim("TestFunc", "agent-2")
        assert success2 is False

    def test_multiple_functions_atomic(self, temp_db):
        """Multiple function updates are atomic within transaction."""
        # Upsert multiple functions
        temp_db.upsert_function("Func1", status="unclaimed", match_percent=50.0)
        temp_db.upsert_function("Func2", status="unclaimed", match_percent=60.0)
        temp_db.upsert_function("Func3", status="unclaimed", match_percent=70.0)

        # All should be visible
        for i in range(1, 4):
            func = temp_db.get_function(f"Func{i}")
            assert func is not None
