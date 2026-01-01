"""Tests for the decomp.me API client.

These tests require a running decomp.me backend.
The URL is auto-detected from LOCAL_DECOMP_CANDIDATES.
Run with: pytest tests/test_client.py -v
"""

import pytest

from src.client import (
    CompileRequest,
    DecompMeAPIClient,
    DecompMeAPIError,
    ForkRequest,
    ScratchCreate,
    ScratchManager,
    ScratchUpdate,
)
from src.cli._common import detect_local_api_url


# Simple test assembly for a function that returns 0
TEST_ASM = """
glabel test_func
/* 00000000 00000000  38 60 00 00 */  li r3, 0
/* 00000004 00000004  4E 80 00 20 */  blr
""".strip()


@pytest.fixture
async def client():
    """Create an API client for testing."""
    api_url = detect_local_api_url()
    if not api_url:
        pytest.skip("No decomp.me server available")
    async with DecompMeAPIClient(api_url) as c:
        yield c


@pytest.fixture
async def manager(client):
    """Create a scratch manager for testing."""
    return ScratchManager(client)


class TestDecompMeAPIClient:
    """Test the low-level API client."""

    @pytest.mark.asyncio
    async def test_create_scratch(self, client):
        """Test creating a new scratch."""
        scratch = await client.create_scratch(
            ScratchCreate(
                name="Test Scratch",
                compiler="mwcc_247_92",
                compiler_flags="-O4,p -nodefaults",
                target_asm=TEST_ASM,
                diff_label="test_func",
                source_code="int test_func(void) { return 0; }",
            )
        )

        assert scratch.slug is not None
        assert scratch.name == "Test Scratch"
        assert scratch.compiler == "mwcc_247_92"
        assert scratch.claim_token is not None  # Present on creation

    @pytest.mark.asyncio
    async def test_get_scratch(self, client):
        """Test retrieving a scratch."""
        # Create scratch first
        created = await client.create_scratch(
            ScratchCreate(
                name="Get Test",
                target_asm=TEST_ASM,
                diff_label="test_func",
            )
        )

        # Retrieve it
        retrieved = await client.get_scratch(created.slug)
        assert retrieved.slug == created.slug
        assert retrieved.name == "Get Test"

    @pytest.mark.asyncio
    async def test_update_scratch(self, client):
        """Test updating a scratch."""
        # Create scratch
        scratch = await client.create_scratch(
            ScratchCreate(
                name="Update Test",
                target_asm=TEST_ASM,
                diff_label="test_func",
                source_code="int test_func(void) { return 0; }",
            )
        )

        # Claim ownership before updating (required for anonymous scratches)
        if scratch.claim_token:
            await client.claim_scratch(scratch.slug, scratch.claim_token)

        # Update source code
        updated = await client.update_scratch(
            scratch.slug,
            ScratchUpdate(
                source_code="int test_func(void) {\n    return 0;\n}",
            ),
        )

        assert updated.slug == scratch.slug
        assert "return 0" in updated.source_code

    @pytest.mark.asyncio
    async def test_compile_scratch(self, client):
        """Test compiling a scratch."""
        scratch = await client.create_scratch(
            ScratchCreate(
                name="Compile Test",
                target_asm=TEST_ASM,
                diff_label="test_func",
                source_code="int test_func(void) { return 0; }",
            )
        )

        result = await client.compile_scratch(scratch.slug, save_score=True)

        assert result.success is True
        assert result.compiler_output is not None
        # May or may not have diff_output depending on compilation

    @pytest.mark.asyncio
    async def test_compile_with_overrides(self, client):
        """Test compiling with temporary overrides."""
        scratch = await client.create_scratch(
            ScratchCreate(
                name="Override Test",
                target_asm=TEST_ASM,
                diff_label="test_func",
                source_code="int test_func(void) { return 0; }",
            )
        )

        # Compile with different source without saving
        result = await client.compile_scratch(
            scratch.slug,
            CompileRequest(source_code="int test_func(void) { return 1; }"),
            save_score=False,
        )

        assert result.success is True

        # Original source should be unchanged
        retrieved = await client.get_scratch(scratch.slug)
        assert "return 0" in retrieved.source_code

    @pytest.mark.asyncio
    async def test_fork_scratch(self, client):
        """Test forking a scratch."""
        original = await client.create_scratch(
            ScratchCreate(
                name="Original",
                target_asm=TEST_ASM,
                diff_label="test_func",
                source_code="int test_func(void) { return 0; }",
            )
        )

        fork = await client.fork_scratch(
            original.slug,
            ForkRequest(name="Forked Version"),
        )

        assert fork.slug != original.slug
        assert fork.name == "Forked Version"
        assert fork.parent == original.slug

    @pytest.mark.asyncio
    async def test_get_family(self, client):
        """Test getting scratch family."""
        scratch = await client.create_scratch(
            ScratchCreate(
                name="Family Test",
                target_asm=TEST_ASM,
                diff_label="test_func",
            )
        )

        family = await client.get_scratch_family(scratch.slug)
        assert len(family) >= 1
        assert any(s.slug == scratch.slug for s in family)

    @pytest.mark.asyncio
    async def test_list_compilers(self, client):
        """Test listing available compilers."""
        compilers = await client.list_compilers()
        assert len(compilers) > 0
        # Check for Melee compiler
        assert any(c.id == "mwcc_247_92" for c in compilers)

    @pytest.mark.asyncio
    async def test_list_presets(self, client):
        """Test listing available presets."""
        presets = await client.list_presets()
        assert isinstance(presets, list)

    @pytest.mark.asyncio
    async def test_list_scratches(self, client):
        """Test listing scratches."""
        # Create a scratch first
        await client.create_scratch(
            ScratchCreate(
                name="List Test",
                target_asm=TEST_ASM,
                diff_label="test_func",
            )
        )

        scratches = await client.list_scratches(page_size=10)
        assert len(scratches) > 0

    @pytest.mark.asyncio
    async def test_error_handling(self, client):
        """Test error handling for invalid requests."""
        with pytest.raises(DecompMeAPIError):
            await client.get_scratch("invalid-slug-that-does-not-exist")


class TestScratchManager:
    """Test the high-level scratch manager."""

    @pytest.mark.asyncio
    async def test_create_from_asm(self, manager):
        """Test creating from assembly."""
        scratch = await manager.create_from_asm(
            target_asm=TEST_ASM,
            function_name="test_func",
            name="Manager Test",
        )

        assert scratch.slug is not None
        assert scratch.name == "Manager Test"
        assert scratch.diff_label == "test_func"

    @pytest.mark.asyncio
    async def test_iterate_without_save(self, manager):
        """Test iterating without saving."""
        scratch = await manager.create_from_asm(
            target_asm=TEST_ASM,
            function_name="test_func",
            source_code="int test_func(void) { return 0; }",
        )

        # Try new source without saving
        result = await manager.iterate(
            scratch,
            "int test_func(void) { return 1; }",
            save=False,
        )

        # Should compile
        assert result.success or result.compiler_output

        # Original should be unchanged
        retrieved = await manager.client.get_scratch(scratch.slug)
        assert "return 0" in retrieved.source_code

    @pytest.mark.asyncio
    async def test_iterate_with_save(self, manager):
        """Test iterating with save."""
        scratch = await manager.create_from_asm(
            target_asm=TEST_ASM,
            function_name="test_func",
            source_code="int test_func(void) { return 0; }",
        )

        # Claim ownership before saving (required for anonymous scratches)
        if scratch.claim_token:
            await manager.client.claim_scratch(scratch.slug, scratch.claim_token)

        # Save new source
        await manager.iterate(
            scratch,
            "int test_func(void) { return 1; }",
            save=True,
        )

        # Should be updated
        retrieved = await manager.client.get_scratch(scratch.slug)
        assert "return 1" in retrieved.source_code

    @pytest.mark.asyncio
    async def test_get_current_score(self, manager):
        """Test getting current score."""
        scratch = await manager.create_from_asm(
            target_asm=TEST_ASM,
            function_name="test_func",
            source_code="int test_func(void) { return 0; }",
        )

        score, max_score = await manager.get_current_score(scratch.slug)
        assert isinstance(score, int)
        assert isinstance(max_score, int)

    @pytest.mark.asyncio
    async def test_batch_compile(self, manager):
        """Test batch compilation."""
        scratch = await manager.create_from_asm(
            target_asm=TEST_ASM,
            function_name="test_func",
        )

        variants = [
            "int test_func(void) { return 0; }",
            "int test_func(void) { int x = 0; return x; }",
        ]

        results = await manager.batch_compile(scratch, variants)
        assert len(results) == 2
        assert all(isinstance(r[1].success, bool) for r in results)

    @pytest.mark.asyncio
    async def test_fork_and_modify(self, manager):
        """Test forking and modifying."""
        original = await manager.create_from_asm(
            target_asm=TEST_ASM,
            function_name="test_func",
            source_code="int test_func(void) { return 0; }",
        )

        fork = await manager.fork_and_modify(
            original.slug,
            new_name="Modified Fork",
            new_source="int test_func(void) { return 1; }",
        )

        assert fork.slug != original.slug
        assert fork.name == "Modified Fork"
        assert "return 1" in fork.source_code

    @pytest.mark.asyncio
    async def test_compilation_result_properties(self, manager):
        """Test CompilationResult helper properties."""
        scratch = await manager.create_from_asm(
            target_asm=TEST_ASM,
            function_name="test_func",
            source_code="int test_func(void) { return 0; }",
        )

        result = await manager.compile_and_check(scratch)

        # Test properties
        assert isinstance(result.success, bool)
        if result.diff_output:
            assert result.score >= 0
            assert result.max_score >= 0
            assert isinstance(result.is_perfect, bool)


class TestModels:
    """Test Pydantic models."""

    def test_scratch_create_defaults(self):
        """Test ScratchCreate default values."""
        scratch = ScratchCreate(
            target_asm=TEST_ASM,
            diff_label="test_func",
        )

        assert scratch.compiler == "mwcc_247_92"
        assert scratch.compiler_flags == ""
        assert scratch.diff_flags == []
        assert scratch.libraries == []

    def test_scratch_update_optional(self):
        """Test ScratchUpdate with optional fields."""
        update = ScratchUpdate(name="New Name")
        assert update.name == "New Name"
        assert update.source_code is None

    def test_compile_request_defaults(self):
        """Test CompileRequest defaults."""
        req = CompileRequest()
        assert req.include_objects is False
        assert req.compiler is None


@pytest.mark.asyncio
async def test_context_manager():
    """Test using client as async context manager."""
    api_url = detect_local_api_url()
    if not api_url:
        pytest.skip("No decomp.me server available")
    async with DecompMeAPIClient(api_url) as client:
        compilers = await client.list_compilers()
        assert len(compilers) > 0
    # Client should be closed after context exit


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
