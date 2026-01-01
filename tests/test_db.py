"""Tests for the state database.

The database is the source of truth for:
- Function claims (who's working on what)
- Match progress (scratch slugs, match percentages)
- Subdirectory locks (worktree isolation)
- Audit history

These tests use an in-memory database to avoid filesystem side effects.
"""

import time
import pytest
from pathlib import Path


@pytest.fixture
def db(tmp_path):
    """Create a fresh database for each test."""
    from src.db import StateDB, reset_db
    # Reset any global DB instance that may have been initialized by other tests
    reset_db()
    db_path = tmp_path / "test_state.db"
    db = StateDB(db_path)
    yield db
    db.close()
    reset_db()  # Clean up after test


class TestClaims:
    """Tests for function claim management.

    Claims prevent multiple agents from working on the same function.
    They have expiration times and ownership tracking.
    """

    def test_add_claim_success(self, db):
        """Adding a new claim should succeed."""
        success, error = db.add_claim("my_func", "agent-1")
        assert success is True
        assert error is None

    def test_add_claim_same_agent_fails(self, db):
        """Same agent claiming twice should fail."""
        db.add_claim("my_func", "agent-1")
        success, error = db.add_claim("my_func", "agent-1")

        assert success is False
        assert "Already claimed by you" in error

    def test_add_claim_different_agent_fails(self, db):
        """Different agent claiming same function should fail."""
        db.add_claim("my_func", "agent-1")
        success, error = db.add_claim("my_func", "agent-2")

        assert success is False
        assert "agent-1" in error

    def test_expired_claim_can_be_reclaimed(self, db):
        """Expired claims should allow reclaiming."""
        # Add claim that expires immediately
        db.add_claim("my_func", "agent-1", timeout_seconds=0)

        # Small delay to ensure expiration
        time.sleep(0.01)

        # Different agent should be able to claim
        success, error = db.add_claim("my_func", "agent-2")
        assert success is True

    def test_release_claim(self, db):
        """Released claims should allow reclaiming."""
        db.add_claim("my_func", "agent-1")
        released = db.release_claim("my_func")

        assert released is True

        # Should be able to reclaim
        success, _ = db.add_claim("my_func", "agent-2")
        assert success is True

    def test_release_nonexistent_claim(self, db):
        """Releasing nonexistent claim should return False."""
        released = db.release_claim("nonexistent_func")
        assert released is False

    def test_release_with_wrong_agent_id(self, db):
        """Releasing with wrong agent_id should fail."""
        db.add_claim("my_func", "agent-1")
        released = db.release_claim("my_func", agent_id="agent-2")

        assert released is False

    def test_get_active_claims(self, db):
        """Should list all active claims."""
        db.add_claim("func1", "agent-1")
        db.add_claim("func2", "agent-2")

        claims = db.get_active_claims()

        assert len(claims) == 2
        func_names = [c["function_name"] for c in claims]
        assert "func1" in func_names
        assert "func2" in func_names


class TestFunctionState:
    """Tests for function state tracking.

    Functions progress through states: claimed -> in_progress -> matched -> committed
    """

    def test_upsert_function_creates(self, db):
        """Upserting new function should create it."""
        db.upsert_function(
            function_name="my_func",
            status="in_progress",
            local_scratch_slug="ABC123",
            match_percent=45.5,
        )

        func = db.get_function("my_func")
        assert func is not None
        assert func["status"] == "in_progress"
        assert func["local_scratch_slug"] == "ABC123"
        assert func["match_percent"] == 45.5

    def test_upsert_function_updates(self, db):
        """Upserting existing function should update it."""
        db.upsert_function("my_func", status="in_progress", match_percent=50)
        db.upsert_function("my_func", status="matched", match_percent=100)

        func = db.get_function("my_func")
        assert func["status"] == "matched"
        assert func["match_percent"] == 100

    def test_get_nonexistent_function(self, db):
        """Getting nonexistent function should return None."""
        func = db.get_function("nonexistent")
        assert func is None

    def test_get_functions_by_status(self, db):
        """Should filter functions by status."""
        db.upsert_function("func1", status="in_progress")
        db.upsert_function("func2", status="matched")
        db.upsert_function("func3", status="in_progress")

        in_progress = db.get_functions_by_status("in_progress")
        assert len(in_progress) == 2

        matched = db.get_functions_by_status("matched")
        assert len(matched) == 1

    def test_get_uncommitted_matches(self, db):
        """Should return 95%+ matches not yet committed."""
        db.upsert_function("func1", status="matched", match_percent=100, is_committed=False)
        db.upsert_function("func2", status="matched", match_percent=95, is_committed=False)
        db.upsert_function("func3", status="matched", match_percent=94, is_committed=False)  # Too low
        db.upsert_function("func4", status="matched", match_percent=100, is_committed=True)  # Already committed

        uncommitted = db.get_uncommitted_matches()

        func_names = [f["function_name"] for f in uncommitted]
        assert "func1" in func_names
        assert "func2" in func_names
        assert "func3" not in func_names
        assert "func4" not in func_names


class TestSubdirectoryLocking:
    """Tests for subdirectory worktree locking.

    Each subdirectory (e.g., ft-chara-ftFox) can be locked by one agent
    to prevent conflicts when committing to worktrees.
    """

    def test_lock_subdirectory_success(self, db):
        """Locking unlocked subdirectory should succeed."""
        success, error = db.lock_subdirectory("lb", "agent-1")
        assert success is True
        assert error is None

    def test_lock_already_locked_by_same_agent(self, db):
        """Re-locking by same agent should succeed (idempotent)."""
        db.lock_subdirectory("lb", "agent-1")
        success, error = db.lock_subdirectory("lb", "agent-1")

        # Should succeed - same agent can re-lock
        assert success is True

    def test_lock_by_different_agent_fails(self, db):
        """Different agent locking same subdirectory should fail."""
        db.lock_subdirectory("lb", "agent-1")
        success, error = db.lock_subdirectory("lb", "agent-2")

        assert success is False
        assert "agent-1" in error

    def test_unlock_subdirectory(self, db):
        """Unlocking should allow other agents to lock."""
        db.lock_subdirectory("lb", "agent-1")
        db.unlock_subdirectory("lb", "agent-1")

        success, _ = db.lock_subdirectory("lb", "agent-2")
        assert success is True

    def test_unlock_by_wrong_agent_fails(self, db):
        """Unlocking by non-owner should fail."""
        db.lock_subdirectory("lb", "agent-1")
        success = db.unlock_subdirectory("lb", "agent-2")

        assert success is False

    def test_get_subdirectory_lock(self, db):
        """Should return lock info for locked subdirectory."""
        db.lock_subdirectory("lb", "agent-1")

        lock = db.get_subdirectory_lock("lb")
        assert lock is not None
        assert lock["locked_by_agent"] == "agent-1"

    def test_get_subdirectory_lock_unlocked(self, db):
        """Should return None for unlocked/nonexistent subdirectory."""
        lock = db.get_subdirectory_lock("nonexistent")
        assert lock is None

    def test_get_agent_subdirectories(self, db):
        """Should list all subdirectories locked by an agent."""
        db.lock_subdirectory("lb", "agent-1")
        db.lock_subdirectory("gr", "agent-1")
        db.lock_subdirectory("it", "agent-2")

        agent1_dirs = db.get_agent_subdirectories("agent-1")
        assert set(agent1_dirs) == {"lb", "gr"}


class TestMatchScoring:
    """Tests for match score tracking.

    Tracks the history of match percentages for each scratch.
    """

    def test_record_match_score(self, db):
        """Recording score should create history entry."""
        # First create the scratch
        db.upsert_scratch("ABC123", "local", "http://localhost:8000")

        # Record a score (score=50, max=100 means 50% match)
        db.record_match_score("ABC123", 50, 100)

        # Check history was recorded
        with db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM match_history WHERE scratch_slug = ?", ("ABC123",)
            )
            history = cursor.fetchall()

        assert len(history) == 1
        assert history[0]["score"] == 50

    def test_record_higher_score_adds_history(self, db):
        """Each score change should add to history."""
        db.upsert_scratch("ABC123", "local", "http://localhost:8000")

        db.record_match_score("ABC123", 50, 100)  # 50% match
        db.record_match_score("ABC123", 25, 100)  # 75% match (lower score = better)

        with db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM match_history WHERE scratch_slug = ? ORDER BY timestamp",
                ("ABC123",)
            )
            history = cursor.fetchall()

        assert len(history) == 2

    def test_duplicate_score_not_recorded(self, db):
        """Same score shouldn't create duplicate history entries."""
        db.upsert_scratch("ABC123", "local", "http://localhost:8000")

        db.record_match_score("ABC123", 50, 100)
        db.record_match_score("ABC123", 50, 100)  # Same score

        with db.connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM match_history WHERE scratch_slug = ?",
                ("ABC123",)
            )
            count = cursor.fetchone()["count"]

        assert count == 1


class TestAuditLog:
    """Tests for audit logging.

    All state changes should be logged for debugging and history.
    """

    def test_log_audit_creates_entry(self, db):
        """Logging should create audit entry."""
        db.log_audit(
            entity_type="function",
            entity_id="my_func",
            action="test_action",
            agent_id="agent-1",
        )

        with db.connection() as conn:
            cursor = conn.execute("SELECT * FROM audit_log")
            entries = cursor.fetchall()

        assert len(entries) == 1
        assert entries[0]["action"] == "test_action"
        assert entries[0]["entity_id"] == "my_func"

    def test_claim_creates_audit_entry(self, db):
        """Adding claim should create audit entry."""
        db.add_claim("my_func", "agent-1")

        with db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM audit_log WHERE action = 'created' AND entity_type = 'claim'"
            )
            entries = cursor.fetchall()

        assert len(entries) >= 1


class TestDatabaseIntegrity:
    """Tests for database schema and integrity."""

    def test_database_file_created(self, tmp_path):
        """Database file should be created on init."""
        from src.db import StateDB

        db_path = tmp_path / "new_db.db"
        assert not db_path.exists()

        db = StateDB(db_path)
        assert db_path.exists()
        db.close()

    def test_transaction_rollback_on_error(self, db):
        """Failed transactions should rollback."""
        # Add a claim
        db.add_claim("my_func", "agent-1")

        # Try to do something that will fail inside a transaction
        try:
            with db.transaction() as conn:
                conn.execute(
                    "DELETE FROM claims WHERE function_name = ?",
                    ("my_func",)
                )
                # Force an error
                raise ValueError("Simulated error")
        except ValueError:
            pass

        # Claim should still exist (rollback happened)
        claims = db.get_active_claims()
        assert len(claims) == 1

    def test_concurrent_access_safety(self, tmp_path):
        """Multiple DB instances should handle locking."""
        from src.db import StateDB

        db_path = tmp_path / "shared.db"
        db1 = StateDB(db_path)
        db2 = StateDB(db_path)

        # Both should be able to operate
        db1.add_claim("func1", "agent-1")
        db2.add_claim("func2", "agent-2")

        # Both should see all claims
        claims1 = db1.get_active_claims()
        claims2 = db2.get_active_claims()

        assert len(claims1) == 2
        assert len(claims2) == 2

        db1.close()
        db2.close()
