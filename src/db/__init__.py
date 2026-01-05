"""SQLite state database for agent coordination.

This module provides a centralized database for tracking:
- Function claims and work status
- Scratch URLs (local and production)
- Agent activity and worktrees
- Full audit trail of all changes

Usage:
    from src.db import get_db

    db = get_db()
    with db.connection() as conn:
        conn.execute("SELECT * FROM functions WHERE match_percent >= 95")
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from .schema import INITIAL_META, SCHEMA_SQL, SCHEMA_VERSION, get_migrations

# Database location
DECOMP_CONFIG_DIR = Path.home() / ".config" / "decomp-me"
DEFAULT_DB_PATH = DECOMP_CONFIG_DIR / "agent_state.db"

# Thread-local storage for connections
_local = threading.local()


class StateDB:
    """SQLite database for agent state management.

    Thread-safe connection management with automatic schema initialization.
    Provides methods for common operations and audit logging.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema if needed."""
        with self.connection() as conn:
            # Check if db_meta table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='db_meta'"
            )
            if not cursor.fetchone():
                # First time - create schema
                conn.executescript(SCHEMA_SQL)
                # Insert initial metadata
                for key, value in INITIAL_META:
                    if value is None:
                        conn.execute(
                            "INSERT INTO db_meta (key) VALUES (?)",
                            (key,)
                        )
                    else:
                        conn.execute(
                            "INSERT INTO db_meta (key, value) VALUES (?, ?)",
                            (key, value)
                        )
            else:
                # Check for schema migrations
                self._run_migrations(conn)

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Run any pending schema migrations."""
        cursor = conn.execute(
            "SELECT value FROM db_meta WHERE key = 'schema_version'"
        )
        row = cursor.fetchone()
        current_version = int(row[0]) if row else 0

        migrations = get_migrations()
        for version in range(current_version, SCHEMA_VERSION):
            if version in migrations:
                conn.executescript(migrations[version])

        if current_version < SCHEMA_VERSION:
            conn.execute(
                "UPDATE db_meta SET value = ?, updated_at = unixepoch('now', 'subsec') "
                "WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),)
            )

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a thread-local database connection.

        Connections are reused within the same thread for efficiency.
        Uses autocommit mode by default.
        """
        if not hasattr(_local, 'connection') or _local.connection is None:
            _local.connection = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # Autocommit by default
            )
            _local.connection.row_factory = sqlite3.Row
            # Enable foreign keys and WAL mode for better concurrency
            _local.connection.execute("PRAGMA foreign_keys = ON")
            _local.connection.execute("PRAGMA journal_mode = WAL")

        yield _local.connection

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Execute within a transaction (explicit commit/rollback)."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(_local, 'connection') and _local.connection is not None:
            _local.connection.close()
            _local.connection = None

    # =========================================================================
    # Audit Logging
    # =========================================================================

    def log_audit(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        agent_id: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Log an audit entry for a state change.

        Args:
            entity_type: Type of entity ('function', 'scratch', 'claim', 'agent')
            entity_id: ID of the entity (function name, slug, agent ID)
            action: Action performed ('created', 'updated', 'deleted', etc.)
            agent_id: Agent that performed the action
            old_value: Previous state (as dict, will be JSON-encoded)
            new_value: New state (as dict, will be JSON-encoded)
            metadata: Additional context (as dict, will be JSON-encoded)
        """
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO audit_log (entity_type, entity_id, action, agent_id,
                                       old_value, new_value, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity_type,
                    entity_id,
                    action,
                    agent_id,
                    json.dumps(old_value) if old_value else None,
                    json.dumps(new_value) if new_value else None,
                    json.dumps(metadata) if metadata else None,
                )
            )

    # =========================================================================
    # Claim Operations
    # =========================================================================

    def add_claim(
        self,
        function_name: str,
        agent_id: str,
        timeout_seconds: int = 3600,
    ) -> tuple[bool, str | None]:
        """Add a claim for a function.

        Args:
            function_name: Function to claim
            agent_id: Agent claiming the function
            timeout_seconds: Claim expiry in seconds (default 1 hour)

        Returns:
            (success, error_message) tuple
        """
        now = time.time()
        expires_at = now + timeout_seconds

        with self.transaction() as conn:
            # Check for existing claim
            cursor = conn.execute(
                "SELECT agent_id, expires_at FROM claims WHERE function_name = ?",
                (function_name,)
            )
            existing = cursor.fetchone()

            if existing:
                if existing['expires_at'] > now:
                    if existing['agent_id'] == agent_id:
                        return False, f"Already claimed by you ({agent_id})"
                    return False, f"Claimed by {existing['agent_id']}"
                else:
                    # Expired claim, remove it
                    conn.execute(
                        "DELETE FROM claims WHERE function_name = ?",
                        (function_name,)
                    )

            # Add new claim
            conn.execute(
                """
                INSERT INTO claims (function_name, agent_id, claimed_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (function_name, agent_id, now, expires_at)
            )

            # Update function status
            conn.execute(
                """
                INSERT INTO functions (function_name, status, claimed_by_agent, claimed_at, updated_at)
                VALUES (?, 'claimed', ?, ?, ?)
                ON CONFLICT(function_name) DO UPDATE SET
                    status = 'claimed',
                    claimed_by_agent = excluded.claimed_by_agent,
                    claimed_at = excluded.claimed_at,
                    updated_at = excluded.updated_at
                """,
                (function_name, agent_id, now, now)
            )

            self.log_audit(
                'claim', function_name, 'created',
                agent_id=agent_id,
                new_value={'agent_id': agent_id, 'expires_at': expires_at}
            )

        return True, None

    def release_claim(self, function_name: str, agent_id: str | None = None) -> bool:
        """Release a claim on a function.

        Args:
            function_name: Function to release
            agent_id: Optional agent ID to verify ownership

        Returns:
            True if claim was released, False if not found
        """
        with self.transaction() as conn:
            # Get current claim for audit
            cursor = conn.execute(
                "SELECT agent_id, claimed_at FROM claims WHERE function_name = ?",
                (function_name,)
            )
            existing = cursor.fetchone()

            if not existing:
                return False

            if agent_id and existing['agent_id'] != agent_id:
                return False  # Not owned by this agent

            conn.execute(
                "DELETE FROM claims WHERE function_name = ?",
                (function_name,)
            )

            # Update function status (only if currently claimed)
            conn.execute(
                """
                UPDATE functions SET status = 'unclaimed', updated_at = ?
                WHERE function_name = ? AND status = 'claimed'
                """,
                (time.time(), function_name)
            )

            self.log_audit(
                'claim', function_name, 'released',
                agent_id=existing['agent_id'],
                old_value={'agent_id': existing['agent_id']}
            )

        return True

    def get_active_claims(self) -> list[dict]:
        """Get all active (non-expired) claims."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT function_name, agent_id, claimed_at, expires_at,
                       minutes_remaining, match_percent, local_scratch_slug
                FROM v_active_claims
                ORDER BY claimed_at DESC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Function Operations
    # =========================================================================

    def upsert_function(
        self,
        function_name: str,
        agent_id: str | None = None,
        **fields: Any,
    ) -> None:
        """Insert or update a function record.

        Args:
            function_name: Function name (primary key)
            agent_id: Agent performing the update (for audit)
            **fields: Fields to update (match_percent, status, etc.)
        """
        now = time.time()
        fields['updated_at'] = now

        with self.transaction() as conn:
            # Get current state for audit
            cursor = conn.execute(
                "SELECT * FROM functions WHERE function_name = ?",
                (function_name,)
            )
            old_row = cursor.fetchone()
            old_value = dict(old_row) if old_row else None

            # Build upsert query
            field_names = list(fields.keys())
            placeholders = ', '.join(['?'] * len(fields))
            updates = ', '.join([f"{f} = excluded.{f}" for f in field_names])

            conn.execute(
                f"""
                INSERT INTO functions (function_name, {', '.join(field_names)})
                VALUES (?, {placeholders})
                ON CONFLICT(function_name) DO UPDATE SET {updates}
                """,
                (function_name, *fields.values())
            )

            self.log_audit(
                'function', function_name,
                'updated' if old_value else 'created',
                agent_id=agent_id,
                old_value=old_value,
                new_value={'function_name': function_name, **fields}
            )

    def get_function(self, function_name: str) -> dict | None:
        """Get a function record by name."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM functions WHERE function_name = ?",
                (function_name,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_functions_by_status(self, status: str) -> list[dict]:
        """Get all functions with a given status."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM functions WHERE status = ? ORDER BY updated_at DESC",
                (status,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_uncommitted_matches(self) -> list[dict]:
        """Get functions that are 95%+ matched but not committed."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM v_uncommitted_matches ORDER BY match_percent DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Address Tracking Operations
    # =========================================================================

    def get_function_by_address(self, address: str) -> dict | None:
        """Look up function by canonical address.

        Args:
            address: Hex address like "0x80003100"

        Returns:
            Function record dict or None if not found
        """
        # Normalize address format
        normalized = self._normalize_address(address)
        if not normalized:
            return None

        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM functions WHERE canonical_address = ?",
                (normalized,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def _normalize_address(self, address: str | int | None) -> str | None:
        """Normalize address to 0x{UPPERCASE_8_HEX} format.

        Args:
            address: Address in various formats:
                - "0x80003100" (hex string with prefix)
                - "80003100" (hex string without prefix, contains a-f)
                - "2147506496" (decimal string, all digits)
                - 2147506496 (decimal integer)

        Returns:
            Normalized string like "0x80003100" or None if invalid
        """
        if address is None:
            return None

        try:
            if isinstance(address, int):
                return f"0x{address:08X}"
            elif isinstance(address, str):
                addr_str = address.strip()
                if addr_str.upper().startswith("0X"):
                    # Explicit hex prefix
                    addr_int = int(addr_str, 16)
                elif any(c in addr_str.upper() for c in "ABCDEF"):
                    # Contains hex digits a-f, must be hex
                    addr_int = int(addr_str, 16)
                elif addr_str.isdigit() and len(addr_str) > 8:
                    # Long all-digit string is likely decimal (like virtual_address)
                    addr_int = int(addr_str, 10)
                else:
                    # Short hex without prefix (like "80003100")
                    addr_int = int(addr_str, 16)
                return f"0x{addr_int:08X}"
        except (ValueError, TypeError):
            return None

    def record_function_alias(
        self,
        canonical_address: str,
        old_name: str,
        new_name: str | None = None,
        source: str = "report_sync",
    ) -> None:
        """Record a function rename (alias).

        Args:
            canonical_address: Hex address of the function
            old_name: Previous function name
            new_name: New function name (optional, for reference)
            source: How detected: 'report_sync', 'manual', 'git_history', 'symbols'
        """
        normalized = self._normalize_address(canonical_address)
        if not normalized:
            return

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO function_aliases (canonical_address, old_name, new_name, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(canonical_address, old_name) DO UPDATE SET
                    new_name = COALESCE(excluded.new_name, new_name),
                    source = excluded.source
                """,
                (normalized, old_name, new_name, source)
            )

            self.log_audit(
                'alias', old_name, 'recorded',
                new_value={
                    'canonical_address': normalized,
                    'old_name': old_name,
                    'new_name': new_name,
                    'source': source,
                }
            )

    def get_aliases_for_address(self, address: str) -> list[dict]:
        """Get all known names for an address.

        Args:
            address: Hex address like "0x80003100"

        Returns:
            List of alias records with old_name, new_name, renamed_at, source
        """
        normalized = self._normalize_address(address)
        if not normalized:
            return []

        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT old_name, new_name, renamed_at, source
                FROM function_aliases
                WHERE canonical_address = ?
                ORDER BY renamed_at DESC
                """,
                (normalized,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_function_by_name_or_address(
        self,
        name: str | None = None,
        address: str | None = None,
    ) -> dict | None:
        """Look up function by name first, then by address.

        This is the primary lookup method for handling renames:
        1. Try to find by exact name match
        2. If not found and address provided, look up by address
        3. If found by address with different name, it's a rename

        Args:
            name: Function name to look up
            address: Hex address as fallback

        Returns:
            Function record dict or None
        """
        # Try name first
        if name:
            func = self.get_function(name)
            if func:
                return func

        # Fallback to address
        if address:
            return self.get_function_by_address(address)

        return None

    def bulk_update_addresses(
        self,
        address_map: dict[str, str],
        agent_id: str | None = None,
    ) -> int:
        """Bulk update canonical_address for many functions.

        Args:
            address_map: Dict mapping function_name -> canonical_address
            agent_id: Agent performing the update (for audit)

        Returns:
            Number of functions updated
        """
        if not address_map:
            return 0

        updated = 0
        now = time.time()

        with self.transaction() as conn:
            for func_name, address in address_map.items():
                normalized = self._normalize_address(address)
                if not normalized:
                    continue

                cursor = conn.execute(
                    """
                    UPDATE functions
                    SET canonical_address = ?, updated_at = ?
                    WHERE function_name = ? AND (canonical_address IS NULL OR canonical_address != ?)
                    """,
                    (normalized, now, func_name, normalized)
                )
                if cursor.rowcount > 0:
                    updated += 1

            if updated > 0:
                self.log_audit(
                    'bulk_update', 'addresses', 'updated',
                    agent_id=agent_id,
                    metadata={'count': updated}
                )

        return updated

    def merge_function_records(
        self,
        old_name: str,
        new_name: str,
        canonical_address: str,
        agent_id: str | None = None,
    ) -> bool:
        """Merge old function record into new one, preserving history.

        When a function is renamed, merge data from the old record
        into the new one, preserving scratch slugs, PR info, etc.

        Args:
            old_name: Previous function name
            new_name: New function name
            canonical_address: Hex address
            agent_id: Agent performing the merge

        Returns:
            True if merge was performed, False otherwise
        """
        normalized = self._normalize_address(canonical_address)
        if not normalized:
            return False

        with self.transaction() as conn:
            # Get both records
            cursor = conn.execute(
                "SELECT * FROM functions WHERE function_name IN (?, ?)",
                (old_name, new_name)
            )
            rows = {row['function_name']: dict(row) for row in cursor.fetchall()}

            old_record = rows.get(old_name)
            new_record = rows.get(new_name)

            if not old_record:
                return False  # Nothing to merge from

            # Record the alias
            self.record_function_alias(normalized, old_name, new_name, source='report_sync')

            # If new record exists, merge valuable data from old into new
            if new_record:
                # Fields to preserve from old record if new record doesn't have them
                merge_fields = [
                    'local_scratch_slug', 'production_scratch_slug',
                    'commit_hash', 'branch', 'worktree_path',
                    'pr_url', 'pr_number', 'pr_state',
                    'notes',
                ]

                updates = []
                params = []
                for field in merge_fields:
                    if old_record.get(field) and not new_record.get(field):
                        updates.append(f"{field} = ?")
                        params.append(old_record[field])

                if updates:
                    params.extend([time.time(), normalized, new_name])
                    conn.execute(
                        f"""
                        UPDATE functions
                        SET {', '.join(updates)}, updated_at = ?, canonical_address = ?
                        WHERE function_name = ?
                        """,
                        params
                    )

                # Delete the old record
                conn.execute(
                    "DELETE FROM functions WHERE function_name = ?",
                    (old_name,)
                )

                self.log_audit(
                    'function', old_name, 'merged',
                    agent_id=agent_id,
                    old_value=old_record,
                    new_value={'merged_into': new_name},
                    metadata={'canonical_address': normalized}
                )

            else:
                # No new record - just update the old record with new name and address
                # This is trickier since function_name is the primary key
                # We need to insert new and delete old
                conn.execute(
                    """
                    INSERT INTO functions (
                        function_name, match_percent, current_score, max_score, status,
                        build_status, build_diagnosis, is_documented, documentation_status,
                        documented_at, local_scratch_slug, production_scratch_slug,
                        is_committed, commit_hash, branch, worktree_path,
                        pr_url, pr_number, pr_state, claimed_by_agent, claimed_at,
                        source_file_path, canonical_address, notes,
                        created_at, updated_at,
                        local_scratch_verified_at, production_scratch_verified_at, git_verified_at
                    )
                    SELECT
                        ?, match_percent, current_score, max_score, status,
                        build_status, build_diagnosis, is_documented, documentation_status,
                        documented_at, local_scratch_slug, production_scratch_slug,
                        is_committed, commit_hash, branch, worktree_path,
                        pr_url, pr_number, pr_state, claimed_by_agent, claimed_at,
                        source_file_path, ?, notes,
                        created_at, ?,
                        local_scratch_verified_at, production_scratch_verified_at, git_verified_at
                    FROM functions WHERE function_name = ?
                    """,
                    (new_name, normalized, time.time(), old_name)
                )
                conn.execute(
                    "DELETE FROM functions WHERE function_name = ?",
                    (old_name,)
                )

                self.log_audit(
                    'function', old_name, 'renamed',
                    agent_id=agent_id,
                    old_value=old_record,
                    new_value={'new_name': new_name},
                    metadata={'canonical_address': normalized}
                )

        return True

    # =========================================================================
    # Scratch Operations
    # =========================================================================

    def upsert_scratch(
        self,
        slug: str,
        instance: str,
        base_url: str,
        agent_id: str | None = None,
        **fields: Any,
    ) -> None:
        """Insert or update a scratch record.

        Args:
            slug: Scratch slug (primary key)
            instance: 'local' or 'production'
            base_url: Base URL of the decomp.me instance
            agent_id: Agent performing the update (for audit)
            **fields: Additional fields (function_name, score, etc.)
        """
        with self.transaction() as conn:
            # Get current state for audit
            cursor = conn.execute(
                "SELECT * FROM scratches WHERE slug = ?",
                (slug,)
            )
            old_row = cursor.fetchone()
            old_value = dict(old_row) if old_row else None

            all_fields = {
                'instance': instance,
                'base_url': base_url,
                **fields
            }
            field_names = list(all_fields.keys())
            placeholders = ', '.join(['?'] * len(all_fields))
            updates = ', '.join([f"{f} = excluded.{f}" for f in field_names])

            conn.execute(
                f"""
                INSERT INTO scratches (slug, {', '.join(field_names)})
                VALUES (?, {placeholders})
                ON CONFLICT(slug) DO UPDATE SET {updates}
                """,
                (slug, *all_fields.values())
            )

            self.log_audit(
                'scratch', slug,
                'updated' if old_value else 'created',
                agent_id=agent_id,
                old_value=old_value,
                new_value={'slug': slug, **all_fields}
            )

    def record_match_score(
        self,
        scratch_slug: str,
        score: int,
        max_score: int,
        worktree_path: str | None = None,
        branch: str | None = None,
    ) -> None:
        """Record a match score for a scratch in history.

        Args:
            scratch_slug: The scratch identifier
            score: Current diff score (0 = perfect match)
            max_score: Maximum possible score
            worktree_path: Path to the worktree where work was done
            branch: Git branch name where work was done
        """
        match_percent = 100.0 if score == 0 else (
            (1.0 - score / max_score) * 100 if max_score > 0 else 0.0
        )

        with self.connection() as conn:
            # Check if this is a duplicate of the last entry
            cursor = conn.execute(
                """
                SELECT score, max_score FROM match_history
                WHERE scratch_slug = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (scratch_slug,)
            )
            last = cursor.fetchone()
            if last and last['score'] == score and last['max_score'] == max_score:
                return  # No change, skip

            conn.execute(
                """
                INSERT INTO match_history (scratch_slug, score, max_score, match_percent, worktree_path, branch)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (scratch_slug, score, max_score, match_percent, worktree_path, branch)
            )

            # Update scratch record
            conn.execute(
                """
                UPDATE scratches SET score = ?, max_score = ?, match_percent = ?,
                       last_compiled_at = unixepoch('now', 'subsec')
                WHERE slug = ?
                """,
                (score, max_score, match_percent, scratch_slug)
            )

    # =========================================================================
    # Branch Progress Operations
    # =========================================================================

    def upsert_branch_progress(
        self,
        function_name: str,
        branch: str,
        scratch_slug: str | None = None,
        match_percent: float = 0.0,
        score: int | None = None,
        max_score: int | None = None,
        agent_id: str | None = None,
        worktree_path: str | None = None,
        is_committed: bool = False,
        commit_hash: str | None = None,
    ) -> None:
        """Record or update progress for a function on a specific branch.

        This tracks match state per (function, branch) for recovery and debugging.
        """
        now = time.time()
        with self.transaction() as conn:
            # Check if record exists
            cursor = conn.execute(
                "SELECT * FROM function_branch_progress WHERE function_name = ? AND branch = ?",
                (function_name, branch)
            )
            old_row = cursor.fetchone()
            old_value = dict(old_row) if old_row else None

            conn.execute(
                """
                INSERT INTO function_branch_progress
                    (function_name, branch, scratch_slug, match_percent, score, max_score,
                     agent_id, worktree_path, is_committed, commit_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(function_name, branch) DO UPDATE SET
                    scratch_slug = COALESCE(excluded.scratch_slug, scratch_slug),
                    match_percent = excluded.match_percent,
                    score = excluded.score,
                    max_score = excluded.max_score,
                    agent_id = COALESCE(excluded.agent_id, agent_id),
                    worktree_path = COALESCE(excluded.worktree_path, worktree_path),
                    is_committed = excluded.is_committed,
                    commit_hash = COALESCE(excluded.commit_hash, commit_hash),
                    updated_at = excluded.updated_at
                """,
                (function_name, branch, scratch_slug, match_percent, score, max_score,
                 agent_id, worktree_path, is_committed, commit_hash, now)
            )

            self.log_audit(
                'branch_progress', f"{function_name}@{branch}",
                'updated' if old_value else 'created',
                agent_id=agent_id,
                old_value=old_value,
                new_value={
                    'function_name': function_name,
                    'branch': branch,
                    'match_percent': match_percent,
                    'is_committed': is_committed,
                }
            )

    def get_branch_progress(self, function_name: str) -> list[dict]:
        """Get all branch progress entries for a function."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM function_branch_progress
                WHERE function_name = ?
                ORDER BY match_percent DESC
                """,
                (function_name,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_best_branch_progress(self, function_name: str) -> dict | None:
        """Get the branch with the highest match for a function."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM function_branch_progress
                WHERE function_name = ?
                ORDER BY match_percent DESC
                LIMIT 1
                """,
                (function_name,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # =========================================================================
    # Agent Operations
    # =========================================================================

    def upsert_agent(
        self,
        agent_id: str,
        worktree_path: str | None = None,
        branch_name: str | None = None,
    ) -> None:
        """Insert or update an agent record."""
        now = time.time()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO agents (agent_id, worktree_path, branch_name, last_active_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    worktree_path = COALESCE(excluded.worktree_path, worktree_path),
                    branch_name = COALESCE(excluded.branch_name, branch_name),
                    last_active_at = excluded.last_active_at
                """,
                (agent_id, worktree_path, branch_name, now)
            )

    def get_agent_summary(self) -> list[dict]:
        """Get summary of all agents and their work."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM v_agent_summary ORDER BY last_active_at DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Subdirectory Worktree Operations
    # =========================================================================

    def upsert_subdirectory(
        self,
        subdirectory_key: str,
        worktree_path: str,
        branch_name: str,
        locked_by_agent: str | None = None,
    ) -> None:
        """Insert or update a subdirectory allocation record.

        Args:
            subdirectory_key: Subdirectory key (e.g., "ft-chara-ftFox")
            worktree_path: Path to the worktree
            branch_name: Git branch name
            locked_by_agent: Agent ID if locked, None if unlocked
        """
        now = time.time()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO subdirectory_allocations
                    (subdirectory_key, worktree_path, branch_name, locked_by_agent,
                     locked_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(subdirectory_key) DO UPDATE SET
                    worktree_path = excluded.worktree_path,
                    branch_name = excluded.branch_name,
                    locked_by_agent = COALESCE(excluded.locked_by_agent, locked_by_agent),
                    locked_at = CASE WHEN excluded.locked_by_agent IS NOT NULL
                                     THEN excluded.locked_at
                                     ELSE locked_at END,
                    updated_at = excluded.updated_at
                """,
                (subdirectory_key, worktree_path, branch_name, locked_by_agent,
                 now if locked_by_agent else None, now)
            )

            self.log_audit(
                'subdirectory', subdirectory_key, 'upserted',
                agent_id=locked_by_agent,
                new_value={
                    'subdirectory_key': subdirectory_key,
                    'worktree_path': worktree_path,
                    'branch_name': branch_name,
                    'locked_by_agent': locked_by_agent,
                }
            )

    def lock_subdirectory(
        self,
        subdirectory_key: str,
        agent_id: str,
        timeout_minutes: int = 30,
    ) -> tuple[bool, str | None]:
        """Lock a subdirectory for exclusive access by an agent.

        Args:
            subdirectory_key: Subdirectory to lock
            agent_id: Agent requesting the lock
            timeout_minutes: Lock expiry in minutes (for high-contention zones)

        Returns:
            (success, error_message) tuple
        """
        now = time.time()
        expires_at = now + (timeout_minutes * 60)

        with self.transaction() as conn:
            # Check current lock status
            cursor = conn.execute(
                """
                SELECT locked_by_agent, lock_expires_at
                FROM subdirectory_allocations
                WHERE subdirectory_key = ?
                """,
                (subdirectory_key,)
            )
            row = cursor.fetchone()

            if row:
                current_lock = row['locked_by_agent']
                lock_expires = row['lock_expires_at']

                if current_lock:
                    # Check if lock has expired
                    if lock_expires and lock_expires > now:
                        if current_lock == agent_id:
                            # Already locked by this agent, extend the lock
                            conn.execute(
                                """
                                UPDATE subdirectory_allocations
                                SET lock_expires_at = ?, updated_at = ?
                                WHERE subdirectory_key = ?
                                """,
                                (expires_at, now, subdirectory_key)
                            )
                            return True, None
                        else:
                            return False, f"Locked by {current_lock}"
                    # Lock expired, we can take it

            # Acquire or update lock (UPSERT to handle missing rows)
            conn.execute(
                """
                INSERT INTO subdirectory_allocations
                    (subdirectory_key, worktree_path, branch_name,
                     locked_by_agent, locked_at, lock_expires_at, updated_at)
                VALUES (?, '', '', ?, ?, ?, ?)
                ON CONFLICT(subdirectory_key) DO UPDATE SET
                    locked_by_agent = excluded.locked_by_agent,
                    locked_at = excluded.locked_at,
                    lock_expires_at = excluded.lock_expires_at,
                    updated_at = excluded.updated_at
                """,
                (subdirectory_key, agent_id, now, expires_at, now)
            )

            # Also record assignment
            conn.execute(
                """
                INSERT INTO agent_subdirectory_assignments (agent_id, subdirectory_key)
                VALUES (?, ?)
                ON CONFLICT DO NOTHING
                """,
                (agent_id, subdirectory_key)
            )

            self.log_audit(
                'subdirectory', subdirectory_key, 'locked',
                agent_id=agent_id,
                new_value={'locked_by': agent_id, 'expires_at': expires_at}
            )

        return True, None

    def unlock_subdirectory(
        self,
        subdirectory_key: str,
        agent_id: str | None = None,
    ) -> bool:
        """Unlock a subdirectory.

        Args:
            subdirectory_key: Subdirectory to unlock
            agent_id: Optional agent ID to verify ownership

        Returns:
            True if unlocked successfully
        """
        now = time.time()
        with self.transaction() as conn:
            # Verify ownership if agent_id provided
            if agent_id:
                cursor = conn.execute(
                    """
                    SELECT locked_by_agent FROM subdirectory_allocations
                    WHERE subdirectory_key = ?
                    """,
                    (subdirectory_key,)
                )
                row = cursor.fetchone()
                if row and row['locked_by_agent'] != agent_id:
                    return False  # Not owned by this agent

            conn.execute(
                """
                UPDATE subdirectory_allocations
                SET locked_by_agent = NULL, locked_at = NULL, lock_expires_at = NULL,
                    updated_at = ?
                WHERE subdirectory_key = ?
                """,
                (now, subdirectory_key)
            )

            self.log_audit(
                'subdirectory', subdirectory_key, 'unlocked',
                agent_id=agent_id
            )

        return True

    def get_subdirectory_lock(self, subdirectory_key: str) -> dict | None:
        """Get the current lock status for a subdirectory.

        Returns:
            Dict with lock info or None if not locked/not found
        """
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT subdirectory_key, locked_by_agent, locked_at, lock_expires_at,
                       worktree_path, branch_name
                FROM subdirectory_allocations
                WHERE subdirectory_key = ?
                """,
                (subdirectory_key,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            result = dict(row)
            now = time.time()

            # Check if lock is expired
            if result['lock_expires_at'] and result['lock_expires_at'] <= now:
                result['locked_by_agent'] = None
                result['lock_expired'] = True

            return result

    def get_subdirectory_status(self) -> list[dict]:
        """Get status of all subdirectory allocations."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM v_subdirectory_status
                ORDER BY subdirectory_key
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_agent_subdirectories(self, agent_id: str) -> list[str]:
        """Get all subdirectories assigned to an agent."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT subdirectory_key FROM agent_subdirectory_assignments
                WHERE agent_id = ?
                ORDER BY assigned_at DESC
                """,
                (agent_id,)
            )
            return [row['subdirectory_key'] for row in cursor.fetchall()]

    def increment_pending_commits(self, subdirectory_key: str) -> None:
        """Increment the pending commits count for a subdirectory."""
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE subdirectory_allocations
                SET pending_commits = pending_commits + 1,
                    last_commit_at = unixepoch('now', 'subsec'),
                    updated_at = unixepoch('now', 'subsec')
                WHERE subdirectory_key = ?
                """,
                (subdirectory_key,)
            )

    def reset_pending_commits(self, subdirectory_key: str) -> None:
        """Reset the pending commits count (after merge/collect)."""
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE subdirectory_allocations
                SET pending_commits = 0, updated_at = unixepoch('now', 'subsec')
                WHERE subdirectory_key = ?
                """,
                (subdirectory_key,)
            )

    # =========================================================================
    # Sync State Operations
    # =========================================================================

    def record_sync(
        self,
        local_slug: str,
        production_slug: str,
        function_name: str | None = None,
    ) -> None:
        """Record a local to production sync."""
        with self.connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sync_state
                    (local_slug, production_slug, function_name, synced_at)
                VALUES (?, ?, ?, unixepoch('now', 'subsec'))
                """,
                (local_slug, production_slug, function_name)
            )

            # Update function if we know which one
            if function_name:
                conn.execute(
                    """
                    UPDATE functions SET production_scratch_slug = ?, updated_at = ?
                    WHERE function_name = ?
                    """,
                    (production_slug, time.time(), function_name)
                )

    # =========================================================================
    # Stale Data Detection
    # =========================================================================

    def get_stale_data(self, hours_threshold: float = 1.0) -> list[dict]:
        """Get data that may be stale and needs verification.

        Args:
            hours_threshold: Consider data stale after this many hours

        Returns:
            List of stale data entries with type and age
        """
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM v_stale_data
                WHERE hours_stale >= ?
                ORDER BY hours_stale DESC
                """,
                (hours_threshold,)
            )
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Audit History
    # =========================================================================

    def get_history(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get audit history entries.

        Args:
            entity_type: Filter by entity type
            entity_id: Filter by entity ID
            limit: Maximum entries to return

        Returns:
            List of audit entries, newest first
        """
        query = "SELECT * FROM audit_log WHERE 1=1"
        params: list[Any] = []

        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)
        if entity_id:
            query += " AND entity_id = ?"
            params.append(entity_id)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cursor = conn.execute(query, params)
            rows = []
            for row in cursor.fetchall():
                entry = dict(row)
                # Parse JSON fields
                for field in ('old_value', 'new_value', 'metadata'):
                    if entry.get(field):
                        try:
                            entry[field] = json.loads(entry[field])
                        except json.JSONDecodeError:
                            pass
                rows.append(entry)
            return rows

    # =========================================================================
    # Metadata
    # =========================================================================

    def get_meta(self, key: str) -> str | None:
        """Get a metadata value."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT value FROM db_meta WHERE key = ?",
                (key,)
            )
            row = cursor.fetchone()
            return row['value'] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Set a metadata value."""
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO db_meta (key, value, updated_at)
                VALUES (?, ?, unixepoch('now', 'subsec'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value)
            )

    # =========================================================================
    # Worktree Health Operations
    # =========================================================================

    def get_worktree_broken_count(self, worktree_path: str) -> tuple[int, list[str]]:
        """Get count of functions with broken builds in a worktree.

        Args:
            worktree_path: Path to the worktree

        Returns:
            (broken_count, list of function names)
        """
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT function_name FROM functions
                WHERE worktree_path = ?
                  AND build_status = 'broken'
                ORDER BY updated_at DESC
                """,
                (worktree_path,)
            )
            functions = [row['function_name'] for row in cursor.fetchall()]
            return len(functions), functions

    def get_subdirectory_broken_count(self, subdirectory_key: str) -> tuple[int, list[str]]:
        """Get count of functions with broken builds in a subdirectory.

        Args:
            subdirectory_key: Subdirectory key (e.g., "ft-chara-ftFox")

        Returns:
            (broken_count, list of function names)
        """
        # Map subdirectory key back to path pattern for matching source_file_path
        # e.g., "ft-chara-ftFox" -> "/ft/chara/ftFox/" pattern
        path_pattern = f"%/{subdirectory_key.replace('-', '/')}/%"

        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT function_name FROM functions
                WHERE (source_file_path LIKE ? OR worktree_path LIKE ?)
                  AND build_status = 'broken'
                ORDER BY updated_at DESC
                """,
                (path_pattern, f"%dir-{subdirectory_key}%")
            )
            functions = [row['function_name'] for row in cursor.fetchall()]
            return len(functions), functions

    def get_all_broken_builds(self) -> dict[str, list[str]]:
        """Get all functions with broken builds, grouped by worktree.

        Returns:
            Dict mapping worktree_path to list of function names
        """
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT worktree_path, function_name FROM functions
                WHERE build_status = 'broken'
                  AND worktree_path IS NOT NULL
                ORDER BY worktree_path, updated_at DESC
                """
            )
            result: dict[str, list[str]] = {}
            for row in cursor.fetchall():
                wt = row['worktree_path']
                if wt not in result:
                    result[wt] = []
                result[wt].append(row['function_name'])
            return result


# Global instance (lazy initialized)
_db: StateDB | None = None


def get_db(db_path: Path | None = None) -> StateDB:
    """Get the global database instance.

    Args:
        db_path: Optional custom path (only used on first call)

    Returns:
        StateDB instance
    """
    global _db
    if _db is None:
        _db = StateDB(db_path or DEFAULT_DB_PATH)
    return _db


def reset_db() -> None:
    """Reset the global database instance (for testing)."""
    global _db
    if _db is not None:
        _db.close()
        _db = None
