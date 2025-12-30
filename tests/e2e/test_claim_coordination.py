"""Test single-agent claim/release operations.

These tests verify the claim mechanics work correctly for individual agents.
"""

import time

import pytest


class TestClaimAdd:
    """Test claim add operations."""

    def test_claim_unclaimed_function_succeeds(self, temp_db):
        """Can claim an unclaimed function."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        success, error = temp_db.add_claim("TestFunc", "agent-1")

        assert success is True
        assert error is None

    def test_claim_updates_function_status(self, temp_db):
        """Claiming updates function status to claimed."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        func = temp_db.get_function("TestFunc")
        assert func["status"] == "claimed"
        assert func["claimed_by_agent"] == "agent-1"

    def test_claim_sets_claimed_at(self, temp_db):
        """Claiming sets claimed_at timestamp."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        before = time.time()
        temp_db.add_claim("TestFunc", "agent-1")
        after = time.time()

        func = temp_db.get_function("TestFunc")
        assert func["claimed_at"] is not None
        assert before <= func["claimed_at"] <= after

    def test_claim_creates_claim_record(self, temp_db):
        """Claiming creates a record in claims table."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        claims = temp_db.get_active_claims()
        assert len(claims) == 1
        assert claims[0]["function_name"] == "TestFunc"
        assert claims[0]["agent_id"] == "agent-1"

    def test_claim_has_expiry(self, temp_db):
        """Claims have an expiry timestamp."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1", timeout_seconds=3600)

        claims = temp_db.get_active_claims()
        assert len(claims) == 1
        # Should have ~60 minutes remaining (use >= for boundary tolerance)
        assert 55 <= claims[0]["minutes_remaining"] <= 61

    def test_reclaim_by_same_agent_fails(self, temp_db):
        """Cannot reclaim function already claimed by same agent."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        success, error = temp_db.add_claim("TestFunc", "agent-1")

        assert success is False
        assert "agent-1" in error

    def test_claim_nonexistent_function_creates_it(self, temp_db):
        """Claiming a nonexistent function creates the record."""
        success, error = temp_db.add_claim("NewFunc", "agent-1")

        assert success is True
        func = temp_db.get_function("NewFunc")
        assert func is not None
        assert func["status"] == "claimed"


class TestClaimRelease:
    """Test claim release operations."""

    def test_release_claimed_function_succeeds(self, temp_db):
        """Can release a claimed function."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        result = temp_db.release_claim("TestFunc", "agent-1")

        assert result is True

    def test_release_removes_claim_record(self, temp_db):
        """Releasing removes the claim from claims table."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        assert len(temp_db.get_active_claims()) == 1

        temp_db.release_claim("TestFunc", "agent-1")

        assert len(temp_db.get_active_claims()) == 0

    def test_release_resets_status_to_unclaimed(self, temp_db):
        """Releasing resets function status to unclaimed."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        assert temp_db.get_function("TestFunc")["status"] == "claimed"

        temp_db.release_claim("TestFunc", "agent-1")

        assert temp_db.get_function("TestFunc")["status"] == "unclaimed"

    def test_release_unclaimed_returns_false(self, temp_db):
        """Releasing unclaimed function returns false."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        result = temp_db.release_claim("TestFunc")

        assert result is False

    def test_release_by_different_agent_fails(self, temp_db):
        """Cannot release claim owned by different agent."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        result = temp_db.release_claim("TestFunc", "agent-2")

        assert result is False
        # Claim should still exist
        assert len(temp_db.get_active_claims()) == 1

    def test_release_without_agent_id_succeeds(self, temp_db):
        """Can release claim without specifying agent (force release)."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1")

        result = temp_db.release_claim("TestFunc")

        assert result is True
        assert len(temp_db.get_active_claims()) == 0


class TestClaimExpiry:
    """Test claim expiry behavior."""

    def test_expired_claim_allows_new_claim(self, temp_db):
        """Can claim function with expired claim."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        # Create a claim that's already expired
        with temp_db.transaction() as conn:
            now = time.time()
            conn.execute(
                """
                INSERT INTO claims (function_name, agent_id, claimed_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                ("TestFunc", "agent-1", now - 7200, now - 3600)  # Expired 1 hour ago
            )

        # New agent should be able to claim
        success, error = temp_db.add_claim("TestFunc", "agent-2")

        assert success is True
        claims = temp_db.get_active_claims()
        assert len(claims) == 1
        assert claims[0]["agent_id"] == "agent-2"

    def test_active_claims_excludes_expired(self, temp_db):
        """get_active_claims excludes expired claims."""
        temp_db.upsert_function("TestFunc", status="unclaimed")

        # Create an expired claim directly
        with temp_db.transaction() as conn:
            now = time.time()
            conn.execute(
                """
                INSERT INTO claims (function_name, agent_id, claimed_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                ("TestFunc", "agent-1", now - 7200, now - 3600)
            )

        claims = temp_db.get_active_claims()
        assert len(claims) == 0


class TestClaimList:
    """Test listing claims."""

    def test_list_shows_all_active_claims(self, temp_db):
        """claim list shows all active claims."""
        for i in range(3):
            temp_db.upsert_function(f"Func{i}", status="unclaimed")
            temp_db.add_claim(f"Func{i}", f"agent-{i}")

        claims = temp_db.get_active_claims()
        assert len(claims) == 3

    def test_list_shows_time_remaining(self, temp_db):
        """Claims show time remaining."""
        temp_db.upsert_function("TestFunc", status="unclaimed")
        temp_db.add_claim("TestFunc", "agent-1", timeout_seconds=1800)  # 30 min

        claims = temp_db.get_active_claims()
        assert len(claims) == 1
        # Should have ~30 minutes remaining (use >= for boundary tolerance)
        assert 25 <= claims[0]["minutes_remaining"] <= 31

    def test_list_includes_match_info(self, temp_db):
        """Claims include function match info."""
        temp_db.upsert_function(
            "TestFunc",
            status="unclaimed",
            match_percent=75.5,
            local_scratch_slug="test-scratch"
        )
        temp_db.add_claim("TestFunc", "agent-1")

        claims = temp_db.get_active_claims()
        assert len(claims) == 1
        assert claims[0]["match_percent"] == 75.5
        assert claims[0]["local_scratch_slug"] == "test-scratch"
