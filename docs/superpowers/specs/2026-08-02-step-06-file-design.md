# Step 6 Filing Design

## Status

- Build-order step: 6 — File
- Implementation status: in progress
- Base: verified `main` at merge commit `3d6ebe2` (step 5 merged)
- Governing decisions: ADR-001, ADR-002, ADR-003, ADR-004, ADR-005, ADR-007, ADR-013, ADR-014, ADR-019, ADR-020
- Primary requirements: REQ-GOV-001, REQ-INTK-002, REQ-INTK-004, REQ-VLT-004, REQ-MODEL-002, REQ-ORCH-001
- Reaching Verified in this step: REQ-GOV-001, REQ-INTK-002, REQ-INTK-004, REQ-VLT-004, REQ-MODEL-002
- Staying Partial: REQ-DATA-004 (archive is undesigned — ledger open question 4) and REQ-TEST-001
  (8 of 9 required tests exist after this step; `secret_never_appears_in_logs_or_notes` is outstanding)

## 1. Objective

Add one explicit filing operation:

```text
metis file <capture-id>
```

It turns a recorded human approval into the permanent note the loop exists to produce: one typed note in
`vault/notes/filed/`, carrying provenance back to its evidence, linked to an existing goal or project, with
`intake.state = filed` and `approval.committed_at` set in the same transaction.

This step retires four of the nine required tests: `unapproved_write_is_refused`,
`unresolvable_link_blocks_commit`, `note_without_provenance_fails_validation`, and
`duplicate_replay_creates_one_note`.

## 2. Scope

### Included

- One `metis file` CLI subcommand taking a capture ID, matching the `classify` / `propose` shape.
- A deterministic `FilingService` that owns every Step-6 transition.
- Full revalidation of the evidence chain before any permanent write, as Step 5 §5 requires.
- Link resolution against `vault/goals/` and `vault/projects/`, refusing to file an orphan.
- Exclusive, fsync'd, read-back write of `vault/notes/filed/note.<capture_id>.md`.
- Atomic `approved → filed` intake transition paired with `approval.committed_at`.
- Replay: a second run files no second note.

### Excluded

- Audit-event emission and durable review items — Step 7 (REQ-ORCH-004, REQ-GOV-002).
- Creating goal or project notes. Metis never authors them (ADR-020); the human does.
- Deleting or moving the proposed draft after filing. Nothing requires it and deletion is irreversible.
- Filing rejected proposals, revocation, supersession, archive (ledger open questions 2 and 4).
- A queue-sweep `metis file` with no argument. One capture, one explicit command.

### No migration

`filed` is already legal in the intake `CHECK` (migration 003) and `approval.committed_at` already exists and
is always `NULL` after Step 5 — it is the filed-at marker by construction. The filed note path is derived
(`vault/notes/filed/note.<capture_id>.md`), exactly as the draft path is, so nothing new is persisted.
Step 6 adds no `005_*.sql`.

## 3. `intake.state = approved` is not clearance to file

Step 5's design hands Step 6 an explicit precondition (§5): an approval record attests to *what the human
saw*, not to what the files say now. Nothing re-reads the vault between approval and filing, so the whole
chain is revalidated before the first permanent byte is written.

Order of validation, all before any write:

1. **Intake state is exactly `approved`.** Any other state refuses. This is `unapproved_write_is_refused`:
   the writer's authority comes from the state machine and the approval row, never from the file.
2. **Structural invariants.** Proposal exists, is `approved`, matches the capture and its classification, has
   the registered draft path and body path.
3. **The approval row exists**, is `decision = approved`, `approver LIKE 'human:%'`, `committed_at IS NULL`,
   `revoked_at IS NULL`.
4. **Evidence chain revalidated** — capture evidence, classification evidence, proposal evidence, and the
   canonical proposal content, with hashes and identities agreeing with their rows.
5. **The draft is re-read** through `DraftNoteStore.validate` and must still observe `status: approved`. A
   body edited in the approval→filing window is caught here and fails closed.
6. **Links resolve** (§5).

The note body is taken from `proposal-content/<proposal_id>/body.md`, never from the draft file. The approval
attests to the canonical content; the draft is a view of it.

## 4. What gets written

One renderer produces both the proposed draft and the filed note, so their content is provably identical
outside the fields that are supposed to differ. `render_proposed_draft(proposal, body)` keeps its exact
current signature and output and delegates to:

```python
render_note(proposal, canonical_body, *, status=DraftStatus.PROPOSED, links=(), approved=None) -> bytes
```

Filing calls it with `status=DraftStatus.APPROVED`, the observed links, and the approval's `detected_at` as
`approved`. Three frontmatter values differ from the draft — `status`, `approved`, `links` — and nothing else.
`id`, `capture_id`, `evidence`, `confidence`, `sensitivity`, `risk_level`, `verification`, and the body are
byte-identical to what the human approved.

`verification` stays `unverified`. Approving that a note should exist is not verifying its content
(REQ-DATA-005).

**`approved` carries the approval's `detected_at`, not the filing time.** Schema §4.3's "set on commit"
describes when the field is written, not what it holds, and the note should date itself to the human's
decision. The binding reason is determinism: if `approved` held `committed_at`, a re-run after a crash would
render different bytes every time, and §6's resume-on-identical-file could never match. The filing time is
recorded in `approval.committed_at`, where it belongs.

### Provenance validation

`note_without_provenance_fails_validation` must be able to fail, so it tests the *validator*, not the
renderer. `FiledNoteStore.create` refuses bytes whose frontmatter lacks a `capture_id:` line or an `evidence:`
block, or that do not carry exactly one `status: approved` line — mirroring `_validate_expected_bytes` in the
draft store. The renderer always emits provenance; the validator is what makes that a guarantee rather than
an accident.

## 5. Link resolution

ADR-020 assigns this to the Step-6 writer by name: resolve each link against an existing note's `id` in
`vault/goals/` or `vault/projects/`, and refuse to file on an unresolvable one.

- The resolver reads `*.md` directly in `vault/goals/` and `vault/projects/`, extracts the `id:` field from
  each file's frontmatter, and builds the set of resolvable targets. No YAML dependency; the field is read
  the same deterministic way the draft store reads frontmatter.
- A missing `vault/goals/` or `vault/projects/` directory yields an empty set — **unresolvable, not a crash**.
  Those directories do not exist yet (REQ-VLT-001).
- **At least one link is required.** The MVP completion criterion in `AGENTS.md` says the filed note is
  "linked to an existing goal or project"; REQ-INTK-004 exists to prevent orphans. An approved draft still
  carrying `links: []` therefore blocks. It gets its own reason code — `filing.links_absent` versus
  `filing.link_unresolvable` — because "you forgot to add a link" and "`[[goal.foo]]` does not exist" are
  different human errors, and the CLI message is the only review surface until Step 7.

Both are recoverable without a second approval: `links` stays human-editable, so the owner adds or corrects
the link in the draft and re-runs `metis file` with the intake still at `approved`.

**An unresolvable link writes nothing, anywhere.** No file, no database row. ADR-020 is explicit that it
blocks the permanent write without invalidating the approval, so the intake stays at `approved` and the run
reports `failed` with a visible reason. There is no `approved → failed` edge in the state machine and Step 6
does not invent one — that would need its own merged ADR. The visible CLI failure is the whole obligation
here; the durable review item arrives with audit emission in Step 7.

## 6. Write ordering and the crash window

Artifact first, then state — the shape `register_proposal_draft` already uses:

1. `FiledNoteStore.create` writes `vault/notes/filed/note.<capture_id>.md` with `open("xb")`, `fsync`, and a
   read-back comparison. Exclusive creation is the collision guard.
2. `record_filing` runs one `BEGIN IMMEDIATE` transaction: compare-and-swap intake `approved → filed`, and
   `UPDATE approval SET committed_at = ? WHERE approval_id = ? AND committed_at IS NULL`.

A crash between the two leaves a filed note with `intake.state = approved`. The next run recomputes the
expected bytes, finds the collision, and **resumes**: if the existing file is byte-identical to what this run
would write, it proceeds to the state transition; if it differs, it fails closed and repairs nothing. This
mirrors Step 4's `resume_proposal_draft` rather than making a crash terminal. Resume is safe because the
transition is still gated on the approval row and the CAS — a hand-planted file cannot manufacture an
approval, and reproducing the expected bytes requires the whole approved proposal to already exist.

## 7. Replay

`duplicate_replay_creates_one_note`. A second `metis file` on a `filed` intake writes nothing and reports
`duplicate`. Combined with exclusive creation and the `approved → filed` CAS, the same input can never
produce a second note.

`duplicate` is a success claim, so it is earned rather than assumed: the replay path recomputes the expected
bytes and compares them to the file on disk, exactly as `ProposalService._replay` revalidates its artifact. A
filed note that has been deleted or altered reports `failed` / `filing.filed_note_missing`, never `duplicate`
(non-negotiable rule 4). This is the same comparison §6's resume path performs, on the other side of the CAS.

The end-to-end replay test drives the whole loop twice: capture → classify → propose → approve → file, and
asserts one intake, one proposal, one approval, one filed note.

## 8. Result vocabulary

| Status | Meaning | Stream | Exit |
|---|---|---|---|
| `filed` | permanent note written, intake `filed`, approval committed | stdout | 0 |
| `duplicate` | already filed; no second note written | stdout | 0 |
| `refused` | intake is not `approved` — nothing to file; a correct, successful outcome | stdout | 0 |
| `failed` | chain, draft, or link state undetermined; nothing written | stderr | 1 |

Matches `CaptureStatus` and `ProposalStatus` exactly. Reason codes are namespaced `filing.*`.

`cli.py` currently routes propose through a bare `else:` in both the command branch and the
state-initialization fallback, so `file` would silently take the propose path in each. Both become explicit
`elif arguments.command == "file":` branches, and the fallback returns a `FilingResult` with
`state_initialization_failed`, mirroring the other three verbs.

## 9. State-store contract

Two additions, no schema change:

```python
def find_approval_by_proposal_id(self, proposal_id: str) -> Optional[ApprovalRecord]
def record_filing(self, capture_id, proposal_id, approval_id, committed_at) -> IntakeRecord
```

`record_filing` refuses unless, under compare-and-swap inside `BEGIN IMMEDIATE`: the intake is `approved`
with no failure reason; the proposal is `approved` and belongs to that capture; the approval row matches the
given ID and proposal, carries `decision = approved`, and has `committed_at IS NULL` and `revoked_at IS NULL`.

## 10. Tests

Required tests retired by this step:

- `test_unapproved_write_is_refused` — one subtest per non-`approved` intake state
- `test_unresolvable_link_blocks_commit`
- `test_note_without_provenance_fails_validation`
- `test_duplicate_replay_creates_one_note`

Step behavior:

- `test_approved_note_is_filed_with_provenance_and_links`
- `test_filed_note_matches_the_approved_content_outside_status_links_and_approved`
- `test_missing_link_targets_write_nothing_and_leave_the_intake_approved`
- `test_approved_draft_without_links_blocks_commit`
- `test_absent_goal_and_project_directories_are_unresolvable_not_a_crash`
- `test_body_edited_after_approval_fails_closed`
- `test_missing_or_broken_evidence_chain_fails_closed`
- `test_filing_without_an_approval_row_is_refused`
- `test_filing_a_rejected_proposal_is_refused`
- `test_second_file_run_reports_duplicate_without_touching_the_vault`
- `test_deleted_filed_note_never_reports_duplicate`
- `test_file_command_is_routed_and_not_treated_as_propose`
- `test_crash_between_write_and_transition_resumes_on_the_identical_file`
- `test_differing_filed_note_fails_closed_without_repair`
- `test_illegal_intake_states_are_rejected_for_filing`
- `test_filing_sets_committed_at_exactly_once`
- `test_file_shell_outcomes_use_stable_json_streams_and_codes`
- `test_capture_classify_propose_approve_file_completes_the_loop` (integration)
