# Step 4 Proposal Design

## Status

- Build-order step: 4 — Propose
- Design approved by the human owner: 2026-08-02
- Implementation status: not started
- Base: verified public `origin/main` at merge commit `76a1358eb2362532fa1dfdcabbdab10a9ed79b8c`
- Verified Step-3 head: `ded07cfe338bdf5b126f6b8e89b5d6aab11d517c`
- Governing decisions: ADR-002, ADR-003, ADR-004, ADR-005, ADR-007, ADR-008, ADR-014,
  ADR-016, ADR-017, ADR-019
- Primary requirements: REQ-GOV-003, REQ-INTK-003, REQ-INTK-004, REQ-INTK-005,
  REQ-MODEL-001, REQ-MODEL-002, REQ-MODEL-003, REQ-ORCH-001, REQ-TEST-003

## 1. Objective

Add one explicit proposal operation for one valid classified capture:

```text
metis propose <capture_id>
```

The operation revalidates the immutable capture and its classification evidence, reserves one proposal
identity, asks Claude for bounded semantic content through the existing provider seam, preserves the raw model
response, validates and renders deterministic proposal content, persists one proposal through `StateStore`,
and creates one reviewable draft at `vault/notes/proposed/` with `status: proposed`.

The draft is not permanent knowledge. Step 4 does not recognize approval, file a note, enforce final links,
or emit audit events. An exact replay returns the existing proposal and draft without a second model call or a
second note.

## 2. Scope

### Included

- One `metis propose <capture_id>` CLI subcommand.
- A deterministic `ProposalService` that owns all Step-4 transitions and authority decisions.
- A bounded hybrid content strategy: Claude proposes semantic text; deterministic code owns every identifier,
  metadata field, path, state, risk value, validation rule, persistence operation, and replay decision.
- One immutable packaged prompt, `propose-v1`.
- Exact raw proposal-response preservation before parsing.
- Deterministically rendered normalized proposal content with a recorded SHA-256 hash.
- One proposal row persisted through the data-access layer.
- One reviewable Markdown draft written exclusively under `vault/notes/proposed/`.
- A reservation-first protocol with a 15-minute lease and token-fenced recovery.
- Mechanical uniqueness by capture and classification identity.
- Stable JSON CLI results and honest exit behavior.
- Crash-boundary, collision, corruption, replay, scope-exclusion, and architecture-boundary tests.
- Conservative requirement-ledger updates only where named passing tests provide implementation evidence.

### Excluded

- Approval detection, an approval command, or any other approval surface.
- `approved` or `rejected` transitions, which belong to Step 5.
- Permanent filing into `vault/notes/filed/`.
- Final goal or project link enforcement; Step 4 always emits `links: []`.
- Audit-event emission or an audit table write.
- Watchers, background recovery, agents, integrations, containers, UI, vector databases, or graph databases.
- A second provider, provider router, agent framework, or any module other than the existing Claude adapter
  importing the provider SDK.
- A live paid provider call during implementation or verification without separate authorization.
- Any inspection or change to `METIS-EXECUTION-SPINE.md`.

## 3. Considered approaches

### 3.1 Reservation-first bounded hybrid — selected

Reserve a durable proposal identity and token-fenced lease before the model request. Claude returns only
semantic fields; deterministic code validates, renders, persists, and transitions state.

Advantages:

- Establishes one identity before any costly or externally uncertain work.
- Makes concurrent invocation and recovery explicit rather than inferred from filesystem artifacts.
- Preserves ADR-008's single provider seam while using the model only where semantic judgment helps.
- Reuses a valid preserved response after a crash, avoiding a second paid call.
- Allows deterministic fencing of a stale process after a lease is reclaimed.

Costs:

- Adds one operational table and the transient `proposing` state.
- Requires explicit lease, reclaim, and token-consistency tests.
- Coordinates SQLite state and append-only files without pretending they form one transaction.

### 3.2 Artifact-first bounded hybrid

Allocate an identity locally, write model-response and normalized-content artifacts first, then register the
proposal and draft in SQLite.

Advantages: fewer operational rows and direct evidence-first recovery. Costs: concurrency ownership is less
explicit, competing processes must infer intent from partial artifacts, and stale work is harder to fence.

### 3.3 Deterministic-only proposal content

Render proposal title and body entirely from the captured text and classification.

Advantages: no new model call, minimal failure surface, and inexpensive replay. Costs: it adds little semantic
value beyond classification, produces weak review drafts, and does not exercise the intended reasoning seam.

A model-controlled proposal was rejected: the model must not choose identity, note type, sensitivity, risk,
paths, links, status, persistence, state transitions, or authority.

## 4. Architecture

```text
CLI: metis propose <capture_id>
                 |
                 v
          ProposalService
          - validates all prior evidence
          - owns reservation and transitions
          - validates semantic output
          - derives metadata and risk
          - renders content and draft
        /          |          |          \
       v           v          v           v
 StateStore   ModelAdapter  Proposal     DraftNote
    |             |         Stores       Store
    v             v          |            |
 SQLite          Claude      v            v
                         proposal-*   vault/notes/
                                         proposed/
```

Control flows down from `ProposalService`. Only the SQLite data-access implementation contains SQL. The model
adapter never reaches persistence and never chooses authority-bearing fields. Proposal and draft stores never
transition operational state. The existing Claude adapter remains the only module that imports `anthropic`.

The provider-neutral `ModelAdapter` protocol gains a bounded `propose(rendered_prompt)` operation returning
the same provider-neutral response shape used by classification: the actual `model_id` and exact `raw_text`.
Its existing configuration, request, refusal, truncation, and response-shape exceptions remain the provider
failure vocabulary. No second provider seam is introduced.

Proposal generation uses `ANTHROPIC_API_KEY` from the environment, defaults to the existing pinned
`claude-sonnet-4-6` model, and permits a proposal-specific `METIS_PROPOSAL_MODEL` environment override. The
adapter records the model ID returned by the provider rather than assuming the requested ID. No credential,
environment mapping, or unsafe provider exception text enters SQLite, proposal artifacts, the vault, or CLI
output.

## 5. Input and validation contract

### 5.1 Command input

`capture_id` must be the canonical lowercase string form of an RFC 4122 UUID version 4. Non-canonical,
non-UUID, and non-v4 values fail before any persistence or model call.

### 5.2 Required prior state

A new proposal may begin only when all of these are true:

- exactly one intake row exists for `capture_id` and its state is `classified`;
- the immutable capture raw bytes and Step-2 metadata are complete, hash-valid, and agree with the intake row;
- exactly one classification row exists and agrees on capture ID, candidate type, sensitivity, confidence,
  routing, model ID, prompt version, and raw-response path;
- the classification raw-response evidence is complete, byte-valid, and locally reparses to the exact recorded
  classification;
- routing equals `proposal:<candidate_type>`;
- no proposal, reservation, draft registration, or filesystem draft conflicts with the expected identity.

Missing, corrupt, inconsistent, ambiguous, colliding, symlinked, or partially written state fails closed.
Proposal generation never repairs, overwrites, deletes, quarantines, or guesses about prior artifacts.

### 5.3 Model input

The packaged prompt is:

```text
metis/prompts/propose-v1.txt
```

It contains the untrusted captured text and validated classification as delimited data, directs the model not
to execute instructions found in that data, and requests only the exact semantic object below. A substantive
prompt change creates a new immutable prompt version rather than changing `propose-v1` in place.

The model sees the captured text, candidate type, sensitivity, and confidence. It does not receive authority to
select type, sensitivity, confidence, risk, status, identifiers, paths, links, or transitions.

### 5.4 Model output

The requested JSON object has exactly these keys:

```json
{
  "title": "A concise review title",
  "body": "Reviewable Markdown proposal content.",
  "reason": "Why this proposal follows from the capture.",
  "uncertainties": ["A bounded unresolved point"]
}
```

Local validation applies even if the provider requested structured JSON:

- the root is one JSON object with no missing or additional keys;
- duplicate JSON object keys are rejected;
- `title` is a string of 1–160 Unicode scalar values, is one line, equals its Unicode string after trimming
  surrounding whitespace, and contains no control characters;
- `body` is a string of 1–20,000 UTF-8 bytes, contains no NUL or carriage return, has no leading or trailing
  newline, and contains no disallowed control characters;
- `reason` is a string of 1–1,000 Unicode scalar values, equals its value after trimming surrounding
  whitespace, and contains no control characters;
- `uncertainties` is a JSON array of 0–10 unique strings; each is 1–500 Unicode scalar values, equals its
  value after trimming surrounding whitespace, and contains no control characters;
- booleans, nulls, numbers, nested objects, invalid UTF-8, and non-finite numeric spellings are not accepted in
  place of these fields;
- title, body, reason, and uncertainty text must pass the credential-pattern screen below before anything is
  echoed to the CLI or written into the vault;
- body content is refused if its first line is `---`, if it contains a raw HTML tag opener, a Markdown image
  (`![`), a Markdown link (`](`), an Obsidian link or embed (`[[` or `![[`), or a case-insensitive `data:` URL;
  Step 4 creates no semantic links and therefore does not silently sanitize these forms.

The credential-pattern screen applies these case-sensitive expressions except where `(?i)` is present:

```text
-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----
(?:^|[^A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})(?:$|[^A-Za-z0-9])
(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\b\s*[:=]\s*\S{8,}
```

A raw HTML tag opener is the expression `<[A-Za-z!/][^>]*>`. Matches are policy refusals, not proof that a
credential or exploit is genuine. The raw provider response remains evidence, but matched text never reaches
the vault, SQLite text fields, or CLI output.

Raw provider text is evidence and is preserved exactly before parsing. Normalized proposal content is a
separate deterministic rendering, not a rewrite of the response evidence.

## 6. Identity and idempotency

- `proposal_id` is a standards-compliant 26-character Crockford Base32 ULID generated behind an injected ID
  factory.
- `classification_id` is the replay and idempotency key.
- `proposal.capture_id` and `proposal.classification_id` each receive a unique index.
- `proposal_reservation.capture_id` and `proposal_reservation.classification_id` are each unique.
- One reservation owns one stable `proposal_id`; expiration and reclaim do not allocate another proposal ID.
- The draft note identity and filename derive from `capture_id`, so replay cannot choose a second path.

An exact, complete `awaiting_approval` proposal returns `duplicate` after revalidating the proposal row,
content evidence, draft registration, and draft bytes. It performs no provider call and no write.

If the only human change is a recognized status value, Step 4 preserves it and refuses to interpret it:
`approved` and `rejected` belong to Step 5. Any other modification to a Metis-owned draft field or body is a
consistency failure. Step 4 never rewrites the draft on replay.

## 7. Persistence schema

### 7.1 Proposal reservation

A new sixth operational table, `proposal_reservation`, contains:

| Column | Contract |
|---|---|
| `proposal_id` | canonical ULID primary key |
| `capture_id` | canonical UUID4, unique foreign key to intake |
| `classification_id` | canonical ULID, unique foreign key to classification; replay key |
| `lease_token` | canonical UUID4 fencing token |
| `reserved_at` | canonical UTC timestamp |
| `lease_expires_at` | canonical UTC timestamp exactly 15 minutes after reservation or reclaim |

This is transient operational coordination, not a proposal, approval record, permanent knowledge, or audit
event. A lease token, not time alone, authorizes a completion. If an expired lease has not been reclaimed, its
holder may still complete; once a compare-and-swap reclaim changes the token, the stale holder cannot commit.

### 7.2 Proposal row

The existing proposal table requires a migration. Step 4 preserves its current `proposal_id`, `capture_id`,
`classification_id`, type, title, body path, proposed links, evidence references, confidence, risk, reason,
draft path, state, and timestamp fields and adds:

| Column | Contract |
|---|---|
| `sensitivity` | `normal` or `sensitive`, copied from classification |
| `model_id` | actual provider model identifier |
| `prompt_version` | exactly `propose-v1` for this step |
| `raw_response_path` | repository-relative path to exact proposal response evidence |
| `content_hash` | lowercase SHA-256 of canonical normalized content bytes |
| `uncertainties_json` | canonical compact JSON array of validated uncertainty strings |

The existing `capture_id` and `classification_id` columns each receive a unique index. Step 4 inserts only
`state = pending`; it never writes `approved`, `rejected`, or `superseded`. `note_type` is the classification
`candidate_type`. `proposed_links` is exactly `[]`. `evidence_refs` is the canonical compact JSON array below,
in this fixed order and with no additional entry:

```json
["evidence/<capture_id>/raw.txt","classification-evidence/<classification_id>/raw-response.txt","proposal-evidence/<proposal_id>/raw-response.txt"]
```

Risk is deterministic:

- `sensitivity = normal` produces `risk_level = low`;
- `sensitivity = sensitive` produces `risk_level = high`.

Confidence is copied visibly from classification and never grants authority or changes the workflow.

### 7.3 StateStore operations

The provider-neutral `StateStore` protocol gains operations that let the orchestrator:

- load proposal, reservation, and draft-path registration state by capture and classification identity;
- atomically create a reservation and compare-and-swap `classified -> proposing`;
- atomically reclaim an expired reservation with the same proposal ID, a new lease token, and
  `failed (proposal.*) -> proposing` or `proposing -> proposing` as appropriate;
- record a known proposal failure only when the caller still owns the lease token;
- atomically insert one proposal, delete its reservation, and compare-and-swap `proposing -> proposed`;
- after validating a completed proposal left by a recorded draft failure, compare-and-swap
  `failed (proposal.*) -> proposed`;
- atomically register one exact draft path and compare-and-swap `proposed -> awaiting_approval`.

Only the SQLite implementation contains these operations' SQL. Unique constraints and compare-and-swap row
counts are part of correctness, not optional optimizations. Draft registration uses the existing
`proposal.draft_note_path` column; Step 4 does not add a separate draft table.

## 8. Proposal evidence and normalized content

### 8.1 Raw response

Every received model response is exclusively created before parsing at:

```text
proposal-evidence/
└── <proposal_id>/
    ├── raw-response.txt
    └── meta.json
```

`raw-response.txt` is exactly `raw_text.encode("utf-8")`. `meta.json` has exactly:

```json
{
  "proposal_id": "01K1...",
  "classification_id": "01K1...",
  "capture_id": "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70",
  "model_id": "claude-sonnet-4-6",
  "prompt_version": "propose-v1",
  "received_at": "2026-08-02T20:00:00Z",
  "byte_size": 231,
  "schema_version": 1
}
```

The store validates the exact directory/file/key set, all identifier forms, UTC timestamp, byte count, and
agreement with the expected reservation. It exclusively creates files and never completes or repairs partial
evidence. If an exclusive create loses a race, the service re-reads the established artifact: one complete,
valid artifact for the same proposal/classification/capture becomes the canonical first response and is
reused; a partial artifact or metadata disagreement fails closed.

### 8.2 Canonical content

Validated semantic content is rendered at:

```text
proposal-content/
└── <proposal_id>/
    ├── body.md
    └── meta.json
```

`body.md` contains, in order:

1. the validated `body` exactly, followed by one line feed;
2. one blank line and `## Proposal rationale`, followed by the validated `reason` and one line feed;
3. one blank line and `## Uncertainties`;
4. either one Markdown bullet per validated uncertainty in array order or the literal
   `None identified by the proposal model.`;
5. exactly one final line feed.

No YAML frontmatter appears in normalized content. Its `meta.json` contains exactly:

```json
{
  "proposal_id": "01K1...",
  "classification_id": "01K1...",
  "capture_id": "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70",
  "raw_response_hash": "<lowercase sha256 of raw-response.txt>",
  "content_hash": "<lowercase sha256>",
  "byte_size": 310,
  "schema_version": 1
}
```

`proposal.body_path` points to this `body.md`. The content store validates exact bytes and metadata, uses
exclusive creation, rejects symlinks, and never overwrites or repairs disagreements. A complete artifact that
wins an exclusive-create race is reused only when its raw-response hash proves it was derived from the
canonical response; otherwise the disagreement fails closed.

Both proposal stores are ignored by Git. They are evidence and operational artifacts, not permanent vault
knowledge.

## 9. Draft-note contract

### 9.1 Path and identity

The one allowed draft path is:

```text
vault/notes/proposed/note.<capture_id>.md
```

The root is configured by the application, but the relative directory and filename are fixed. The resolved
path must remain beneath that root, every existing parent must be a real directory rather than a symlink, and
the final file is exclusively created. Any pre-existing non-exact file, wrong type, symlink, partial file, or
second candidate path is a collision and fails closed.

### 9.2 Frontmatter

The draft uses deterministic YAML frontmatter in this exact field order:

```yaml
---
id: "note.<capture_id>"
proposal_id: "<proposal_id>"
classification_id: "<classification_id>"
capture_id: "<capture_id>"
type: "<candidate_type>"
title: "<JSON-escaped title>"
status: proposed
verification: unverified
created: "<canonical UTC timestamp>"
approved: null
confidence: <canonical decimal>
sensitivity: normal
risk_level: low
evidence:
  capture: "evidence/<capture_id>/raw.txt"
  classification: "classification-evidence/<classification_id>/raw-response.txt"
  proposal: "proposal-evidence/<proposal_id>/raw-response.txt"
links: []
---
```

All string scalars are emitted with `json.dumps(value, ensure_ascii=False)` as a YAML-compatible double-quoted
scalar. The actual sensitivity and deterministic risk value replace the example values. Confidence uses one
locale-independent representation produced by canonical JSON serialization of the stored numeric value.
`created` is the proposal row creation timestamp and is therefore stable across replay.

### 9.3 Body and ownership

The bytes after frontmatter are one blank line followed by the exact canonical `body.md` bytes. Thus the draft
contains the model body, `## Proposal rationale`, and `## Uncertainties` sections with visible provenance and
review context.

Metis owns every path, identifier, metadata value, title, evidence reference, link list, and body byte. The
only human-editable field is the single frontmatter scalar `status`. Step 4 writes only `proposed` and does not
interpret `approved` or `rejected`. It provides no alternative approval surface.

The draft store returns a hash and exact path after an exclusive, flush-and-close write followed by read-back
validation. SQLite registration occurs only after read-back proves the exact expected bytes exist.

## 10. State model

Step 4 adds the transient intake state `proposing` and uses the existing `proposed` and
`awaiting_approval` states.

### Allowed Step-4 transitions

```text
classified --------------------------> proposing
failed (proposal reason, reservation) -> proposing
failed (proposal reason, proposal row) -> proposed
proposing ---------------------------> proposed
proposing ---------------------------> failed
proposed ----------------------------> awaiting_approval
proposed ----------------------------> failed
```

No Step-4 code transitions to `approved`, `rejected`, or `filed`.

### State invariants

- `classified`: exactly one valid classification and no proposal reservation, proposal row, or registered
  draft exists.
- `proposing`: exactly one reservation exists, no proposal row exists, and any proposal artifacts agree with
  that reservation.
- `proposed`: exactly one complete proposal row and complete response/content artifacts exist, the reservation
  is gone, and no draft registration exists.
- `awaiting_approval`: the `proposed` invariant holds plus one registered draft whose path and bytes satisfy
  the draft contract.
- `failed` with `proposal.*`: the recorded reason and whatever complete artifacts exist agree. A reservation
  may remain only as an immediately expired recovery record; a completed proposal may remain after a draft
  failure. Incomplete or inconsistent artifacts are not retryable automatically.

Any other row/artifact combination is undetermined state and fails closed. No service infers a transition
from the mere presence or absence of a file.

## 11. Reservation-first data flow

### 11.1 New proposal

1. Validate the canonical capture ID.
2. Load intake, classification, proposal, reservation, and draft registration through `StateStore`.
3. Validate capture evidence, classification evidence, routing, state invariants, and absence of collisions.
4. Allocate one ULID `proposal_id` and UUID4 `lease_token`.
5. In one SQLite transaction, insert the reservation and compare-and-swap `classified -> proposing`.
6. Render `propose-v1` and call the existing model adapter.
7. If raw assistant text was received, exclusively preserve it and its metadata before parsing.
8. Parse and locally validate the exact model object.
9. Render and exclusively preserve canonical content; compute and validate its SHA-256.
10. Re-read operational and artifact state and verify the reservation's lease token still belongs to this
    invocation.
11. In one SQLite transaction, insert the proposal row, delete the reservation, and compare-and-swap
    `proposing -> proposed`.
12. Deterministically render the draft; preflight the vault path; exclusively create, flush, close, and
    read-back validate the exact bytes.
13. In one SQLite transaction, register the draft path and compare-and-swap
    `proposed -> awaiting_approval`.
14. Re-read and validate the complete invariant, then return `proposed`.

Success is never reported before step 14.

### 11.2 Replay and recovery

Every explicit `metis propose` invocation begins with the same full preflight:

- a complete exact `awaiting_approval` result returns `duplicate` without writes or a model call;
- an unexpired reservation owned by another token returns `refused` with `proposal_in_progress`;
- after 15 minutes, an explicit invocation may compare-and-swap reclaim the same proposal ID with a new token;
- a valid complete raw-response artifact is reparsed without a second provider call;
- a valid complete normalized-content artifact resumes proposal registration without a provider call;
- a valid proposal row in `proposed` resumes only draft creation and registration;
- a valid proposal row left by a recorded draft failure first compare-and-swaps `failed -> proposed`, then
  resumes only draft creation and registration;
- a valid exact unregistered draft is registered rather than rewritten;
- partial, corrupt, inconsistent, ambiguous, or colliding artifacts fail closed and are not repaired;
- a worker whose lease token was replaced cannot record failure, register a proposal, or advance state.

No watcher, timer process, background worker, or autonomous recovery is added. Recovery occurs only because a
human explicitly invokes `metis propose` again.

### 11.3 Known failure recording

When failure is known while the invocation owns the lease, `ProposalService` records a `proposal.<reason>`
failure and makes the existing reservation immediately reclaimable by setting its expiry to the failure time.
The proposal ID is retained for the retry. This failure update and `proposing -> failed` compare-and-swap are
atomic.

After proposal registration has removed the reservation, a draft failure atomically changes
`proposed -> failed` with a `proposal.<reason>` failure. A later explicit retry validates the registered
proposal and resumes the draft stage without a model call.

If failure recording cannot be proven, the service returns `proposal_state_undetermined`; it never reports a
clean retry state or success.

## 12. Failure and collision behavior

Stable public reasons include:

- `proposal_consistency_failed`
- `proposal_in_progress`
- `model_configuration_failed`
- `model_request_failed`
- `model_response_refused`
- `model_response_truncated`
- `model_response_invalid`
- `proposal_evidence_failed`
- `proposal_content_failed`
- `proposal_persistence_failed`
- `draft_write_failed`
- `draft_collision`
- `proposal_state_undetermined`

`proposal_in_progress` is a policy refusal. Unsafe semantic content is also `refused`, uses the public reason
`proposal_content_failed`, and emits a safe message with no unsafe content in the CLI or vault; the intake
failure reason is `proposal.proposal_content_failed`.

All other inability to establish a safe outcome is `failed`. Error messages expose identifiers and safe
paths only where useful and never include captured text, raw model text, proposed content, environment values,
credentials, or provider exception payloads that may contain those values.

Filesystem operations use exact expected paths, reject symlinks and unexpected entries, use exclusive create,
and validate after close. SQLite operations use transactions, uniqueness constraints, foreign keys, and
compare-and-swap row counts. Neither side is described as atomically committed with the other; the explicit
recovery states handle every boundary.

## 13. CLI contract

The service result and CLI JSON use this stable complete key set:

- `status`: `proposed`, `duplicate`, `refused`, or `failed`
- `capture_id`
- `classification_id`, nullable before a valid classification is established
- `proposal_id`, nullable before a reservation or existing proposal is established
- `note_type`, nullable before a valid classification is established
- `title`, nullable before safe validated proposal content exists
- `confidence`, nullable before a valid classification is established
- `sensitivity`, nullable before a valid classification is established
- `risk_level`, nullable before deterministic derivation
- `raw_response_path`, nullable when no response was received or safely established
- `content_path`, nullable until canonical content is safely established
- `draft_path`, nullable until an exact draft is safely established
- `intake_state`, the last safely observed state or null
- `reason`, a stable machine-readable reason or null
- `message`, a safe human-readable message or null

The CLI emits one sorted JSON object:

- `proposed`, `duplicate`, and `refused` go to stdout with exit code 0;
- `failed` goes to stderr with exit code 1.

Every non-null path in the result is a normalized, runtime-root-relative POSIX path. No absolute host path is
part of the public result.

There is no `--approve`, `--reject`, `--file`, `--link`, or status-changing option. The command does not scan
Obsidian for approval and does not interpret a human status change.

## 14. Testing strategy

All provider behavior uses a fake adapter. No test makes a live paid provider call.

### 14.1 Contract and happy-path tests

- one valid classified capture produces exactly one schema-valid proposal row;
- response evidence is byte-exact and exists before any parse attempt;
- the actual model ID and `propose-v1` are recorded;
- exact-key, type, bound, control-character, duplicate-key, and Markdown-safety validation is enforced;
- canonical content bytes and SHA-256 are deterministic;
- the exact draft filename, path, field order, field values, body, and read-back hash validate;
- confidence, sensitivity, classification identity, all three evidence references, and deterministic risk are
  visibly retained;
- the happy path ends in `awaiting_approval` with proposal state `pending`.

### 14.2 Reservation and state tests

- reservation creation and `classified -> proposing` are atomic;
- capture and classification reservation uniqueness are mechanical;
- an active lease refuses a competing invocation;
- an expired lease is reclaimed by compare-and-swap with the same proposal ID and a new token;
- a stale token cannot complete or record failure;
- proposal insert, reservation deletion, and `proposing -> proposed` are atomic;
- draft registration and `proposed -> awaiting_approval` are atomic;
- every illegal edge involving `classified`, `proposing`, `proposed`, and `awaiting_approval` is rejected;
- only `proposal.*` failures can enter Step-4 retry paths.

### 14.3 Replay and crash tests

Inject a crash or failure after each boundary: reservation, provider return, response bytes, response metadata,
parse, content bytes, content metadata, proposal transaction, draft create, draft close, draft validation, and
draft registration.

Tests prove:

- complete response evidence prevents a repeat model call;
- complete content resumes registration;
- a complete proposal resumes the draft stage;
- an exact unregistered draft is registered without rewrite;
- exact replay creates one proposal and one draft and returns `duplicate`;
- a human status change is preserved but not interpreted by Step 4;
- a change to a Metis-owned draft field or body fails closed;
- partial files, unexpected files, symlinks, hash disagreement, wrong metadata, ambiguous rows, and collisions
  never cause overwrite, deletion, speculative repair, or false success.

### 14.4 Safety, architecture, and exclusion tests

- sensitive classifications can never produce a risk below `high`;
- confidence never skips a state or grants authority;
- unsafe content never appears in the vault or CLI output;
- SQL appears only in the data-access layer;
- only the Claude adapter imports the provider SDK;
- no file is created under `vault/notes/filed/`;
- no approval record is inserted and no approval command or second approval surface exists;
- no `approved`, `rejected`, `filed`, or `superseded` state is written;
- `links` remains exactly empty and final link enforcement is absent;
- no audit event is emitted;
- capture and classification behavior remain unchanged;
- the complete Python 3.13 suite passes.

## 15. Requirement-ledger policy

This design is not implementation evidence. Recording it does not make a requirement Verified.

The implementation pull request may change a requirement to Verified only when the same pull request names a
fresh passing test or observed behavior that proves the complete requirement. Requirements whose completion
depends on approval, permanent filing, final link enforcement, or audit remain conservative and unverified in
Step 4. Broad orchestrator and test requirements remain partial unless their entire stated contract is proven.

## 16. Acceptance criteria

Step 4 is complete only when fresh named tests prove all of the following:

1. One valid classified capture creates one valid pending proposal and one exact proposed draft.
2. The result visibly retains classification identity, confidence, sensitivity, deterministic risk, source
   provenance, and capture/classification/proposal evidence references.
3. State moves deterministically through `classified -> proposing -> proposed -> awaiting_approval`.
4. Reservation uniqueness, expiry, reclaim, and stale-token fencing are mechanical and tested.
5. Raw response exists before parsing and complete artifacts support deterministic recovery.
6. Missing, corrupt, inconsistent, ambiguous, colliding, symlinked, and partial state fails closed.
7. Exact replay produces no second provider call, proposal, content artifact, or draft note.
8. CLI status, stream, exit code, reason, message, and paths are honest for success, duplicate, refusal, and
   failure.
9. No permanent note, approval decision, final link enforcement, audit event, second approval surface, or
   other deferred capability is introduced.
10. Provider and SQL boundaries remain enforced, no secret appears in the vault or CLI, and the full Python
    3.13 suite passes without a live provider call.

## 17. Approved implementation gate

This specification records the approved design only. Before implementation:

1. the human owner reviews and explicitly approves this recorded specification;
2. a detailed named-step TDD implementation plan is presented and explicitly approved;
3. implementation begins test-first only after that second approval.
