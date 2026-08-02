CREATE TEMP TABLE migration_003_proposal_guard (
    row_count INTEGER NOT NULL CHECK (row_count = 0)
);

INSERT INTO migration_003_proposal_guard (row_count)
SELECT COUNT(*) FROM proposal;

DROP TABLE migration_003_proposal_guard;

CREATE TABLE intake_v3 (
    capture_id TEXT PRIMARY KEY NOT NULL,
    content_hash TEXT UNIQUE NOT NULL,
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
    trace_id TEXT NOT NULL
);

INSERT INTO intake_v3 (
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
FROM intake;

CREATE TABLE classification_v3 (
    classification_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT UNIQUE NOT NULL,
    candidate_type TEXT NOT NULL CHECK (
        candidate_type IN ('idea', 'reference', 'decision', 'question', 'task')
    ),
    sensitivity TEXT NOT NULL CHECK (sensitivity IN ('normal', 'sensitive')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    routing TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    raw_response_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (capture_id) REFERENCES intake_v3 (capture_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

INSERT INTO classification_v3 (
    classification_id,
    capture_id,
    candidate_type,
    sensitivity,
    confidence,
    routing,
    model_id,
    prompt_version,
    raw_response_path,
    created_at
)
SELECT
    classification_id,
    capture_id,
    candidate_type,
    sensitivity,
    confidence,
    routing,
    model_id,
    prompt_version,
    raw_response_path,
    created_at
FROM classification;

DROP TABLE approval;
DROP TABLE proposal;
DROP TABLE classification;
DROP TABLE intake;

ALTER TABLE intake_v3 RENAME TO intake;
ALTER TABLE classification_v3 RENAME TO classification;

CREATE TABLE proposal (
    proposal_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT NOT NULL,
    classification_id TEXT NOT NULL,
    note_type TEXT NOT NULL CHECK (
        note_type IN ('idea', 'reference', 'decision', 'question', 'task')
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

CREATE UNIQUE INDEX idx_proposal_capture_id_unique
ON proposal (capture_id);

CREATE UNIQUE INDEX idx_proposal_classification_id_unique
ON proposal (classification_id);

CREATE TABLE approval (
    approval_id TEXT PRIMARY KEY NOT NULL,
    proposal_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    approver TEXT NOT NULL CHECK (approver LIKE 'human:%'),
    observed_status TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    committed_at TEXT,
    revoked_at TEXT,
    FOREIGN KEY (proposal_id) REFERENCES proposal (proposal_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE TABLE proposal_reservation (
    proposal_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT UNIQUE NOT NULL,
    classification_id TEXT UNIQUE NOT NULL,
    lease_token TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    FOREIGN KEY (capture_id) REFERENCES intake (capture_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (classification_id) REFERENCES classification (classification_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);
