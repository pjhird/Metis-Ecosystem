# Build-Order Step 2: Immutable Capture Design

**Status:** Approved design recorded for written review

**Branch:** `step/02-capture`

**Build-order scope:** Step 2 — capture, evidence storage, hashing, capture ID, and exact replay protection

## 1. Purpose

Step 2 accepts exactly one typed CLI value and preserves it as immutable source evidence before registering a
captured intake row. It proves byte-exact preservation, capture metadata, exact replay protection, and safe
recovery from a state-registration failure. It does not interpret the input or create permanent knowledge.

This design follows ADR-002, ADR-003, ADR-007, ADR-014, ADR-015, and ADR-017. It remains within the
single-process, single-writer MVP boundary established by ADR-011 and ADR-012.

## 2. Scope

### Included

- One CLI command with one positional text argument: `metis capture "<text>"`.
- The equivalent module entry point: `python -m metis capture "<text>"`.
- UTF-8 encoding of the argument with no trimming, normalization, or added newline.
- SHA-256 over the encoded raw bytes only, represented as `sha256:<lowercase hex>`.
- A UUID4 `capture_id` for genuinely new evidence.
- Immutable `evidence/<capture_id>/raw.txt` and write-once `meta.json`.
- A `captured` intake row registered only through `StateStore`.
- Exact replay detection by `content_hash`.
- Recovery and reuse of complete evidence left orphaned by a prior registration failure.
- Explicit `captured`, `duplicate`, `refused`, and `failed` outcomes.
- Minimal standard-library packaging needed for the two CLI entry points.
- Conservative requirement-ledger updates backed by step-2 tests.

### Excluded

Step 2 does not add classification, a model adapter, model calls, proposals, an Obsidian vault or draft,
approval, permanent-note filing, link resolution, audit events, integrations, agents, registries, watchers,
containers, Postgres, a UI, vector or graph databases, or autonomous behavior.

It does not add or claim either `duplicate_replay_creates_one_note` or
`source_survives_classification_failure`. Those behaviors require later build steps that do not yet exist.

`METIS-EXECUTION-SPINE.md` is unrelated, non-binding, and outside this change. It must not be read as a design
source or modified, staged, renamed, or committed.

## 3. Governing identifier decision

ADR-014 requires a UUID for every capture, while the current capture examples and descriptions in
`METIS-SCHEMAS.md` say ULID. The human owner selected UUID4 for `capture_id` only because it follows the
binding ADR and is available in Python's standard library.

Before implementation code is written, `METIS-SCHEMAS.md` must be reconciled as follows:

- change only capture-identifier descriptions and capture examples from ULID to UUID4;
- update capture-derived evidence paths and note provenance examples to use the UUID4 capture value;
- do not change the documented formats of `classification_id`, `proposal_id`, `approval_id`, or `event_id`;
- do not imply that sortable capture IDs remain part of the contract.

The existing `intake.trace_id` column must be populated at registration. Step 2 will use the new
`capture_id` as that capture's initial trace grouping value. This introduces no second identifier or new
downstream identifier format; a distinct trace-ID policy remains outside step 2.

## 4. Components and boundaries

### `metis/evidence.py`

Owns all evidence-filesystem behavior:

- exclusive creation of a UUID4 evidence directory;
- exclusive, write-once creation of `raw.txt` and `meta.json`;
- validation of existing evidence;
- scanning for evidence whose raw bytes match a requested content hash;
- recovery of complete, unregistered evidence;
- detection of partial, corrupt, conflicting, or colliding evidence.

It never registers operational state, deletes evidence, overwrites a file, or automatically repairs an
uncertain directory.

### `metis/capture.py`

Owns deterministic capture orchestration:

- encodes input and computes the content hash;
- queries `StateStore` before creating evidence;
- coordinates evidence validation, recovery, creation, and intake registration in the approved order;
- maps bounded conditions to `CaptureResult`;
- preserves evidence when registration fails.

It depends on the evidence and `StateStore` interfaces. It contains no SQL and no provider or vault access.

### `metis/data_access/contracts.py`

Adds:

- immutable `IntakeRecord`, matching all columns required to register the existing `intake` row;
- `find_intake_by_content_hash(content_hash)`;
- `register_intake(record)`;
- an engine-neutral registration result that distinguishes successful registration from an exact duplicate.

The contract does not expose `sqlite3` exceptions or SQL details to capture orchestration.

### `metis/data_access/sqlite.py`

Implements the two intake operations. It remains the only Python module that issues SQL. The existing
`UNIQUE(content_hash)` constraint is the final exact-replay backstop. A uniqueness violation for
`content_hash` becomes the engine-neutral duplicate result; unrelated database errors remain failures.

### `metis/cli.py`, `metis/__main__.py`, and packaging metadata

Provide both supported invocations through the same capture code path. The runtime root defaults to the
current working directory, with `state/metis.db` and `evidence/` beneath it. The command accepts exactly one
text value and adds no runtime dependency.

The CLI prints an explicit status and the relevant capture ID, evidence path, and reason when present. The
bounded `CaptureResult` is the machine-readable application contract; the implementation plan will lock the
exact stable CLI strings in tests.

## 5. Data contracts

### Raw evidence

`raw.txt` contains exactly the UTF-8 bytes produced from the CLI argument:

- leading and trailing whitespace are retained;
- embedded newlines are retained;
- Unicode is neither normalized nor rewritten;
- no newline or terminator is appended.

The content hash is computed over these bytes only. Metadata bytes, filenames, timestamps, and paths do not
participate in the digest.

### Evidence metadata

`meta.json` has exactly these fields:

| Field | Contract |
|---|---|
| `capture_id` | Canonical UUID4 string matching the directory name |
| `content_hash` | `sha256:<lowercase hex>` matching a fresh digest of `raw.txt` |
| `captured_at` | UTC timestamp serialized with a `Z` suffix |
| `source_type` | Literal `cli-typed` |
| `source_detail` | Literal `metis capture` |
| `byte_size` | Exact length of `raw.txt` in bytes |
| `mime_type` | Literal `text/plain` |
| `schema_version` | Integer `1` |

No additional metadata keys are permitted in step 2.

### Intake registration

The registered `IntakeRecord` contains:

- the UUID4 `capture_id`;
- the content hash;
- the same `captured_at` represented in metadata;
- `source_type = "cli-typed"`;
- the evidence-directory path relative to the runtime root;
- `state = "captured"`;
- `state_updated_at` equal to the capture timestamp at initial registration;
- `failure_reason = None`;
- `trace_id` equal to `capture_id` for this initial single-capture trace.

Only `StateStore.register_intake()` may persist this record.

### Capture result

`CaptureResult` is a bounded value with:

- `status`: `captured`, `duplicate`, `refused`, or `failed`;
- `capture_id` when known;
- `evidence_path` when known;
- a stable reason code and explanatory message when the outcome is not `captured`.

The result describes what actually happened. It never reports capture success when evidence or state is
undetermined.

## 6. Persistence and recovery flow

For input text `T`, capture proceeds in this order:

1. Encode `T` as UTF-8 without changing it.
2. Compute `H = sha256:<hex>` over those bytes.
3. Ask `StateStore` for an intake row with `content_hash = H`.
4. Scan and validate evidence relevant to `H`.
5. Resolve the pre-existing state:
   - A row and one valid matching evidence directory return `duplicate`.
   - One complete matching evidence directory with no row is an orphan from a prior registration failure; it
     is reused and registration is retried with its existing UUID4 and timestamp.
   - A row without matching valid evidence, evidence that conflicts with the row, more than one matching
     evidence directory, or partial/corrupt evidence makes replay state uncertain and returns `failed`.
6. Only when neither the row nor matching evidence exists, generate a UUID4.
7. Create `evidence/<capture_id>/` exclusively.
8. Create and close `raw.txt` exclusively.
9. Create and close `meta.json` exclusively.
10. Validate both finalized files before registration.
11. Register the captured intake row through `StateStore`.
12. Return `captured` only after registration succeeds.

The evidence directory and both files use exclusive-create operations. They are never opened in a mode that
can truncate or replace an existing path.

If the generated evidence path already exists, capture returns `refused` without changing that path or
registering a row. If creation fails after the directory or one file exists, the partial evidence remains
visible for human review; it is not deleted or repaired. If registration fails after evidence is finalized,
capture returns `failed`, reports the preserved evidence path, and leaves the complete orphan available for a
safe retry.

The SQLite uniqueness constraint protects against an unexpected duplicate registration. Under the binding
single-process, single-writer MVP model, normal duplicate replays are detected before new evidence creation.
If the backstop reports a duplicate after new evidence was created, orchestration fails closed because the
row/evidence relationship is no longer known to be singular; it preserves both stores for review.

## 7. Outcome and shell semantics

| Status | Meaning | Filesystem/database effect | Exit code |
|---|---|---|---|
| `captured` | New or recovered evidence was registered | One valid evidence directory and one intake row | `0` |
| `duplicate` | The same bytes were already validly captured | No mutation | `0` |
| `refused` | Policy safely blocked mutation, such as an evidence-path collision | No mutation of the existing target and no row registration | `0` |
| `failed` | Filesystem, database, or consistency state is undetermined or genuinely failed | Evidence may be preserved and reported; never auto-deleted | Nonzero |

`refused` is a valid policy-enforcement outcome, not an execution error. `failed` never presents as complete.

## 8. Validation rules for existing evidence

Evidence is reusable or duplicate-safe only when all of these agree:

- the directory name is a canonical UUID4;
- `raw.txt` and `meta.json` both exist as regular files;
- metadata has exactly the required key set and values of the required types;
- metadata `capture_id` equals the directory name;
- `byte_size` equals the actual raw-byte length;
- a fresh SHA-256 of `raw.txt` equals metadata `content_hash`;
- metadata source fields, MIME type, and schema version equal their fixed values;
- any registered intake row agrees on capture ID, content hash, capture timestamp, source type, evidence path,
  initial state data, and trace grouping.

A violation is not repaired automatically. It produces a visible `failed` result because overwrite, deletion,
or guessed recovery would violate immutable-evidence and fail-closed rules.

## 9. Test design

Tests use standard-library `unittest`, temporary directories, real filesystem operations, and real SQLite
where practical. Small recording fakes are limited to failure injection and ordering observations.

The TDD implementation plan must include focused tests for:

1. Byte-exact raw evidence, including Unicode, leading/trailing whitespace, and embedded newlines.
2. The exact metadata key set, values, UUID4, UTC timestamp, byte size, MIME type, and schema version.
3. A fresh independent SHA-256 of `raw.txt` matching `content_hash`.
4. Both finalized evidence files existing and validating before state registration is attempted.
5. Registration failure returning `failed` while preserving and reporting complete evidence.
6. Retry after registration failure reusing the orphaned UUID4 directory and registering one intake row.
7. Exact replay creating one intake row and one evidence directory.
8. A pre-existing evidence path never being overwritten and producing `refused` without registration.
9. Partial, corrupt, conflicting, or multiple matching evidence failing closed without further mutation.
10. Data-access hash lookup, successful registration, engine-neutral duplicate registration, and propagation of
    unrelated database failures.
11. The SQL-boundary test remaining green.
12. CLI honesty for `captured`, `duplicate`, `refused`, and `failed`, with both entry points using one code path.

For each behavior, development proceeds red → green using the narrowest test first, then its containing test
module. The complete suite runs before any completion claim.

## 10. Documentation and ledger effects during implementation

The implementation change will:

- reconcile only the capture-ID portions and examples in `METIS-SCHEMAS.md` to UUID4 before code;
- update the documented command status only after both CLI entry points exist;
- update `METIS-REQUIREMENT-LEDGER.md` only with evidence actually produced by the completed test run.

Ledger status remains conservative:

- `REQ-INTK-001` may move at most to **Partial** because step 2 proves ordering before registration, not before
  future classification.
- `REQ-INTK-002` may move at most to **Partial** because step 2 proves one intake row and evidence directory,
  not one permanent note.
- `REQ-INTK-005` may move at most to **Partial** because step 2 proves state-registration failure recovery,
  not classification-failure recovery or a later review item.
- `REQ-TEST-003` remains **Partial**, with evidence-metadata validation added to its evidence description.

No requirement becomes Verified merely because this design exists.

## 11. Completion boundary

Step 2 is eligible for the local annotated tag `step-02-capture-verified` only after the implementation is
approved, completed through TDD, the complete suite passes, the schemas and ledger accurately reflect the
observed evidence, no unintended files are changed, no runtime evidence or state is committed, and no secrets
are introduced.

Until then, this document records an approved design for review; it is not implementation evidence and makes
no capability claim.
