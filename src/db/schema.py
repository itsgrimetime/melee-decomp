"""SQLite schema for agent state management."""

SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- Core function tracking
CREATE TABLE IF NOT EXISTS functions (
    function_name TEXT PRIMARY KEY,
    -- Match state
    match_percent REAL DEFAULT 0.0,
    current_score INTEGER,
    max_score INTEGER,
    status TEXT CHECK(status IN (
        'unclaimed', 'claimed', 'in_progress', 'matched', 'committed', 'merged'
    )) DEFAULT 'unclaimed',
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
    timestamp REAL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS idx_match_history_slug ON match_history(scratch_slug);

-- Local to production sync tracking
CREATE TABLE IF NOT EXISTS sync_state (
    local_slug TEXT NOT NULL,
    production_slug TEXT NOT NULL,
    function_name TEXT,
    synced_at REAL,
    PRIMARY KEY (local_slug, production_slug)
);

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
        # Future migrations go here
        # 1: "ALTER TABLE functions ADD COLUMN new_field TEXT;",
    }
