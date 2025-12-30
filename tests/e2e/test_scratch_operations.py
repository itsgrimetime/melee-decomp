"""Test scratch create/compile/get operations with mocked API.

These tests verify scratch operations work correctly with the mocked decomp.me server.
"""

import pytest


class TestScratchCreate:
    """Test scratch creation operations."""

    def test_mock_server_creates_scratch(self, mock_decomp_server):
        """Mock server can create a scratch."""
        import httpx

        response = httpx.post(
            f"{mock_decomp_server['base_url']}/api/scratch",
            json={
                "name": "TestFunction",
                "compiler": "mwcc_247_92",
                "platform": "gc_wii",
                "source_code": "void TestFunction(void) {}",
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "slug" in data
        assert data["name"] == "TestFunction"
        assert data["claim_token"] is not None

    def test_scratch_stored_in_mock(self, mock_decomp_server):
        """Created scratch is stored in mock server."""
        import httpx

        # Create
        create_resp = httpx.post(
            f"{mock_decomp_server['base_url']}/api/scratch",
            json={"name": "TestFunction"}
        )
        slug = create_resp.json()["slug"]

        # Verify stored
        assert slug in mock_decomp_server["scratches"]
        assert mock_decomp_server["scratches"][slug]["name"] == "TestFunction"


class TestScratchGet:
    """Test scratch retrieval operations."""

    def test_can_get_created_scratch(self, mock_decomp_server):
        """Can retrieve a created scratch."""
        import httpx

        # Create
        create_resp = httpx.post(
            f"{mock_decomp_server['base_url']}/api/scratch",
            json={"name": "TestFunction"}
        )
        slug = create_resp.json()["slug"]

        # Get
        get_resp = httpx.get(f"{mock_decomp_server['base_url']}/api/scratch/{slug}")

        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["slug"] == slug
        assert data["name"] == "TestFunction"

    def test_get_nonexistent_returns_404(self, mock_decomp_server):
        """Getting nonexistent scratch returns 404."""
        import httpx

        response = httpx.get(
            f"{mock_decomp_server['base_url']}/api/scratch/nonexistent-slug"
        )

        assert response.status_code == 404


class TestScratchCompile:
    """Test scratch compilation operations."""

    def test_compile_updates_source(self, mock_decomp_server):
        """Compiling updates the scratch source code."""
        import httpx

        # Create
        create_resp = httpx.post(
            f"{mock_decomp_server['base_url']}/api/scratch",
            json={"name": "TestFunction", "source_code": "// original"}
        )
        slug = create_resp.json()["slug"]

        # Compile with new source
        compile_resp = httpx.post(
            f"{mock_decomp_server['base_url']}/api/scratch/{slug}/compile",
            json={"source_code": "// updated code"}
        )

        assert compile_resp.status_code == 200
        assert mock_decomp_server["scratches"][slug]["source_code"] == "// updated code"

    def test_compile_returns_score(self, mock_decomp_server):
        """Compile response includes score information."""
        import httpx

        # Create
        create_resp = httpx.post(
            f"{mock_decomp_server['base_url']}/api/scratch",
            json={"name": "TestFunction"}
        )
        slug = create_resp.json()["slug"]

        # Compile
        compile_resp = httpx.post(
            f"{mock_decomp_server['base_url']}/api/scratch/{slug}/compile",
            json={"source_code": "void TestFunction(void) {}"}
        )

        data = compile_resp.json()
        assert "score" in data or "diff_output" in data
        assert data.get("success") is True

    def test_set_match_helper(self, mock_decomp_server):
        """Can use set_match helper to simulate match improvement."""
        import httpx

        # Create
        create_resp = httpx.post(
            f"{mock_decomp_server['base_url']}/api/scratch",
            json={"name": "TestFunction"}
        )
        slug = create_resp.json()["slug"]

        # Use helper to set perfect match
        mock_decomp_server["set_match"](slug, score=0, max_score=100)

        # Verify
        scratch = mock_decomp_server["scratches"][slug]
        assert scratch["score"] == 0
        assert scratch["match_percent"] == 100.0


class TestDatabaseScratchTracking:
    """Test that scratch operations are tracked in database."""

    def test_upsert_scratch_creates_record(self, temp_db):
        """upserting scratch creates database record."""
        temp_db.upsert_scratch(
            slug="test-scratch-1",
            instance="local",
            base_url="http://localhost:8000",
            function_name="TestFunction",
            score=50,
            max_score=100
        )

        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM scratches WHERE slug = ?",
                ("test-scratch-1",)
            )
            scratch = dict(cursor.fetchone())

        assert scratch["slug"] == "test-scratch-1"
        assert scratch["function_name"] == "TestFunction"
        assert scratch["instance"] == "local"

    def test_scratch_links_to_function(self, temp_db):
        """Scratch can be linked to a function."""
        temp_db.upsert_function(
            "TestFunction",
            status="in_progress",
            local_scratch_slug="test-scratch-1"
        )

        temp_db.upsert_scratch(
            slug="test-scratch-1",
            instance="local",
            base_url="http://localhost:8000",
            function_name="TestFunction"
        )

        func = temp_db.get_function("TestFunction")
        assert func["local_scratch_slug"] == "test-scratch-1"


class TestMatchHistory:
    """Test match score history tracking."""

    def test_record_multiple_scores(self, temp_db):
        """Can record multiple match scores over time."""
        temp_db.upsert_scratch(
            slug="test-scratch",
            instance="local",
            base_url="http://localhost:8000"
        )

        # Record improving scores
        temp_db.record_match_score("test-scratch", score=100, max_score=100)  # 0%
        temp_db.record_match_score("test-scratch", score=50, max_score=100)   # 50%
        temp_db.record_match_score("test-scratch", score=10, max_score=100)   # 90%
        temp_db.record_match_score("test-scratch", score=0, max_score=100)    # 100%

        with temp_db.connection() as conn:
            cursor = conn.execute(
                """
                SELECT score, match_percent FROM match_history
                WHERE scratch_slug = ?
                ORDER BY timestamp
                """,
                ("test-scratch",)
            )
            history = [dict(row) for row in cursor.fetchall()]

        assert len(history) == 4
        assert history[0]["score"] == 100
        assert history[3]["score"] == 0
        assert history[3]["match_percent"] == 100.0

    def test_score_updates_scratch_record(self, temp_db):
        """Recording score updates the scratch record."""
        temp_db.upsert_scratch(
            slug="test-scratch",
            instance="local",
            base_url="http://localhost:8000"
        )

        temp_db.record_match_score("test-scratch", score=25, max_score=100)

        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT score, max_score, match_percent FROM scratches WHERE slug = ?",
                ("test-scratch",)
            )
            scratch = dict(cursor.fetchone())

        assert scratch["score"] == 25
        assert scratch["max_score"] == 100
        assert scratch["match_percent"] == 75.0  # 1 - 25/100 = 0.75

    def test_duplicate_scores_not_recorded(self, temp_db):
        """Same score recorded twice only creates one history entry."""
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

        assert count == 1


class TestSyncState:
    """Test local to production sync state tracking."""

    def test_record_sync(self, temp_db):
        """Can record a sync between local and production."""
        temp_db.record_sync(
            local_slug="local-scratch",
            production_slug="prod-scratch",
            function_name="TestFunction"
        )

        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM sync_state WHERE local_slug = ?",
                ("local-scratch",)
            )
            sync = dict(cursor.fetchone())

        assert sync["production_slug"] == "prod-scratch"
        assert sync["function_name"] == "TestFunction"

    def test_sync_updates_function(self, temp_db):
        """Recording sync updates function's production slug."""
        temp_db.upsert_function("TestFunction", status="in_progress")

        temp_db.record_sync(
            local_slug="local-scratch",
            production_slug="prod-scratch",
            function_name="TestFunction"
        )

        func = temp_db.get_function("TestFunction")
        assert func["production_scratch_slug"] == "prod-scratch"
