"""Shared fixtures for end-to-end tests.

This module provides:
- temp_db: Isolated SQLite database per test
- temp_melee_repo: Minimal git repo with sample C files
- mock_decomp_server: Mocked decomp.me API using respx
- agent_factory: Create multiple simulated agents
- cli_runner: Typer CLI runner with patched environment
"""

import json
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
from typer.testing import CliRunner

from src.cli import app
from src.db import StateDB, reset_db


# =============================================================================
# Database Fixtures
# =============================================================================


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Create an isolated SQLite database for testing.

    Patches get_db() to use temporary database.
    Ensures clean state for each test.
    """
    db_path = tmp_path / "test_agent_state.db"
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Reset any existing global instance
    reset_db()

    # Create fresh DB at temp path
    test_db = StateDB(db_path)

    # Patch environment and module-level constants
    monkeypatch.setenv("DECOMP_CONFIG_DIR", str(config_dir))

    # Patch the get_db function to return our test instance
    with patch('src.db.get_db', return_value=test_db):
        with patch('src.db._db', test_db):
            yield test_db

    # Cleanup
    test_db.close()
    reset_db()


# =============================================================================
# Melee Repository Fixtures
# =============================================================================


@pytest.fixture
def temp_melee_repo(tmp_path):
    """Create a minimal melee repository structure for testing.

    Includes:
    - src/melee/ with sample C files
    - configure.py with Object() declarations
    - config/GALE01/symbols.txt, splits.txt
    - Initialized git repo with initial commit
    """
    melee_root = tmp_path / "melee"
    melee_root.mkdir()

    # Create directory structure
    src_dir = melee_root / "src" / "melee" / "lb"
    src_dir.mkdir(parents=True)

    config_dir = melee_root / "config" / "GALE01"
    config_dir.mkdir(parents=True)

    # Create sample C file with test functions
    (src_dir / "lbcommand.c").write_text('''
#include "lb/types.h"

void TestFunction(void) {
    // Stub implementation
}

void AnotherFunction(int x) {
    return;
}

void ThirdFunction(int a, int b) {
    int result = a + b;
    return;
}
''')

    # Create another subdirectory for testing isolation
    ft_dir = melee_root / "src" / "melee" / "ft" / "chara" / "ftFox"
    ft_dir.mkdir(parents=True)
    (ft_dir / "ftFx_SpecialHi.c").write_text('''
#include "ft/forward.h"

void ftFx_SpecialHi_Enter(void) {
    // Fox Up-B entry
}
''')

    # Create configure.py
    (melee_root / "configure.py").write_text('''
# Test configure file

MeleeLib("lb (Library)")
Object(NonMatching, "melee/lb/lbcommand.c")

MeleeLib("ft (Fighters)")
Object(NonMatching, "melee/ft/chara/ftFox/ftFx_SpecialHi.c")
''')

    # Create minimal symbols.txt
    (config_dir / "symbols.txt").write_text('''
TestFunction = .text:0x80005940; // type:function size:0x30
AnotherFunction = .text:0x80005970; // type:function size:0x20
ThirdFunction = .text:0x80005990; // type:function size:0x40
ftFx_SpecialHi_Enter = .text:0x800B1000; // type:function size:0x100
''')

    # Create minimal splits.txt
    (config_dir / "splits.txt").write_text('''
melee/lb/lbcommand.c:
    .text       start:0x80005940 end:0x800059D0

melee/ft/chara/ftFox/ftFx_SpecialHi.c:
    .text       start:0x800B1000 end:0x800B1100
''')

    # Initialize git repo
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }
    subprocess.run(["git", "init"], cwd=melee_root, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=melee_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=melee_root, capture_output=True, check=True, env=git_env
    )

    yield melee_root


# =============================================================================
# Mock decomp.me Server Fixtures
# =============================================================================


@pytest.fixture
def mock_decomp_server():
    """Mock decomp.me API responses without running a real server.

    Uses respx to intercept HTTP requests.
    Stores scratch data in memory for stateful testing.

    Yields a dict with:
    - scratches: dict mapping slug to scratch data
    - base_url: the mock server URL
    - set_match: function to manually set match score
    """
    scratches = {}  # slug -> scratch data
    scratch_counter = [0]
    base_url = "http://localhost:8000"

    def create_scratch(request):
        data = json.loads(request.content)
        scratch_counter[0] += 1
        slug = f"test-scratch-{scratch_counter[0]}"

        scratch = {
            "slug": slug,
            "url": f"{base_url}/scratch/{slug}",
            "html_url": f"{base_url}/scratch/{slug}",
            "name": data.get("name", "Unnamed"),
            "compiler": data.get("compiler", "mwcc_247_92"),
            "platform": data.get("platform", "gc_wii"),
            "source_code": data.get("source_code", ""),
            "context": data.get("context", ""),
            "target_asm": data.get("target_asm", ""),
            "score": 100,  # Default non-matching
            "max_score": 100,
            "match_percent": 0.0,
            "claim_token": f"token-{slug}",
            "owner": None,
        }
        scratches[slug] = scratch
        return httpx.Response(200, json=scratch)

    def get_scratch(request):
        # Extract slug from URL path
        path = str(request.url.path)
        match = re.search(r'/scratch/([^/]+)/?$', path)
        if not match:
            return httpx.Response(404, json={"error": "Not found"})
        slug = match.group(1)

        if slug not in scratches:
            return httpx.Response(404, json={"error": "Scratch not found"})
        return httpx.Response(200, json=scratches[slug])

    def compile_scratch(request):
        # Extract slug from URL path
        path = str(request.url.path)
        match = re.search(r'/scratch/([^/]+)/compile/?$', path)
        if not match:
            return httpx.Response(404, json={"error": "Not found"})
        slug = match.group(1)

        if slug not in scratches:
            return httpx.Response(404, json={"error": "Scratch not found"})

        # Simulate compilation - update score if source changed
        data = json.loads(request.content) if request.content else {}
        if data.get("source_code"):
            scratches[slug]["source_code"] = data["source_code"]

        scratch = scratches[slug]
        return httpx.Response(200, json={
            "success": True,
            "compiler_output": "",
            "diff_output": {
                "current_score": scratch["score"],
                "max_score": scratch["max_score"],
            },
            "score": scratch["score"],
            "max_score": scratch["max_score"],
        })

    def set_match(slug: str, score: int, max_score: int = 100):
        """Helper to manually set match score for testing."""
        if slug in scratches:
            scratches[slug]["score"] = score
            scratches[slug]["max_score"] = max_score
            match_pct = 100.0 if score == 0 else (1.0 - score / max_score) * 100
            scratches[slug]["match_percent"] = match_pct

    with respx.mock(assert_all_called=False) as respx_mock:
        # Route patterns - use route() instead of add()
        respx_mock.post(url__regex=r".*/api/scratch/?$").mock(side_effect=create_scratch)
        respx_mock.get(url__regex=r".*/api/scratch/[^/]+/?$").mock(side_effect=get_scratch)
        respx_mock.post(url__regex=r".*/api/scratch/[^/]+/compile/?$").mock(
            side_effect=compile_scratch
        )
        # Health check endpoint
        respx_mock.get(url__regex=r".*/api/?$").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        yield {
            "scratches": scratches,
            "base_url": base_url,
            "set_match": set_match,
        }


# =============================================================================
# Multi-Agent Fixtures
# =============================================================================


@pytest.fixture
def agent_factory(tmp_path):
    """Factory for creating multiple simulated agents.

    Each agent has:
    - Unique agent_id
    - Isolated environment settings

    Usage:
        agent1 = agent_factory("agent-1")
        agent2 = agent_factory("agent-2")
    """
    agents = []

    def create_agent(agent_id: str):
        agent = {
            "id": agent_id,
            "env": {"DECOMP_AGENT_ID": agent_id},
        }
        agents.append(agent)
        return agent

    yield create_agent


# =============================================================================
# CLI Runner Fixtures
# =============================================================================


@pytest.fixture
def cli_runner():
    """Typer CLI runner for invoking commands.

    Returns a function that invokes the CLI app with given arguments.
    """
    runner = CliRunner(mix_stderr=False)

    def run(*args, catch_exceptions=True, env=None, **kwargs):
        """Run a CLI command.

        Args:
            *args: Command arguments (e.g., "state", "status")
            catch_exceptions: If False, let exceptions propagate
            env: Optional environment variables dict
            **kwargs: Additional kwargs for runner.invoke

        Returns:
            CliRunner Result object
        """
        return runner.invoke(
            app,
            list(args),
            catch_exceptions=catch_exceptions,
            env=env,
            **kwargs
        )

    return run


@pytest.fixture
def cli_with_db(cli_runner, temp_db, tmp_path, monkeypatch):
    """CLI runner with isolated database and paths.

    This fixture combines temp_db and cli_runner with proper patching
    for testing CLI commands that access the database.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)

    # Patch CLI module constants
    monkeypatch.setattr('src.cli._common.DECOMP_CONFIG_DIR', config_dir)

    def run(*args, agent_id="test-agent", **kwargs):
        env = kwargs.pop('env', {}) or {}
        env["DECOMP_AGENT_ID"] = agent_id
        return cli_runner(*args, env=env, **kwargs)

    return run


# =============================================================================
# Utility Fixtures
# =============================================================================


@pytest.fixture
def git_env():
    """Environment variables for git operations in tests."""
    return {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }
