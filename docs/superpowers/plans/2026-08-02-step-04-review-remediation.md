# Step 4 Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three Important Step-4 review findings without adding Step-5 behavior or changing the approved proposal contract.

**Architecture:** Strengthen the three Step-4 filesystem boundaries so every configured ancestor is a real directory, make proposal orchestration revalidate and reuse only exact matching exclusive-create race winners, and prove restart behavior at every approved crash boundary with controlled fault-injection doubles. Keep storage formats, public result shapes, state transitions, provider isolation, and approval ownership unchanged.

**Tech Stack:** Python 3.13 standard library, `unittest`, SQLite through `StateStore`, filesystem stores, fake `ModelAdapter` implementations.

## Global Constraints

- Work only in `/Users/philly/Desktop/Metis-Ecosystem/.worktrees/step-04-propose` on `step/04-propose`.
- Do not inspect, modify, stage, rename, or commit `METIS-EXECUTION-SPINE.md`; exclude it from every status and diff command.
- Use strict red-green TDD: add one observable failing test, run it and confirm the expected failure, then write the minimum production code.
- One classified capture still produces one pending proposal and one `status: proposed` draft.
- Proposed links remain exactly `[]`; no approval detection, approved/rejected transition, permanent filing, final links, audit writes, second approval surface, or Step-5 command.
- No live paid provider call; every model interaction uses a fake adapter.
- SQL remains only in `metis/data_access/`.
- Do not push, merge, tag, delete the branch/worktree, or change PR state.
- Use `/opt/miniconda3/bin/python3.13` and `PYTHONPYCACHEPREFIX=/private/tmp/metis-step-04-remediation-pycache` for verification.

---

### Task 1: Reject Symlinked Ancestors at Step-4 Filesystem Boundaries

**Files:**
- Modify: `metis/draft_notes.py:155-218`
- Modify: `metis/proposal_evidence.py:105-171`
- Modify: `metis/proposal_content.py:100-165`
- Test: `tests/test_draft_notes.py`
- Test: `tests/test_proposal_evidence.py`
- Test: `tests/test_proposal_content.py`

**Interfaces:**
- Consumes: configured runtime root and the fixed `vault/notes/proposed`, `proposal-evidence`, and `proposal-content` layouts.
- Produces: creation and replay validation that refuse a symlinked runtime root, store root, or intermediate draft parent before reading or writing the target artifact.

- [ ] **Step 1: Add the failing draft replay test**

  Add `test_validate_refuses_parent_replaced_by_symlink` to `DraftNoteTests`. Create a valid proposed draft, move the real `vault/notes/proposed` directory elsewhere under the temporary root, replace it with a symlink to the moved directory, and assert `DraftNoteStore.validate()` raises `DraftNoteConsistencyError`. The production mutation caught is removal or omission of ancestor validation during replay.

- [ ] **Step 2: Run the draft test and verify red**

  Run:

  ```bash
  env PYTHONPYCACHEPREFIX=/private/tmp/metis-step-04-remediation-pycache /opt/miniconda3/bin/python3.13 -m unittest tests.test_draft_notes.DraftNoteTests.test_validate_refuses_parent_replaced_by_symlink -v
  ```

  Expected: FAIL because current validation follows the symlinked parent and accepts the exact bytes.

- [ ] **Step 3: Add failing proposal-store ancestry tests**

  In each proposal store test module, add one creation test using a symlinked store root and one replay test that creates a valid artifact, moves the real store root, replaces it with a symlink, and calls `validate_directory()` through the configured store path. Assert the store-specific write or consistency error. The production mutations caught are following a symlink in `mkdir(parents=True, exist_ok=True)` and validating only the leaf artifact directory.

- [ ] **Step 4: Run the proposal-store ancestry tests and verify red**

  Run the new named tests from `tests.test_proposal_evidence` and `tests.test_proposal_content`; each must fail because the current stores follow the symlinked root.

- [ ] **Step 5: Implement minimum ancestry validation**

  In `DraftNoteStore`, validate the configured runtime root plus `vault`, `notes`, and `proposed` as non-symlink directories during both creation and replay validation. In each proposal store, require the configured runtime root and fixed store root to be real directories, require the supplied artifact directory to be the exact configured child, and perform these checks before creating or reading artifact files. Preserve current exclusive-create, exact-file-set, and non-regular-leaf checks.

- [ ] **Step 6: Run focused filesystem tests and verify green**

  ```bash
  env PYTHONPYCACHEPREFIX=/private/tmp/metis-step-04-remediation-pycache /opt/miniconda3/bin/python3.13 -m unittest tests.test_draft_notes tests.test_proposal_evidence tests.test_proposal_content -v
  ```

- [ ] **Step 7: Commit the reviewed task**

  Commit only the six task files with contiguous trailers for `REQ-INTK-005`, `REQ-SEC-002`, `ADR-003`, `ADR-007`, the new named tests, and the Codex co-author.

---

### Task 2: Reuse Only Exact Matching Exclusive-Create Race Winners

**Files:**
- Modify: `metis/proposal.py:320-360`
- Modify: `metis/proposal.py:960-1015`
- Modify: `metis/proposal.py:1276-1296`
- Test: `tests/test_proposal.py`
- Test: `tests/test_proposal_recovery.py`

**Interfaces:**
- Consumes: `ProposalEvidenceCollision`, `ProposalContentCollision`, the stable reservation identity, exact raw response bytes, raw-response SHA-256, and canonical body bytes.
- Produces: one helper path per artifact type that creates normally or, after a collision, validates the exact configured artifact and reuses it only when every identity, hash, and expected byte agrees.

- [ ] **Step 1: Add failing matching-response race test**

  Add a store double whose `create()` first delegates to a real `ProposalEvidenceStore` and then raises `ProposalEvidenceCollision`. Assert a fresh `ProposalService.propose()` returns `proposed`, makes one provider call, and preserves one response artifact. The production mutation caught is treating all evidence collisions as `proposal_evidence_failed`.

- [ ] **Step 2: Run the matching-response test and verify red**

  Run the single new test and confirm it returns `failed` before implementation.

- [ ] **Step 3: Implement exact response winner reuse**

  Catch only `ProposalEvidenceCollision` in `_preserve_response()`, validate `<runtime_root>/proposal-evidence/<proposal_id>`, and compare proposal ID, classification ID, capture ID, model ID, `propose-v1`, reserved timestamp, byte size, and exact UTF-8 response bytes. Return the record only on complete agreement; return failure for partial, corrupt, symlinked, or disagreeing artifacts. Keep other proposal-evidence errors fail-closed.

- [ ] **Step 4: Run the response race test and verify green**

  Run the new matching test plus existing proposal-evidence collision and recovery tests.

- [ ] **Step 5: Add failing matching-content race tests**

  Add a content-store double whose `create()` first delegates to the real `ProposalContentStore` and then raises `ProposalContentCollision`. Cover both the fresh proposal path and expired-reservation recovery path. Assert proposal completion succeeds without a second model call and uses the one canonical content artifact. The production mutation caught is failing a complete matching content winner between the existence check and exclusive create.

- [ ] **Step 6: Add mismatch tests for both artifact types**

  Use race doubles that establish a complete but disagreeing response or canonical body before raising the collision. Assert the service fails closed, does not overwrite bytes, does not report `proposed`, and records only a safe stable reason.

- [ ] **Step 7: Run all new race tests and verify red where behavior is absent**

  Confirm matching content races fail before implementation while mismatch cases already fail closed or expose any unsafe reuse.

- [ ] **Step 8: Implement exact content winner reuse**

  Centralize fresh and recovery content creation through a helper that catches only `ProposalContentCollision`, validates `<runtime_root>/proposal-content/<proposal_id>`, reads `body.md`, and compares proposal/classification/capture identity, raw-response hash, content hash, byte size, and exact expected body bytes. Reuse only complete agreement; preserve existing stable failure outcomes for every disagreement.

- [ ] **Step 9: Run focused proposal and recovery suites and verify green**

  ```bash
  env PYTHONPYCACHEPREFIX=/private/tmp/metis-step-04-remediation-pycache /opt/miniconda3/bin/python3.13 -m unittest tests.test_proposal tests.test_proposal_recovery tests.test_proposal_evidence tests.test_proposal_content -v
  ```

- [ ] **Step 10: Commit the reviewed task**

  Commit only `metis/proposal.py` and the two task test files with contiguous trailers for `REQ-INTK-002`, `REQ-INTK-005`, `REQ-ORCH-001`, `ADR-014`, the new named tests, and the Codex co-author.

---

### Task 3: Complete the Approved Crash-and-Restart Matrix

**Files:**
- Modify: `tests/test_proposal_recovery.py`
- Modify only if a minimal observable seam is required: `metis/proposal.py`

**Interfaces:**
- Consumes: real `ProposalService`, real SQLite state store and filesystem stores, fake model adapter, and narrowly wrapped state/store doubles that raise `BaseException` after one named boundary.
- Produces: restart evidence for reservation, provider return, response bytes, response metadata, parse, content bytes, content metadata, proposal transaction, draft create, draft close, draft validation, and draft registration.

- [ ] **Step 1: Add controlled crash doubles**

  Add narrowly scoped test-only wrappers for: state mutation after reservation; proposal evidence before write, after raw bytes, and after complete metadata; content storage before write, after body bytes, and after complete metadata; state mutation after proposal completion; draft storage after exclusive file creation, after exact close, and after validation; and state mutation after draft registration. Each wrapper delegates real behavior except at its single named crash boundary.

- [ ] **Step 2: Add reservation and provider-return restart tests**

  Prove a crash immediately after reservation retains one stable proposal ID and can be reclaimed only after lease expiry. Prove a crash after provider return but before response evidence may require one repeat provider call after reclaim, never reports success from the interrupted attempt, and creates no duplicate row or draft.

- [ ] **Step 3: Add response-boundary restart tests**

  Prove raw-bytes-only evidence is preserved and fails closed without overwrite; complete response metadata is reused without another provider call; and a crash after parse but before content creation reparses the canonical response without another provider call.

- [ ] **Step 4: Add content-boundary restart tests**

  Prove body-bytes-only content is preserved and fails closed without overwrite, while complete content metadata is revalidated and reused without another provider call.

- [ ] **Step 5: Add proposal and draft-boundary restart tests**

  Prove a committed proposal transaction resumes only the draft stage; an empty or partial exclusively created draft fails closed without rewrite; an exact closed draft and an exact validated draft are registered without rewrite; and a crash after committed draft registration returns `duplicate` on restart.

- [ ] **Step 6: Run the new crash tests and verify each red case before any required production change**

  For behavior already supported by existing recovery code, demonstrate that the newly injected test passes without production changes and record it as characterization evidence. For any missing behavior, confirm the test fails for the expected state/artifact reason before changing production code.

- [ ] **Step 7: Add only the minimum production seam required by a legitimate red test**

  Prefer test-only wrappers around existing public boundaries. If a crash point cannot be observed without a seam, add one private/default-no-op injection point to `ProposalService`; it must not change CLI construction, public result fields, provider requests, storage formats, or production behavior when omitted.

- [ ] **Step 8: Run focused recovery and integration verification**

  ```bash
  env PYTHONPYCACHEPREFIX=/private/tmp/metis-step-04-remediation-pycache /opt/miniconda3/bin/python3.13 -m unittest tests.test_proposal_recovery tests.test_proposal_integration -v
  ```

  Confirm the integrated path still ends with one pending proposal, one `status: proposed` draft, zero approval rows, zero audit rows, and no filed directory.

- [ ] **Step 9: Commit the reviewed task**

  Commit only the recovery test and any strictly required proposal seam with contiguous trailers for `REQ-INTK-002`, `REQ-INTK-005`, `REQ-ORCH-001`, `ADR-007`, `ADR-014`, the crash-boundary tests, and the Codex co-author.

---

## Final Verification Gate

- Run the focused Step-4 suites.
- Run `/opt/miniconda3/bin/python3.13 -m unittest discover -s tests -v` with the configured temporary bytecode cache.
- Run `git diff --check` over the Step-4 branch range while excluding `METIS-EXECUTION-SPINE.md`.
- Confirm only `capture`, `classify`, and `propose` CLI commands exist; proposal links remain `[]`; approval and audit row counts remain zero in the integrated Step-4 test.
- Obtain a broad read-only code review against base `76a1358eb2362532fa1dfdcabbdab10a9ed79b8c`.
- Stop with local commits only. Do not push or change PR #5 without explicit authorization.
