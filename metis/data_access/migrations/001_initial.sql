CREATE TABLE intake (
    capture_id TEXT PRIMARY KEY NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    captured_at TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type = 'cli-typed'),
    evidence_path TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'captured',
            'classifying',
            'classified',
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

CREATE TABLE classification (
    classification_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT NOT NULL,
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
    FOREIGN KEY (capture_id) REFERENCES intake (capture_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

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
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    reason TEXT NOT NULL,
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

CREATE TABLE audit_event (
    event_id TEXT PRIMARY KEY NOT NULL,
    trace_id TEXT NOT NULL,
    capture_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure', 'refused')),
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
