# Step 4 Proposal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform one valid classified capture into one persisted proposal and one reviewable
`status: proposed` draft with deterministic state transitions, crash recovery, and replay protection.

**Architecture:** `ProposalService` coordinates a token-fenced reservation, the existing model seam,
append-only response and content stores, SQLite transactions, and an exclusive draft-note store. Claude
supplies only bounded semantic fields; deterministic code owns every authority-bearing value.

**Tech Stack:** Python 3.13, standard-library `unittest`, SQLite, existing `anthropic>=0.104,<1` dependency.

## Global Constraints

- Work only on `step/04-propose`, from approved design commit `6d91f3f4763be76e0c60c1240786936098c72764`.
- Follow red-green TDD for every behavior: write one test, observe the intended failure, implement the minimum,
  rerun the focused test, and keep earlier tests green.
- Never inspect, modify, stage, rename, or commit `METIS-EXECUTION-SPINE.md`.
- Make no live provider call.
- Add no approval detection, approval command, permanent filing, final-link enforcement, audit emission,
  watcher, integration, agent, container, UI, vector database, graph database, or second approval surface.
- Keep SQL under `metis/data_access/` and `anthropic` imports in `metis/model_adapters/claude.py` only.
- Use `/opt/miniconda3/bin/python3` with a task-specific `PYTHONPYCACHEPREFIX` for verification.
- Keep requirement-ledger statuses conservative; no named passing test means no implementation evidence.
- Do not push, open a pull request, tag, or change GitHub settings without separate authorization.

---

### Task 1: Add the Bounded Proposal Prompt and Provider Operation

**Files:**
- Create: `metis/prompts/propose-v1.txt`
- Modify: `metis/prompts/__init__.py`
- Modify: `metis/model_adapters/contracts.py`
- Modify: `metis/model_adapters/claude.py`
- Modify: `tests/test_model_contracts.py`
- Modify: `tests/test_claude_adapter.py`

**Interfaces:**
- Consumes: existing `ModelResponse` and bounded adapter exceptions.
- Produces: `PROPOSAL_PROMPT_VERSION`, `load_proposal_prompt()`,
  `ModelAdapter.propose(prompt: str) -> ModelResponse`, and `ClaudeModelAdapter.propose()`.

- [ ] Write failing tests named `test_proposal_prompt_is_immutable_version_one`,
  `test_model_adapter_contract_includes_propose`,
  `test_claude_proposal_uses_exact_schema_and_model_override`,
  `test_proposal_refusal_and_truncation_preserve_received_text`, and
  `test_classification_configuration_remains_unchanged`.
- [ ] Run `env PYTHONPYCACHEPREFIX=/private/tmp/metis-step-04-task1-red /opt/miniconda3/bin/python3 -m unittest tests.test_model_contracts tests.test_claude_adapter -v`; verify failures are caused by the absent proposal operation and prompt.
- [ ] Add the exact four-field JSON schema, immutable `propose-v1` prompt, proposal-specific model override
  `METIS_PROPOSAL_MODEL`, and a private shared Claude request helper without moving the SDK import.
- [ ] Rerun the two focused modules and verify green.
- [ ] Commit as `feat(propose): add bounded model contract` with REQ-MODEL-001, REQ-MODEL-003, ADR-008, named tests, and the Codex co-author trailer.

### Task 2: Migrate the Operational Schema Safely

**Files:**
- Create: `metis/data_access/migrations/003_proposal_reservation.sql`
- Modify: `tests/data_access/test_migrations.py`

**Interfaces:**
- Consumes: schema version 2 with five operational tables.
- Produces: schema version 3, intake state `proposing`, extended proposal columns and uniqueness, and the
  sixth `proposal_reservation` table.

- [ ] Write failing tests for six-table schema, `proposing`, all new proposal fields, proposal and reservation
  uniqueness, v2 intake/classification preservation, refusal of unverifiable preexisting proposal rows,
  atomic rollback, and idempotent reapplication.
- [ ] Run `env PYTHONPYCACHEPREFIX=/private/tmp/metis-step-04-task2-red /opt/miniconda3/bin/python3 -m unittest tests.data_access.test_migrations -v`; verify schema-version and missing-table failures.
- [ ] Add migration 003. Use an empty-proposal guard, rebuild intake/classification/proposal/approval in foreign-key order, preserve intake/classification rows, leave audit rows untouched, add all Step-4 constraints, and create the reservation table.
- [ ] Rerun migration tests and verify green.
- [ ] Commit as `feat(propose): migrate reservation-first schema` with REQ-GOV-003, REQ-TEST-003, ADR-002, named tests, and the Codex co-author trailer.

### Task 3: Extend StateStore with Fenced Proposal Operations

**Files:**
- Create: `tests/data_access/test_proposal_store.py`
- Modify: `metis/data_access/contracts.py`
- Modify: `metis/data_access/sqlite.py`
- Modify: `metis/data_access/__init__.py`
- Modify: `tests/data_access/test_migrations.py`

**Interfaces:**
- Produces frozen `ProposalReservationRecord` and `ProposalRecord` values plus lookup, begin, reclaim,
  failure, completion, draft-resume, and draft-registration operations defined in the approved plan.

- [ ] Write failing tests for atomic reservation plus `classified -> proposing`, active-lease refusal,
  expired-lease CAS reclaim with the same proposal ID, stale-token refusal, atomic proposal completion,
  atomic draft registration, recorded draft-failure recovery, every illegal edge, and rollback after errors.
- [ ] Run `env PYTHONPYCACHEPREFIX=/private/tmp/metis-step-04-task3-red /opt/miniconda3/bin/python3 -m unittest tests.data_access.test_proposal_store -v`; verify absent-contract failures.
- [ ] Implement the exact protocol and SQLite methods using `BEGIN IMMEDIATE`, prior-value comparisons,
  uniqueness constraints, row-count checks, rollback, and safe error wrapping.
- [ ] Run proposal-store, migration, classification-store, and intake-store tests and verify green.
- [ ] Commit as `feat(propose): add fenced proposal state transitions` with REQ-ORCH-001, ADR-002, ADR-007, named tests, and the Codex co-author trailer.

### Task 4: Preserve Proposal Responses Before Parsing

**Files:**
- Create: `metis/proposal_evidence.py`
- Create: `tests/test_proposal_evidence.py`
- Modify: `.gitignore`
- Modify: `tests/test_repository_skeleton.py`

**Interfaces:**
- Produces `ProposalEvidenceRecord`, `ProposalEvidenceStore.create()`, `validate_directory()`, and bounded
  collision, consistency, and write errors.

- [ ] Write failing tests for exact bytes and metadata, all file/directory collisions, partial writes,
  symlinks, non-regular entries, UUID/ULID/timestamp validation, invalid UTF-8, byte disagreement, exact-key
  enforcement, matching race reuse, and identity disagreement.
- [ ] Run the focused evidence and ignore tests and verify red.
- [ ] Implement exclusive creation at `proposal-evidence/<proposal_id>/`, strict validation, relative safe
  paths, and a computed 64-character lowercase raw-response SHA-256.
- [ ] Rerun focused tests and verify green.
- [ ] Commit as `feat(propose): preserve raw proposal responses` with REQ-INTK-005, REQ-TEST-003, ADR-003, ADR-008, named tests, and the Codex co-author trailer.

### Task 5: Validate Semantic Proposal Content Deterministically

**Files:**
- Create: `metis/proposal_contract.py`
- Create: `tests/test_proposal_contract.py`

**Interfaces:**
- Produces frozen `SemanticProposal`, `parse_proposal_response()`, `render_proposal_body()`, and
  `risk_for_sensitivity()`.

- [ ] Write failing tests for exact keys, duplicate keys, field types, every length/byte/count boundary,
  invalid Unicode/control characters, uncertainty uniqueness/order, every approved credential expression,
  every disallowed Markdown form, exact canonical rendering, and both deterministic risk values.
- [ ] Run the focused contract test and verify red.
- [ ] Implement strict local JSON parsing, validation, safety screening, LF-only rendering, and deterministic
  risk without silent normalization.
- [ ] Rerun the focused module and verify green.
- [ ] Commit as `feat(propose): validate bounded proposal content` with REQ-MODEL-002, REQ-TEST-003, ADR-004, named tests, and the Codex co-author trailer.

### Task 6: Persist Canonical Proposal Content Append-Only

**Files:**
- Create: `metis/proposal_content.py`
- Create: `tests/test_proposal_content.py`
- Modify: `.gitignore`
- Modify: `tests/test_repository_skeleton.py`

**Interfaces:**
- Produces `ProposalContentRecord`, `ProposalContentStore.create()`, and `validate_directory()`.

- [ ] Write failing tests for exact `body.md` and metadata, content hashing, response-hash lineage, exclusive
  creation, matching race reuse, disagreement refusal, partial/extra files, symlinks, invalid IDs, and invalid UTF-8.
- [ ] Run focused content and ignore tests and verify red.
- [ ] Implement strict append-only storage at `proposal-content/<proposal_id>/` with exact metadata and hashes.
- [ ] Rerun focused tests and verify green.
- [ ] Commit as `feat(propose): persist canonical proposal content` with REQ-GOV-003, REQ-TEST-003, ADR-003, named tests, and the Codex co-author trailer.

### Task 7: Render and Validate the Proposed Draft Note

**Files:**
- Create: `metis/draft_notes.py`
- Create: `tests/test_draft_notes.py`

**Interfaces:**
- Produces `DraftStatus`, `DraftNoteRecord`, `render_proposed_draft()`, `DraftNoteStore.create()`, and
  `DraftNoteStore.validate()`.

- [ ] Write failing tests for the exact filename, frontmatter field order/values, safe scalar quoting, all
  provenance references, empty links, exact body, proposed replay, approved/rejected preservation without
  interpretation, rejection of every other edit, root escape, symlink parents/final file, collision, partial
  write, and read-back disagreement.
- [ ] Run the focused draft test and verify red.
- [ ] Implement deterministic rendering, exclusive flush-and-close creation, read-back SHA-256, and comparison
  that permits only the single status scalar to differ.
- [ ] Rerun focused tests and verify green.
- [ ] Commit as `feat(propose): create reviewable Obsidian draft` with REQ-GOV-001, REQ-GOV-003, ADR-004, ADR-005, named tests, and the Codex co-author trailer.

### Task 8: Implement the New-Proposal Happy Path

**Files:**
- Create: `metis/proposal.py`
- Create: `tests/test_proposal.py`
- Modify: `metis/classification.py`

**Interfaces:**
- Produces `ProposalStatus`, the exact frozen `ProposalResult`, and `ProposalService.propose(capture_id)`.
- Makes the existing classification parser a module-level pure helper without changing Step-3 behavior.

- [ ] Write failing tests for one valid classified capture, evidence-before-parse ordering, reservation then
  proposal then draft ordering, deterministic ownership despite conflicting model text, sensitive/high risk,
  confidence without authority, and final read-back before success.
- [ ] Run the focused proposal test and verify red.
- [ ] Implement only the approved 14-stage new-proposal path and safe result construction.
- [ ] Run proposal, classification, evidence, and data-access focused modules and verify green.
- [ ] Commit as `feat(propose): orchestrate one proposal and draft` with REQ-GOV-003, REQ-INTK-005, REQ-ORCH-001, ADR-007, ADR-008, named tests, and the Codex co-author trailer.

### Task 9: Add Fail-Closed Policy and Consistency Outcomes

**Files:**
- Modify: `metis/proposal.py`
- Modify: `tests/test_proposal.py`

- [ ] Write one failing observable test for every stable reason code and for missing, corrupt, inconsistent,
  ambiguous, symlinked, and colliding intake/classification/proposal/draft state.
- [ ] Run the focused proposal tests and verify each new test fails for the absent branch.
- [ ] Add minimum safe failure/refusal branches, `proposal.<reason>` recording, immediate lease expiry for known
  failures, stale-token fencing, and safe messages that contain no captured/model/provider/database text.
- [ ] Rerun focused and prior Step-2/Step-3 tests and verify green.
- [ ] Commit as `feat(propose): fail closed on proposal inconsistency` with REQ-INTK-005, REQ-SEC-002, ADR-004, ADR-007, ADR-017, named tests, and the Codex co-author trailer.

### Task 10: Implement Lease Reclaim, Replay, and Crash Recovery

**Files:**
- Create: `tests/test_proposal_recovery.py`
- Modify: `metis/proposal.py`

- [ ] Write failing tests at every approved crash boundary plus active reservation, expired reclaim, stale
  worker, complete-response reuse, complete-content resume, completed-proposal resume, exact unregistered-draft
  registration, exact replay, partial-artifact refusal, and human-status preservation.
- [ ] Run the focused recovery test and verify red for the absent recovery branches.
- [ ] Implement explicit invocation-driven recovery only; revalidate each established artifact before advancing
  and never rewrite or automatically repair it.
- [ ] Rerun recovery, proposal, store, and artifact tests and verify green.
- [ ] Commit as `feat(propose): recover proposal attempts idempotently` with REQ-INTK-002, REQ-INTK-005, REQ-ORCH-001, ADR-014, named tests, and the Codex co-author trailer.

### Task 11: Add the `metis propose` CLI Contract

**Files:**
- Modify: `metis/cli.py`
- Modify: `tests/test_cli.py`

- [ ] Write failing tests for exact argument count, stable complete JSON keys, stdout/exit-zero success,
  duplicate/refusal, stderr/exit-one failure, relative POSIX paths, safe initialization failure, no text leak,
  and absence of approval/file/link/status-changing options.
- [ ] Run `tests.test_cli` and verify red.
- [ ] Wire real stores and `ProposalService` into the explicit subcommand while preserving capture/classify.
- [ ] Rerun CLI and existing integration tests and verify green.
- [ ] Commit as `feat(propose): expose explicit proposal command` with REQ-GOV-003, REQ-INTK-005, ADR-005, ADR-007, named tests, and the Codex co-author trailer.

### Task 12: Prove Integration Boundaries and Update Governed Documentation

**Files:**
- Create: `tests/test_proposal_integration.py`
- Modify: `tests/test_data_access_boundary.py`
- Modify: `tests/test_provider_boundary.py`
- Modify: `tests/test_repository_skeleton.py`
- Modify: `AGENTS.md`
- Modify: `METIS-SCHEMAS.md`
- Modify: `METIS-REQUIREMENT-LEDGER.md`

- [ ] Write failing integration/scope tests proving capture-classify-propose reaches `awaiting_approval`, replay
  leaves one proposal and draft, filed/approval/audit writes are absent, forbidden states are not written, links
  remain empty, SQL/provider boundaries hold, no live call occurs, prompt packaging works, and runtime paths are ignored.
- [ ] Run the focused integration and boundary modules and verify red.
- [ ] Add only required packaging/documentation/ledger changes. Keep Step-5-or-later requirements unverified;
  move REQ-GOV-003 at most to Partial and cite only named passing tests.
- [ ] Run focused integration/boundary modules, then the complete Python 3.13 suite.
- [ ] Run `git diff --check`, inspect in-scope status/diff excluding the execution spine, verify trailers, and scan
  for secrets and unintended files.
- [ ] Commit as `docs(propose): record verified step 4 evidence` with only requirements actually evidenced,
  named tests, decisions, and the Codex co-author trailer.

## Completion Gate

After all tasks, use `superpowers:verification-before-completion`, then
`superpowers:finishing-a-development-branch`. Do not tag, push, or open a pull request until the human owner
chooses and authorizes the integration path.
