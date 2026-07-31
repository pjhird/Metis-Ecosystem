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

Step 1 has verified only the repository entrypoints, clean-checkout test setup, and current data-access
boundary. No capture, vault, model, orchestration, approval, filing, or audit behavior is Verified. A
requirement moves to Verified only when a test run or observed behavior proves it — never because a document
mentions it.

Last reviewed: 2026-07-31 · Repository state at review: build-order step 1

---

## Governance and approval

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-GOV-001 | No permanent knowledge write occurs without explicit human approval | MP §21–22, BP §7 | Missing | ADR-004, ADR-005 | `unapproved_write_is_refused` |
| REQ-GOV-002 | System fails closed when permission, provenance, or approval state is undetermined | BP §7 | Missing | ADR-007 | Test: ambiguous approval state halts and creates a review item |
| REQ-GOV-003 | Proposal records carry ID, evidence, proposed change, reason, confidence, affected records, risk, approver, decision, timestamp | BP §7 | Missing | Schema doc, `proposal` table | Schema validation test |
| REQ-GOV-004 | Approval is a real state transition, not a prose instruction | BP §7 | Missing | ADR-005, ADR-006 | Test: a note written directly to the vault without an approval record is not treated as approved |
| REQ-GOV-005 | Agents and skills never expand their own permissions or self-assign skills | MP §22 | Deferred | — | Applies from Phase 8; no agents exist |

## Data and memory architecture

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-DATA-001 | Four-layer separation: evidence, knowledge, operational state, development system | BP §3 | Missing | ADR-001, ADR-002, ADR-003 | Directory and schema inspection |
| REQ-DATA-002 | Secrets excluded from all four layers | MP §31, BP §3 | Missing | ADR-017 | Secret-scanning check in CI |
| REQ-DATA-003 | State access abstracted so the engine is swappable | BP §7, §16 | Verified | `StateStore`, `SQLiteStateStore`, ADR-002, ADR-012 | `test_state_store_contract_is_engine_agnostic`; `test_sql_appears_only_in_data_layer` |
| REQ-DATA-004 | Knowledge lifecycle: raw → capture → proposal → review → approved note → verification → archive | BP §8 | Missing | Schema doc state machine | End-to-end test |
| REQ-DATA-005 | Unverified content stays visibly unverified | BP §11 | Missing | `verification` field on note schema | Test: an approved note carries its verification state honestly |

## Universal intake

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-INTK-001 | Capture preserves input immutably before any processing | BP §9, §11 | Missing | ADR-003, ADR-015 | Test: evidence written before classification runs |
| REQ-INTK-002 | Replaying the same input creates no duplicate permanent record | BP §9, §11 | Missing | ADR-014 | `duplicate_replay_creates_one_note` |
| REQ-INTK-003 | Classification produces candidate type, sensitivity, routing, confidence | MP §24, BP §9 | Missing | `classification` table | Fixture test asserting shape and confidence bounds |
| REQ-INTK-004 | Approved intake links to an existing goal or project without duplicates or orphans | BP §13, Phase 6 | Missing | ADR-013, note schemas | `unresolvable_link_blocks_commit` |
| REQ-INTK-005 | Failure preserves the source and produces a visible review state, never a false "complete" | BP §9, §11 | Missing | State machine failure states | `source_survives_classification_failure` |

## Orchestration

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-ORCH-001 | A deterministic orchestrator owns all state transitions | MP §18, BP §10 | Missing | ADR-007 | `illegal_state_transition_is_rejected` |
| REQ-ORCH-002 | Skills never call each other or reach persistence directly | BP §10 | Missing | ADR-007 | Permission test |
| REQ-ORCH-003 | Time, cost, and retry limits established per execution | MP §14, §18 | Deferred | — | Low priority for a single-user MVP; named rather than dropped |
| REQ-ORCH-004 | Every material action produces an audit event | MP §22, BP §7 | Missing | `audit_event` table | Test: each transition emits exactly one event |

## Model access

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-MODEL-001 | Providers are replaceable without rewriting the system | MP §30 | Missing | ADR-008 | `provider_sdk_imported_only_by_adapter` |
| REQ-MODEL-002 | Model output is a proposal, never a fact | MP §32, BP §7 | Missing | ADR-004, proposal schema | Test: classification output cannot reach the vault unapproved |
| REQ-MODEL-003 | Prompt versions are recorded with each execution | MP §30 | Missing | `classification.prompt_version` | Schema validation |

## Obsidian vault

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-VLT-001 | A vault exists, separate from any pre-existing vault | README | Missing | Phase 1 | Directory inspection |
| REQ-VLT-002 | Note schemas exist for Project, Goal, and typed intake notes | MP §23 | Missing | Schema doc | Frontmatter validation test |
| REQ-VLT-003 | Obsidian is the approval surface via a status field | ADR-005 | Missing | ADR-005, ADR-006 | Test: approval command reads status correctly |
| REQ-VLT-004 | Every approved note carries provenance back to its evidence | BP §8 | Missing | Note schema `capture_id` + `evidence` fields | `note_without_provenance_fails_validation` |

## Security

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-SEC-001 | Permission levels enforced technically, not by prompt wording | MP §22, §31, BP §7 | Missing | ADR-007 | Permission test suite |
| REQ-SEC-002 | Credentials never enter Git history, the vault, or logs | MP §31 | Missing | ADR-017 | `secret_never_appears_in_logs_or_notes` |
| REQ-SEC-003 | External content is treated as untrusted; prompt injection resisted | MP §22 | Deferred | — | Applies when external content enters (ADR-016 blocks this for now) |

## Integrations

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-INTG-001 | First integration is read-only and least-privilege | BP §14, Phase 9 | Deferred | ADR-016 | Trigger: MVP acceptance test passes |
| REQ-INTG-002 | Disconnecting an integration does not corrupt Metis | BP Phase 9 | Deferred | — | Trigger: first integration exists |

## Testing and evidence

| ID | Requirement | Source | Status | Design artifact | Evidence needed |
|---|---|---|---|---|---|
| REQ-TEST-001 | Critical negative tests exist and pass | BP §11 | Missing | Test plan | The nine tests named in AGENTS.md |
| REQ-TEST-002 | No capability declared working without a recorded test run | BP §11 | Missing | — | Standing constraint on all reporting, including this ledger |
| REQ-TEST-003 | Schema validation for every structured artifact | BP §11 | Partial | Schema doc; `001_initial.sql` | All five SQLite table structures and SQL-enforced constraints validated; JSON field contents, evidence metadata, and note schemas remain unimplemented |

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
