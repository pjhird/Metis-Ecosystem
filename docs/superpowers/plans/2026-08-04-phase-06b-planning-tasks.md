# Phase 6 Slice B — First-Class Planning Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human create Task notes under an existing Project through `metis capture --as task --project <proj-id>`, using the existing capture → classify → propose → approve → file → audit loop, filing to `vault/tasks/`.

**Architecture:** The planning pin (`type_pin` + `parent_id`) is written to immutable evidence before any model call, projected into `intake` in the same transaction, and used to route rendering and filing. The intake uniqueness key becomes `content_hash + type_pin + parent_id` with `NOT NULL DEFAULT ''` sentinel columns, so identical text under two different parents is two captures while identical text under a differing pin is refused.

**Tech Stack:** Python 3.11+ stdlib only, existing Metis services, SQLite through `metis/data_access/`, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-04-phase-06b-planning-tasks-design.md`
**Decision:** ADR-022 (Task 1 — must be merged on `main` before any Task 2+ code lands)

**Test command (all tasks):**

```bash
/opt/miniconda3/bin/python3 -m unittest discover -s tests
```

Single test:

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_planning_tasks.PlanningTaskFiling.test_a_pinned_task_files_under_vault_tasks_with_empty_links -v
```

## Global Constraints

- Never write permanent knowledge without a recorded human approval (ADR-004, ADR-005).
- Evidence before interpretation: the pin is written to evidence before any model call (ADR-003).
- Fail closed. `refused` is a successful outcome and must be recorded, not raised as an error.
- The orchestrator owns every state transition; skills return bounded results (ADR-007).
- SQL appears only in `metis/data_access/` (ADR-002). Provider SDK only in the model adapter (ADR-008).
- Drafts have exactly two human-editable fields, `status` and `links` (ADR-020).
- Obsidian is the sole approval surface (ADR-005). Do not add a second one.
- `type_pin` and `parent_id` are `NOT NULL DEFAULT ''`. NULL is never used in the uniqueness key.
- Intake pin columns are a derived projection; evidence meta is the immutable record; divergence is a fail-closed refusal.
- Planning `status` on a filed task is `open` only. No other value is produced in this slice.
- Planning notes carry no `verification` field.
- Commits carry `Requirement:` / `Decision: ADR-022` / `Test:` trailers. Prefer merge commits, never squash.
- Never commit `state/`, evidence, `.env`, vault content, or `metis_ecosystem.egg-info/`.
- Do not push or open a PR until the owner authorizes it.

---

## File map

| File | Responsibility after this plan |
|---|---|
| `metis/data_access/migrations/007_intake_pin_projection.sql` | Rebuild `intake` with sentinel pin columns and the composite unique key |
| `metis/data_access/contracts.py` | `IntakeRecord.type_pin` / `.parent_id`; `find_intake_by_pin_key` on the `StateStore` protocol |
| `metis/data_access/sqlite.py` | Only place that reads/writes those columns in SQL |
| `metis/evidence.py` | Evidence meta v3 (`type_pin`, `parent_id`), v2 read compatibility, `find_all_by_content_hash` |
| `metis/capture.py` | Composite replay / `pin_conflict` decision; writes the projection |
| `metis/cli.py` | `--as task`, `--project`, flag validation |
| `metis/classification.py` | `PIN_TYPES` gains `task`; pin still overrides candidate type |
| `metis/draft_notes.py` | `tasks` stage; pin-driven planning field (`project:`); no `verification` on planning notes |
| `metis/filing.py` | Route by pin, resolve parent project, audit detail |
| `tests/test_planning_tasks.py` (new) | Slice-B acceptance tests |
| `METIS-SCHEMAS.md`, `AGENTS.md`, `METIS-REQUIREMENT-LEDGER.md` | Docs and traceability |

### Naming decision locked here

The existing code calls the parent `parent_goal_id` in `evidence.py`, `capture.py`, `draft_notes.py`, and `filing.py`. A task's parent is a project, so the field becomes **`parent_id`** everywhere, with the pin type saying what kind of parent it is. Evidence already written under schema_version 2 is immutable and keeps its `parent_goal_id` key; the reader maps it to `parent_id`. Do not leave two live names in new code.

### Routing rule (ADR-022 clause 11, not a plan decision)

Filing chooses the vault stage from the **pin**, not from `proposal.note_type`. A classifier-typed note may legitimately be `type: task` and must still file to `vault/notes/filed/`; only a pinned capture is a planning task. Every routing site reads `evidence.type_pin`. Task 1 must land this as clause 11 before Task 8 implements it.

### Where the v2 → v3 mapping lives

Exactly one site: `EvidenceStore.validate_directory` (`metis/evidence.py:155-184`) is the only function that parses `meta.json` into an `EvidenceRecord`; `_validate_metadata` selects the key set by `schema_version`. Every other module — `capture.py`, `proposal.py`, `approval.py`, `filing.py`, `draft_notes.py` — reads the record attribute and never the file. Do not add a second reader; if a caller needs the pin, pass the record.

Immutable v2 evidence is never rewritten (ADR-003). The projection in `intake` is used only for the uniqueness constraint and consistency checks; routing, rendering, and parent resolution read the pin from the evidence record (ADR-022 clause 9).

---

### Task 1: ADR-022 (ADR-only pull request)

**Files:**
- Modify: `METIS-DECISIONS.md` (append ADR-022, update the decision index table at the end)

**Branch:** `adr/022-planning-task-capture` — **no code in this PR.**

**Produces:** The merged authority for everything in Tasks 2–9. Nothing else in this plan may start until this is on `main`.

- [ ] **Step 1: Create the branch**

```bash
git checkout main && git pull --ff-only origin main
git checkout -b adr/022-planning-task-capture
```

- [ ] **Step 2: Append ADR-022 to `METIS-DECISIONS.md`**

Follow the existing ADR shape exactly (Context / Decision / Alternatives / Consequences / Reversal path / Revisit if). Copy clauses 1–11, the header line, and the migration paragraph from spec §8. Clause 11 (routing reads the pin, never `note_type`) determines what code is legal, so it belongs in the ADR rather than in this plan. The header must read:

> Amends ADR-014 (extends the uniqueness key for all intakes). Supersedes the parent-conflict behavior established in the ADR-021 implementation, under which a differing parent produced `pin_conflict`.

Note the one-word correction against the spec draft: the header says **all intakes**, not "pinned captures", because clause 9 applies the key to every intake row.

- [ ] **Step 3: Add the index row**

In the decision table at the foot of `METIS-DECISIONS.md`, add:

```markdown
| ADR-022 | Planning tasks via pinned capture under a project | Adopted |
```

- [ ] **Step 4: Verify no code changed**

```bash
git diff --name-only main
```

Expected: exactly `METIS-DECISIONS.md`.

- [ ] **Step 5: Commit**

```bash
git add METIS-DECISIONS.md
git commit -m "adr(022): record pinned capture for planning tasks

Decision: ADR-022"
```

- [ ] **Step 6: On owner go-ahead, push and open the ADR-only PR; merge before Task 2**

---

### Task 2: Intake pin projection in the data layer

**Files:**
- Create: `metis/data_access/migrations/007_intake_pin_projection.sql`
- Modify: `metis/data_access/contracts.py`, `metis/data_access/sqlite.py`
- Test: `tests/test_state_store.py` (extend), `tests/test_migrations.py` if present — otherwise add cases to the existing data-layer test module

**Interfaces:**
- Produces: `IntakeRecord(capture_id, content_hash, captured_at, source_type, evidence_path, state, state_updated_at, failure_reason, trace_id, type_pin: str, parent_id: str)` — both new fields are `str`, never `None`, `''` when absent.
- Produces: `StateStore.find_intake_by_pin_key(content_hash: str, type_pin: str, parent_id: str) -> Optional[IntakeRecord]` — used only where evidence did not already name a capture id (see Task 4)
- Keeps: `StateStore.find_intake_by_capture_id(capture_id: str) -> Optional[IntakeRecord]` — unchanged, and the lookup capture prefers
- Produces: `StateStore.find_intakes_by_content_hash(content_hash: str) -> Tuple[IntakeRecord, ...]` — used by capture to detect a conflicting pin.
- Removes: `find_intake_by_content_hash` (single-row lookup is no longer well-defined).

**Branch:** `step/09-planning-tasks` off `main` after ADR-022 merges.

- [ ] **Step 1: Write the failing tests**

```python
def test_identical_text_under_two_parents_registers_two_rows(self) -> None:
    store = self._store()
    store.register_intake(intake_record("a", type_pin="task", parent_id="proj.one"))
    second = store.register_intake(intake_record("b", type_pin="task", parent_id="proj.two"))

    self.assertIs(second.status, IntakeRegistrationStatus.REGISTERED)
    self.assertEqual(len(store.find_intakes_by_content_hash(CONTENT_HASH)), 2)

def test_identical_text_and_pin_registers_once(self) -> None:
    store = self._store()
    store.register_intake(intake_record("a", type_pin="task", parent_id="proj.one"))
    second = store.register_intake(intake_record("b", type_pin="task", parent_id="proj.one"))

    self.assertIs(second.status, IntakeRegistrationStatus.DUPLICATE)

def test_unpinned_replay_still_registers_once(self) -> None:
    """Regression: NULLs would compare distinct and silently allow both."""
    store = self._store()
    store.register_intake(intake_record("a"))
    second = store.register_intake(intake_record("b"))

    self.assertIs(second.status, IntakeRegistrationStatus.DUPLICATE)

def test_pin_columns_reject_null(self) -> None:
    store = self._store()
    with self.assertRaises(StateStoreError):
        store.register_intake(intake_record("a", type_pin=None, parent_id=None))
```

Where `intake_record` is the module's existing helper, extended with `type_pin: str = ""` and `parent_id: str = ""` keyword arguments and a distinct `capture_id` per call.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_state_store -v
```

Expected: `TypeError` on the new keyword arguments, or `AttributeError: find_intakes_by_content_hash`.

- [ ] **Step 3: Write the migration**

`intake` currently declares `content_hash TEXT UNIQUE NOT NULL`. SQLite cannot drop a column-level UNIQUE, so the table is rebuilt using the same pattern migration 006 established (drop, recreate, refill — renaming a replacement table does not clear the deferred foreign-key counter).

```sql
-- ADR-022: the intake pin projection joins the uniqueness key.
--
-- `type_pin` and `parent_id` are NOT NULL DEFAULT '' so that NULL never enters
-- the key: SQLite treats NULLs as distinct, and a nullable column would
-- silently disable replay protection for unpinned captures.
--
-- Existing rows carry the sentinel through the refill SELECT; there is no
-- separate backfill statement, and no reconciliation is required because the
-- previous UNIQUE(content_hash) already permitted at most one row per hash.

PRAGMA defer_foreign_keys = ON;

CREATE TEMP TABLE intake_carry AS SELECT * FROM intake;

DROP TABLE intake;

CREATE TABLE intake (
    capture_id TEXT PRIMARY KEY NOT NULL,
    content_hash TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type = 'cli-typed'),
    evidence_path TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'captured',
            'classifying',
            'classified',
            'proposing',
            'proposed',
            'awaiting_approval',
            'approved',
            'filed',
            'rejected',
            'failed'
        )
    ),
    state_updated_at TEXT NOT NULL,
    failure_reason TEXT,
    trace_id TEXT NOT NULL,
    type_pin TEXT NOT NULL DEFAULT '' CHECK (
        type_pin IN ('', 'goal', 'project', 'task')
    ),
    parent_id TEXT NOT NULL DEFAULT '' CHECK (
        parent_id = '' OR parent_id NOT GLOB '*[^A-Za-z0-9._-]*'
    ),
    UNIQUE (content_hash, type_pin, parent_id)
);

INSERT INTO intake (
    capture_id,
    content_hash,
    captured_at,
    source_type,
    evidence_path,
    state,
    state_updated_at,
    failure_reason,
    trace_id
)
SELECT
    capture_id,
    content_hash,
    captured_at,
    source_type,
    evidence_path,
    state,
    state_updated_at,
    failure_reason,
    trace_id
FROM intake_carry;

DROP TABLE intake_carry;
```

- [ ] **Step 4: Extend the record and the store**

In `metis/data_access/contracts.py`, add the two fields to `IntakeRecord` with no defaults (every construction site must be explicit) and declare the two new protocol methods:

```python
@dataclass(frozen=True)
class IntakeRecord:
    capture_id: str
    content_hash: str
    captured_at: str
    source_type: str
    evidence_path: str
    state: str
    state_updated_at: str
    failure_reason: Optional[str]
    trace_id: str
    type_pin: str
    parent_id: str
```

```python
    def find_intake_by_pin_key(
        self,
        content_hash: str,
        type_pin: str,
        parent_id: str,
    ) -> Optional[IntakeRecord]:
        ...

    def find_intakes_by_content_hash(
        self,
        content_hash: str,
    ) -> Tuple[IntakeRecord, ...]:
        ...
```

In `metis/data_access/sqlite.py`, extend the intake column tuple (module constants near lines 32 and 70) with `"type_pin"` and `"parent_id"`, add the two columns to every intake `SELECT` and to the `INSERT` in `register_intake`, replace `find_intake_by_content_hash` with:

```python
    def find_intake_by_pin_key(
        self,
        content_hash: str,
        type_pin: str,
        parent_id: str,
    ) -> Optional[IntakeRecord]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT capture_id, content_hash, captured_at, source_type, "
                "evidence_path, state, state_updated_at, failure_reason, trace_id, "
                "type_pin, parent_id "
                "FROM intake WHERE content_hash = ? AND type_pin = ? AND parent_id = ?",
                (content_hash, type_pin, parent_id),
            ).fetchone()
        return None if row is None else IntakeRecord(*row)

    def find_intakes_by_content_hash(
        self,
        content_hash: str,
    ) -> Tuple[IntakeRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT capture_id, content_hash, captured_at, source_type, "
                "evidence_path, state, state_updated_at, failure_reason, trace_id, "
                "type_pin, parent_id "
                "FROM intake WHERE content_hash = ? ORDER BY capture_id",
                (content_hash,),
            ).fetchall()
        return tuple(IntakeRecord(*row) for row in rows)
```

`register_intake` currently resolves a duplicate by looking up the content hash; change that resolution to `find_intake_by_pin_key(record.content_hash, record.type_pin, record.parent_id)`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_state_store -v
```

Expected: PASS. Other suites will fail on the new required fields — Tasks 3–4 fix them.

- [ ] **Step 6: Commit**

```bash
git add metis/data_access tests/test_state_store.py
git commit -m "feat(data): project the capture pin into the intake uniqueness key

Requirement: REQ-INTK-002
Decision: ADR-022
Test: test_identical_text_under_two_parents_registers_two_rows"
```

---

### Task 3: Evidence meta v3 with a polymorphic parent

**Files:**
- Modify: `metis/evidence.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: nothing from Task 2.
- Produces: `EvidenceRecord(..., type_pin: Optional[str], parent_id: Optional[str])`; `EvidenceStore.create(capture_id, raw_bytes, content_hash, captured_at, type_pin=None, parent_id=None)`; `EvidenceStore.find_all_by_content_hash(content_hash) -> Tuple[EvidenceRecord, ...]`.
- Produces: `PIN_TYPES = frozenset({"goal", "project", "task"})` and `PARENT_REQUIRED = frozenset({"project", "task"})`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_task_pin_records_its_parent_project(self) -> None:
    record = self.store.create(
        CAPTURE_ID, RAW, CONTENT_HASH, CAPTURED_AT, "task", "proj.weekly-7d4e8eb8"
    )

    self.assertEqual(record.type_pin, "task")
    self.assertEqual(record.parent_id, "proj.weekly-7d4e8eb8")
    metadata = json.loads(record.meta_path.read_text(encoding="utf-8"))
    self.assertEqual(metadata["schema_version"], 3)
    self.assertEqual(metadata["parent_id"], "proj.weekly-7d4e8eb8")

def test_a_task_pin_without_a_parent_is_refused(self) -> None:
    with self.assertRaises(EvidenceWriteError):
        self.store.create(CAPTURE_ID, RAW, CONTENT_HASH, CAPTURED_AT, "task", None)

def test_v2_evidence_reads_its_parent_goal_as_parent_id(self) -> None:
    """Evidence is immutable (ADR-003), so v2 directories are read, not rewritten.

    This is the only read-time mapping site; no other module parses meta.json.
    """
    directory = self._write_v2_directory(type_pin="project", parent_goal_id="goal.abc")

    record = self.store.validate_directory(directory)

    self.assertEqual(record.type_pin, "project")
    self.assertEqual(record.parent_id, "goal.abc")

def test_find_all_by_content_hash_returns_every_parent(self) -> None:
    self.store.create(CAPTURE_ID, RAW, CONTENT_HASH, CAPTURED_AT, "task", "proj.one")
    self.store.create(OTHER_ID, RAW, CONTENT_HASH, CAPTURED_AT, "task", "proj.two")

    records = self.store.find_all_by_content_hash(CONTENT_HASH)

    self.assertEqual({record.parent_id for record in records}, {"proj.one", "proj.two"})
```

`_write_v2_directory` writes a `meta.json` with `schema_version: 2` and the old `parent_goal_id` key, plus a matching `raw.txt`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_evidence -v
```

Expected: FAIL — `create()` rejects `"task"`, and `EvidenceRecord` has no `parent_id`.

- [ ] **Step 3: Implement v3 metadata and version-aware validation**

```python
PIN_TYPES = frozenset({"goal", "project", "task"})
# A goal has nothing above it; a project names a goal and a task names a project.
PARENT_REQUIRED = frozenset({"project", "task"})
METADATA_KEYS_V2 = {..., "type_pin", "parent_goal_id", "schema_version"}
METADATA_KEYS_V3 = {..., "type_pin", "parent_id", "schema_version"}
```

`create()` writes `"parent_id": parent_id` and `"schema_version": 3`. `_validate_metadata` selects the key set and the parent key by `metadata["schema_version"]`, accepting 2 and 3 and rejecting anything else. `validate_directory` builds the record with `parent_id=metadata.get("parent_id", metadata.get("parent_goal_id"))`. `_validate_pin` becomes:

```python
    @staticmethod
    def _validate_pin(type_pin: object, parent_id: object) -> None:
        if type_pin is not None and type_pin not in PIN_TYPES:
            raise ValueError("metadata type_pin is invalid")
        if parent_id is not None:
            if type(parent_id) is not str or PARENT_ID.fullmatch(parent_id) is None:
                raise ValueError("metadata parent_id is invalid")
        if (type_pin in PARENT_REQUIRED) != (parent_id is not None):
            raise ValueError("metadata type_pin and parent_id disagree")
```

Rename `PARENT_GOAL_ID` to `PARENT_ID`. Replace `find_by_content_hash` with `find_all_by_content_hash`, returning every valid match sorted by `capture_id` (two parents are now legal, so "multiple matches" is no longer an inconsistency).

- [ ] **Step 4: Run the tests to verify they pass**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_evidence -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metis/evidence.py tests/test_evidence.py
git commit -m "feat(evidence): record a polymorphic planning parent in meta v3

Requirement: REQ-INTK-001
Decision: ADR-022
Test: test_a_task_pin_records_its_parent_project"
```

---

### Task 4: Capture decides replay, conflict, and the projection

**Files:**
- Modify: `metis/capture.py`
- Test: `tests/test_planning_capture.py`, `tests/test_capture.py`

**Interfaces:**
- Consumes: `find_intake_by_pin_key`, `find_intakes_by_content_hash` (Task 2); `find_all_by_content_hash`, `PIN_TYPES`, `PARENT_REQUIRED` (Task 3).
- Produces: `CaptureService.capture(text, *, type_pin: Optional[str] = None, parent_id: Optional[str] = None) -> CaptureResult`.

- [ ] **Step 1: Write the failing tests**

```python
def test_same_text_under_two_projects_creates_two_captures(self) -> None:
    """Capture half of spec §9's `same_text_under_two_projects_creates_two_tasks`;
    Task 8 proves the filed half end to end."""
    first = self._capture(TEXT, type_pin="task", parent_id="proj.one")
    second = self._capture(TEXT, type_pin="task", parent_id="proj.two")

    self.assertIs(second.status, CaptureStatus.CAPTURED)
    self.assertNotEqual(second.capture_id, first.capture_id)

def test_replay_with_the_same_task_pin_is_a_duplicate(self) -> None:
    first = self._capture(TEXT, type_pin="task", parent_id="proj.one")
    second = self._capture(TEXT, type_pin="task", parent_id="proj.one")

    self.assertIs(second.status, CaptureStatus.DUPLICATE)
    self.assertEqual(second.capture_id, first.capture_id)

def test_conflicting_pin_on_identical_text_is_refused(self) -> None:
    self._capture(TEXT, type_pin="task", parent_id="proj.one")
    second = self._capture(TEXT, type_pin="project", parent_id="proj.one")

    self.assertIs(second.status, CaptureStatus.REFUSED)
    self.assertEqual(second.reason, "pin_conflict")

def test_duplicate_plain_capture_still_creates_one_note(self) -> None:
    first = self._capture(TEXT)
    second = self._capture(TEXT)

    self.assertIs(second.status, CaptureStatus.DUPLICATE)
    self.assertEqual(second.capture_id, first.capture_id)

def test_capture_as_task_requires_project_flag(self) -> None:
    result = self._capture(TEXT, type_pin="task")

    self.assertIs(result.status, CaptureStatus.REFUSED)
    self.assertEqual(result.reason, "pin_incomplete")

def test_intake_pin_columns_match_evidence_meta(self) -> None:
    """The projection is derived; divergence is refused, not preferred away."""
    first = self._capture(TEXT, type_pin="task", parent_id="proj.one")
    self._corrupt_intake_pin(first.capture_id, type_pin="goal", parent_id="")

    replay = self._capture(TEXT, type_pin="task", parent_id="proj.one")

    self.assertIs(replay.status, CaptureStatus.FAILED)
    self.assertEqual(replay.reason, "state_evidence_mismatch")

def test_a_legacy_unprojected_planning_row_fails_closed(self) -> None:
    """A pre-ADR-022 row carries the sentinel while its evidence records a pin.

    The migration reads *.sql only and cannot open evidence, so this state is
    reachable on any store that predates ADR-022. It must refuse, not resolve.
    """
    first = self._capture(TEXT, type_pin="goal")
    self._corrupt_intake_pin(first.capture_id, type_pin="", parent_id="")

    replay = self._capture(TEXT, type_pin="goal")

    self.assertIs(replay.status, CaptureStatus.FAILED)
    self.assertEqual(replay.reason, "state_evidence_mismatch")
    self.assertEqual(replay.capture_id, first.capture_id)
```

`_corrupt_intake_pin` writes the divergent values directly with `sqlite3` in the test, simulating a hand-edited or half-migrated row.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_planning_capture -v
```

Expected: FAIL — `capture()` has no `parent_id` keyword.

- [ ] **Step 3: Implement the composite decision**

Rename the parameter to `parent_id` throughout, and replace the two single-row lookups with the composite ones. The conflict check runs before the replay check so a differing pin can never be read as a duplicate:

```python
        if type_pin is not None and type_pin not in PIN_TYPES:
            return CaptureResult(
                CaptureStatus.REFUSED, None, None, "pin_invalid",
                f"type pin must be one of {sorted(PIN_TYPES)}",
            )
        if (type_pin in PARENT_REQUIRED) != (parent_id is not None):
            return CaptureResult(
                CaptureStatus.REFUSED, None, None, "pin_incomplete",
                "a project or task pin requires a parent, and only they may carry one",
            )
```

```python
        # Identical text under a different parent is a different intent, not a
        # replay; identical text and parent under a different pin is a conflict.
        candidates = self._evidence_store.find_all_by_content_hash(content_hash)
        conflicting = [
            record
            for record in candidates
            if record.parent_id == parent_id and record.type_pin != type_pin
        ]
        if conflicting:
            return CaptureResult(
                CaptureStatus.REFUSED,
                conflicting[0].capture_id,
                conflicting[0].evidence_path,
                "pin_conflict",
                "this text was already captured under a different planning pin",
            )
        evidence = next(
            (
                record
                for record in candidates
                if record.type_pin == type_pin and record.parent_id == parent_id
            ),
            None,
        )
```

The state lookup must be **by capture id when evidence resolved it**, not by pin key. A row written before ADR-022 carries the sentinel while its evidence records a pin, so a pin-key lookup would miss it, fall through to `_register_evidence`, and collide on the `capture_id` primary key with no row to resolve against. Looking up the handle evidence already named turns that into the defined refusal `_row_matches_evidence` produces:

```python
        existing = (
            self._state_store.find_intake_by_capture_id(evidence.capture_id)
            if evidence is not None
            else self._state_store.find_intake_by_pin_key(
                content_hash, type_pin or "", parent_id or ""
            )
        )
```

`find_intake_by_pin_key` still covers the branch where a state row exists with no evidence behind it — that path must keep reporting `state_evidence_mismatch`.

`_register_evidence` writes the projection when constructing `IntakeRecord`:

```python
            type_pin=evidence.type_pin or "",
            parent_id=evidence.parent_id or "",
```

and `_row_matches_evidence` gains the projection comparison, which is what makes divergence fail closed:

```python
            and row.type_pin == (evidence.type_pin or "")
            and row.parent_id == (evidence.parent_id or "")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_planning_capture tests.test_capture -v
```

Expected: PASS. Update the two ADR-021-era tests that asserted a differing parent is `pin_conflict` — under ADR-022 a differing parent is a new capture. Keep `test_replay_dropping_a_pin_is_refused` and `test_replay_adding_a_pin_is_refused`: those hold the parent constant and change the pin.

- [ ] **Step 5: Commit**

```bash
git add metis/capture.py tests/test_capture.py tests/test_planning_capture.py
git commit -m "feat(capture): decide replay and conflict on the composite pin key

Requirement: REQ-INTK-002
Decision: ADR-022
Test: test_same_text_under_two_projects_creates_two_captures"
```

---

### Task 5: CLI flags for a pinned task

**Files:**
- Modify: `metis/cli.py:38-73`
- Test: `tests/test_cli.py`, `tests/test_planning_capture.py`

**Interfaces:**
- Consumes: `CaptureService.capture(text, *, type_pin, parent_id)` (Task 4).
- Produces: `metis capture --as task --project <id> "<text>"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_capture_as_task_requires_project_flag_at_the_cli(self) -> None:
    result = self._run(["capture", "--as", "task", TEXT])

    self.assertNotEqual(result.exit_code, 0)
    self.assertIn("--as task requires --project", result.stderr)

def test_project_flag_without_task_pin_writes_no_evidence(self) -> None:
    result = self._run(["capture", "--project", "proj.one", TEXT])

    self.assertNotEqual(result.exit_code, 0)
    self.assertFalse((self.runtime_root / "evidence").exists())

def test_capture_as_task_records_the_pin_and_parent_project(self) -> None:
    result = self._run(["capture", "--as", "task", "--project", "proj.one", TEXT])

    self.assertEqual(result.exit_code, 0)
    metadata = self._only_evidence_metadata()
    self.assertEqual(metadata["type_pin"], "task")
    self.assertEqual(metadata["parent_id"], "proj.one")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_cli -v
```

Expected: FAIL — `--as` rejects `task`; `--project` is unknown.

- [ ] **Step 3: Implement the flags**

```python
    capture_parser.add_argument(
        "--as", dest="type_pin", choices=("goal", "project", "task")
    )
    capture_parser.add_argument("--goal", dest="parent_goal_id")
    capture_parser.add_argument("--project", dest="parent_project_id")
```

```python
        if arguments.type_pin == "project" and arguments.parent_goal_id is None:
            parser.error("--as project requires --goal <goal-id>")
        if arguments.type_pin == "task" and arguments.parent_project_id is None:
            parser.error("--as task requires --project <project-id>")
        if arguments.parent_goal_id is not None and arguments.type_pin != "project":
            parser.error("--goal is only valid with --as project")
        if arguments.parent_project_id is not None and arguments.type_pin != "task":
            parser.error("--project is only valid with --as task")
        parent_id = arguments.parent_goal_id or arguments.parent_project_id
        if parent_id is not None and LINK_TARGET.fullmatch(parent_id) is None:
            parser.error("a parent must be a note id matching [A-Za-z0-9._-]+")
```

Pass `parent_id=parent_id` to `CaptureService.capture`. `parser.error` exits before any evidence write, which is what `test_project_flag_without_task_pin_writes_no_evidence` proves.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_cli tests.test_planning_capture -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metis/cli.py tests/test_cli.py
git commit -m "feat(cli): accept --as task with a parent project

Requirement: REQ-INTK-001
Decision: ADR-022
Test: test_project_flag_without_task_pin_writes_no_evidence"
```

---

### Task 6: Classification honors a task pin without capturing typed tasks

**Files:**
- Modify: `metis/classification.py:39-58`
- Test: `tests/test_classification.py` or `tests/test_planning_classify.py`

**Interfaces:**
- Consumes: `EvidenceRecord.type_pin` (Task 3).
- Produces: `effective_type(candidate_type, evidence)` returning `"task"` for a pinned task while `RESPONSE_TYPES` still contains `"task"` for classifier-typed notes.

- [ ] **Step 1: Write the failing tests**

```python
def test_task_pin_overrides_the_model_candidate_type(self) -> None:
    evidence = evidence_record(type_pin="task", parent_id="proj.one")

    self.assertEqual(effective_type("idea", evidence), "task")

def test_the_model_may_still_propose_a_typed_task(self) -> None:
    """A classifier `task` is an ordinary typed note, not a planning task."""
    self.assertIn("task", RESPONSE_TYPES)

def test_pin_override_preserves_the_model_response_verbatim(self) -> None:
    capture_id = self._capture_pinned_task()
    self._classify(capture_id, model_candidate_type="idea")

    raw = self._classification_evidence(capture_id).read_text(encoding="utf-8")
    self.assertIn('"candidate_type": "idea"', raw)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_classification -v
```

Expected: FAIL on the pinned-task fixture keyword.

- [ ] **Step 3: Implement**

`effective_type` already returns `evidence.type_pin or candidate_type`, so the only change is that `PIN_TYPES` now includes `task` (Task 3) and `ROUTING` already carries a `task` entry. Confirm `RESPONSE_TYPES` still subtracts only `{"goal", "project"}` and update the comment to say why `task` stays model-selectable:

```python
# The model proposes only these. Planning identity is the owner's intent, pinned
# at capture, so `goal` and `project` are routable but never model-selectable
# (ADR-021). `task` stays selectable because a classifier task is an ordinary
# typed note; a planning task exists only under a pin (ADR-022).
RESPONSE_TYPES = frozenset(ROUTING) - {"goal", "project"}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_classification -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metis/classification.py tests/test_classification.py
git commit -m "feat(classify): let a task pin override the model candidate type

Requirement: REQ-INTK-003
Decision: ADR-022
Test: test_task_pin_overrides_the_model_candidate_type"
```

---

### Task 7: Render a task draft and add the tasks stage

**Files:**
- Modify: `metis/draft_notes.py:24-163`
- Test: `tests/test_draft_notes.py`, `tests/test_planning_drafts.py`

**Interfaces:**
- Consumes: `EvidenceRecord.type_pin`, `.parent_id` (Task 3).
- Produces: `render_note(proposal, canonical_body, *, status, links, approved, type_pin=None, parent_id=None, note_id=None)`; `STAGES["tasks"] = (("vault", "tasks"), "task.", "open")`; `PLANNING_STAGES = {"goal": "goals", "project": "projects", "task": "tasks"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_pinned_task_proposes_a_draft_naming_its_parent_project(self) -> None:
    rendered = render_proposed_draft(
        proposal(note_type="task"), BODY, type_pin="task", parent_id="proj.one"
    )

    self.assertIn(b'project: "[[proj.one]]"\n', rendered)

def test_a_task_note_carries_no_verification_field(self) -> None:
    rendered = render_note(
        proposal(note_type="task"),
        BODY,
        status="open",
        type_pin="task",
        parent_id="proj.one",
        note_id="task.weekly-7d4e8eb8",
    )

    self.assertNotIn(b"verification:", rendered)

def test_a_typed_task_note_still_carries_verification(self) -> None:
    rendered = render_note(proposal(note_type="task"), BODY)

    self.assertIn(b"verification: unverified\n", rendered)

def test_a_task_without_a_parent_is_refused(self) -> None:
    with self.assertRaises(DraftNoteConsistencyError):
        render_proposed_draft(proposal(note_type="task"), BODY, type_pin="task")

def test_a_tasks_stage_refuses_a_path_outside_vault_tasks(self) -> None:
    store = DraftNoteStore(self.runtime_root, stage="tasks")

    with self.assertRaises(DraftNoteWriteError):
        store.create("vault/notes/filed/task.escape.md", b"x")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_planning_drafts -v
```

Expected: FAIL — `render_note` has no `type_pin` keyword and `STAGES` has no `tasks`.

- [ ] **Step 3: Implement**

```python
STAGES = {
    "proposed": (("vault", "notes", "proposed"), "note.", DraftStatus.PROPOSED.value),
    "filed": (("vault", "notes", "filed"), "note.", DraftStatus.APPROVED.value),
    "goals": (("vault", "goals"), "goal.", "active"),
    "projects": (("vault", "projects"), "proj.", "active"),
    # A planning task enters its lifecycle at `open`; this slice produces no
    # other value, and the field authorizes nothing (ADR-022 clause 10).
    "tasks": (("vault", "tasks"), "task.", "open"),
}
PLANNING_STAGES = {"goal": "goals", "project": "projects", "task": "tasks"}
```

The planning field is chosen by the pin, not by `note_type`, because a typed note may also be `type: task`:

`PARENT_REQUIRED` is mirrored locally rather than imported from `metis/evidence.py`: the existing comment on `LINK_TARGET` records that persistence must not import the vault layer, and the reverse import would invert that seam. Keep the two definitions in step if the rule ever changes.

```python
# ponytail: mirrors evidence.PARENT_REQUIRED; kept local so the vault layer and
# the evidence layer stay independent. Keep the two in step.
PARENT_REQUIRED = frozenset({"project", "task"})


def _planning_field(type_pin: Optional[str], parent_id: Optional[str]) -> str:
    """The one frontmatter line a planning note adds, written by the system."""
    if (type_pin in PARENT_REQUIRED) != (parent_id is not None):
        raise DraftNoteConsistencyError(
            "a project or task note requires a parent, and only they may carry one"
        )
    if parent_id is not None and LINK_TARGET.fullmatch(parent_id) is None:
        raise DraftNoteConsistencyError("parent is not a valid note id")
    if type_pin == "goal":
        return "horizon: annual\n"
    if type_pin == "project":
        return f'goal: "[[{parent_id}]]"\n'
    if type_pin == "task":
        return f'project: "[[{parent_id}]]"\n'
    return ""
```

In `render_note`, replace the `parent_goal_id` parameter with `type_pin` and `parent_id`, and emit `verification: unverified` only when `type_pin is None` — planning identity is declared by a human, so REQ-DATA-005's field does not apply to it (spec §3):

```python
    verification_field = "" if type_pin else "verification: unverified\n"
```

Insert `{verification_field}` where the literal line stands today.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_draft_notes tests.test_planning_drafts -v
```

Expected: PASS. Existing goal/project draft tests need their keyword renamed from `parent_goal_id` to `parent_id` plus the matching `type_pin`.

- [ ] **Step 5: Commit**

```bash
git add metis/draft_notes.py tests/test_draft_notes.py tests/test_planning_drafts.py
git commit -m "feat(vault): render task drafts and add the tasks stage

Requirement: REQ-VLT-002
Decision: ADR-022
Test: test_a_pinned_task_proposes_a_draft_naming_its_parent_project"
```

---

### Task 8: File a task under its project and record it in the audit

**Files:**
- Modify: `metis/filing.py:225-330`
- Test: `tests/test_planning_tasks.py` (new)

**Interfaces:**
- Consumes: everything from Tasks 3–7.
- Produces: filed notes at `vault/tasks/task.<slug>-<8 hex>.md`; refusal reason `filing.parent_project_unresolvable`; audit `detail` carrying `effective_type`, `type_pin`, `parent_id`.

- [ ] **Step 1: Write the failing tests**

```python
class PlanningTaskFiling(unittest.TestCase):
    def test_a_pinned_task_files_under_vault_tasks_with_empty_links(self) -> None:
        capture_id = self._approved_task(parent="proj.weekly-7d4e8eb8")

        result = self._run(["file", capture_id])

        self.assertEqual(result["status"], "filed")
        note = self._only_file(self.vault / "tasks")
        self.assertTrue(note.name.startswith("task."))
        body = note.read_bytes()
        self.assertIn(b"type: task\n", body)
        self.assertIn(b"status: open\n", body)
        self.assertIn(b'project: "[[proj.weekly-7d4e8eb8]]"\n', body)
        self.assertIn(f"capture_id: \"{capture_id}\"".encode(), body)

    def test_a_task_whose_parent_project_is_not_filed_is_refused(self) -> None:
        capture_id = self._approved_task(parent="proj.missing", file_parent=False)

        result = self._run(["file", capture_id])

        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["reason"], "filing.parent_project_unresolvable")
        self.assertFalse((self.vault / "tasks").exists())

    def test_a_task_may_not_name_a_goal_as_its_parent(self) -> None:
        capture_id = self._approved_task(parent="goal.health-7d1200d2")

        result = self._run(["file", capture_id])

        self.assertEqual(result["reason"], "filing.parent_project_unresolvable")

    def test_an_unapproved_planning_task_is_refused(self) -> None:
        capture_id = self._proposed_task(parent="proj.weekly-7d4e8eb8")

        result = self._run(["file", capture_id])

        self.assertEqual(result["status"], "refused")
        self.assertFalse((self.vault / "tasks").exists())

    def test_duplicate_replay_creates_one_planning_task(self) -> None:
        capture_id = self._approved_task(parent="proj.weekly-7d4e8eb8")
        self._run(["file", capture_id])

        second = self._run(["file", capture_id])

        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(len(list((self.vault / "tasks").iterdir())), 1)

    def test_a_planning_task_without_provenance_fails_validation(self) -> None:
        capture_id = self._approved_task(parent="proj.weekly-7d4e8eb8")
        self._strip_capture_id_from_draft(capture_id)

        result = self._run(["file", capture_id])

        self.assertEqual(result["status"], "refused")

    def test_planning_task_and_typed_note_task_are_distinguishable(self) -> None:
        pinned = self._approved_task(parent="proj.weekly-7d4e8eb8")
        typed = self._approved_typed_note(note_type="task", link="proj.weekly-7d4e8eb8")

        self._run(["file", pinned])
        self._run(["file", typed])

        self.assertEqual(len(list((self.vault / "tasks").iterdir())), 1)
        self.assertTrue((self.vault / "notes" / "filed" / f"note.{typed}.md").is_file())

    def test_task_filing_emits_audit_with_effective_type_and_parent(self) -> None:
        capture_id = self._approved_task(parent="proj.weekly-7d4e8eb8")

        self._run(["file", capture_id])

        detail = self._audit_detail(capture_id, action="note.filed")
        self.assertEqual(detail["effective_type"], "task")
        self.assertEqual(detail["type_pin"], "task")
        self.assertEqual(detail["parent_id"], "proj.weekly-7d4e8eb8")

    def test_editing_a_tasks_parent_project_is_refused(self) -> None:
        """`project:` is system-written, so a hand edit fails closed (ADR-020)."""
        capture_id = self._proposed_task(parent="proj.weekly-7d4e8eb8")
        draft = self.vault / "notes" / "proposed" / f"note.{capture_id}.md"
        draft.write_bytes(
            draft.read_bytes()
            .replace(b"status: proposed\n", b"status: approved\n")
            .replace(b'project: "[[proj.weekly-7d4e8eb8]]"', b'project: "[[proj.other]]"')
        )

        result = self._run(["approvals"])

        self.assertEqual(result["failed"], 1)
        self.assertEqual(self._intake_state(capture_id), "proposed")

    def test_same_text_under_two_projects_creates_two_tasks(self) -> None:
        first = self._approved_task(TASK_TEXT, parent="proj.weekly-7d4e8eb8")
        second = self._approved_task(TASK_TEXT, parent="proj.reading-2b91c4de")

        self._run(["file", first])
        self._run(["file", second])

        self.assertEqual(len(list((self.vault / "tasks").iterdir())), 2)

    def test_status_edit_on_filed_task_is_inert(self) -> None:
        capture_id = self._approved_task(parent="proj.weekly-7d4e8eb8")
        self._run(["file", capture_id])
        note = self._only_file(self.vault / "tasks")
        note.write_bytes(note.read_bytes().replace(b"status: open\n", b"status: done\n"))

        result = self._run(["approvals"])

        self.assertEqual(result["decisions"], [])
        self.assertEqual(self._intake_state(capture_id), "filed")
```

Build the helpers on the existing planning-notes test harness in `tests/test_planning_notes.py`; `_approved_task` files a parent project first unless `file_parent=False`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_planning_tasks -v
```

Expected: FAIL — no `vault/tasks/` destination, no parent-project check.

- [ ] **Step 3: Implement routing and the parent check**

Route by pin rather than by `note_type`, so a classifier-typed `task` still files to `vault/notes/filed/`:

```python
    def _destination(
        self,
        proposal: ProposalRecord,
        source: EvidenceRecord,
    ) -> Tuple[str, str, str]:
        """Route by the capture pin. The id is pure, so a replay recomputes it."""
        stage = PLANNING_STAGES.get(source.type_pin or "", "filed")
        parts, prefix, status = STAGES[stage]
        note_id = (
            f"note.{proposal.capture_id}"
            if stage == "filed"
            else prefix + note_slug(proposal.title or "", proposal.capture_id)
        )
        return "/".join((*parts, f"{note_id}.md")), note_id, status
```

Replace the project-only parent check with one that reads the pin and names the stage its parent must live in:

Add the rule table as a module-level constant beside the other filing constants:

```python
# pin -> (vault stage the parent must already occupy, the word a refusal uses)
PARENT_STAGES = {"project": ("goals", "goal"), "task": ("projects", "project")}
```

Then, in `_prepared`, replace the project-only block with:

```python
        parent_rule = PARENT_STAGES.get(source.type_pin or "")
        if parent_rule is not None:
            stage, word = parent_rule
            if source.parent_id is None or self._unresolved((source.parent_id,), (stage,)):
                return self._failed(
                    intake.capture_id,
                    f"filing.parent_{word}_unresolvable",
                    f"no filed {word} carries the ID {source.parent_id}; "
                    f"file that {word}, then run this again",
                    proposal_id=proposal.proposal_id,
                    intake_state=intake.state,
                )
```

Resolving only against `("projects",)` is what makes `test_a_task_may_not_name_a_goal_as_its_parent` pass: a goal id is never present in that stage.

Update the two `render_*` call sites to pass `type_pin=source.type_pin, parent_id=source.parent_id`, update `_commit` to select the store with `self._planning_stores.get(source.type_pin or "", self._filed_store)`, and add the three fields to the filing audit detail:

```python
            detail={
                "effective_type": proposal.note_type,
                "type_pin": source.type_pin or "",
                "parent_id": source.parent_id or "",
                "filed_path": filed_path,
            },
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_planning_tasks -v
/opt/miniconda3/bin/python3 -m unittest discover -s tests
```

Expected: both PASS. The full run is required here — this task is the first point where every earlier rename is exercised end to end.

- [ ] **Step 5: Commit**

```bash
git add metis/filing.py tests/test_planning_tasks.py
git commit -m "feat(file): route pinned tasks to vault/tasks under their project

Requirement: REQ-INTK-004
Decision: ADR-022
Test: test_a_pinned_task_files_under_vault_tasks_with_empty_links"
```

---

### Task 9: Schemas, AGENTS, and the requirement ledger

**Files:**
- Modify: `METIS-SCHEMAS.md`, `AGENTS.md`, `METIS-REQUIREMENT-LEDGER.md`
- Test: `tests/test_repository_skeleton.py` (it asserts the AGENTS command block)

**Interfaces:**
- Consumes: the test names produced by Tasks 2–8.

- [ ] **Step 1: Write the failing test**

```python
    def test_agents_documents_the_task_capture_command(self) -> None:
        agents = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn(
            'metis capture --as task --project <project-id> "<text>"', agents
        )
```

- [ ] **Step 2: Run it to verify it fails**

```bash
/opt/miniconda3/bin/python3 -m unittest tests.test_repository_skeleton -v
```

Expected: FAIL — the command is not documented yet.

- [ ] **Step 3: Update the documents**

`METIS-SCHEMAS.md`: add §4.3 Task (before the typed-note section, renumbering the typed note to §4.4) with the frontmatter block from spec §3, the disambiguation paragraph from spec §3.1, `tasks/` in the §4.4 vault layout, the `task` row in the filing-routes list, and a line recording that planning notes carry no `verification` field. Update the §2 intake table to show `type_pin` and `parent_id` as `NOT NULL` sentinel columns in the uniqueness key.

`AGENTS.md`: add the command line to the Commands block, mark build-order step 9 done, and move the current-phase paragraph to slice B complete / slice C (outcomes) not started.

`METIS-REQUIREMENT-LEDGER.md`: update the review stamp, and extend REQ-INTK-001, REQ-INTK-002, REQ-INTK-004, REQ-VLT-002, and REQ-VLT-004 with the new test names. Do not mark anything Verified whose test has not been run in this branch.

- [ ] **Step 4: Run the full suite**

```bash
/opt/miniconda3/bin/python3 -m unittest discover -s tests
```

Expected: PASS, with the count higher than the ~434 recorded for step-08.

- [ ] **Step 5: Commit**

```bash
git add METIS-SCHEMAS.md AGENTS.md METIS-REQUIREMENT-LEDGER.md tests/test_repository_skeleton.py
git commit -m "docs: record the planning task schema, command, and evidence

Requirement: REQ-VLT-002
Decision: ADR-022
Test: test_agents_documents_the_task_capture_command"
```

- [ ] **Step 6: On owner go-ahead, push `step/09-planning-tasks` and open a draft PR against `main`**

---

## Legacy rows in the owner's smoke store

After migration 007, the three rows in `~/metis-smoke/state/metis.db` carry `type_pin = ''` and `parent_id = ''`. Two of them have evidence recording a pin — the goal `7d1200d2…` and the project `7d4e8eb8…` — so those rows diverge from their evidence by construction. Nothing filed is affected: filing reads the pin from evidence, and both notes are already in the vault. Only a replay of that exact text would touch them, and it fails closed as `state_evidence_mismatch`.

If the owner wants replay to keep working on those two captures, the repair is two statements, run once, with Metis not running:

```sql
UPDATE intake SET type_pin = 'goal'
 WHERE capture_id = '7d1200d2-5ff9-415e-a4ae-9a6f91a3723f';
UPDATE intake SET type_pin = 'project', parent_id = '<the goal id in that capture''s meta.json>'
 WHERE capture_id = '7d4e8eb8-32fd-459e-aa60-b91fdf2a371b';
```

Read the parent id from `evidence/7d4e8eb8-.../meta.json` rather than typing it from memory. This is housekeeping, not a code path: the plan builds no repair tooling.

## Live smoke (owner, after merge)

Run in `~/metis-smoke` with `ANTHROPIC_API_KEY` exported. Edit drafts in Obsidian **Source mode** only — the Properties panel strips quotes and breaks `approved: null`.

```bash
cd ~/metis-smoke
metis capture --as task --project proj.build-a-weekly-weigh-in-habit-7d4e8eb8 "Weigh in every Sunday morning"
metis classify <capture_id>
metis propose <capture_id>
# Obsidian Source mode: set status: approved on the draft in vault/notes/proposed/
metis approvals
metis file <capture_id>
```

Expect one note under `vault/tasks/` carrying `project: "[[proj.build-a-weekly-weigh-in-habit-7d4e8eb8]]"`, `status: open`, and full provenance.

## Done when

- Every test named in spec §9 exists and passes, including `duplicate_plain_capture_still_creates_one_note`, `intake_pin_columns_match_evidence_meta`, `v2_evidence_reads_its_parent_goal_as_parent_id`, and `a_legacy_unprojected_planning_row_fails_closed`.
- Full suite green on `step/09-planning-tasks`.
- ADR-022 merged before any Task 2+ commit; every implementation commit carries `Decision: ADR-022`.
- Ledger updated in the same PR as the tests that prove each row.
- No tag until the owner merges and the acceptance tests pass on `main`; then `git tag -a step-09-planning-tasks-verified`.
