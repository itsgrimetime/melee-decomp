"""SQLite schema for agent state management."""

SCHEMA_VERSION = 8

SCHEMA_SQL = """
-- Core function tracking
CREATE TABLE IF NOT EXISTS functions (
    function_name TEXT PRIMARY KEY,
    -- Match state
    match_percent REAL DEFAULT 0.0,
    current_score INTEGER,
    max_score INTEGER,
    status TEXT CHECK(status IN (
        'unclaimed', 'claimed', 'in_progress', 'matched', 'committed', 'committed_needs_fix', 'merged', 'in_review'
    )) DEFAULT 'unclaimed',
    -- Build status for committed functions
    build_status TEXT CHECK(build_status IN ('passing', 'broken') OR build_status IS NULL),
    build_diagnosis TEXT,  -- Agent's explanation of why build is broken
    -- Documentation status
    is_documented BOOLEAN DEFAULT FALSE,
    documentation_status TEXT CHECK(documentation_status IN ('none', 'partial', 'complete') OR documentation_status IS NULL) DEFAULT 'none',
    documented_at REAL,
    -- Scratch references
    local_scratch_slug TEXT,
    production_scratch_slug TEXT,
    -- Git/commit info
    is_committed BOOLEAN DEFAULT FALSE,
    commit_hash TEXT,
    branch TEXT,
    worktree_path TEXT,
    -- PR info
    pr_url TEXT,
    pr_number INTEGER,
    pr_state TEXT CHECK(pr_state IN ('OPEN', 'CLOSED', 'MERGED') OR pr_state IS NULL),
    -- Agent ownership
    claimed_by_agent TEXT,
    claimed_at REAL,
    -- File location
    source_file_path TEXT,
    -- Address tracking (stable identifier for renames)
    canonical_address TEXT,
    -- Metadata
    notes TEXT,
    created_at REAL DEFAULT (unixepoch('now', 'subsec')),
    updated_at REAL DEFAULT (unixepoch('now', 'subsec')),
    -- Staleness tracking
    local_scratch_verified_at REAL,
    production_scratch_verified_at REAL,
    git_verified_at REAL
);

-- Short-lived claims for active work
CREATE TABLE IF NOT EXISTS claims (
    function_name TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    claimed_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

-- All scratches (local and production)
CREATE TABLE IF NOT EXISTS scratches (
    slug TEXT PRIMARY KEY,
    function_name TEXT,
    instance TEXT CHECK(instance IN ('local', 'production')) NOT NULL,
    base_url TEXT NOT NULL,
    claim_token TEXT,
    owner_agent TEXT,
    score INTEGER,
    max_score INTEGER,
    match_percent REAL,
    source_code TEXT,
    created_at REAL,
    last_compiled_at REAL,
    verified_at REAL
);

CREATE INDEX IF NOT EXISTS idx_scratches_function ON scratches(function_name);
CREATE INDEX IF NOT EXISTS idx_scratches_instance ON scratches(instance);

-- Active agents and their worktrees
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    worktree_path TEXT,
    branch_name TEXT,
    last_active_at REAL,
    created_at REAL DEFAULT (unixepoch('now', 'subsec'))
);

-- Subdirectory-based worktree allocations
-- Each source subdirectory gets its own worktree for easy merges
CREATE TABLE IF NOT EXISTS subdirectory_allocations (
    subdirectory_key TEXT PRIMARY KEY,  -- e.g., "ft-chara-ftFox", "lb", "gr"
    worktree_path TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    locked_by_agent TEXT,               -- NULL if unlocked
    locked_at REAL,
    lock_expires_at REAL,               -- For high-contention zones like ftCommon
    last_commit_at REAL,
    pending_commits INTEGER DEFAULT 0,
    created_at REAL DEFAULT (unixepoch('now', 'subsec')),
    updated_at REAL DEFAULT (unixepoch('now', 'subsec'))
);

-- Track which agents are assigned to which subdirectories
-- An agent can work in multiple subdirectories, but only one agent per subdirectory
CREATE TABLE IF NOT EXISTS agent_subdirectory_assignments (
    agent_id TEXT NOT NULL,
    subdirectory_key TEXT NOT NULL,
    assigned_at REAL DEFAULT (unixepoch('now', 'subsec')),
    PRIMARY KEY (agent_id, subdirectory_key),
    FOREIGN KEY (subdirectory_key) REFERENCES subdirectory_allocations(subdirectory_key)
);

CREATE INDEX IF NOT EXISTS idx_agent_subdir_agent ON agent_subdirectory_assignments(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_subdir_key ON agent_subdirectory_assignments(subdirectory_key);

-- Per-branch progress tracking for functions
-- Allows tracking different match states across branches/worktrees
CREATE TABLE IF NOT EXISTS function_branch_progress (
    function_name TEXT NOT NULL,
    branch TEXT NOT NULL,
    scratch_slug TEXT,
    match_percent REAL DEFAULT 0.0,
    score INTEGER,
    max_score INTEGER,
    agent_id TEXT,
    worktree_path TEXT,
    is_committed BOOLEAN DEFAULT FALSE,
    commit_hash TEXT,
    created_at REAL DEFAULT (unixepoch('now', 'subsec')),
    updated_at REAL DEFAULT (unixepoch('now', 'subsec')),
    PRIMARY KEY (function_name, branch)
);

CREATE INDEX IF NOT EXISTS idx_branch_progress_function ON function_branch_progress(function_name);
CREATE INDEX IF NOT EXISTS idx_branch_progress_branch ON function_branch_progress(branch);
CREATE INDEX IF NOT EXISTS idx_branch_progress_match ON function_branch_progress(match_percent DESC);

-- Full audit trail
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL DEFAULT (unixepoch('now', 'subsec')),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    agent_id TEXT,
    old_value TEXT,
    new_value TEXT,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

-- Match score history per scratch
CREATE TABLE IF NOT EXISTS match_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scratch_slug TEXT NOT NULL,
    score INTEGER NOT NULL,
    max_score INTEGER NOT NULL,
    match_percent REAL NOT NULL,
    worktree_path TEXT,
    branch TEXT,
    timestamp REAL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS idx_match_history_slug ON match_history(scratch_slug);
CREATE INDEX IF NOT EXISTS idx_match_history_branch ON match_history(branch);

-- Local to production sync tracking
CREATE TABLE IF NOT EXISTS sync_state (
    local_slug TEXT NOT NULL,
    production_slug TEXT NOT NULL,
    function_name TEXT,
    synced_at REAL,
    PRIMARY KEY (local_slug, production_slug)
);

-- Function rename/alias tracking
-- Tracks when functions get renamed (e.g., mn_80229860 -> MatchCondition)
-- Uses canonical_address as the stable identifier
CREATE TABLE IF NOT EXISTS function_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_address TEXT NOT NULL,
    old_name TEXT NOT NULL,
    new_name TEXT,
    renamed_at REAL NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    source TEXT CHECK(source IN ('report_sync', 'manual', 'git_history', 'symbols') OR source IS NULL),
    UNIQUE(canonical_address, old_name)
);

CREATE INDEX IF NOT EXISTS idx_aliases_address ON function_aliases(canonical_address);
CREATE INDEX IF NOT EXISTS idx_aliases_old_name ON function_aliases(old_name);
CREATE INDEX IF NOT EXISTS idx_functions_address ON functions(canonical_address);

-- Database metadata
CREATE TABLE IF NOT EXISTS db_meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL DEFAULT (unixepoch('now', 'subsec'))
);

-- Views for common queries

-- Active claims with expiry info
CREATE VIEW IF NOT EXISTS v_active_claims AS
SELECT
    c.function_name,
    c.agent_id,
    c.claimed_at,
    c.expires_at,
    (c.expires_at - unixepoch('now', 'subsec')) / 60.0 as minutes_remaining,
    f.match_percent,
    f.local_scratch_slug
FROM claims c
LEFT JOIN functions f ON c.function_name = f.function_name
WHERE c.expires_at > unixepoch('now', 'subsec');

-- Functions 95%+ but not committed
CREATE VIEW IF NOT EXISTS v_uncommitted_matches AS
SELECT
    f.function_name,
    f.match_percent,
    f.local_scratch_slug,
    f.production_scratch_slug,
    f.claimed_by_agent,
    f.branch,
    f.updated_at
FROM functions f
WHERE f.match_percent >= 95.0
  AND f.is_committed = FALSE
  AND f.status != 'merged';

-- Stale data needing refresh
CREATE VIEW IF NOT EXISTS v_stale_data AS
SELECT
    function_name,
    'local_scratch' as stale_type,
    local_scratch_verified_at as last_verified,
    (unixepoch('now', 'subsec') - local_scratch_verified_at) / 3600.0 as hours_stale
FROM functions
WHERE local_scratch_slug IS NOT NULL
  AND (local_scratch_verified_at IS NULL
       OR unixepoch('now', 'subsec') - local_scratch_verified_at > 3600)
UNION ALL
SELECT
    function_name,
    'production_scratch' as stale_type,
    production_scratch_verified_at,
    (unixepoch('now', 'subsec') - production_scratch_verified_at) / 3600.0
FROM functions
WHERE production_scratch_slug IS NOT NULL
  AND (production_scratch_verified_at IS NULL
       OR unixepoch('now', 'subsec') - production_scratch_verified_at > 86400)
UNION ALL
SELECT
    function_name,
    'git' as stale_type,
    git_verified_at,
    (unixepoch('now', 'subsec') - git_verified_at) / 3600.0
FROM functions
WHERE is_committed = TRUE
  AND (git_verified_at IS NULL
       OR unixepoch('now', 'subsec') - git_verified_at > 86400);

-- Agent work summary
CREATE VIEW IF NOT EXISTS v_agent_summary AS
SELECT
    a.agent_id,
    a.worktree_path,
    a.branch_name,
    a.last_active_at,
    (SELECT COUNT(*) FROM claims c
     WHERE c.agent_id = a.agent_id
       AND c.expires_at > unixepoch('now', 'subsec')) as active_claims,
    (SELECT COUNT(*) FROM functions f
     WHERE f.claimed_by_agent = a.agent_id
       AND f.is_committed = TRUE) as committed_functions
FROM agents a;

-- Subdirectory allocation summary
CREATE VIEW IF NOT EXISTS v_subdirectory_status AS
SELECT
    sa.subdirectory_key,
    sa.worktree_path,
    sa.branch_name,
    sa.locked_by_agent,
    sa.locked_at,
    sa.lock_expires_at,
    CASE
        WHEN sa.lock_expires_at IS NOT NULL
             AND sa.lock_expires_at > unixepoch('now', 'subsec')
        THEN (sa.lock_expires_at - unixepoch('now', 'subsec')) / 60.0
        ELSE NULL
    END as lock_minutes_remaining,
    sa.pending_commits,
    sa.last_commit_at,
    (SELECT COUNT(*) FROM functions f
     WHERE f.source_file_path LIKE '%' || REPLACE(sa.subdirectory_key, '-', '/') || '%'
       AND f.is_committed = FALSE
       AND f.match_percent >= 95.0) as ready_to_commit,
    (SELECT COUNT(*) FROM agent_subdirectory_assignments asa
     WHERE asa.subdirectory_key = sa.subdirectory_key) as assigned_agents
FROM subdirectory_allocations sa;

-- Branch progress summary per function
CREATE VIEW IF NOT EXISTS v_function_branch_progress AS
SELECT
    fbp.function_name,
    fbp.branch,
    fbp.scratch_slug,
    fbp.match_percent,
    fbp.agent_id,
    fbp.is_committed,
    fbp.updated_at,
    f.match_percent as canonical_match_percent,
    f.status as canonical_status,
    CASE WHEN fbp.match_percent > COALESCE(f.match_percent, 0) THEN 1 ELSE 0 END as is_best_match
FROM function_branch_progress fbp
LEFT JOIN functions f ON fbp.function_name = f.function_name
ORDER BY fbp.function_name, fbp.match_percent DESC;
"""

INITIAL_META = [
    ('schema_version', str(SCHEMA_VERSION)),
    ('created_at', None),  # Will use SQL default
    ('last_full_rebuild', None),
    ('last_git_sync', None),
    ('last_api_sync', None),
]


def get_migrations() -> dict[int, str]:
    """Get migration SQL for each schema version upgrade.

    Returns dict of {from_version: migration_sql}.
    """
    return {
        # Version 1 -> 2: Add 'in_review' to status CHECK constraint
        # SQLite doesn't support ALTER CHECK, so we recreate the table
        1: """
            -- Drop views that depend on functions table
            DROP VIEW IF EXISTS v_active_claims;
            DROP VIEW IF EXISTS v_uncommitted_matches;
            DROP VIEW IF EXISTS v_stale_data;
            DROP VIEW IF EXISTS v_agent_summary;

            -- Recreate functions table with updated CHECK constraint
            CREATE TABLE IF NOT EXISTS functions_new (
                function_name TEXT PRIMARY KEY,
                match_percent REAL DEFAULT 0.0,
                current_score INTEGER,
                max_score INTEGER,
                status TEXT CHECK(status IN (
                    'unclaimed', 'claimed', 'in_progress', 'matched', 'committed', 'merged', 'in_review'
                )) DEFAULT 'unclaimed',
                local_scratch_slug TEXT,
                production_scratch_slug TEXT,
                is_committed BOOLEAN DEFAULT FALSE,
                commit_hash TEXT,
                branch TEXT,
                worktree_path TEXT,
                pr_url TEXT,
                pr_number INTEGER,
                pr_state TEXT CHECK(pr_state IN ('OPEN', 'CLOSED', 'MERGED') OR pr_state IS NULL),
                claimed_by_agent TEXT,
                claimed_at REAL,
                source_file_path TEXT,
                notes TEXT,
                created_at REAL DEFAULT (unixepoch('now', 'subsec')),
                updated_at REAL DEFAULT (unixepoch('now', 'subsec')),
                local_scratch_verified_at REAL,
                production_scratch_verified_at REAL,
                git_verified_at REAL
            );

            -- Copy data from old table
            INSERT INTO functions_new SELECT * FROM functions;

            -- Drop old table and rename new
            DROP TABLE functions;
            ALTER TABLE functions_new RENAME TO functions;

            -- Recreate views
            CREATE VIEW IF NOT EXISTS v_active_claims AS
            SELECT
                c.function_name,
                c.agent_id,
                c.claimed_at,
                c.expires_at,
                (c.expires_at - unixepoch('now')) as seconds_remaining,
                f.match_percent,
                f.local_scratch_slug
            FROM claims c
            LEFT JOIN functions f ON c.function_name = f.function_name
            WHERE c.expires_at > unixepoch('now');

            CREATE VIEW IF NOT EXISTS v_uncommitted_matches AS
            SELECT
                function_name,
                match_percent,
                local_scratch_slug,
                production_scratch_slug,
                status,
                updated_at
            FROM functions
            WHERE match_percent >= 95.0
              AND is_committed = FALSE
              AND (pr_state IS NULL OR pr_state != 'MERGED');

            CREATE VIEW IF NOT EXISTS v_stale_data AS
            SELECT
                function_name,
                'local_scratch' as data_type,
                local_scratch_verified_at as verified_at,
                (unixepoch('now') - local_scratch_verified_at) / 3600.0 as hours_stale
            FROM functions
            WHERE local_scratch_slug IS NOT NULL
              AND (local_scratch_verified_at IS NULL
                   OR (unixepoch('now') - local_scratch_verified_at) > 3600)
            UNION ALL
            SELECT
                function_name,
                'production_scratch' as data_type,
                production_scratch_verified_at as verified_at,
                (unixepoch('now') - production_scratch_verified_at) / 3600.0 as hours_stale
            FROM functions
            WHERE production_scratch_slug IS NOT NULL
              AND (production_scratch_verified_at IS NULL
                   OR (unixepoch('now') - production_scratch_verified_at) > 86400)
            UNION ALL
            SELECT
                function_name,
                'git_commit' as data_type,
                git_verified_at as verified_at,
                (unixepoch('now') - git_verified_at) / 3600.0 as hours_stale
            FROM functions
            WHERE is_committed = TRUE
              AND (git_verified_at IS NULL
                   OR (unixepoch('now') - git_verified_at) > 86400);

            CREATE VIEW IF NOT EXISTS v_agent_summary AS
            SELECT
                claimed_by_agent as agent_id,
                COUNT(*) as total_functions,
                SUM(CASE WHEN match_percent >= 95 THEN 1 ELSE 0 END) as matched_functions,
                SUM(CASE WHEN is_committed THEN 1 ELSE 0 END) as committed_functions,
                MAX(updated_at) as last_activity
            FROM functions
            WHERE claimed_by_agent IS NOT NULL
            GROUP BY claimed_by_agent;
        """,
        # Version 2 -> 3: Add subdirectory-based worktree tables
        2: """
            -- Subdirectory-based worktree allocations
            CREATE TABLE IF NOT EXISTS subdirectory_allocations (
                subdirectory_key TEXT PRIMARY KEY,
                worktree_path TEXT NOT NULL,
                branch_name TEXT NOT NULL,
                locked_by_agent TEXT,
                locked_at REAL,
                lock_expires_at REAL,
                last_commit_at REAL,
                pending_commits INTEGER DEFAULT 0,
                created_at REAL DEFAULT (unixepoch('now', 'subsec')),
                updated_at REAL DEFAULT (unixepoch('now', 'subsec'))
            );

            -- Agent-subdirectory assignments
            CREATE TABLE IF NOT EXISTS agent_subdirectory_assignments (
                agent_id TEXT NOT NULL,
                subdirectory_key TEXT NOT NULL,
                assigned_at REAL DEFAULT (unixepoch('now', 'subsec')),
                PRIMARY KEY (agent_id, subdirectory_key),
                FOREIGN KEY (subdirectory_key) REFERENCES subdirectory_allocations(subdirectory_key)
            );

            CREATE INDEX IF NOT EXISTS idx_agent_subdir_agent ON agent_subdirectory_assignments(agent_id);
            CREATE INDEX IF NOT EXISTS idx_agent_subdir_key ON agent_subdirectory_assignments(subdirectory_key);

            -- Subdirectory status view
            CREATE VIEW IF NOT EXISTS v_subdirectory_status AS
            SELECT
                sa.subdirectory_key,
                sa.worktree_path,
                sa.branch_name,
                sa.locked_by_agent,
                sa.locked_at,
                sa.lock_expires_at,
                CASE
                    WHEN sa.lock_expires_at IS NOT NULL
                         AND sa.lock_expires_at > unixepoch('now', 'subsec')
                    THEN (sa.lock_expires_at - unixepoch('now', 'subsec')) / 60.0
                    ELSE NULL
                END as lock_minutes_remaining,
                sa.pending_commits,
                sa.last_commit_at
            FROM subdirectory_allocations sa;
        """,
        # Version 3 -> 4: Add per-branch progress tracking
        3: """
            -- Per-branch progress tracking for functions
            CREATE TABLE IF NOT EXISTS function_branch_progress (
                function_name TEXT NOT NULL,
                branch TEXT NOT NULL,
                scratch_slug TEXT,
                match_percent REAL DEFAULT 0.0,
                score INTEGER,
                max_score INTEGER,
                agent_id TEXT,
                worktree_path TEXT,
                is_committed BOOLEAN DEFAULT FALSE,
                commit_hash TEXT,
                created_at REAL DEFAULT (unixepoch('now', 'subsec')),
                updated_at REAL DEFAULT (unixepoch('now', 'subsec')),
                PRIMARY KEY (function_name, branch)
            );

            CREATE INDEX IF NOT EXISTS idx_branch_progress_function ON function_branch_progress(function_name);
            CREATE INDEX IF NOT EXISTS idx_branch_progress_branch ON function_branch_progress(branch);
            CREATE INDEX IF NOT EXISTS idx_branch_progress_match ON function_branch_progress(match_percent DESC);

            -- Branch progress summary view
            CREATE VIEW IF NOT EXISTS v_function_branch_progress AS
            SELECT
                fbp.function_name,
                fbp.branch,
                fbp.scratch_slug,
                fbp.match_percent,
                fbp.agent_id,
                fbp.is_committed,
                fbp.updated_at,
                f.match_percent as canonical_match_percent,
                f.status as canonical_status,
                CASE WHEN fbp.match_percent > COALESCE(f.match_percent, 0) THEN 1 ELSE 0 END as is_best_match
            FROM function_branch_progress fbp
            LEFT JOIN functions f ON fbp.function_name = f.function_name
            ORDER BY fbp.function_name, fbp.match_percent DESC;
        """,
        # Version 4 -> 5: Add build_status and build_diagnosis for tracking broken commits
        4: """
            -- Add build_status and build_diagnosis columns
            ALTER TABLE functions ADD COLUMN build_status TEXT CHECK(build_status IN ('passing', 'broken') OR build_status IS NULL);
            ALTER TABLE functions ADD COLUMN build_diagnosis TEXT;

            -- Update status CHECK constraint by recreating table
            -- (SQLite doesn't support ALTER CHECK)
            DROP VIEW IF EXISTS v_active_claims;
            DROP VIEW IF EXISTS v_uncommitted_matches;
            DROP VIEW IF EXISTS v_stale_data;
            DROP VIEW IF EXISTS v_agent_summary;
            DROP VIEW IF EXISTS v_function_branch_progress;

            CREATE TABLE IF NOT EXISTS functions_new (
                function_name TEXT PRIMARY KEY,
                match_percent REAL DEFAULT 0.0,
                current_score INTEGER,
                max_score INTEGER,
                status TEXT CHECK(status IN (
                    'unclaimed', 'claimed', 'in_progress', 'matched', 'committed', 'committed_needs_fix', 'merged', 'in_review'
                )) DEFAULT 'unclaimed',
                build_status TEXT CHECK(build_status IN ('passing', 'broken') OR build_status IS NULL),
                build_diagnosis TEXT,
                local_scratch_slug TEXT,
                production_scratch_slug TEXT,
                is_committed BOOLEAN DEFAULT FALSE,
                commit_hash TEXT,
                branch TEXT,
                worktree_path TEXT,
                pr_url TEXT,
                pr_number INTEGER,
                pr_state TEXT CHECK(pr_state IN ('OPEN', 'CLOSED', 'MERGED') OR pr_state IS NULL),
                claimed_by_agent TEXT,
                claimed_at REAL,
                source_file_path TEXT,
                notes TEXT,
                created_at REAL DEFAULT (unixepoch('now', 'subsec')),
                updated_at REAL DEFAULT (unixepoch('now', 'subsec')),
                local_scratch_verified_at REAL,
                production_scratch_verified_at REAL,
                git_verified_at REAL
            );

            -- Copy data from old table (columns in order, NULLs for new columns)
            INSERT INTO functions_new (
                function_name, match_percent, current_score, max_score, status,
                local_scratch_slug, production_scratch_slug, is_committed, commit_hash, branch,
                worktree_path, pr_url, pr_number, pr_state, claimed_by_agent, claimed_at,
                source_file_path, notes, created_at, updated_at,
                local_scratch_verified_at, production_scratch_verified_at, git_verified_at
            )
            SELECT
                function_name, match_percent, current_score, max_score, status,
                local_scratch_slug, production_scratch_slug, is_committed, commit_hash, branch,
                worktree_path, pr_url, pr_number, pr_state, claimed_by_agent, claimed_at,
                source_file_path, notes, created_at, updated_at,
                local_scratch_verified_at, production_scratch_verified_at, git_verified_at
            FROM functions;

            DROP TABLE functions;
            ALTER TABLE functions_new RENAME TO functions;

            -- Recreate views
            CREATE VIEW IF NOT EXISTS v_active_claims AS
            SELECT
                c.function_name,
                c.agent_id,
                c.claimed_at,
                c.expires_at,
                (c.expires_at - unixepoch('now', 'subsec')) / 60.0 as minutes_remaining,
                f.match_percent,
                f.local_scratch_slug
            FROM claims c
            LEFT JOIN functions f ON c.function_name = f.function_name
            WHERE c.expires_at > unixepoch('now', 'subsec');

            CREATE VIEW IF NOT EXISTS v_uncommitted_matches AS
            SELECT
                f.function_name,
                f.match_percent,
                f.local_scratch_slug,
                f.production_scratch_slug,
                f.claimed_by_agent,
                f.branch,
                f.updated_at
            FROM functions f
            WHERE f.match_percent >= 95.0
              AND f.is_committed = FALSE
              AND f.status != 'merged';

            CREATE VIEW IF NOT EXISTS v_stale_data AS
            SELECT
                function_name,
                'local_scratch' as stale_type,
                local_scratch_verified_at as last_verified,
                (unixepoch('now', 'subsec') - local_scratch_verified_at) / 3600.0 as hours_stale
            FROM functions
            WHERE local_scratch_slug IS NOT NULL
              AND (local_scratch_verified_at IS NULL
                   OR unixepoch('now', 'subsec') - local_scratch_verified_at > 3600)
            UNION ALL
            SELECT
                function_name,
                'production_scratch' as stale_type,
                production_scratch_verified_at,
                (unixepoch('now', 'subsec') - production_scratch_verified_at) / 3600.0
            FROM functions
            WHERE production_scratch_slug IS NOT NULL
              AND (production_scratch_verified_at IS NULL
                   OR unixepoch('now', 'subsec') - production_scratch_verified_at > 86400)
            UNION ALL
            SELECT
                function_name,
                'git' as stale_type,
                git_verified_at,
                (unixepoch('now', 'subsec') - git_verified_at) / 3600.0
            FROM functions
            WHERE is_committed = TRUE
              AND (git_verified_at IS NULL
                   OR unixepoch('now', 'subsec') - git_verified_at > 86400);

            CREATE VIEW IF NOT EXISTS v_agent_summary AS
            SELECT
                a.agent_id,
                a.worktree_path,
                a.branch_name,
                a.last_active_at,
                (SELECT COUNT(*) FROM claims c
                 WHERE c.agent_id = a.agent_id
                   AND c.expires_at > unixepoch('now', 'subsec')) as active_claims,
                (SELECT COUNT(*) FROM functions f
                 WHERE f.claimed_by_agent = a.agent_id
                   AND f.is_committed = TRUE) as committed_functions
            FROM agents a;

            -- View for functions needing build fixes per worktree
            CREATE VIEW IF NOT EXISTS v_worktree_broken_builds AS
            SELECT
                worktree_path,
                COUNT(*) as broken_count,
                GROUP_CONCAT(function_name, ', ') as broken_functions
            FROM functions
            WHERE build_status = 'broken'
              AND worktree_path IS NOT NULL
            GROUP BY worktree_path;

            CREATE VIEW IF NOT EXISTS v_function_branch_progress AS
            SELECT
                fbp.function_name,
                fbp.branch,
                fbp.scratch_slug,
                fbp.match_percent,
                fbp.agent_id,
                fbp.is_committed,
                fbp.updated_at,
                f.match_percent as canonical_match_percent,
                f.status as canonical_status,
                CASE WHEN fbp.match_percent > COALESCE(f.match_percent, 0) THEN 1 ELSE 0 END as is_best_match
            FROM function_branch_progress fbp
            LEFT JOIN functions f ON fbp.function_name = f.function_name
            ORDER BY fbp.function_name, fbp.match_percent DESC;
        """,
        # Version 5 -> 6: Add documentation tracking columns
        5: """
            -- Add documentation tracking columns
            ALTER TABLE functions ADD COLUMN is_documented BOOLEAN DEFAULT FALSE;
            ALTER TABLE functions ADD COLUMN documentation_status TEXT CHECK(documentation_status IN ('none', 'partial', 'complete') OR documentation_status IS NULL) DEFAULT 'none';
            ALTER TABLE functions ADD COLUMN documented_at REAL;

            -- View for documented functions
            CREATE VIEW IF NOT EXISTS v_documented_functions AS
            SELECT
                function_name,
                match_percent,
                documentation_status,
                documented_at,
                is_committed,
                status,
                updated_at
            FROM functions
            WHERE is_documented = TRUE OR documentation_status IN ('partial', 'complete');
        """,
        # Version 6 -> 7: Add canonical_address for stable function identification and alias tracking
        6: """
            -- Add canonical_address column to functions table
            ALTER TABLE functions ADD COLUMN canonical_address TEXT;

            -- Create index for address lookups
            CREATE INDEX IF NOT EXISTS idx_functions_address ON functions(canonical_address);

            -- Function rename/alias tracking table
            CREATE TABLE IF NOT EXISTS function_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_address TEXT NOT NULL,
                old_name TEXT NOT NULL,
                new_name TEXT,
                renamed_at REAL NOT NULL DEFAULT (unixepoch('now', 'subsec')),
                source TEXT CHECK(source IN ('report_sync', 'manual', 'git_history', 'symbols') OR source IS NULL),
                UNIQUE(canonical_address, old_name)
            );

            CREATE INDEX IF NOT EXISTS idx_aliases_address ON function_aliases(canonical_address);
            CREATE INDEX IF NOT EXISTS idx_aliases_old_name ON function_aliases(old_name);

            -- View for functions with known aliases
            CREATE VIEW IF NOT EXISTS v_function_aliases AS
            SELECT
                f.function_name,
                f.canonical_address,
                f.match_percent,
                f.status,
                fa.old_name as previous_name,
                fa.renamed_at,
                fa.source as rename_source
            FROM functions f
            LEFT JOIN function_aliases fa ON f.canonical_address = fa.canonical_address
            WHERE fa.old_name IS NOT NULL
            ORDER BY f.function_name, fa.renamed_at DESC;
        """,
        # Version 7 -> 8: Add worktree_path and branch to match_history for tracing work location
        7: """
            -- Add worktree tracking columns to match_history
            ALTER TABLE match_history ADD COLUMN worktree_path TEXT;
            ALTER TABLE match_history ADD COLUMN branch TEXT;

            -- Create index for branch-based queries
            CREATE INDEX IF NOT EXISTS idx_match_history_branch ON match_history(branch);
        """,
    }
