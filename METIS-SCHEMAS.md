# Metis Schemas — Phase 1

> The information and state model required by [METIS-EXECUTION-BLUEPRINT.md](METIS-EXECUTION-BLUEPRINT.md)
> Phase 1. Decisions referenced are in [METIS-DECISIONS.md](METIS-DECISIONS.md).
>
> The first five operational-state tables are implemented by
> `metis/data_access/migrations/001_initial.sql`; migration `002_unique_classification_capture.sql` enforces one
> classification per capture; migration `003_proposal_reservation.sql` adds reservation-first proposal state,
> the expanded proposal contract, and replay uniqueness; migration `004_unique_approval_proposal.sql` enforces
> one approval per proposal; migration `005_audit_event_append_only.sql` makes `audit_event` refuse every
> update and delete. Source, classification-response, proposal-response, canonical proposal-content, and
> proposed-draft storage are implemented, as are approval detection and recording, permanent filing and
> linking, and audit emission across every transition.

## Scope

Covers exactly what the MVP loop needs: one typed capture moving from raw input to an approved, linked,
audited note. Anything not required by that path is deliberately absent.

Three stores, per ADR-001 / ADR-002 / ADR-003:

- **Evidence** — append-only files on disk
- **Operational state** — SQLite, reached only through the data-access layer
- **Durable knowledge** — Obsidian Markdown with YAML frontmatter

---

## 1. Evidence store

### 1.1 Source capture evidence

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
  "capture_id": "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70",
  "content_hash": "sha256:9f2b...c41e",
  "captured_at": "2026-07-28T22:41:07Z",
  "source_type": "cli-typed",
  "source_detail": "metis capture",
  "byte_size": 412,
  "mime_type": "text/plain",
  "type_pin": "goal",
  "parent_goal_id": null,
  "schema_version": 2
}
```

Rules:

- Written **before** anything interprets the input (REQ-INTK-001).
- Never modified after creation. Corrections create new captures that supersede, never overwrite.
- `capture_id` is a UUID4 generated only for genuinely new evidence.
- UUID4 capture identifiers are stable handles, not chronological sort keys.
- `content_hash` is computed over the raw bytes only, not the metadata.
- `type_pin` is `null`, `"goal"`, or `"project"` — the human's planning intent from `metis capture --as`,
  recorded here before any model call so classification can never invent it (ADR-021). `parent_goal_id` is
  set only for a project and names the goal it belongs to.
- The pin is part of the evidence, not a later annotation: replaying identical text under a different pin is
  refused as `pin_conflict` rather than rewriting the original. Because evidence is immutable, text captured
  unpinned cannot be re-pinned — capture it again as the type you meant.
- `schema_version` is `2` from the ADR-021 pin fields onward. The store accepts exactly these ten keys and
  only version 2; version 1 evidence, which predates the pin, fails closed rather than being upgraded in
  place — evidence is never rewritten.

### 1.2 Classification response evidence

Layout:

```text
classification-evidence/
└── <classification_id>/
    ├── raw-response.txt # exact assistant text encoded as UTF-8
    └── meta.json        # response provenance, written once
```

`meta.json`:

```json
{
  "classification_id": "01K1D5Q5M00000000000000000",
  "capture_id": "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70",
  "model_id": "claude-sonnet-4-6",
  "prompt_version": "classify-v1",
  "received_at": "2026-08-01T20:00:00Z",
  "byte_size": 72,
  "schema_version": 1
}
```

Rules:

- `classification_id` is a canonical 26-character Crockford Base32 ULID.
- `raw-response.txt` contains exactly `raw_text.encode("utf-8")`; it is not trimmed, normalized, or rewritten.
- The response directory is exclusively created and validated before the response is parsed or classification is persisted.
- Response evidence is separate from immutable source evidence and is never permanent knowledge.
- Partial, colliding, corrupt, or inconsistent response evidence is preserved and fails closed; Metis does not repair it automatically.

### 1.3 Proposal response evidence and canonical content

The exact provider response is append-only at
`proposal-evidence/<proposal_id>/{raw-response.txt,meta.json}`. Its metadata binds the proposal, classification,
capture, actual model, immutable `propose-v1` prompt version, received timestamp, and byte count. Metis validates
that evidence before parsing it.

Validated semantic content is rendered separately at
`proposal-content/<proposal_id>/{body.md,meta.json}`. Its metadata binds the same identities, the raw-response
SHA-256, canonical body SHA-256, and byte count. Neither store is durable knowledge. Complete matching artifacts
may be reused during explicit crash recovery; partial, corrupt, colliding, symlinked, or disagreeing artifacts
fail closed and are never repaired or overwritten.

---

## 2. Operational state (SQLite)

### 2.1 `intake`

One row per capture. The spine of the loop.

| Column | Type | Notes |
|---|---|---|
| `capture_id` | TEXT PK | UUID4, matches the evidence directory |
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
| `capture_id` | TEXT FK → intake, UNIQUE | migration 002 enforces one classification per capture |
| `candidate_type` | TEXT | `idea` · `reference` · `decision` · `question` · `task` |
| `sensitivity` | TEXT | `normal` · `sensitive` |
| `confidence` | REAL | 0.0–1.0 |
| `routing` | TEXT | deterministic `proposal:<candidate_type>` label; Step 3 does not create a proposal |
| `model_id` | TEXT | the model actually used |
| `prompt_version` | TEXT | REQ-MODEL-003 |
| `raw_response_path` | TEXT | the model's unmodified output, stored as evidence |
| `created_at` | TEXT | |

The model's raw response is preserved before parsing. A classification is evidence of what a model said, not
a fact, and Step 3 grants it no write authority over durable knowledge.

### 2.3 `proposal`

| Column | Type | Notes |
|---|---|---|
| `proposal_id` | TEXT PK | ULID |
| `capture_id` | TEXT FK → intake, UNIQUE | one proposal per capture |
| `classification_id` | TEXT FK → classification, UNIQUE | replay/idempotency key |
| `note_type` | TEXT | the typed note this would become |
| `title` | TEXT | proposed title |
| `body_path` | TEXT | proposed note body, on disk, not yet in the vault |
| `proposed_links` | TEXT (JSON) | always `[]` — Metis never proposes a link (ADR-020). The human types links into the draft, and Step 6 resolves them at filing time; they are not persisted here |
| `evidence_refs` | TEXT (JSON) | everything this proposal rests on |
| `confidence` | REAL | |
| `sensitivity` | TEXT | copied from classification: `normal` · `sensitive` |
| `risk_level` | TEXT | deterministic: `normal → low`, `sensitive → high` |
| `reason` | TEXT | why this classification and destination |
| `uncertainties_json` | TEXT (JSON) | validated unresolved points |
| `model_id` | TEXT | actual proposal model returned by the adapter |
| `prompt_version` | TEXT | exactly `propose-v1` |
| `raw_response_path` | TEXT | exact proposal-response evidence path |
| `content_hash` | TEXT | lowercase SHA-256 of canonical `body.md` bytes |
| `draft_note_path` | TEXT NULL | where the draft was written in the vault |
| `state` | TEXT | `pending` · `approved` · `rejected` · `superseded` |
| `created_at` | TEXT | |

Partially implements REQ-GOV-003. A proposal writes nothing permanent — the draft note in the vault is
explicitly marked `status: proposed` and is not durable knowledge. Approver and decision live in `approval`
(§2.5) and are recorded by Step 5; `state` moves to `approved` or `rejected` in the same transaction.

### 2.4 `proposal_reservation`

Transient coordination for reservation-first proposal creation; it is not a proposal, approval, or audit row.

| Column | Type | Notes |
|---|---|---|
| `proposal_id` | TEXT PK | stable ULID retained across reclaim |
| `capture_id` | TEXT FK → intake, UNIQUE | |
| `classification_id` | TEXT FK → classification, UNIQUE | |
| `lease_token` | TEXT | UUID4 fencing token |
| `reserved_at` | TEXT | canonical UTC timestamp |
| `lease_expires_at` | TEXT | exactly 15 minutes after reservation or reclaim |

An active lease refuses competing work. An explicit invocation may compare-and-swap reclaim an expired lease
with the same proposal ID and a new token. A stale token cannot complete or record failure.

### 2.5 `approval`

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
than inferred. Migration 004 makes `proposal_id` unique: a second decision for one proposal fails at the data
layer. `committed_at` is set by permanent filing (Step 6) and is `NULL` while only a decision exists.
`revoked_at` is unused until revocation is designed (open question 2).

### 2.6 `audit_event`

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
recorded as such, not as an error. A `duplicate` — a replayed capture, proposal, or filing — is refused for
the same reason: nothing new was written.

The orchestrator builds every event; the data layer only writes it (ADR-007). A transition carries its
event into the transition's own transaction, so the two commit together or neither does — one event per
transition, structurally. An action that transitions nothing appends its event on its own, under
`command.<name>`. Migration 005 refuses every `UPDATE` and `DELETE` on this table, so "never updated, never
deleted" is enforced rather than promised. Nothing reads the trail yet; `metis status` is unimplemented.

---

## 3. Intake state machine

```text
captured ──→ classifying ──→ classified ──→ proposing ──→ proposed ──→ awaiting_approval
                  │               │             │            │               │
                  ↓               ↓             ↓            ↓               ├──→ approved ──→ filed
                failed          failed        failed        failed            └──→ rejected
```

Legal transitions only. Any other jump is rejected by the orchestrator and recorded as `refused`
(REQ-ORCH-001).

| State | Meaning | Exit condition |
|---|---|---|
| `captured` | Evidence written and hashed | Classification dispatched |
| `classifying` | Model call in flight | Response received or timeout |
| `classified` | Candidate type and confidence recorded | Proposal built |
| `proposing` | One fenced reservation owns proposal generation | Proposal persisted or known failure recorded |
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

Created through `metis capture --as goal` → classify → propose → approve → `metis file` (ADR-021). Filed under
`vault/goals/` with a deterministic id `goal.<title-slug>-<8 hex of capture_id>`. Planning `status` defaults to
`active` at file time; the approval decision lives in SQLite + audit, not in this field.

```yaml
---
id: goal.health-baseline-8f14e45f
type: goal
title: Establish a health baseline
status: active          # active · achieved · abandoned
horizon: annual         # long-term · annual · quarterly  (system default: annual)
created: 2026-07-28
capture_id: 8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70
evidence:
  capture: evidence/8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70/raw.txt
links: []               # empty is allowed — a goal has nothing above it
---
```

### 4.2 Project

Per ADR-013 — one entity, runtime is an optional property. Created through
`metis capture --as project --goal <goal-id>` (ADR-021). Filed under `vault/projects/` as
`proj.<title-slug>-<8 hex of capture_id>`. The parent is system-written as `goal: "[[…]]"` from the capture pin
(not human-editable under ADR-020) and must resolve to an existing note in `vault/goals/` at file time.

```yaml
---
id: proj.metis-core-8f14e45f
type: project
title: Metis core loop
status: active          # active · paused · completed · archived
goal: "[[goal.personal-systems-aaaaaaaa]]"
created: 2026-07-28
capture_id: 8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70
evidence:
  capture: evidence/8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70/raw.txt
links: []               # parent lives in `goal:`; links may stay empty
runtime: none           # none · docker   (ADR-011)
runtime_ref: null       # path to container definition when runtime is docker
---
```

Most projects carry `runtime: none`. The property exists from day one so that adding containers later is a
value change, not a schema migration. Runtime fields remain optional on Metis-filed projects until a later
slice writes them.

### 4.3 Typed note — what an approved capture becomes

Plain `metis capture` (no `--as`) still produces typed notes under `vault/notes/filed/` (ADR-021 leaves this
path unchanged). Drafts for every type — including pinned goals and projects — still land in
`vault/notes/proposed/` so Obsidian remains one approval inbox.

```yaml
---
id: note.01J8X2K4P7M3QRSTVWXYZ0ABCD
type: idea              # idea · reference · decision · question · task
title: Batch weekly review into Sunday evening
status: proposed        # proposed · approved · rejected   ← ADR-005: the approval field
verification: unverified # unverified · verified
created: 2026-07-28
approved: null          # timestamp, set on commit
capture_id: 8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70
evidence: evidence/8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70/raw.txt
confidence: 0.82
sensitivity: normal
links:
  - "[[proj.metis-core-8f14e45f]]"
---
```

Rules:

- A draft has exactly **two** human-editable fields, `status` and `links` (ADR-020). Everything else is
  written by the system and is byte-exact; the draft store refuses a draft that differs anywhere else.
- `status` is the only field that **authorizes** a change (ADR-005). `links` supplies content and authorizes
  nothing, so there is still exactly one approval surface. On a filed goal or project the planning `status`
  is `active` (not `approved`); the authorizing decision remains the draft flip recorded in SQLite.
- `links` is either `links: []` or `links:` followed by one or more `  - "[[target]]"` lines. Targets are
  restricted to `[A-Za-z0-9._-]+`, must be unique, and are typed by the human — Metis never proposes a link.
  Goals and projects are created through pinned capture (ADR-021), not by inventing a link target.
- Link rules by effective type (ADR-021): a **typed note** needs ≥1 resolvable link; a **goal** may file with
  `links: []`; a **project** names its parent in system-written `goal:` and may use `links: []`.
- `capture_id` and `evidence` are mandatory on typed notes, goals, and projects — a note without provenance
  is invalid (REQ-VLT-004).
- `verification` is separate from `status`. Approving that a note should exist is not the same as verifying
  its content is true (REQ-DATA-005).
- Every present link must resolve to an existing note — matched against the `id` field of a note in
  `vault/goals/` or `vault/projects/`, not against its filename. An unresolvable link blocks the commit rather
  than creating an orphan (REQ-INTK-004). It does not invalidate the approval: correct the link and re-run
  `metis file`.
- Filing routes by effective type: `goal` → `vault/goals/`, `project` → `vault/projects/`, other →
  `vault/notes/filed/` (ADR-021).
- `approved` holds the timestamp at which the human's decision was *detected*, not the moment of filing, so
  the filed bytes are a deterministic function of the approval. The filing time lives in
  `approval.committed_at`.

### 4.4 Vault layout

```text
vault/
├── goals/            # permanent Goal notes (filed via ADR-021)
├── projects/         # permanent Project notes (filed via ADR-021)
├── notes/
│   ├── proposed/     # drafts awaiting your decision (all types)
│   └── filed/        # approved typed notes
└── archive/
```

Drafts live in a separate directory so that "everything in `filed/` is approved" is structurally true, not a
convention to remember. Goals and projects are permanent outside `notes/filed/` because their planning
`status` vocabulary is not the approval field.

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
