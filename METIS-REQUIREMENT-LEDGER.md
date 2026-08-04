# Metis Requirement Ledger

> The living traceability record required by [METIS-EXECUTION-BLUEPRINT.md](METIS-EXECUTION-BLUEPRINT.md) §15.
> Decisions referenced here are recorded in [METIS-DECISIONS.md](METIS-DECISIONS.md).

## How to read this

Status vocabulary is the blueprint's own:

- **Verified** — working evidence exists: a recorded test run, observed behavior, or an inspected artifact
- **Partial** — an artifact exists but acceptance criteria are unmet
- **Deferred** — postponed by an explicit decision with a stated trigger
- **Missing** — no artifact exists yet
- **Superseded** — replaced by a later decision

Step 1 verified the repository and data-access foundation. Step 2 added immutable typed capture and exact
replay protection. Step 3 added explicit classification. Step 4 added reservation-first proposal generation,
exact response and canonical-content evidence, one `status: proposed` Obsidian draft, and replay/crash recovery.
Step 5 adds approval detection: `metis approvals` reads the vault status field and records one human decision
per proposal. Step 6 adds permanent filing: `metis file` revalidates the whole evidence chain, resolves the
human's links against existing goal and project notes, writes one typed note to `vault/notes/filed/`, and
commits the approval. Step 7 adds audit emission: every material transition writes exactly one append-only
`audit_event` inside the transition's own transaction, and every refusal, duplicate, or failure that
transitions nothing writes one of its own. Phase 6 slice A (ADR-021) adds pinned planning capture:
`metis capture --as goal|project` records `type_pin` before any model call, classification may not override
the pin, and `metis file` routes approved goals and projects to `vault/goals/` / `vault/projects/` with
provenance. A requirement moves to Verified only when a named test run or observed behavior proves it — never
because a document mentions it.

Last reviewed: 2026-08-04 · Repository state at review: `main` through `step-08-planning-notes-verified` (Phase 6 slice A done; slice B not started). Outstanding Partial: `secret_never_appears_in_logs_or_notes` (REQ-SEC-002 / REQ-TEST-001).

---

## Governance and approval

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-GOV-001 | No permanent knowledge write occurs without explicit human approval | MP §21–22, BP §7 | Verified | ADR-004, ADR-005; `FilingService` | `test_unapproved_write_is_refused` (one subtest per non-`approved` intake state, each asserting an unchanged vault); `test_filing_without_an_approval_row_is_refused`; `test_a_note_dropped_into_the_filed_directory_is_not_an_approval`; `test_filing_an_unapproved_capture_writes_nothing_and_exits_zero`; `test_an_unapproved_planning_note_is_refused` |
| REQ-GOV-002 | System fails closed when permission, provenance, or approval state is undetermined | BP §7 | Verified | ADR-007; `ApprovalService`; `FilingService`; `AuditTrail` | `test_draft_edited_outside_status_fails_closed_without_recording`, `test_missing_draft_fails_closed`, `test_content_disagreement_fails_closed`, and `test_mixed_queue_reports_failed_without_losing_valid_decisions` prove an ambiguous approval halts, records nothing, and reports a visible `failed` review item. Filing fails closed on the same terms and writes nothing: `test_body_edited_after_approval_fails_closed`, `test_status_reverted_after_approval_fails_closed`, `test_broken_evidence_chain_fails_closed`, `test_canonical_content_disagreement_fails_closed`, `test_a_differing_note_at_the_filed_path_fails_closed_without_repair`. The durable review-item record arrived with Step 7: every fail-closed outcome now writes one append-only event carrying its reason — `test_a_refused_write_is_recorded_as_refused_not_failure`, `test_a_recorded_failure_emits_one_failure_event`, `test_an_invalid_capture_id_is_refused_before_any_lookup`. The record is durable and queryable through the data layer; no read command surfaces it yet (`metis status` is unimplemented) |
| REQ-GOV-003 | Proposal records carry ID, evidence, proposed change, reason, confidence, affected records, risk, approver, decision, timestamp | BP §7 | Verified | `ProposalService`; `ApprovalService`; migrations 003–004; proposal artifacts and draft contract | Step-4 fields: `test_capture_classify_propose_and_replay_stop_before_step_five`, `test_valid_classified_capture_creates_proposal_and_draft`. Approver, decision, and timestamp: `test_approved_status_records_one_decision_and_transitions_intake`, `test_capture_classify_propose_approve_stops_before_filing` |
| REQ-GOV-004 | Approval is a real state transition, not a prose instruction | BP §7 | Verified | ADR-005, ADR-006; `ApprovalService`; `record_approval` | `test_note_written_directly_to_the_vault_is_not_treated_as_approved`; `test_illegal_intake_states_are_rejected_for_approval`; `test_draft_edited_outside_status_fails_closed_without_recording` |
| REQ-GOV-005 | Agents and skills never expand their own permissions or self-assign skills | MP §22 | Deferred | — | Applies from Phase 8; no agents exist |

## Data and memory architecture

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-DATA-001 | Four-layer separation: evidence, knowledge, operational state, development system | BP §3 | Missing | ADR-001, ADR-002, ADR-003 | Directory and schema inspection |
| REQ-DATA-002 | Secrets excluded from all four layers | MP §31, BP §3 | Missing | ADR-017 | Secret-scanning check in CI |
| REQ-DATA-003 | State access abstracted so the engine is swappable | BP §7, §16 | Verified | `StateStore`, `SQLiteStateStore`, ADR-002, ADR-012 | `test_state_store_contract_is_engine_agnostic`; `test_sql_appears_only_in_data_layer` |
| REQ-DATA-004 | Knowledge lifecycle: raw → capture → proposal → review → approved note → verification → archive | BP §8 | Partial | Schema doc state machine; `metis capture`…`metis file` | `test_capture_classify_propose_approve_file_completes_the_loop` runs raw → capture → proposal → review → approved note end to end. Still needed: verification and archive, both undesigned (open question 4). |
| REQ-DATA-005 | Unverified content stays visibly unverified | BP §11 | Partial | `verification` field on note schema; single note renderer | `test_approved_note_is_filed_with_provenance_and_links` and `test_capture_classify_propose_approve_file_completes_the_loop` prove a filed, human-approved note still reads `verification: unverified`, so approval never claims content is true. This is evidence of *emission*, not of preservation: the renderer hardcodes the value and no transition can yet change it, so no test can fail. Still needed for Verified: a verification transition to stay honest against. |

## Universal intake

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-INTK-001 | Capture preserves input immutably before any processing | BP §9, §11 | Verified | ADR-003, ADR-015, ADR-021; `EvidenceStore` | Byte-exact evidence and pre-registration ordering tests; `test_source_survives_classification_failure`; `test_model_failure_records_failed_and_preserves_source`. Pin before classify: `test_capture_as_goal_records_the_pin`; `test_capture_as_project_records_the_pin_and_parent_goal`; `test_capture_as_project_requires_goal_flag` |
| REQ-INTK-002 | Replaying the same input creates no duplicate permanent record | BP §9, §11 | Verified | ADR-014; unique capture/classification/proposal/approval keys; `approved → filed` compare-and-swap; ADR-021 pin conflict on mismatched replay | `test_duplicate_replay_creates_one_note` drives the loop to a filed note, replays the identical typed input, and proves one intake, one proposal, one approval, and one file in `vault/notes/filed/`. Planning: `test_duplicate_replay_creates_one_planning_note`. Also `test_second_file_run_reports_duplicate_without_a_second_note`, `test_filing_sets_committed_at_exactly_once`, `test_filing_twice_is_refused`. Pin replay: `test_replay_with_the_same_pin_is_a_duplicate`; `test_replay_with_a_different_pin_is_refused` |
| REQ-INTK-003 | Classification produces candidate type, sensitivity, routing, confidence | MP §24, BP §9 | Verified | `ClassificationService`, `classification` table; ADR-021 pin override | `test_valid_response_is_preserved_then_persisted`; `test_invalid_enum_confidence_boolean_nonfinite_and_out_of_range_are_rejected`; `test_capture_classify_and_replay_complete_local_path`. Pin: `test_goal_pin_overrides_the_model_candidate_type`; `test_project_pin_overrides_the_model_candidate_type`; `test_pin_override_preserves_the_model_response_verbatim`; `test_pin_does_not_override_sensitivity_or_confidence`; `test_the_model_may_not_select_a_planning_type` |
| REQ-INTK-004 | Approved intake links to an existing goal or project without duplicates or orphans | BP §13, Phase 6 | Verified | ADR-013, ADR-020, ADR-021, note schemas; `FilingService._unresolved` | Typed notes still need ≥1 resolvable link: `test_unresolvable_link_blocks_commit`; `test_partly_unresolvable_links_block_the_whole_commit`; `test_approved_draft_without_links_blocks_commit`; `test_a_plain_capture_still_requires_a_link_to_file`; `test_absent_goal_and_project_directories_are_unresolvable_not_a_crash`; `test_a_link_resolves_on_note_id_not_on_filename`; `test_a_project_note_resolves_a_link_as_well_as_a_goal`; `test_a_filed_goal_resolves_a_typed_notes_link`. ADR-021 planning rules: `test_a_pinned_goal_files_under_vault_goals_with_empty_links`; `test_a_project_whose_parent_goal_is_not_filed_is_refused`; `test_a_project_may_not_name_another_project_as_its_parent`; `test_a_goal_link_that_does_not_resolve_still_blocks_the_commit`. Duplicates are rejected earlier by `test_malformed_links_block_fails_closed` |
| REQ-INTK-005 | Failure preserves the source and produces a visible review state, never a false "complete" | BP §9, §11 | Verified | State machine failure states; classification/proposal/approval reason codes; proposed draft; `AuditTrail` | Source-preservation tests plus `test_failure_recording_failure_reports_state_undetermined`, artifact-failure tests, and `test_capture_classify_propose_and_replay_stop_before_step_five` prove honest Step-4 outcomes. `test_mixed_queue_reports_failed_without_losing_valid_decisions` proves a partly-failed approval run never reports success. `test_deleted_filed_note_never_reports_duplicate` and `test_altered_filed_note_never_reports_duplicate` prove filing never claims a note that is not there, and `test_broken_evidence_chain_fails_closed` proves the source survives a blocked filing. The durable review-item record arrived with Step 7: `test_a_recorded_failure_emits_one_failure_event` proves a model failure records `classification.started` then `classification.failed`/`failure` and nothing else, so a failed run leaves a durable reason behind and never reads as complete |

## Orchestration

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-ORCH-001 | A deterministic orchestrator owns all state transitions | MP §18, BP §10 | Verified | ADR-007; capture/classification/proposal/approval/filing services; `AuditTrail`; `StateStore` | Classification and proposal compare-and-swap tests pass; `test_reclaimed_token_fences_stale_worker_completion` and the integrated Step-4 test prove proposal orchestration. `test_illegal_intake_states_are_rejected_for_approval`, `test_approval_requires_a_registered_pending_proposal`, and `test_concurrent_approval_leaves_exactly_one_decision` prove the approval transitions. `test_illegal_intake_states_are_rejected_for_filing`, `test_filing_commits_the_approval_and_marks_the_intake_filed`, and `test_filing_twice_is_refused` prove the `approved → filed` transition. Step 7 closes the last gap: the services author every event and the data layer only writes it, so `test_every_material_transition_emits_exactly_one_event` reads the trail back as the state path the capture walked, and `test_a_rolled_back_transition_is_recorded_as_refused` proves a lost compare-and-swap transitions nothing and says so |
| REQ-ORCH-002 | Skills never call each other or reach persistence directly | BP §10 | Missing | ADR-007 | Permission test |
| REQ-ORCH-003 | Time, cost, and retry limits established per execution | MP §14, §18 | Deferred | — | Low priority for a single-user MVP; named rather than dropped |
| REQ-ORCH-004 | Every material action produces an audit event | MP §22, BP §7 | Verified | ADR-007; `AuditTrail`; `audit_event` table; migration 005 | `test_every_material_transition_emits_exactly_one_event` drives the whole loop and asserts the trail is exactly the eight transitions the capture walked, in order, each with its trace and actor. The other five transition edges are asserted where they are already driven: `classification.failed` by `test_a_recorded_failure_emits_one_failure_event`; `proposal.failed` as `failure` by `test_model_request_failure_is_recorded_safely` and as `refused` by `test_unsafe_content_is_refused_without_echo_or_draft`; `draft.failed` by `test_draft_write_failure_is_recorded_without_unsafe_detail`; `proposal.reclaimed` by `test_expired_reservation_reclaims_same_proposal_id`; `draft.failed` and `proposal.resumed` by `test_recorded_draft_failure_resumes_without_model_call`. A rolled-back transition is recorded as `refused` rather than as a failure: `test_a_rolled_back_transition_is_recorded_as_refused`. Atomicity both ways: `test_a_refused_transition_writes_no_event`, `test_an_invalid_event_rolls_back_its_transition`. Append-only is enforced, not promised: `test_audit_events_are_append_only`. Boundary, stated rather than hidden: a `pending` approval poll transitions nothing and emits nothing by design, and an unreadable or uninitialized database can record no event because it can record nothing |

## Model access

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-MODEL-001 | Providers are replaceable without rewriting the system | MP §30 | Verified | ADR-008; `ModelAdapter`; `ClaudeModelAdapter` | `test_model_adapter_contract_is_provider_neutral`; `test_provider_sdk_imported_only_by_adapter` |
| REQ-MODEL-002 | Model output is a proposal, never a fact | MP §32, BP §7 | Verified | ADR-004; proposal response/content evidence; proposed draft; `FilingService` | `test_capture_classify_propose_and_replay_stop_before_step_five` proves model output stops as a pending proposal with no filed note. Approval gating on the permanent write: `test_unapproved_write_is_refused`, `test_filing_an_unapproved_capture_writes_nothing_and_exits_zero`. Nothing the model produced becomes permanent without the human's `status` edit. |
| REQ-MODEL-003 | Prompt versions are recorded with each execution | MP §30 | Verified | packaged `classify-v1`; `classification.prompt_version` | `test_classification_prompt_is_immutable_version_one`; `test_valid_response_is_preserved_then_persisted`; `test_capture_classify_and_replay_complete_local_path` |

## Obsidian vault

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-VLT-001 | A vault exists, separate from any pre-existing vault | README | Missing | Phase 1 | Directory inspection |
| REQ-VLT-002 | Note schemas exist for Project, Goal, and typed intake notes | MP §23 | Verified | ADR-021; `METIS-SCHEMAS.md` §4.1–§4.3; `DraftNoteStore` stages `goals` / `projects` / `filed` | Goal: `test_a_pinned_goal_files_under_vault_goals_with_empty_links`; `test_a_pinned_goal_proposes_a_draft_with_its_horizon`. Project: `test_a_pinned_project_files_under_vault_projects_naming_its_goal`; `test_a_pinned_project_proposes_a_draft_naming_its_parent_goal`. Typed note path unchanged: `test_a_plain_capture_still_requires_a_link_to_file`; `test_a_filed_goal_resolves_a_typed_notes_link`. Store contract: `test_a_valid_goal_note_is_written_and_read_back`; `test_a_goal_stage_refuses_a_path_outside_vault_goals` |
| REQ-VLT-003 | Obsidian is the approval surface via a status field | ADR-005 | Verified | ADR-005, ADR-006, ADR-020; deterministic proposed draft; `metis approvals` | `test_approved_status_records_one_decision_and_transitions_intake`; `test_rejected_status_is_a_recorded_successful_outcome`; `test_proposed_status_stays_pending_without_an_approval_record`; `test_approval_run_writes_nothing_to_the_vault`. Two-field draft contract: `test_human_added_links_are_accepted_alongside_the_status_edit`; `test_edits_outside_status_and_links_are_still_refused_when_linked`; `test_malformed_links_block_fails_closed`. Planning drafts stay on the same surface: `test_approvals_reads_a_planning_draft_it_did_not_render`; `test_approving_a_goal_draft_is_read_as_approved`; `test_editing_a_projects_parent_goal_is_refused` |
| REQ-VLT-004 | Every approved note carries provenance back to its evidence | BP §8 | Verified | Note schema `capture_id` + `evidence` fields; `DraftNoteStore._validate_provenance`; ADR-021 extends this to goals/projects | `test_note_without_provenance_fails_validation` (three subtests, each stripping one provenance field from otherwise valid bytes and asserting the write is refused); `test_a_planning_note_without_provenance_fails_validation`; `test_approved_note_is_filed_with_provenance_and_links`; `test_a_pinned_goal_files_under_vault_goals_with_empty_links`; `test_capture_classify_propose_approve_file_completes_the_loop` |

## Security

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-SEC-001 | Permission levels enforced technically, not by prompt wording | MP §22, §31, BP §7 | Missing | ADR-007 | Permission test suite |
| REQ-SEC-002 | Credentials never enter Git history, the vault, or logs | MP §31 | Partial | ADR-017; proposal content screen | `test_unsafe_content_is_refused_without_echo_or_draft` proves screened proposal content reaches neither CLI nor vault. Still needed: the full `secret_never_appears_in_logs_or_notes` and Git-history scan. |
| REQ-SEC-003 | External content is treated as untrusted; prompt injection resisted | MP §22 | Deferred | — | Applies when external content enters (ADR-016 blocks this for now) |

## Integrations

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-INTG-001 | First integration is read-only and least-privilege | BP §14, Phase 9 | Deferred | ADR-016 | Trigger: MVP acceptance test passes |
| REQ-INTG-002 | Disconnecting an integration does not corrupt Metis | BP Phase 9 | Deferred | — | Trigger: first integration exists |

## Testing and evidence

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-TEST-001 | Critical negative tests exist and pass | BP §11 | Partial | Test plan | Eight of the nine tests named in AGENTS.md now exist and pass: `unapproved_write_is_refused`, `duplicate_replay_creates_one_note`, `source_survives_classification_failure`, `illegal_state_transition_is_rejected` (carried by the per-stage `test_illegal_intake_states_are_rejected_for_approval` and `..._for_filing`, one subtest per illegal edge), `note_without_provenance_fails_validation`, `unresolvable_link_blocks_commit`, `sql_appears_only_in_data_layer`, `provider_sdk_imported_only_by_adapter`. Outstanding: `secret_never_appears_in_logs_or_notes` |
| REQ-TEST-002 | No capability declared working without a recorded test run | BP §11 | Missing | — | Standing constraint on all reporting, including this ledger |
| REQ-TEST-003 | Schema validation for every structured artifact | BP §11 | Verified | Schema doc; migrations 001–005; evidence/content/draft stores; `_validated_audit` | SQLite constraints, all Step-2–4 evidence metadata, model JSON, proposal content, draft bytes, and CLI result shapes are validated. `test_inconsistent_approval_records_are_refused_before_any_write`, `test_approval_proposal_is_unique`, and `test_approval_shell_outcomes_use_stable_json_streams_and_codes` add the approval record and result shapes. `test_note_without_provenance_fails_validation` and `test_file_shell_outcomes_use_stable_json_streams_and_codes` add the filed-note and filing result shapes. Audit events are validated at the data layer before they are written — ULID, trace, actor, action, outcome, JSON-object detail, canonical UTC timestamp — by `test_an_unusable_event_is_refused_by_the_data_layer` and `test_audit_outcome_is_enforced` |

## Repository and tooling

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-REPO-001 | Concise governing instruction file readable by the tools in use | BP §4 | Verified | ADR-009, `AGENTS.md`, `CLAUDE.md` | `test_governance_entrypoints_exist` |
| REQ-REPO-002 | Directories created only as the active phase needs them | BP §6 | Missing | — | Review at each phase |
| REQ-REPO-003 | Setup works from a clean checkout | BP Phase 2 | Verified | Standard-library test harness | Fresh-clone run: `python3 -m unittest discover -s tests -v` |

---

## Open questions

Recorded so they are not silently resolved by implementation choices.

1. **Semantic duplicate detection.** ADR-014 handles exact replay. Two differently-worded captures about the
   same idea remain unaddressed. Deferred to a phase after the loop works.
2. **Approval expiry.** Approvals to act *externally* need a TTL. Whether *knowledge* approvals also need one
   is undecided — probably not, since filing a note is reversible.
3. **Confidence thresholds.** Master prompt §24 calls for them. What confidence level should force escalation
   rather than proposal is not yet chosen; needs real classification output to calibrate against.
4. **Archive and supersession mechanics.** Blueprint §8 requires that corrections supersede prior knowledge
   while preserving history. The mechanism is undesigned.
5. **Vault backup and recovery.** Git covers the repository. Whether the vault is a Git repository itself, or
   backed up separately, is undecided.

## Ledger maintenance

Update this file when a requirement changes status, when a new requirement is identified, or when an open
question is resolved. A status change to **Verified** requires naming the test or observation that proves it.
