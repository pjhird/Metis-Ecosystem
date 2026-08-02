# Step 5 Approval Design

## Status

- Build-order step: 5 — Approve
- Implementation status: in progress
- Base: verified `main` at merge commit `5b3cd46` (step 4 merged)
- Governing decisions: ADR-002, ADR-004, ADR-005, ADR-006, ADR-007, ADR-014, ADR-019, ADR-020
- Primary requirements: REQ-GOV-004, REQ-VLT-003, REQ-ORCH-001, REQ-INTK-005, REQ-TEST-003

## 1. Objective

Add one explicit approval operation:

```text
metis approvals
```

The command reads the operational-state approval queue, re-reads each registered draft in the vault,
and records the human decision the vault frontmatter carries. It writes nothing to the vault and files
no permanent note.

## 2. Scope

### Included

- One `metis approvals` CLI subcommand taking no arguments.
- A deterministic `ApprovalService` that owns every Step-5 transition.
- One `approval` row per decided proposal, with `approver`, `observed_status`, and `detected_at`.
- Atomic `awaiting_approval → approved` and `awaiting_approval → rejected` intake transitions, paired
  with the matching `proposal.state` change in one transaction.
- Migration `004` — mechanical one-approval-per-proposal uniqueness.
- Illegal-edge tests for every intake state that must not produce an approval.
- The two-field draft contract from ADR-020: `status` and `links` are human-editable, nothing else is.

### Excluded

- Permanent filing into `vault/notes/filed/` — Step 6.
- Link resolution — Step 6.
- Audit-event emission — Step 7.
- Any vault write, including moving or deleting a rejected draft.
- Watchers (ADR-006), a second approval surface (ADR-005), agents, integrations, UI.
- Approval revocation and expiry — `revoked_at` stays `NULL`; ledger open question 2 is unresolved.

## 3. The approval signal

`status` in the draft's YAML frontmatter is the sole approval signal (ADR-005). Step 5 reads it through
`DraftNoteStore.validate(path, expected_proposed_bytes)`.

Per ADR-020 a draft has two human-editable fields, `status` and `links`, so `validate` matches two variable
regions and requires every other byte to be exact:

- the frontmatter head must equal exactly one of the three rendered `status` variants;
- the trailing links region must be `links: []` or `links:` followed by `  - "[[target]]"` lines, with
  targets restricted to `[A-Za-z0-9._-]+` and no duplicates;
- everything after the frontmatter — the body — must match byte-for-byte.

`render_proposed_draft` emits `links: []` as the last line of the frontmatter, so the editable region is a
clean suffix and the split needs no YAML parser. A draft edited anywhere else, or carrying a malformed link,
raises `DraftNoteConsistencyError` and fails closed. `links: []` in the *body* is not a frontmatter field —
the split is frontmatter-scoped and tested.

`links` supplies content; it does not authorize. Step 5 reports the observed links in its result and records
no link data in the database. Resolving them against existing goal and project notes is Step 6.

## 4. The queue is driven by operational state, not the filesystem

The command enumerates intake rows in `awaiting_approval`, then reads each row's registered
`draft_note_path`. It never discovers work by scanning the vault directory.

This is the security-relevant choice of the step: a Markdown file dropped into `vault/notes/proposed/`
with `status: approved` and no proposal row is structurally not an approval vector. It is never read,
never matched, and can never produce an approval record. This is the evidence REQ-GOV-004 asks for.

## 5. Validation chain per queued capture

1. The proposal row exists, is `pending`, has a registered `draft_note_path` matching its capture, and
   carries a ULID proposal ID.
2. `proposal-content/<proposal_id>/` validates and its identities and `content_hash` agree with the row.
3. The canonical body bytes hash to `proposal.content_hash`.
4. `render_proposed_draft(proposal, body)` reproduces the expected draft bytes.
5. `DraftNoteStore.validate` matches the observed file to exactly one status variant and a
   well-formed links region, with every other byte exact.

Any disagreement is a determinate failure with a visible review item. Nothing is repaired.

**Precondition handed to Step 6.** Step 5 deliberately does *not* revalidate the source-capture or
classification evidence chain, because it writes nothing permanent. `intake.state = approved` is a
record of a human decision, **not** clearance to file. Before any permanent write the Step-6 note writer
must:

1. Revalidate the full evidence chain — capture evidence, classification evidence, proposal evidence —
   exactly as `ProposalService._validated_prior_state` does today.
2. Re-validate the draft bytes against `render_proposed_draft(proposal, canonical_body)` again. Nothing
   re-reads the draft between approval and filing, so a human can edit the body in that window.
3. File from `proposal-content/<proposal_id>/body.md`, **never** from the draft file. The approval record
   attests to the content the human saw, not to whatever the file says at filing time.

## 6. Approver identity

Nothing in the system distinguishes people: there is no authentication, no accounts, one local owner.
Identity cannot come from the vault — a frontmatter `approver` field would have to become a third
human-editable field, and a human-typed approver name is self-certification, not identity. A `--approver`
flag would let whoever runs the command type any name, which is no stronger than a constant.

Step 5 therefore records the module constant `human:owner`, satisfying the existing
`CHECK (approver LIKE 'human:%')`. Upgrade trigger: authenticated or multi-user identity exists.

## 7. Result vocabulary

| Per-capture status | Meaning | Stream |
|---|---|---|
| `pending` | draft still says `status: proposed`; nothing to record | stdout |
| `approved` | human approved; decision recorded | stdout |
| `rejected` | human declined; decision recorded — a successful outcome, not an error | stdout |
| `failed` | approval state undetermined; visible review item, nothing recorded | stderr |

Run status is `completed` when no capture failed, `failed` otherwise, so a mixed queue never reports
success (non-negotiable rule 4). Exit code follows the existing CLI convention: `1` on `failed`.

Step 4 uses a separate `refused` status for "this draft's status belongs to the approval step." Step 5
is the status owner, so that bucket has no analogue here. The outcome non-negotiable rule 4 names —
a refusal recorded as a success — is `rejected`: it is written to stdout and exits `0`.

## 8. Schema

`approval` already exists from migration 001/003 and needs no column change. Migration `004` adds:

```sql
CREATE UNIQUE INDEX idx_approval_proposal_id_unique ON approval (proposal_id);
```

Per ADR-014's principle, the constraint *is* the replay protection: a second approval for one proposal
fails at the data layer rather than relying on the service noticing.

`committed_at` and `revoked_at` stay `NULL` through Step 5. `committed_at IS NULL` after an approval is
the structural proof that nothing was filed.

## 9. State-store contract

One new atomic operation plus one queue read:

```python
def find_intakes_awaiting_approval(self) -> tuple[IntakeRecord, ...]
def record_approval(self, record: ApprovalRecord) -> IntakeRecord
```

`record_approval` runs `BEGIN IMMEDIATE` and refuses unless, under compare-and-swap: the intake is
`awaiting_approval` with no failure reason; the proposal is `pending` with a registered draft path and
the given proposal ID; no approval row exists; the decision is `approved` or `rejected`; the observed
status equals the decision; and `committed_at` and `revoked_at` are `NULL`.

## 10. Tests

- `test_approved_status_records_one_decision_and_transitions_intake`
- `test_rejected_status_is_a_recorded_successful_outcome`
- `test_proposed_status_stays_pending_without_an_approval_record`
- `test_draft_edited_outside_status_fails_closed_without_recording`
- `test_missing_or_replaced_draft_fails_closed`
- `test_content_disagreement_fails_closed`
- `test_approval_run_writes_nothing_to_the_vault`
- `test_note_written_directly_to_the_vault_is_not_treated_as_approved` (REQ-GOV-004)
- `test_second_run_after_a_decision_records_no_second_approval`
- `test_mixed_queue_reports_failed_without_losing_valid_decisions`
- `test_illegal_intake_states_are_rejected_for_approval` (one subtest per illegal edge)
- `test_approval_requires_a_registered_pending_proposal`
- `test_duplicate_approval_is_refused_by_sqlite`
- `test_approval_proposal_is_unique` (migration)
- `test_capture_classify_propose_approve_stops_before_filing` (integration, hand-authored link)
- `test_human_added_links_are_accepted_alongside_the_status_edit`
- `test_links_are_editable_without_a_status_change`
- `test_links_like_body_lines_are_not_frontmatter_fields`
- `test_malformed_links_block_fails_closed`
- `test_edits_outside_status_and_links_are_still_refused_when_linked`
- `test_hand_authored_links_are_approved_and_reported`
- `test_malformed_links_fail_closed_without_recording`
