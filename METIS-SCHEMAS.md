# Metis Schemas — Phase 1

> The information and state model required by [METIS-EXECUTION-BLUEPRINT.md](METIS-EXECUTION-BLUEPRINT.md)
> Phase 1. Decisions referenced are in [METIS-DECISIONS.md](METIS-DECISIONS.md).
>
> This is a design document. No table, file, or note described here exists yet.

## Scope

Covers exactly what the MVP loop needs: one typed capture moving from raw input to an approved, linked,
audited note. Anything not required by that path is deliberately absent.

Three stores, per ADR-001 / ADR-002 / ADR-003:

- **Evidence** — append-only files on disk
- **Operational state** — SQLite, reached only through the data-access layer
- **Durable knowledge** — Obsidian Markdown with YAML frontmatter

---

## 1. Evidence store

Layout:

```text
evidence/
└── <capture_id>/
    ├── raw.txt        # the input, byte-for-byte, never modified
    └── meta.json      # provenance about the capture, written once
```

`meta.json`:

```json
{
  "capture_id": "01J8X2K4P7M3QRSTVWXYZ0ABCD",
  "content_hash": "sha256:9f2b...c41e",
  "captured_at": "2026-07-28T22:41:07Z",
  "source_type": "cli-typed",
  "source_detail": "metis capture",
  "byte_size": 412,
  "mime_type": "text/plain",
  "schema_version": 1
}
```

Rules:

- Written **before** anything interprets the input (REQ-INTK-001).
- Never modified after creation. Corrections create new captures that supersede, never overwrite.
- `capture_id` is a ULID — sortable by creation time, which makes the directory listing chronological.
- `content_hash` is computed over the raw bytes only, not the metadata.

---

## 2. Operational state (SQLite)

### 2.1 `intake`

One row per capture. The spine of the loop.

| Column | Type | Notes |
|---|---|---|
| `capture_id` | TEXT PK | ULID, matches the evidence directory |
| `content_hash` | TEXT **UNIQUE** | ADR-014 — the uniqueness constraint *is* the replay protection |
| `captured_at` | TEXT | ISO-8601 UTC |
| `source_type` | TEXT | `cli-typed` for now |
| `evidence_path` | TEXT | relative path to the evidence directory |
| `state` | TEXT | see §3 |
| `state_updated_at` | TEXT | ISO-8601 UTC |
| `failure_reason` | TEXT NULL | populated only in `failed` |
| `trace_id` | TEXT | groups all events for one run |

The UNIQUE constraint on `content_hash` means a replayed capture fails at the data layer rather than relying
on application logic to notice. This is the enforcement mechanism for REQ-INTK-002.

### 2.2 `classification`

| Column | Type | Notes |
|---|---|---|
| `classification_id` | TEXT PK | ULID |
| `capture_id` | TEXT FK → intake | |
| `candidate_type` | TEXT | `idea` · `reference` · `decision` · `question` · `task` |
| `sensitivity` | TEXT | `normal` · `sensitive` |
| `confidence` | REAL | 0.0–1.0 |
| `routing` | TEXT | which downstream path was chosen |
| `model_id` | TEXT | the model actually used |
| `prompt_version` | TEXT | REQ-MODEL-003 |
| `raw_response_path` | TEXT | the model's unmodified output, stored as evidence |
| `created_at` | TEXT | |

The model's raw response is preserved. A classification is evidence of what a model said, not a fact.

### 2.3 `proposal`

| Column | Type | Notes |
|---|---|---|
| `proposal_id` | TEXT PK | ULID |
| `capture_id` | TEXT FK → intake | |
| `classification_id` | TEXT FK → classification | |
| `note_type` | TEXT | the typed note this would become |
| `title` | TEXT | proposed title |
| `body_path` | TEXT | proposed note body, on disk, not yet in the vault |
| `proposed_links` | TEXT (JSON) | array of target note IDs — must resolve (REQ-INTK-004) |
| `evidence_refs` | TEXT (JSON) | everything this proposal rests on |
| `confidence` | REAL | |
| `risk_level` | TEXT | `low` · `medium` · `high` |
| `reason` | TEXT | why this classification and destination |
| `draft_note_path` | TEXT NULL | where the draft was written in the vault |
| `state` | TEXT | `pending` · `approved` · `rejected` · `superseded` |
| `created_at` | TEXT | |

Satisfies REQ-GOV-003. A proposal writes nothing permanent — the draft note in the vault is explicitly marked
`status: proposed` and is not durable knowledge until approved.

### 2.4 `approval`

| Column | Type | Notes |
|---|---|---|
| `approval_id` | TEXT PK | ULID |
| `proposal_id` | TEXT FK → proposal | |
| `decision` | TEXT | `approved` · `rejected` |
| `approver` | TEXT | `human:<name>` — never an agent |
| `observed_status` | TEXT | the literal value read from the note's frontmatter |
| `detected_at` | TEXT | when the approval command found it |
| `committed_at` | TEXT NULL | when the resulting write completed |
| `revoked_at` | TEXT NULL | set if withdrawn before commit |

`observed_status` records what the vault actually said, so a disputed approval can be reconstructed rather
than inferred.

### 2.5 `audit_event`

Append-only. Never updated, never deleted.

| Column | Type | Notes |
|---|---|---|
| `event_id` | TEXT PK | ULID |
| `trace_id` | TEXT | groups one run end to end |
| `capture_id` | TEXT NULL | |
| `actor` | TEXT | `orchestrator` · `skill:<name>` · `human:<name>` |
| `action` | TEXT | e.g. `capture.written`, `approval.detected`, `note.committed` |
| `outcome` | TEXT | `success` · `failure` · `refused` |
| `detail` | TEXT (JSON) | |
| `created_at` | TEXT | |

`refused` is a first-class outcome. A blocked unapproved write is a successful enforcement and must be
recorded as such, not as an error.

---

## 3. Intake state machine

```text
captured ──→ classifying ──→ classified ──→ proposed ──→ awaiting_approval
                  │               │             │               │
                  ↓               ↓             ↓               ├──→ approved ──→ filed
                failed          failed        failed            └──→ rejected
```

Legal transitions only. Any other jump is rejected by the orchestrator and recorded as `refused`
(REQ-ORCH-001).

| State | Meaning | Exit condition |
|---|---|---|
| `captured` | Evidence written and hashed | Classification dispatched |
| `classifying` | Model call in flight | Response received or timeout |
| `classified` | Candidate type and confidence recorded | Proposal built |
| `proposed` | Proposal record exists | Draft written to vault |
| `awaiting_approval` | Draft visible in Obsidian, `status: proposed` | Human flips the status |
| `approved` | Approval recorded | Note committed |
| `filed` | Permanent note exists, linked, audited | Terminal |
| `rejected` | Human declined | Terminal — evidence retained |
| `failed` | A step errored | Retryable; evidence always survives (REQ-INTK-005) |

`failed` never presents as complete. Retry resumes from the last good state rather than re-capturing.

---

## 4. Obsidian note schemas

### 4.1 Goal

```yaml
---
id: goal.health-baseline
type: goal
title: Establish a health baseline
status: active          # active · achieved · abandoned
horizon: annual         # long-term · annual · quarterly
created: 2026-07-28
---
```

### 4.2 Project

Per ADR-013 — one entity, runtime is an optional property.

```yaml
---
id: proj.metis-core
type: project
title: Metis core loop
status: active          # active · paused · completed · archived
goal: "[[goal.personal-systems]]"
created: 2026-07-28
runtime: none           # none · docker   (ADR-011)
runtime_ref: null       # path to container definition when runtime is docker
---
```

Most projects carry `runtime: none`. The property exists from day one so that adding containers later is a
value change, not a schema migration.

### 4.3 Typed note — what an approved capture becomes

```yaml
---
id: note.01J8X2K4P7M3QRSTVWXYZ0ABCD
type: idea              # idea · reference · decision · question · task
title: Batch weekly review into Sunday evening
status: proposed        # proposed · approved · rejected   ← ADR-005: the approval field
verification: unverified # unverified · verified
created: 2026-07-28
approved: null          # timestamp, set on commit
capture_id: 01J8X2K4P7M3QRSTVWXYZ0ABCD
evidence: evidence/01J8X2K4P7M3QRSTVWXYZ0ABCD/raw.txt
confidence: 0.82
sensitivity: normal
links:
  - "[[proj.metis-core]]"
---
```

Rules:

- `status` is the **only** field a human edits to authorize a change (ADR-005). Everything else is written by
  the system.
- `capture_id` and `evidence` are mandatory — a note without provenance is invalid (REQ-VLT-004).
- `verification` is separate from `status`. Approving that a note should exist is not the same as verifying
  its content is true (REQ-DATA-005).
- `links` must resolve to existing notes. An unresolvable link blocks the commit rather than creating an
  orphan (REQ-INTK-004).

### 4.4 Vault layout

```text
vault/
├── goals/
├── projects/
├── notes/
│   ├── proposed/     # drafts awaiting your decision
│   └── filed/        # approved, permanent
└── archive/
```

Drafts live in a separate directory so that "everything in `filed/` is approved" is structurally true, not a
convention to remember.

---

## 5. What is deliberately absent

| Not included | Why |
|---|---|
| Agent and skill registry tables | No runtime agents exist (ADR-016, BP §14) |
| Embeddings / vector columns | ADR-018 |
| Relationship tables beyond `links` | Markdown links are sufficient until proven otherwise (ADR-018) |
| Per-project runtime tables | ADR-011 defers containers |
| Cost, token, and rate-limit tracking | REQ-ORCH-003 deferred |
| Supersession and archive mechanics | Open question 4 in the requirement ledger |
| Semantic duplicate detection | Open question 1 |

Each absence is a recorded decision, not an oversight.

---

## 6. Validation requirements

Before any of this is called working:

- Every table has a schema-validation test (REQ-TEST-003).
- The state machine rejects every illegal transition, with a test per illegal edge.
- `content_hash` uniqueness is proven by a replay test, not assumed from the constraint.
- Frontmatter validation runs against every note the system writes.
- A note without `capture_id` or `evidence` fails validation.
- The data-access layer is the only module containing SQL (REQ-DATA-003).
