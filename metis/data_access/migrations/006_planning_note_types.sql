-- ADR-021: goal and project become storable note types.
--
-- SQLite cannot alter a CHECK constraint, so each table is rebuilt. The order is
-- deliberate: DROP TABLE performs an implicit DELETE FROM, which increments the
-- deferred foreign-key counter for every child row that referenced it. Only a
-- real INSERT back into the parent clears that counter, so each table is dropped,
-- recreated under its own name, and refilled. Renaming a replacement table into
-- place does not clear it and fails at COMMIT.
--
-- `defer_foreign_keys` is used rather than `foreign_keys` because the migration
-- runner wraps this script in a transaction, where `foreign_keys` is a no-op.

PRAGMA defer_foreign_keys = ON;

CREATE TEMP TABLE classification_carry AS SELECT * FROM classification;

DROP TABLE classification;

CREATE TABLE classification (
    classification_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT UNIQUE NOT NULL,
    candidate_type TEXT NOT NULL CHECK (
        candidate_type IN (
            'idea', 'reference', 'decision', 'question', 'task', 'goal', 'project'
        )
    ),
    sensitivity TEXT NOT NULL CHECK (sensitivity IN ('normal', 'sensitive')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    routing TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    raw_response_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (capture_id) REFERENCES intake (capture_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

INSERT INTO classification SELECT * FROM classification_carry;

DROP TABLE classification_carry;

CREATE TEMP TABLE proposal_carry AS SELECT * FROM proposal;

DROP TABLE proposal;

CREATE TABLE proposal (
    proposal_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT NOT NULL,
    classification_id TEXT NOT NULL,
    note_type TEXT NOT NULL CHECK (
        note_type IN (
            'idea', 'reference', 'decision', 'question', 'task', 'goal', 'project'
        )
    ),
    title TEXT NOT NULL,
    body_path TEXT NOT NULL,
    proposed_links TEXT NOT NULL,
    evidence_refs TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    sensitivity TEXT NOT NULL CHECK (sensitivity IN ('normal', 'sensitive')),
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    reason TEXT NOT NULL,
    uncertainties_json TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    raw_response_path TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (
        length(content_hash) = 64
        AND content_hash NOT GLOB '*[^0-9a-f]*'
    ),
    draft_note_path TEXT,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'approved', 'rejected', 'superseded')
    ),
    created_at TEXT NOT NULL,
    FOREIGN KEY (capture_id) REFERENCES intake (capture_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (classification_id) REFERENCES classification (classification_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

INSERT INTO proposal SELECT * FROM proposal_carry;

DROP TABLE proposal_carry;

CREATE UNIQUE INDEX idx_proposal_capture_id_unique
ON proposal (capture_id);

CREATE UNIQUE INDEX idx_proposal_classification_id_unique
ON proposal (classification_id);
