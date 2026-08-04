-- ADR-022 clause 9: the intake pin projection joins the uniqueness key.
--
-- `type_pin` and `parent_id` are NOT NULL DEFAULT '' so that NULL never enters
-- the key: SQLite treats NULLs as distinct, and a nullable column would
-- silently disable replay protection for unpinned captures.
--
-- SQLite cannot drop a column-level UNIQUE, so `intake` is rebuilt on the
-- pattern migration 006 established: DROP TABLE performs an implicit DELETE
-- FROM, which increments the deferred foreign-key counter for every child row
-- that referenced it, and only a real INSERT back into the parent clears that
-- counter. Renaming a replacement table into place does not clear it and fails
-- at COMMIT, so the table is dropped, recreated under its own name, and
-- refilled.
--
-- Existing rows carry the sentinel through the refill SELECT; there is no
-- separate backfill statement, and no reconciliation is required because the
-- previous UNIQUE(content_hash) already permitted at most one row per hash.
-- The migration runner reads *.sql only and cannot open evidence, so a row
-- captured before this ADR keeps the sentinel even where its evidence records
-- a pin. Those rows diverge by construction and fail closed on replay as
-- `intake_pin_unprojected`; repairing one is a documented UPDATE, not a code
-- path.

PRAGMA defer_foreign_keys = ON;

CREATE TEMP TABLE intake_carry AS SELECT * FROM intake;

DROP TABLE intake;

CREATE TABLE intake (
    capture_id TEXT PRIMARY KEY NOT NULL,
    content_hash TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type = 'cli-typed'),
    evidence_path TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'captured',
            'classifying',
            'classified',
            'proposing',
            'proposed',
            'awaiting_approval',
            'approved',
            'filed',
            'rejected',
            'failed'
        )
    ),
    state_updated_at TEXT NOT NULL,
    failure_reason TEXT,
    trace_id TEXT NOT NULL,
    type_pin TEXT NOT NULL DEFAULT '' CHECK (
        type_pin IN ('', 'goal', 'project', 'task')
    ),
    parent_id TEXT NOT NULL DEFAULT '' CHECK (
        parent_id = '' OR parent_id NOT GLOB '*[^A-Za-z0-9._-]*'
    ),
    UNIQUE (content_hash, type_pin, parent_id)
);

INSERT INTO intake (
    capture_id,
    content_hash,
    captured_at,
    source_type,
    evidence_path,
    state,
    state_updated_at,
    failure_reason,
    trace_id
)
SELECT
    capture_id,
    content_hash,
    captured_at,
    source_type,
    evidence_path,
    state,
    state_updated_at,
    failure_reason,
    trace_id
FROM intake_carry;

DROP TABLE intake_carry;
