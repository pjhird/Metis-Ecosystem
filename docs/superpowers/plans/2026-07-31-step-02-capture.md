# Build-Order Step 2: Immutable Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement one typed CLI capture path that preserves exact UTF-8 evidence, registers one captured intake row, detects exact replays, recovers complete orphan evidence, and fails closed without overwriting evidence.

**Architecture:** `metis/evidence.py` owns exclusive evidence creation and validation; `metis/capture.py` owns deterministic ordering and result mapping; `StateStore` owns intake lookup and registration, with SQL confined to `metis/data_access/sqlite.py`. Both CLI entry points call one standard-library implementation rooted at the current working directory.

**Tech Stack:** Python 3.13, Python standard library (`argparse`, `dataclasses`, `datetime`, `enum`, `hashlib`, `json`, `pathlib`, `sqlite3`, `uuid`, `unittest`), SQLite, and minimal `pyproject.toml` packaging with no runtime dependency.

## Global Constraints

- Work only on `step/02-capture`; never push directly to protected `main`.
- Encode the single CLI text argument as UTF-8 without trimming, normalization, or an added newline.
- Compute `sha256:<lowercase hex>` over raw bytes only.
- Use UUID4 for `capture_id` only; do not change future classification, proposal, approval, event, note, or distinct trace-ID formats.
- Set the initial `intake.trace_id` equal to its `capture_id`; do not create a second identifier in step 2.
- Create `evidence/<capture_id>/raw.txt` and `meta.json` exclusively and write them once; never overwrite, auto-delete, or auto-repair evidence.
- Persist intake state only through `StateStore`; keep every SQL statement in `metis/data_access/sqlite.py` or data-access tests.
- Treat a registered valid match as `duplicate`, a complete orphan as recoverable, a path collision as `refused`, and filesystem/database/consistency uncertainty as `failed`.
- Return shell exit `0` for `captured`, `duplicate`, and policy-enforced `refused`; return nonzero for `failed`.
- Add no model, classification, proposal, vault, approval, filing, link, audit, integration, agent, watcher, container, Postgres, UI, vector, or graph behavior.
- Do not modify, stage, rename, commit, or treat `METIS-EXECUTION-SPINE.md` as binding.
- Do not add or claim `duplicate_replay_creates_one_note` or `source_survives_classification_failure` in step 2.
- Use a failing focused test before each implementation increment, then run its test module; run the complete suite before any completion claim.

## File Map

| File | Responsibility |
|---|---|
| `METIS-SCHEMAS.md` | Reconcile capture identifiers and capture-derived examples to UUID4 without changing downstream identifier contracts |
| `metis/data_access/contracts.py` | Immutable intake record, engine-neutral registration result, state-store error, and intake methods |
| `metis/data_access/sqlite.py` | Intake lookup/registration SQL and SQLite error translation |
| `metis/data_access/__init__.py` | Public data-access exports |
| `metis/evidence.py` | Evidence creation, exact metadata validation, full-store consistency scan, and orphan discovery |
| `metis/capture.py` | Capture status/result and deterministic persistence coordinator |
| `metis/cli.py` | Argument parsing, runtime-store setup, stable JSON result rendering, and exit codes |
| `metis/__main__.py` | `python -m metis` entry point |
| `pyproject.toml` | Package metadata and `metis = "metis.cli:main"` console script |
| `tests/data_access/test_intake_store.py` | StateStore/SQLite intake contract tests, including exact row count integration assertions |
| `tests/data_access/test_migrations.py` | Keep the runtime-checkable fake compliant with the expanded protocol |
| `tests/test_evidence.py` | Byte preservation, metadata, collision, validation, corruption, and scan behavior |
| `tests/test_capture.py` | Ordering, recovery, mismatch, preservation, and result behavior |
| `tests/test_cli.py` | Both entry points, stable output, exit codes, and current-working-directory roots |
| `AGENTS.md` | Mark only `metis capture` as implemented after verification |
| `README.md` | Describe step-2 behavior and the unchanged later-step boundary |
| `METIS-REQUIREMENT-LEDGER.md` | Record only Partial evidence for step-2 requirements |

---

### Task 1: Reconcile the Capture-Identifier Contract

**Files:**
- Modify: `METIS-SCHEMAS.md:21-60`
- Modify: `METIS-SCHEMAS.md:199-221`

**Interfaces:**
- Consumes: Human approval of UUID4 for `capture_id` only.
- Produces: A schema document whose capture examples are authoritative for Tasks 2-7.

- [ ] **Step 1: Confirm the conflict is still present and downstream declarations are identifiable**

Run:

```bash
rg -n "ULID|01J8X2K4P7M3QRSTVWXYZ0ABCD|classification_id|proposal_id|approval_id|event_id" METIS-SCHEMAS.md
```

Expected: capture metadata, intake, and capture provenance still use the ULID example; downstream ID rows are listed separately.

- [ ] **Step 2: Change capture-only schema text and examples**

Use one canonical example throughout capture fields:

```text
8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70
```

Make these exact semantic edits:

```markdown
- `capture_id` is a UUID4 generated only for genuinely new evidence.
- UUID4 capture identifiers are stable handles, not chronological sort keys.
```

Update only:

- `evidence/<capture_id>/` metadata `capture_id`;
- the `intake.capture_id` description;
- capture-derived evidence paths;
- the typed-note `capture_id` provenance field and its evidence path.

Leave `classification_id`, `proposal_id`, `approval_id`, `event_id`, and the typed note's own `id` declaration unchanged.

- [ ] **Step 3: Verify capture declarations changed without downstream drift**

Run:

```bash
rg -n "UUID4|ULID|8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70|classification_id|proposal_id|approval_id|event_id" METIS-SCHEMAS.md
git diff --check -- METIS-SCHEMAS.md
```

Expected: UUID4 appears only for capture identifiers; downstream ID rows still say ULID; the diff check exits `0`.

- [ ] **Step 4: Commit the schema reconciliation before implementation code**

```bash
git add METIS-SCHEMAS.md
git commit -m "docs(schema): reconcile capture identifiers to UUID4" \
  --trailer "Requirement: REQ-INTK-001" \
  --trailer "Decision: ADR-014" \
  --trailer "Test: git diff --check -- METIS-SCHEMAS.md" \
  --trailer "Co-Authored-By: Codex <codex@openai.com>"
```

### Task 2: Add the Intake Data-Access Contract and SQLite Behavior

**Files:**
- Modify: `metis/data_access/contracts.py:5-20`
- Modify: `metis/data_access/sqlite.py:5-129`
- Modify: `metis/data_access/__init__.py:1-6`
- Modify: `tests/data_access/test_migrations.py:8-98`
- Create: `tests/data_access/test_intake_store.py`

**Interfaces:**
- Consumes: Existing `intake` table from `001_initial.sql`.
- Produces: `IntakeRecord`, `IntakeRegistrationStatus`, `IntakeRegistrationResult`, `StateStoreError`, `StateStore.find_intake_by_content_hash()`, and `StateStore.register_intake()`.

- [ ] **Step 1: Write failing contract and lookup tests**

Create `tests/data_access/test_intake_store.py` with a temporary initialized SQLite store and this canonical record helper:

```python
CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"

def intake_record(**changes: object) -> IntakeRecord:
    values = {
        "capture_id": CAPTURE_ID,
        "content_hash": "sha256:" + "a" * 64,
        "captured_at": "2026-07-31T20:00:00Z",
        "source_type": "cli-typed",
        "evidence_path": f"evidence/{CAPTURE_ID}",
        "state": "captured",
        "state_updated_at": "2026-07-31T20:00:00Z",
        "failure_reason": None,
        "trace_id": CAPTURE_ID,
    }
    values.update(changes)
    return IntakeRecord(**values)
```

Add tests with these assertions:

```python
def test_find_intake_by_content_hash_returns_none_when_absent(self) -> None:
    self.assertIsNone(self.store.find_intake_by_content_hash("sha256:" + "0" * 64))

def test_register_and_find_intake_round_trip(self) -> None:
    record = intake_record()
    result = self.store.register_intake(record)
    self.assertEqual(result.status, IntakeRegistrationStatus.REGISTERED)
    self.assertEqual(result.record, record)
    self.assertEqual(self.store.find_intake_by_content_hash(record.content_hash), record)
```

Update `FakeStateStore` in `tests/data_access/test_migrations.py` with both new methods so the runtime protocol test remains meaningful.

- [ ] **Step 2: Run the focused tests and verify the missing API fails**

Run:

```bash
python3 -m unittest tests.data_access.test_intake_store -v
python3 -m unittest tests.data_access.test_migrations.MigrationTests.test_state_store_contract_is_engine_agnostic -v
```

Expected: the intake-store module fails to import the new symbols, and the protocol test fails after its fake is expanded but before `StateStore` is expanded.

- [ ] **Step 3: Add exact engine-neutral contract types**

Add these public shapes to `contracts.py`:

```python
class StateStoreError(RuntimeError):
    """Raised when operational-state persistence cannot be determined."""


class IntakeRegistrationStatus(str, Enum):
    REGISTERED = "registered"
    DUPLICATE = "duplicate"


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


@dataclass(frozen=True)
class IntakeRegistrationResult:
    status: IntakeRegistrationStatus
    record: IntakeRecord
```

Extend `StateStore` with:

```python
def find_intake_by_content_hash(
    self,
    content_hash: str,
) -> Optional[IntakeRecord]:
    """Return the intake row registered for a content hash, if one exists."""

def register_intake(self, record: IntakeRecord) -> IntakeRegistrationResult:
    """Register a captured intake row or return the exact existing duplicate."""
```

Export all four new public types from `metis/data_access/__init__.py`.

Make the expanded `FakeStateStore` protocol-compliant with deterministic in-memory behavior:

```python
def find_intake_by_content_hash(self, content_hash: str) -> Optional[IntakeRecord]:
    return None

def register_intake(self, record: IntakeRecord) -> IntakeRegistrationResult:
    return IntakeRegistrationResult(IntakeRegistrationStatus.REGISTERED, record)
```

- [ ] **Step 4: Implement lookup and successful registration in SQLite**

Add a single intake column tuple and row converter in `sqlite.py`. Implement parameterized SQL only in this file:

```python
def find_intake_by_content_hash(self, content_hash: str) -> Optional[IntakeRecord]:
    try:
        row = self._connect().execute(
            "SELECT capture_id, content_hash, captured_at, source_type, "
            "evidence_path, state, state_updated_at, failure_reason, trace_id "
            "FROM intake WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
    except sqlite3.Error as error:
        raise StateStoreError(f"intake lookup failed: {error}") from error
    return None if row is None else IntakeRecord(*row)
```

`register_intake()` must insert all nine fields, commit on success, and return `REGISTERED` with the input record.

- [ ] **Step 5: Run lookup/registration tests green**

Run:

```bash
python3 -m unittest tests.data_access.test_intake_store.IntakeStoreTests.test_find_intake_by_content_hash_returns_none_when_absent -v
python3 -m unittest tests.data_access.test_intake_store.IntakeStoreTests.test_register_and_find_intake_round_trip -v
```

Expected: both tests pass.

- [ ] **Step 6: Add failing duplicate and failure-translation tests**

Add:

```python
def test_duplicate_content_hash_returns_existing_record(self) -> None:
    original = intake_record()
    duplicate = intake_record(
        capture_id="6ba7b810-9dad-41d1-80b4-00c04fd430c8",
        evidence_path="evidence/6ba7b810-9dad-41d1-80b4-00c04fd430c8",
        trace_id="6ba7b810-9dad-41d1-80b4-00c04fd430c8",
    )
    self.store.register_intake(original)
    result = self.store.register_intake(duplicate)
    self.assertEqual(result.status, IntakeRegistrationStatus.DUPLICATE)
    self.assertEqual(result.record, original)

def test_capture_id_collision_is_a_state_store_failure(self) -> None:
    self.store.register_intake(intake_record())
    with self.assertRaises(StateStoreError):
        self.store.register_intake(intake_record(content_hash="sha256:" + "b" * 64))

def test_missing_intake_table_is_a_state_store_failure(self) -> None:
    with sqlite3.connect(self.database_path) as connection:
        connection.execute("DROP TABLE intake")
    with self.assertRaises(StateStoreError):
        self.store.find_intake_by_content_hash("sha256:" + "a" * 64)
```

- [ ] **Step 7: Implement duplicate translation and rollback**

In `register_intake()`:

```python
except sqlite3.IntegrityError as error:
    connection.rollback()
    existing = self.find_intake_by_content_hash(record.content_hash)
    if existing is not None:
        return IntakeRegistrationResult(IntakeRegistrationStatus.DUPLICATE, existing)
    raise StateStoreError(f"intake registration failed: {error}") from error
except sqlite3.Error as error:
    connection.rollback()
    raise StateStoreError(f"intake registration failed: {error}") from error
```

Do not alter `001_initial.sql`; its existing uniqueness constraint is the backstop.

- [ ] **Step 8: Run the data-access module and SQL boundary**

Run:

```bash
python3 -m unittest tests.data_access.test_intake_store -v
python3 -m unittest tests.data_access.test_migrations -v
python3 -m unittest tests.test_data_access_boundary -v
```

Expected: all three commands end in `OK`.

- [ ] **Step 9: Commit the data-access capability**

```bash
git add metis/data_access/contracts.py metis/data_access/sqlite.py metis/data_access/__init__.py tests/data_access/test_migrations.py tests/data_access/test_intake_store.py
git commit -m "feat(data): add captured intake registration" \
  --trailer "Requirement: REQ-INTK-002" \
  --trailer "Decision: ADR-002" \
  --trailer "Decision: ADR-014" \
  --trailer "Test: tests.data_access.test_intake_store" \
  --trailer "Co-Authored-By: Codex <codex@openai.com>"
```

### Task 3: Create and Validate Immutable Evidence

**Files:**
- Create: `metis/evidence.py`
- Create: `tests/test_evidence.py`

**Interfaces:**
- Consumes: UTF-8 `raw_bytes`, canonical UUID4 string, content hash, and UTC timestamp.
- Produces: `EvidenceRecord`, `EvidenceStore.create()`, `EvidenceStore.validate_directory()`, `EvidenceCollision`, `EvidenceConsistencyError`, and `EvidenceWriteError`.

- [ ] **Step 1: Write failing byte, metadata, and hash tests**

Use a temporary runtime root and fixed values:

```python
CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
CAPTURED_AT = "2026-07-31T20:00:00Z"
RAW_BYTES = "  café\nsecond line\t  ".encode("utf-8")
CONTENT_HASH = "sha256:" + hashlib.sha256(RAW_BYTES).hexdigest()
```

Add:

```python
def test_create_preserves_raw_bytes_exactly(self) -> None:
    record = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
    self.assertEqual(record.raw_path.read_bytes(), RAW_BYTES)

def test_create_writes_exact_metadata_contract(self) -> None:
    record = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
    metadata = json.loads(record.meta_path.read_text(encoding="utf-8"))
    self.assertEqual(metadata, {
        "capture_id": CAPTURE_ID,
        "content_hash": CONTENT_HASH,
        "captured_at": CAPTURED_AT,
        "source_type": "cli-typed",
        "source_detail": "metis capture",
        "byte_size": len(RAW_BYTES),
        "mime_type": "text/plain",
        "schema_version": 1,
    })

def test_content_hash_matches_fresh_sha256_of_raw(self) -> None:
    record = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
    fresh = "sha256:" + hashlib.sha256(record.raw_path.read_bytes()).hexdigest()
    self.assertEqual(record.content_hash, fresh)
```

- [ ] **Step 2: Run the evidence module and verify it fails to import**

```bash
python3 -m unittest tests.test_evidence -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'metis.evidence'`.

- [ ] **Step 3: Implement the evidence record and exclusive create path**

Define:

```python
@dataclass(frozen=True)
class EvidenceRecord:
    capture_id: str
    content_hash: str
    captured_at: str
    evidence_path: str
    directory: Path
    raw_path: Path
    meta_path: Path


class EvidenceError(RuntimeError):
    def __init__(self, message: str, evidence_path: Optional[str] = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path


class EvidenceCollision(EvidenceError):
    """Raised when exclusive evidence creation finds an existing target."""


class EvidenceConsistencyError(EvidenceError):
    """Raised when existing evidence cannot be trusted or uniquely resolved."""


class EvidenceWriteError(EvidenceError):
    """Raised when new evidence cannot be finalized."""
```

`EvidenceStore(runtime_root: Path)` owns `runtime_root / "evidence"`. Implement this exact public method:

```python
def create(
    self,
    capture_id: str,
    raw_bytes: bytes,
    content_hash: str,
    captured_at: str,
) -> EvidenceRecord:
```

Creation order is `directory.mkdir(exist_ok=False)`, `raw_path.open("xb")`, then
`meta_path.open("x", encoding="utf-8")`. Close each file before the next persistence step. Serialize metadata
with `json.dump(metadata, stream, indent=2, sort_keys=True)` plus one metadata-file newline; never add bytes to `raw.txt`.
Map an existing directory to `EvidenceCollision` and other `OSError` values to `EvidenceWriteError`, preserving
any partial directory or file.

- [ ] **Step 4: Run the three creation tests green**

```bash
python3 -m unittest \
  tests.test_evidence.EvidenceStoreTests.test_create_preserves_raw_bytes_exactly \
  tests.test_evidence.EvidenceStoreTests.test_create_writes_exact_metadata_contract \
  tests.test_evidence.EvidenceStoreTests.test_content_hash_matches_fresh_sha256_of_raw -v
```

Expected: all three tests pass.

- [ ] **Step 5: Add failing validation and collision tests**

Add tests that:

```python
def test_validate_directory_returns_the_same_record(self) -> None:
    created = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
    self.assertEqual(self.store.validate_directory(created.directory), created)

def test_existing_capture_directory_is_never_overwritten(self) -> None:
    directory = self.runtime_root / "evidence" / CAPTURE_ID
    directory.mkdir(parents=True)
    original = directory / "raw.txt"
    original.write_bytes(b"original")
    with self.assertRaises(EvidenceCollision):
        self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
    self.assertEqual(original.read_bytes(), b"original")
    self.assertFalse((directory / "meta.json").exists())
```

Add subtests that corrupt exactly one property at a time: UUID version/canonical form, metadata key set,
metadata types, `capture_id`, hash, byte size, UTC `Z` timestamp, source fields, MIME type, schema version,
missing file, and non-regular file. Each must raise `EvidenceConsistencyError` without mutation.

- [ ] **Step 6: Implement strict directory validation**

`validate_directory()` must:

1. require a directory whose name round-trips through `UUID(name)`, has `version == 4`, and equals `str(parsed)`;
2. require exactly regular `raw.txt` and `meta.json` files;
3. parse metadata as a JSON object with exactly the eight specified keys;
4. reject booleans where integer values are required;
5. require a parseable UTC timestamp ending in `Z`;
6. independently read raw bytes and recompute byte size and SHA-256;
7. return an `EvidenceRecord` only after all checks agree.

Wrap JSON, Unicode, UUID, timestamp, and filesystem-read errors as `EvidenceConsistencyError` with the affected
directory in the message.

- [ ] **Step 7: Run the complete evidence validation module**

```bash
python3 -m unittest tests.test_evidence -v
```

Expected: the module ends in `OK`.

- [ ] **Step 8: Commit immutable evidence creation**

```bash
git add metis/evidence.py tests/test_evidence.py
git commit -m "feat(capture): preserve immutable source evidence" \
  --trailer "Requirement: REQ-INTK-001" \
  --trailer "Requirement: REQ-TEST-003" \
  --trailer "Decision: ADR-003" \
  --trailer "Decision: ADR-014" \
  --trailer "Test: tests.test_evidence" \
  --trailer "Co-Authored-By: Codex <codex@openai.com>"
```

### Task 4: Add Evidence Scanning and Fail-Closed Recovery Discovery

**Files:**
- Modify: `metis/evidence.py`
- Modify: `tests/test_evidence.py`

**Interfaces:**
- Consumes: Strict `validate_directory()` from Task 3.
- Produces: `EvidenceStore.find_by_content_hash(content_hash) -> Optional[EvidenceRecord]` with singular-match enforcement.

- [ ] **Step 1: Write failing scan tests**

Add tests for no root, no match, one match, two valid directories with the same raw bytes, and one partial or
corrupt directory. Core assertions:

```python
def test_find_by_content_hash_returns_one_valid_match(self) -> None:
    expected = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
    self.assertEqual(self.store.find_by_content_hash(CONTENT_HASH), expected)

def test_find_by_content_hash_rejects_multiple_matches(self) -> None:
    self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
    self.store.create(
        "6ba7b810-9dad-41d1-80b4-00c04fd430c8",
        RAW_BYTES,
        CONTENT_HASH,
        "2026-07-31T20:01:00Z",
    )
    with self.assertRaises(EvidenceConsistencyError):
        self.store.find_by_content_hash(CONTENT_HASH)

def test_find_by_content_hash_fails_closed_on_partial_directory(self) -> None:
    partial = self.runtime_root / "evidence" / CAPTURE_ID
    partial.mkdir(parents=True)
    (partial / "raw.txt").write_bytes(RAW_BYTES)
    with self.assertRaises(EvidenceConsistencyError):
        self.store.find_by_content_hash(CONTENT_HASH)
```

- [ ] **Step 2: Run scan tests and verify the missing method fails**

```bash
python3 -m unittest tests.test_evidence.EvidenceStoreTests.test_find_by_content_hash_returns_one_valid_match tests.test_evidence.EvidenceStoreTests.test_find_by_content_hash_rejects_multiple_matches tests.test_evidence.EvidenceStoreTests.test_find_by_content_hash_fails_closed_on_partial_directory -v
```

Expected: FAIL because `find_by_content_hash` does not exist.

- [ ] **Step 3: Implement a full consistency scan**

Implement:

```python
def find_by_content_hash(self, content_hash: str) -> Optional[EvidenceRecord]:
```

If `evidence/` is absent, return `None`. Otherwise, iterate children in sorted name order. Every child must be a
valid evidence directory; call `validate_directory()` before comparing its hash. Return `None` for no matches,
the record for exactly one match, and raise `EvidenceConsistencyError` for more than one. Do not delete,
rewrite, quarantine, or skip corrupt entries.

- [ ] **Step 4: Run all evidence tests**

```bash
python3 -m unittest tests.test_evidence -v
```

Expected: the module ends in `OK`.

- [ ] **Step 5: Commit recovery discovery**

```bash
git add metis/evidence.py tests/test_evidence.py
git commit -m "feat(capture): discover recoverable evidence safely" \
  --trailer "Requirement: REQ-INTK-001" \
  --trailer "Requirement: REQ-INTK-005" \
  --trailer "Decision: ADR-003" \
  --trailer "Test: tests.test_evidence" \
  --trailer "Co-Authored-By: Codex <codex@openai.com>"
```

### Task 5: Orchestrate Capture Ordering, Replay, and Recovery

**Files:**
- Create: `metis/capture.py`
- Create: `tests/test_capture.py`
- Create: `tests/data_access/test_capture_integration.py`

**Interfaces:**
- Consumes: `StateStore`, `EvidenceStore`, `IntakeRecord`, and both registration statuses.
- Produces: `CaptureStatus`, `CaptureResult`, and `CaptureService.capture(text: str) -> CaptureResult`.

- [ ] **Step 1: Write failing result and byte-encoding tests**

Define expected public shapes in tests:

```python
class CaptureStatus(str, Enum):
    CAPTURED = "captured"
    DUPLICATE = "duplicate"
    REFUSED = "refused"
    FAILED = "failed"

@dataclass(frozen=True)
class CaptureResult:
    status: CaptureStatus
    capture_id: Optional[str]
    evidence_path: Optional[str]
    reason: Optional[str]
    message: Optional[str]
```

Use an injected ID factory returning UUID4 `8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70` and an injected UTC clock
returning `datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)`. Assert a text value containing whitespace,
Unicode, and an embedded newline produces the SHA-256 of `text.encode("utf-8")` and the expected evidence bytes.

- [ ] **Step 2: Run the focused capture test and verify the module is absent**

```bash
python3 -m unittest tests.test_capture.CaptureServiceTests.test_capture_encodes_text_without_modification -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'metis.capture'`.

- [ ] **Step 3: Implement result types and the new-capture happy path**

Implement:

```python
class CaptureService:
    def __init__(
        self,
        state_store: StateStore,
        evidence_store: EvidenceStore,
        *,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._state_store = state_store
        self._evidence_store = evidence_store
        self._id_factory = id_factory
        self._clock = clock
```

Add the exact public method signature `def capture(self, text: str) -> CaptureResult` and implement the concrete
orchestration described in Steps 3-7. For a new input: encode, hash, lookup state, scan evidence, generate UUID4, create evidence, validate finalized
files, build `IntakeRecord` with `trace_id == capture_id`, register, and return `CAPTURED`. Serialize the injected
clock to whole-second UTC with `Z`. Reject a non-UUID4 ID-factory result as `FAILED` before filesystem mutation.

- [ ] **Step 4: Add and run the ordering failure test**

Use a recording fake whose `register_intake()` asserts:

```python
directory = runtime_root / record.evidence_path
self.assertTrue((directory / "raw.txt").is_file())
self.assertTrue((directory / "meta.json").is_file())
evidence_store.validate_directory(directory)
```

Run:

```bash
python3 -m unittest tests.test_capture.CaptureServiceTests.test_evidence_is_finalized_before_registration -v
```

Expected before implementation: FAIL at the assertion or missing behavior. Expected after the minimal ordering
implementation: PASS.

- [ ] **Step 5: Write failing duplicate, mismatch, refusal, and persistence-failure tests**

Add tests for these exact outcomes and reason codes:

| Condition | Status | Reason |
|---|---|---|
| Valid row and matching evidence | `DUPLICATE` | `exact_replay` |
| Row with absent or disagreeing evidence | `FAILED` | `state_evidence_mismatch` |
| Evidence scan reports corruption/multiple matches | `FAILED` | `evidence_inconsistent` |
| Generated UUID directory already exists | `REFUSED` | `evidence_collision` |
| Evidence creation raises other write error | `FAILED` | `evidence_write_failed` |
| State lookup raises `StateStoreError` | `FAILED` | `state_lookup_failed` |
| Registration raises `StateStoreError` | `FAILED` | `state_registration_failed` |
| New evidence followed by a duplicate registration result | `FAILED` | `late_duplicate_registration` |

Each failed result must include an evidence path when it is known. Each test must assert that pre-existing or
newly finalized evidence bytes remain unchanged.

- [ ] **Step 6: Implement fail-closed result mapping**

Add one private row/evidence comparison that requires agreement on capture ID, content hash, capture time,
source type, relative evidence path, `state == "captured"`, `state_updated_at == captured_at`,
`failure_reason is None`, and `trace_id == capture_id`. Keep exception
handling narrow:

```python
except EvidenceCollision as error:
    return CaptureResult(CaptureStatus.REFUSED, capture_id, evidence_path, "evidence_collision", str(error))
except EvidenceConsistencyError as error:
    return CaptureResult(CaptureStatus.FAILED, None, error.evidence_path, "evidence_inconsistent", str(error))
except EvidenceWriteError as error:
    return CaptureResult(CaptureStatus.FAILED, capture_id, error.evidence_path, "evidence_write_failed", str(error))
```

Map state lookup and registration failures separately so the result never claims registration succeeded.

- [ ] **Step 7: Write and pass orphan-retry tests**

Use a fail-once store wrapper around real `SQLiteStateStore`:

```python
first = service.capture("same input")
second = service.capture("same input")
self.assertEqual(first.status, CaptureStatus.FAILED)
self.assertEqual(first.reason, "state_registration_failed")
self.assertEqual(second.status, CaptureStatus.CAPTURED)
self.assertEqual(second.capture_id, first.capture_id)
self.assertEqual(len(list((runtime_root / "evidence").iterdir())), 1)
```

The second call must reuse the orphan metadata timestamp and UUID4 rather than invoking either factory again.

- [ ] **Step 8: Prove exact replay creates one row and directory using real SQLite**

In `tests/data_access/test_capture_integration.py`, use real `SQLiteStateStore` and direct SQLite inspection
inside the allowed data-access test directory:

```python
first = service.capture("same input")
second = service.capture("same input")
self.assertEqual(first.status, CaptureStatus.CAPTURED)
self.assertEqual(second.status, CaptureStatus.DUPLICATE)
self.assertEqual(first.capture_id, second.capture_id)
self.assertEqual(len(list((runtime_root / "evidence").iterdir())), 1)
with sqlite3.connect(database_path) as connection:
    count = connection.execute("SELECT COUNT(*) FROM intake").fetchone()[0]
self.assertEqual(count, 1)
```

Run:

```bash
python3 -m unittest tests.test_capture -v
python3 -m unittest tests.data_access.test_capture_integration -v
python3 -m unittest tests.test_data_access_boundary -v
```

Expected: all three commands end in `OK`; the boundary test confirms SQL did not escape the data-access layer.

- [ ] **Step 9: Commit the capture coordinator**

```bash
git add metis/capture.py tests/test_capture.py tests/data_access/test_capture_integration.py
git commit -m "feat(capture): orchestrate replay-safe intake capture" \
  --trailer "Requirement: REQ-INTK-001" \
  --trailer "Requirement: REQ-INTK-002" \
  --trailer "Requirement: REQ-INTK-005" \
  --trailer "Decision: ADR-003" \
  --trailer "Decision: ADR-007" \
  --trailer "Decision: ADR-014" \
  --trailer "Decision: ADR-015" \
  --trailer "Test: tests.test_capture" \
  --trailer "Test: tests.data_access.test_capture_integration" \
  --trailer "Co-Authored-By: Codex <codex@openai.com>"
```

### Task 6: Add Both CLI Entry Points and Honest Shell Outcomes

**Files:**
- Create: `metis/cli.py`
- Create: `metis/__main__.py`
- Create: `pyproject.toml`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `CaptureService.capture()` and `SQLiteStateStore`.
- Produces: `metis.cli.main(argv=None, runtime_root=None) -> int`, `python -m metis capture`, and the installed `metis capture` script.

- [ ] **Step 1: Write failing parser and output tests**

Add tests proving exactly one text argument is required and every result renders one JSON object with the same
five keys:

```json
{"capture_id": "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70", "evidence_path": "evidence/8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70", "message": null, "reason": null, "status": "captured"}
```

Use actual values where known. Assert:

- `captured`, `duplicate`, and `refused` write JSON to stdout, leave stderr empty, and return `0`;
- `failed` writes JSON to stderr, leaves stdout empty, and returns `1`;
- the JSON `status` matches the result and reason codes are not converted into success text.

Use `unittest.mock.patch("metis.cli.CaptureService")` only for isolated status-rendering cases.

- [ ] **Step 2: Run CLI tests and verify the module is absent**

```bash
python3 -m unittest tests.test_cli -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'metis.cli'`.

- [ ] **Step 3: Implement one parser and runtime path**

Implement:

```python
def main(
    argv: Optional[Sequence[str]] = None,
    *,
    runtime_root: Optional[Path] = None,
) -> int:
```

Use an `argparse` parser with one `capture` subcommand and one positional `text`. Resolve
`root = Path.cwd()` when `runtime_root` is absent. Initialize `SQLiteStateStore(root / "state" / "metis.db")`
inside its context manager and construct `EvidenceStore(root)`. Convert initialization errors to a `FAILED`
result with reason `state_initialization_failed`. Render `dataclasses.asdict(result)` after converting the enum
to its string value, using `json.dumps(payload, sort_keys=True)`.

Create `metis/__main__.py`:

```python
from .cli import main

raise SystemExit(main())
```

- [ ] **Step 4: Add module-entry integration tests**

Use `subprocess.run()` with `cwd` set to a temporary runtime root and `PYTHONPATH` set to the repository root.
Invoke:

```python
[sys.executable, "-m", "metis", "capture", "  café\n  "]
```

Run it twice and assert first `captured`, second `duplicate`, both exit `0`, one evidence directory exists, and
`raw.txt` equals the exact UTF-8 bytes.

- [ ] **Step 5: Add minimal console-script packaging**

Create `pyproject.toml` with:

```toml
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "metis-ecosystem"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[project.scripts]
metis = "metis.cli:main"

[tool.setuptools.packages.find]
include = ["metis*"]
```

Add a `tomllib` test asserting the script target, empty dependency list, and package name.

- [ ] **Step 6: Run CLI and repository-boundary tests**

```bash
python3 -m unittest tests.test_cli -v
python3 -m unittest tests.test_repository_skeleton -v
python3 -m unittest tests.test_data_access_boundary -v
```

Expected: all commands end in `OK`.

- [ ] **Step 7: Verify the installed `metis` entry point in a disposable environment**

Run:

```bash
tmp_venv="$(mktemp -d)/venv"
python3 -m venv "$tmp_venv"
"$tmp_venv/bin/python" -m pip install --no-deps --no-build-isolation -e .
runtime_root="$(mktemp -d)"
cd "$runtime_root"
"$tmp_venv/bin/metis" capture "entry point check"
```

Expected: installation exits `0`; the final command prints JSON with `"status": "captured"` and exits `0`.
Return to the repository root before continuing. Do not copy the disposable environment or its runtime data
into the repository.

- [ ] **Step 8: Commit CLI and packaging**

```bash
git add metis/cli.py metis/__main__.py pyproject.toml tests/test_cli.py
git commit -m "feat(cli): expose typed capture command" \
  --trailer "Requirement: REQ-INTK-001" \
  --trailer "Requirement: REQ-INTK-002" \
  --trailer "Decision: ADR-014" \
  --trailer "Decision: ADR-015" \
  --trailer "Test: tests.test_cli" \
  --trailer "Co-Authored-By: Codex <codex@openai.com>"
```

### Task 7: Record Verified Step-2 Evidence Conservatively

**Files:**
- Modify: `AGENTS.md:177-185`
- Modify: `README.md:3-66`
- Modify: `METIS-REQUIREMENT-LEDGER.md:5-132`

**Interfaces:**
- Consumes: Fresh successful results from Tasks 2-6.
- Produces: Accurate command documentation, Partial ledger entries, a clean verified branch, and local annotated tag `step-02-capture-verified`.

- [ ] **Step 1: Run the complete suite before changing evidence claims**

```bash
python3 -m unittest discover -s tests -v
```

Expected: exit `0` and a final `OK`. Record the exact test count from this run in the ledger or README only if
the count is copied from the output.

- [ ] **Step 2: Update command and repository-status documentation**

In `AGENTS.md`, change only the capture command comment:

```text
metis capture "<text>"     # immutable typed capture with exact replay protection
```

Leave `metis approvals` and `metis status` marked unimplemented. In `README.md`, replace step-1-only capture
claims with an exact description of immutable typed capture, explicit outcomes, and the remaining absence of
classification, vault, approval, filing, and audit behavior.

- [ ] **Step 3: Update the ledger without overclaiming**

Set `Last reviewed` to build-order step 2 and update only these rows:

- `REQ-INTK-001`: **Partial** — cite byte-exact evidence, metadata/hash, and evidence-before-registration tests;
- `REQ-INTK-002`: **Partial** — cite one intake row and one evidence directory on exact replay, while retaining
  `duplicate_replay_creates_one_note` as the evidence still needed for Verified;
- `REQ-INTK-005`: **Partial** — cite registration-failure preservation and orphan retry, while retaining
  classification failure and visible review state as outstanding;
- `REQ-TEST-003`: remain **Partial** — add exact evidence-metadata validation to the existing evidence text.

Do not mark any of these rows Verified.

- [ ] **Step 4: Verify documentation, secrets, ignored runtime paths, and intended diff**

Run:

```bash
git diff --check
git check-ignore state/metis.db evidence/example/raw.txt
git diff --name-only 16dd7fd7c9c064efcd72ffc82748bfb50829245d..HEAD
git status --short
rg -n "API_KEY|SECRET|TOKEN|PASSWORD" metis tests pyproject.toml AGENTS.md README.md METIS-REQUIREMENT-LEDGER.md
```

Expected: diff check exits `0`; both runtime paths are ignored; only step-2 files and approved documentation
appear; status contains no runtime evidence, state database, secret, or `METIS-EXECUTION-SPINE.md`; secret scan
returns no introduced credential values.

- [ ] **Step 5: Run the full suite again after documentation updates**

```bash
python3 -m unittest discover -s tests -v
```

Expected: exit `0` with the same test count as Step 1 and final `OK`. Existing non-failing SQLite
`ResourceWarning` output, if still present, must be reported as a limitation rather than hidden.

- [ ] **Step 6: Commit the ledger and command evidence**

```bash
git add AGENTS.md README.md METIS-REQUIREMENT-LEDGER.md
git commit -m "docs(capture): record step 2 verification evidence" \
  --trailer "Requirement: REQ-INTK-001" \
  --trailer "Requirement: REQ-INTK-002" \
  --trailer "Requirement: REQ-INTK-005" \
  --trailer "Requirement: REQ-TEST-003" \
  --trailer "Decision: ADR-003" \
  --trailer "Decision: ADR-014" \
  --trailer "Decision: ADR-015" \
  --trailer "Test: python3 -m unittest discover -s tests -v" \
  --trailer "Co-Authored-By: Codex <codex@openai.com>"
```

- [ ] **Step 7: Perform the final verification gate**

Run fresh:

```bash
python3 -m unittest discover -s tests -v
git diff HEAD^ HEAD --check
git status --porcelain
git log --format=fuller --decorate -8
git ls-files state evidence vault .env METIS-EXECUTION-SPINE.md
```

Expected: the suite exits `0` with final `OK`; the commit diff is clean; worktree output is empty; commit
trailers are visible; the forbidden runtime/unrelated paths produce no tracked-file output.

- [ ] **Step 8: Create and inspect the required local annotated tag**

Only after Step 7 succeeds:

```bash
git tag -a step-02-capture-verified -m "REQ-INTK-001 REQ-INTK-002 REQ-INTK-005 REQ-TEST-003 partially verified"
git show --no-patch --decorate step-02-capture-verified
git merge-base --is-ancestor step-02-capture-verified step/02-capture
```

Expected: the tag resolves to the final verified step-2 commit and the ancestry check exits `0`.

## Final Completion Report

Report only observed evidence:

- changes and why each file changed;
- exact narrow, module, and full-suite commands run;
- exact pass/fail counts from fresh output;
- the two CLI entry points and their observed exit/status behavior;
- Partial ledger status for `REQ-INTK-001`, `REQ-INTK-002`, `REQ-INTK-005`, and `REQ-TEST-003`;
- remaining lack of classification, proposal, approval, permanent note, link, and audit behavior;
- any surviving SQLite `ResourceWarning` messages;
- local tag location and whether it has been pushed;
- confirmation that `METIS-EXECUTION-SPINE.md`, runtime evidence, the state database, vault content, and secrets
  were not committed.
