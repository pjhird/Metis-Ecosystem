# Phase 6 Slice A — Planning Notes (Hybrid) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let humans create Goal and Project notes through `metis capture --as`, using the existing propose → approve → file → audit loop, with type-aware filing destinations.

**Architecture:** Pin `goal`/`project` at capture into durable metadata before any model call; classification may not override the pin; propose/render emit planning frontmatter; `metis file` routes by effective type to `vault/goals/` or `vault/projects/` with provenance.

**Tech Stack:** Python 3 stdlib, existing Metis services, SQLite via data-access layer, unittest.

**Spec:** `docs/superpowers/specs/2026-08-03-phase-06-planning-notes-hybrid-design.md`  
**Decision:** ADR-021 (must be merged on `main` before this branch’s code lands)

## Global Constraints

- Never write permanent knowledge without recorded approval (ADR-004/005).
- Evidence before interpretation (ADR-003); pin recorded before classify.
- SQL only in `metis/data_access/`; provider SDK only in model adapter.
- ADR-020 unchanged: drafts editable only `status` + `links`.
- No outcomes/tasks/dependencies; no second approval surface; no watchers/UI/agents.
- Update `METIS-REQUIREMENT-LEDGER.md` in the same PR as verifying tests.
- Do not commit/push/PR until the owner authorizes (unless they already did).

## File map

| File | Role |
|---|---|
| `metis/cli.py` | `--as`, `--goal` on `capture` |
| `metis/capture.py` | Accept pin + parent goal; persist in evidence meta |
| `metis/evidence.py` (or capture meta helpers) | Store/read `type_pin`, `parent_goal_id` |
| `metis/classification.py` | `ROUTING` + pin override when persisting/using type |
| `metis/proposal.py` / `draft_notes.py` | Effective type; goal/project frontmatter; system `goal:` |
| `metis/filing.py` | Route destination; id allocation; link rules by type |
| `METIS-SCHEMAS.md`, `AGENTS.md`, ledger | Docs |
| `tests/test_planning_notes.py` (new) + extend existing | Acceptance |

---

### Task 1: CLI flags and capture pin persistence

**Files:**
- Modify: `metis/cli.py`, `metis/capture.py`, evidence meta writing
- Test: `tests/test_planning_capture_cli.py` (or extend `tests/test_cli.py` / capture tests)

**Produces:** CaptureResult / meta with `type_pin` ∈ {`null`,`goal`,`project`} and optional `parent_goal_id`

- [ ] **Step 1:** Write failing tests: `--as project` without `--goal` exits error with no evidence; `--as goal` writes meta `type_pin=goal` before classify; `--goal` without `--as project` errors
- [ ] **Step 2:** Run tests — confirm fail
- [ ] **Step 3:** Implement argparse + CaptureService API; write pin into evidence `meta.json` (or equivalent) atomically with evidence
- [ ] **Step 4:** Run tests — pass
- [ ] **Step 5:** Commit with trailers `Decision: ADR-021` / relevant REQ / test name

### Task 2: Classification pin override

**Files:**
- Modify: `metis/classification.py` (`ROUTING`, effective type)
- Test: planning / classification tests

**Produces:** Effective `candidate_type` / note type forced to pin when set

- [ ] **Step 1:** Failing test: model returns `idea` but pin `goal` → stored/used type is `goal`; raw response still preserved
- [ ] **Step 2:** Run — fail
- [ ] **Step 3:** Add `goal`/`project` to `ROUTING`; after parse, if pin present, force type; routing from `ROUTING[pin]`
- [ ] **Step 4:** Run — pass
- [ ] **Step 5:** Commit

### Task 3: Propose/render planning drafts

**Files:**
- Modify: `metis/draft_notes.py`, `metis/proposal.py` as needed
- Test: draft/proposal tests

**Produces:** Proposed draft bytes for goal/project shapes; project includes system `goal: "[[id]]"`; ADR-020 validation still holds

- [ ] **Step 1:** Failing tests for rendered goal draft and project draft with parent goal field
- [ ] **Step 2:** Run — fail
- [ ] **Step 3:** Type-aware `render_note` / propose path; keep editable regions only status+links
- [ ] **Step 4:** Run — pass
- [ ] **Step 5:** Commit

### Task 4: Filing router and planning acceptance

**Files:**
- Modify: `metis/filing.py`, possibly path helpers in `draft_notes.py`
- Test: `tests/test_planning_notes.py`, extend filing tests

**Produces:** Files under `vault/goals/` / `vault/projects/`; provenance required; goal empty links OK; project parent must resolve; replay one note

- [ ] **Step 1:** Write failing acceptance tests from design §9
- [ ] **Step 2:** Run — fail
- [ ] **Step 3:** Implement destination routing, id allocation (`goal.` / `proj.` + stable unique suffix from capture_id), validation, audit detail fields if needed
- [ ] **Step 4:** Full suite `python3 -m unittest discover -s tests -v`
- [ ] **Step 5:** Commit

### Task 5: Docs and ledger

**Files:**
- Modify: `METIS-SCHEMAS.md`, `AGENTS.md`, `METIS-REQUIREMENT-LEDGER.md`
- Add: design spec if not yet on branch (`docs/superpowers/specs/2026-08-03-phase-06-planning-notes-hybrid-design.md`)

- [ ] **Step 1:** Amend schemas §4.1–4.3 for provenance + creation path; AGENTS commands + current phase; ledger rows with test names
- [ ] **Step 2:** Commit
- [ ] **Step 3:** On owner go-ahead: push `step/08-planning-notes`, open draft PR against `main`

---

## Done when

- All new tests green; full suite green
- ADR-021 cited in commits
- Draft PR open; no tag until owner merges and accepts
