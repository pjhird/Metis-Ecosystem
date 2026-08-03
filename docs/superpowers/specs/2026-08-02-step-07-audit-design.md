# Step 7 Audit Design

## Status

Implemented on branch `step/07-audit`, not yet committed. Build-order step 7, the last step of the MVP loop.

Requirements: REQ-ORCH-004 (primary), REQ-GOV-002 and REQ-INTK-005 (durable review-item record),
REQ-TEST-003 (audit-detail validation). Decisions: ADR-007 (orchestrator owns transitions and audit
emission), ADR-002 (SQL only in the data layer).

## 1. Objective

Every material transition in the intake state machine emits exactly one append-only `audit_event`, and
every orchestrator decision that ends a command *without* a transition — a refusal, a duplicate, a
pre-transition failure — emits exactly one too. `refused` is recorded as the successful enforcement it is,
never as an error.

The end-to-end acceptance test then reads the trail back: a typed idea walks capture → evidence → classify
→ propose → human links and approves → file, and the audit trail is exactly the state path it walked.

## 2. Scope

### Included

- `AuditEventRecord` in the data-access contract; append and read methods on `StateStore`
- Audit emission threaded through all thirteen existing transition methods, written **inside the
  transition's own transaction**
- Terminal (non-transition) emission from each service's single result constructor
- Migration `005_audit_event_append_only.sql` — `BEFORE UPDATE` / `BEFORE DELETE` triggers that make
  "never updated, never deleted" structural rather than conventional
- The end-to-end audit-trail assertion, replacing the four `audit_event count == 0` assertions that
  currently hold step 7 out

### Excluded

- `metis status` or any other read surface. The ledger asks for a durable review *record*, not a review
  *UI*. The trail is queryable through the data layer; nothing in step 7 needs a second interface.
- Any new table. `audit_event` has existed since migration 001 and its schema already matches §2.6.
- Retention, rotation, export, or trace correlation across captures.
- Anything after step 7.

## 3. Where emission lives (ADR-007)

The orchestrator — the capture, classification, proposal, approval, and filing services — **authors** every
event. The data layer **writes** it. No skill, store, or renderer builds one.

Transitions carry their event into the same SQLite transaction:

```python
store.record_filing(capture_id, proposal_id, approval_id, committed_at, audit=event)
```

`audit` is an optional keyword on all thirteen transition methods. Optional, not required, because 117
existing test call sites drive the store directly and none of them are about audit; a required parameter
would bury the change in noise. The five services always pass it.

Atomicity is the point: a transition cannot commit without its event, and an event cannot survive a
rolled-back transition. That makes "exactly one per transition" structural, not conventional — the
transitions are already compare-and-swap guarded, so a second event for one transition is unreachable.

Terminal outcomes have no transaction to ride, so they go through `store.append_audit_event(event)`.

## 4. Event vocabulary

Thirteen transition actions, one per store method, plus one terminal action per command.

| Transition | Store method | `action` | `actor` | `outcome` |
|---|---|---|---|---|
| ∅ → `captured` | `register_intake` | `capture.written` | orchestrator | success |
| `captured` → `classifying` | `begin_classification` | `classification.started` | orchestrator | success |
| `classifying` → `classified` | `complete_classification` | `classification.completed` | orchestrator | success |
| `classifying` → `failed` | `record_classification_failure` | `classification.failed` | orchestrator | failure |
| `classified` → `proposing` | `begin_proposal` | `proposal.reserved` | orchestrator | success |
| reservation reclaimed | `reclaim_proposal` | `proposal.reclaimed` | orchestrator | success |
| `proposing` → `failed` | `record_proposal_failure` | `proposal.failed` | orchestrator | failure |
| `proposing` → `proposed` | `complete_proposal` | `proposal.recorded` | orchestrator | success |
| `proposed` → `failed` | `record_draft_failure` | `draft.failed` | orchestrator | failure |
| `failed` → `proposed` | `resume_proposal_draft` | `proposal.resumed` | orchestrator | success |
| `proposed` → `awaiting_approval` | `register_proposal_draft` | `draft.registered` | orchestrator | success |
| `awaiting_approval` → `approved`/`rejected` | `record_approval` | `approval.detected` | `human:owner` | success |
| `approved` → `filed` | `record_filing` | `note.committed` | orchestrator | success |

`approval.detected` is the one event whose actor is the human: the orchestrator executes the transition, but
the decision is the vault's, and the audit trail should say whose. It reuses `approval.APPROVER`.

Terminal events use `command.capture`, `command.classify`, `command.propose`, `command.approvals`,
`command.file` — one namespace that can never be mistaken for a transition. Outcome derives from the result
status the command already returns:

| Result status | `outcome` |
|---|---|
| `captured` · `classified` · `proposed` · `approved` · `rejected` · `filed` · `completed` | success |
| `refused` · `duplicate` | **refused** |
| `failed` | failure |

A duplicate is a refused write, not an error — replaying an already-filed capture records
`command.file` / `refused`, and the exit code stays 0.

Two sites override that mapping. When `record_approval` or `record_filing` raises
`StateTransitionRefused`, the transaction rolled back, so no event rode with it and the orchestrator emits
one afterwards with outcome `refused` — a refused transition is a refusal even though the *command* reports
`failed` (the note may be on disk with its transition unrecorded, which is undetermined and must not read as
a clean refusal to the operator). This is what makes `illegal_state_transition_is_rejected` visible in the
trail.

A `pending` approval poll emits nothing. Reading a draft that has not changed is not a material action, and
emitting there would flood the trail on every `metis approvals` run.

## 5. Which return sites emit

Each service has exactly one result constructor, so terminal emission has exactly one home per service:

| Service | Constructor | Post-transition sites that pass `audited=True` |
|---|---|---|
| `CaptureService` | (inline) — `capture()` becomes a four-line wrapper over `_capture()` | status `captured` |
| `ClassificationService` | `_result` | the `classified` success; the recorded-failure return in `_failure_after_start` |
| `ProposalService` | `_result` | the `proposed` success; the recorded-failure returns |
| `ApprovalService` | `_failed` + `_record` | the recorded decision; the post-record state mismatch |
| `FilingService` | `_failed` + inline refused/duplicate/filed | status `filed` only — filing transitions nowhere else |

No instance flags and no runtime tracking: whether an event already rode a transaction is known statically at
the return site, so it is stated there.

## 6. Record and validation

```python
AuditEventRecord(event_id, trace_id, capture_id, actor, action, outcome, detail, created_at)
```

- `event_id` — ULID. Two events in the same millisecond break ties randomly, so the trail is read back in
  append order, not ULID order; an append-only table's insertion order is its emission order
- `trace_id` — `intake.trace_id`, which is the capture ID; for a failure before any intake exists, a fresh ULID
- `capture_id` — the requested ID even when no intake carries it; "someone asked to file X and was refused"
  is exactly what an audit trail is for. The column has no foreign key.
- `detail` — a JSON object of scalars, `sort_keys=True`

The data layer refuses an event whose `event_id` is not a ULID, whose `trace_id` is empty, whose `created_at`
is not canonical UTC, or whose `detail` is not a JSON object (REQ-TEST-003). `outcome` is already enforced by
the CHECK constraint from migration 001. A refused event rolls back its transition with it.

An audit write failure propagates. Losing an event is a governance failure, not something to swallow into a
result field.

## 7. Migration 005

```sql
CREATE TRIGGER audit_event_is_append_only_on_update
BEFORE UPDATE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;
```

…and the same for `DELETE`. Additive: no table rebuild, no data movement, nothing else in the schema moves.
Schema version goes 4 → 5.

## 8. Tests

Failing first, in this order.

**`tests/test_audit.py`**

- `test_every_material_transition_emits_exactly_one_event` — the full loop; assert the trail is exactly the
  eight actions above, in state-path order, each `success`, each with the capture's trace ID
- `test_a_refused_write_is_recorded_as_refused_not_failure` — `metis file` before approval: one
  `command.file` / `refused`, exit code 0, vault untouched
- `test_a_recorded_failure_emits_one_failure_event` — a failing model adapter: `classification.started` /
  success then `classification.failed` / failure, and nothing else
- `test_a_refused_transition_writes_no_event` — a store transition that refuses leaves the trail unchanged
- `test_an_invalid_event_rolls_back_its_transition` — an event the data layer rejects leaves the intake in
  its prior state (proves same-transaction emission in both directions)
- `test_audit_events_are_append_only` — direct UPDATE and DELETE both abort
- `test_audit_detail_must_be_a_json_object` — and the ULID / timestamp / trace validations

**Existing tests updated**

- `test_filing_integration.py` — `test_capture_classify_propose_approve_file_completes_the_loop` gains the
  trail assertion in place of `audit_event count == 0`; `test_duplicate_replay_creates_one_note` gains "and
  no second `note.committed`"
- `test_proposal_integration.py`, `test_approval_integration.py`, `test_approval.py` — the three other
  "must not have crept in early" assertions become the trail each step actually produces
- `tests/data_access/test_migrations.py` — schema version 4 → 5, plus the trigger tests
- `tests/data_access/inspection.py` — `audit_event_rows()` and the mutation-attempt helpers, so no test
  outside `tests/data_access/` contains SQL (`test_sql_appears_only_in_data_layer`)

## 9. Ledger

Same PR, per AGENTS.md. REQ-ORCH-004 Missing → Verified. REQ-GOV-002, REQ-INTK-005, and REQ-TEST-003 move
only as far as the tests that actually run prove, each naming its test. REQ-ORCH-002 is untouched — step 7
adds no permission test. The header block and the stale "audit behavior remains unimplemented" note in
`METIS-SCHEMAS.md` are corrected in the same PR.
