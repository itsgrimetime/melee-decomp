"""Test worktree lock/unlock/list/status commands.

These tests verify the worktree management operations for subdirectory isolation.
"""

import time

import pytest


class TestWorktreeList:
    """Test worktree list operations."""

    def test_list_empty_when_no_worktrees(self, temp_db):
        """worktree list shows empty when no worktrees allocated."""
        status = temp_db.get_subdirectory_status()

        assert isinstance(status, list)
        assert len(status) == 0

    def test_list_shows_allocated_worktrees(self, temp_db):
        """worktree list shows all allocated worktrees."""
        temp_db.upsert_subdirectory("lb", "/path/wt-lb", "branch-lb")
        temp_db.upsert_subdirectory("ft-chara-ftFox", "/path/wt-fox", "branch-fox")
        temp_db.upsert_subdirectory("gr", "/path/wt-gr", "branch-gr")

        status = temp_db.get_subdirectory_status()

        assert len(status) == 3
        keys = [s["subdirectory_key"] for s in status]
        assert "lb" in keys
        assert "ft-chara-ftFox" in keys
        assert "gr" in keys


class TestWorktreeLock:
    """Test worktree lock operations."""

    def test_lock_new_subdirectory(self, temp_db):
        """Can lock a new subdirectory."""
        success, error = temp_db.lock_subdirectory("lb", "agent-1")

        assert success is True
        assert error is None

        lock = temp_db.get_subdirectory_lock("lb")
        assert lock is not None
        assert lock["locked_by_agent"] == "agent-1"

    def test_lock_sets_expiry(self, temp_db):
        """Lock sets an expiry time."""
        temp_db.lock_subdirectory("lb", "agent-1", timeout_minutes=30)

        lock = temp_db.get_subdirectory_lock("lb")

        assert lock["lock_expires_at"] is not None
        # Should expire in ~30 minutes
        time_until_expiry = lock["lock_expires_at"] - time.time()
        assert 25 * 60 < time_until_expiry < 31 * 60

    def test_lock_prevents_other_agent(self, temp_db):
        """Locked subdirectory cannot be locked by another agent."""
        temp_db.lock_subdirectory("lb", "agent-1")

        success, error = temp_db.lock_subdirectory("lb", "agent-2")

        assert success is False
        assert "agent-1" in error

    def test_lock_extend_by_same_agent(self, temp_db):
        """Same agent can extend their lock."""
        temp_db.lock_subdirectory("lb", "agent-1", timeout_minutes=10)

        original_lock = temp_db.get_subdirectory_lock("lb")
        original_expiry = original_lock["lock_expires_at"]

        # Wait a bit then extend
        time.sleep(0.1)
        success, _ = temp_db.lock_subdirectory("lb", "agent-1", timeout_minutes=30)

        assert success is True

        new_lock = temp_db.get_subdirectory_lock("lb")
        assert new_lock["lock_expires_at"] > original_expiry


class TestWorktreeUnlock:
    """Test worktree unlock operations."""

    def test_unlock_own_lock(self, temp_db):
        """Agent can unlock their own lock."""
        temp_db.lock_subdirectory("lb", "agent-1")

        result = temp_db.unlock_subdirectory("lb", "agent-1")

        assert result is True

        lock = temp_db.get_subdirectory_lock("lb")
        assert lock["locked_by_agent"] is None

    def test_unlock_allows_new_lock(self, temp_db):
        """After unlock, another agent can lock."""
        temp_db.lock_subdirectory("lb", "agent-1")
        temp_db.unlock_subdirectory("lb", "agent-1")

        success, _ = temp_db.lock_subdirectory("lb", "agent-2")

        assert success is True

    def test_cannot_unlock_other_agents_lock(self, temp_db):
        """Cannot unlock another agent's lock."""
        temp_db.lock_subdirectory("lb", "agent-1")

        result = temp_db.unlock_subdirectory("lb", "agent-2")

        assert result is False

        # Lock should still be held
        lock = temp_db.get_subdirectory_lock("lb")
        assert lock["locked_by_agent"] == "agent-1"

    def test_force_unlock_without_agent(self, temp_db):
        """Can force unlock without specifying agent."""
        temp_db.lock_subdirectory("lb", "agent-1")

        result = temp_db.unlock_subdirectory("lb")  # No agent

        assert result is True

        lock = temp_db.get_subdirectory_lock("lb")
        assert lock["locked_by_agent"] is None


class TestWorktreeStatus:
    """Test worktree status operations."""

    def test_status_shows_lock_info(self, temp_db):
        """Status shows lock information."""
        temp_db.upsert_subdirectory("lb", "/path/wt-lb", "branch-lb")
        temp_db.lock_subdirectory("lb", "agent-1")

        status = temp_db.get_subdirectory_status()

        lb_status = next(s for s in status if s["subdirectory_key"] == "lb")
        assert lb_status["locked_by_agent"] == "agent-1"

    def test_status_shows_branch_info(self, temp_db):
        """Status shows branch information."""
        temp_db.upsert_subdirectory("lb", "/path/wt-lb", "feature-branch")

        status = temp_db.get_subdirectory_status()

        lb_status = next(s for s in status if s["subdirectory_key"] == "lb")
        assert lb_status["branch_name"] == "feature-branch"

    def test_status_shows_worktree_path(self, temp_db):
        """Status shows worktree path."""
        temp_db.upsert_subdirectory("lb", "/full/path/to/wt-lb", "main")

        status = temp_db.get_subdirectory_status()

        lb_status = next(s for s in status if s["subdirectory_key"] == "lb")
        assert lb_status["worktree_path"] == "/full/path/to/wt-lb"


class TestWorktreePendingCommits:
    """Test pending commits tracking."""

    def test_increment_pending_commits(self, temp_db):
        """Can increment pending commits count."""
        temp_db.upsert_subdirectory("lb", "/path/wt-lb", "main")

        temp_db.increment_pending_commits("lb")
        temp_db.increment_pending_commits("lb")
        temp_db.increment_pending_commits("lb")

        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT pending_commits FROM subdirectory_allocations WHERE subdirectory_key = ?",
                ("lb",)
            )
            row = cursor.fetchone()

        assert row["pending_commits"] == 3

    def test_reset_pending_commits(self, temp_db):
        """Can reset pending commits count."""
        temp_db.upsert_subdirectory("lb", "/path/wt-lb", "main")
        temp_db.increment_pending_commits("lb")
        temp_db.increment_pending_commits("lb")

        temp_db.reset_pending_commits("lb")

        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT pending_commits FROM subdirectory_allocations WHERE subdirectory_key = ?",
                ("lb",)
            )
            row = cursor.fetchone()

        assert row["pending_commits"] == 0

    def test_increment_sets_last_commit_time(self, temp_db):
        """Incrementing pending commits sets last_commit_at."""
        temp_db.upsert_subdirectory("lb", "/path/wt-lb", "main")

        before = time.time()
        temp_db.increment_pending_commits("lb")
        after = time.time()

        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT last_commit_at FROM subdirectory_allocations WHERE subdirectory_key = ?",
                ("lb",)
            )
            row = cursor.fetchone()

        # Allow small tolerance for subsecond timing
        assert before - 0.1 <= row["last_commit_at"] <= after + 0.1


class TestLockExpiry:
    """Test lock expiry behavior."""

    def test_expired_lock_reported_as_expired(self, temp_db):
        """Expired lock is reported as expired."""
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

        lock = temp_db.get_subdirectory_lock("lb")

        # Should show lock as expired
        assert lock.get("lock_expired") is True or lock["locked_by_agent"] is None

    def test_expired_lock_allows_takeover(self, temp_db):
        """Expired lock allows another agent to take over."""
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

        # Another agent should be able to lock
        success, error = temp_db.lock_subdirectory("lb", "agent-2")

        assert success is True
        lock = temp_db.get_subdirectory_lock("lb")
        assert lock["locked_by_agent"] == "agent-2"


class TestAgentSubdirectoryAssignments:
    """Test agent to subdirectory assignments."""

    def test_lock_creates_assignment(self, temp_db):
        """Locking creates agent-subdirectory assignment."""
        temp_db.lock_subdirectory("lb", "agent-1")

        subdirs = temp_db.get_agent_subdirectories("agent-1")

        assert "lb" in subdirs

    def test_multiple_subdirs_assigned(self, temp_db):
        """Agent can be assigned multiple subdirectories."""
        temp_db.lock_subdirectory("lb", "agent-1")
        temp_db.lock_subdirectory("ft-chara-ftFox", "agent-1")
        temp_db.lock_subdirectory("gr", "agent-1")

        subdirs = temp_db.get_agent_subdirectories("agent-1")

        assert len(subdirs) == 3
        assert "lb" in subdirs
        assert "ft-chara-ftFox" in subdirs
        assert "gr" in subdirs

    def test_assignments_persist_after_unlock(self, temp_db):
        """Assignments persist even after unlock (for tracking)."""
        temp_db.lock_subdirectory("lb", "agent-1")
        temp_db.unlock_subdirectory("lb", "agent-1")

        subdirs = temp_db.get_agent_subdirectories("agent-1")

        # Assignment is still recorded even after unlock
        assert "lb" in subdirs
