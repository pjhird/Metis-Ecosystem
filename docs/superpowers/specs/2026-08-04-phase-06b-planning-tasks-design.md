# Phase 6 Slice B — First-Class Planning Tasks

## Status

- Blueprint phase: 6 — Connect Goals, Projects, and Tasks (second slice)
- Implementation status: **design — owner review amendments 2026-08-04 applied; awaiting formal spec approval**
- Base: `main` through `step-08-planning-notes-verified` (ADR-021 merged; docs honesty PR #13 merged)
- Approach chosen: **Hybrid pin** — `metis capture --as task --project <proj.id>`; same capture → classify → propose → approve → file → audit loop
- Parent contract: **project only** (mirrors project → goal)
- Intake uniqueness: **`content_hash + type_pin + parent_id`** for **all** intakes, with **no NULLs in the key** (`NOT NULL` columns, empty-string sentinels; ADR-022 clause 9; amends ADR-014)
- Governing decisions today: ADR-001 … ADR-021 (esp. ADR-003, ADR-004, ADR-005, ADR-007, ADR-018, ADR-020, ADR-021)
- **Blocked on:** a new ADR (proposed number **ADR-022**) recording planning-task creation rules before any code
- Out of scope for this slice: outcomes, task↔task dependencies, decompose-project, calendar/task-manager writes, agents, execution spine, CLI-driven planning-status transitions

## 1. Objective

Give Metis a governed way to create **Task** notes that hang under an existing **Project**, completing the usable planning spine `goal → project → task` without a second approval surface or trusting the classifier to invent planning identity.

Acceptance for this slice:

1. `metis capture --as task --project <project-id> "…"` (after classify → propose → human `status: approved` → `metis file`) writes exactly one note under `vault/tasks/` whose frontmatter matches §3 below **plus** provenance (`capture_id`, `evidence`).
2. The parent is system-written as `project: "[[…]]"` from the capture pin and must resolve at file time to an existing note in `vault/projects/` with `type: project`.
3. Missing parent, wrong-type parent (e.g. a goal id), or unapproved write → `refused`; vault unchanged.
4. Plain `metis capture` (no `--as`) keeps replay protection under the same uniqueness key as pinned captures, using empty-string sentinels for absent pin/parent (§8 clause 9). Goal/project creation paths remain; parent-differing replay behavior is restated by ADR-022 (amends ADR-014; supersedes ADR-021-era parent-conflict application behavior).
5. Replay of the identical capture — same `content_hash` + same `type_pin` + same `parent_id` — creates no second task note.
6. `pin_conflict` is reserved for identical `content_hash` + `parent_id` submitted under a differing `type_pin`. Identical text under two different parents is **two distinct captures**, not a conflict (see §8 clause 9).
7. Audit continues to emit per step 7 (task filing carries `effective_type` and parent in `detail`); unapproved write remains refused.

## 2. Why hybrid under project (not soft links, not decompose-first)

| Approach | Verdict for this slice |
|---|---|
| (1) Soft parent via human `links` only | Rejected — weaker than ADR-021; reintroduces orphans |
| (2) Parent may be goal **or** project | Rejected for this slice — two legal trees blur project vs task; defer |
| (3) Decompose-project multi-note proposals first | Deferred — new authority shape; needs its own ADR after tasks exist |
| (4) Promote typed-note `type: task` in `notes/filed/` as primary | Rejected — label on an idea-shaped note, not a planning object |
| (5) **Hybrid: `--as task --project`** | **Chosen** — one pipe, human declares planning identity at the door |

Optional later: thin alias `metis propose-task` → `capture --as task` without a second pipeline.

## 3. Note schema (filed task)

Created through `metis capture --as task --project <project-id>` → classify → propose → approve → `metis file`. Filed under `vault/tasks/` with deterministic id `task.<title-slug>-<8 hex of capture_id>` (same uniqueness strategy as goals/projects). Planning `status` is set to `open` at file time and stays there for this slice; the approval decision lives in SQLite + audit, not in this field.

```yaml
---
id: task.weekly-weigh-in-7d4e8eb8
type: task
title: Do the weekly weigh-in
status: open              # this slice: open only — no transition produces other values
project: "[[proj.build-a-weekly-weigh-in-habit-7d4e8eb8]]"
created: 2026-08-04
capture_id: 8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70
evidence:
  capture: "evidence/…/raw.txt"
  classification: "classification-evidence/…/raw-response.txt"
  proposal: "proposal-evidence/…/raw-response.txt"
links: []                 # parent lives in project:; empty links OK
---
```

Rules:

- Provenance (`capture_id` + `evidence` map with `capture`, `classification`, `proposal`) is mandatory (REQ-VLT-004).
- `project:` is system-written from the pin; not human-editable under ADR-020 (byte-exact draft contract).
- `links: []` is allowed for planning tasks (same as projects).
- Planning `status` is a **lifecycle** field, not an authorization field (§8 clause 10). This slice emits `open` only — do not declare `in_progress` / `done` / `cancelled` in the schema until a later ADR defines transitions that can produce them (avoid the `proposal.state = superseded` clarify-or-remove defect).
- Planning entities (goal, project, task) are **exempt from `verification`**: that field applies to interpreted content whose truth can be checked later (REQ-DATA-005), not to human-declared planning identity. A schema test written literally against REQ-DATA-005 must not require `verification:` on planning notes.

### 3.1 Planning task vs typed-note `task`

Classifier-owned typed notes may still emit `candidate_type: task` and file under `vault/notes/filed/` with ≥1 resolvable link (unchanged).

A **planning task** is a different entity: `type_pin=task`, files under `vault/tasks/`, carries `project:`, and is never created by plain capture. ADR-022 clause 11 is the mechanical form of this rule — routing reads the pin, never `note_type` — so the distinction is enforced rather than described.

## 4. CLI contract

```text
metis capture --as task --project <project-id> "<text>"
```

Rules:

- `--as task` **requires** `--project <id>` where `<id>` matches `[A-Za-z0-9._-]+`.
- `--project` without `--as task` is a usage error (exit non-zero, no evidence write).
- `--as` gains `task` alongside existing `goal` | `project`.
- Pin and `parent_project_id` are recorded in capture metadata / evidence `meta.json` **before** any model call (ADR-003).
- Classification still runs for sensitivity and confidence; it **must not override** the pin. Conflicting model `candidate_type` → pin wins; raw response preserved.
- Parent existence is **not** required at capture time (offline / draft-first allowed); it **is** required at **file** time (fail closed), matching project→goal behavior.

## 5. Draft and human edits (ADR-020 unchanged)

Drafts remain in `vault/notes/proposed/note.<capture_id>.md` for all types (one approval inbox).

Human-editable fields remain **only** `status` and `links`.

| Effective type | Parent field | `links` at file |
|---|---|---|
| typed note | none (human links) | ≥1 resolvable goal or project (existing); may later resolve tasks — not required for B acceptance |
| `goal` | none | may be `[]` |
| `project` | system `goal:` | may be `[]` |
| `task` | system `project:` | may be `[]` |

`status` remains the only authorizing field.

## 6. Filing routes by effective type

`metis file <capture-id>` stays one command. Destination:

| Effective type | Path |
|---|---|
| `goal` | `vault/goals/<id>.md` |
| `project` | `vault/projects/<id>.md` |
| `task` | `vault/tasks/<id>.md` |
| other | `vault/notes/filed/note.<capture_id>.md` |

Id generation: `task.<title-slug>-<8 hex of capture_id>` — pure function of title + capture_id; collision fails closed.

File-time parent checks:

- Resolve `parent_project_id` against note `id` in `vault/projects/` (not filename).
- Refuse if missing, if target is not `type: project`, or if target lives under `vault/goals/` / elsewhere.
- Reason code family: `filing.parent_project_unresolvable` (mirror `filing.parent_goal_unresolvable`).

### 6.1 Vault layout

```text
vault/
├── goals/
├── projects/
├── tasks/              # NEW — permanent Task notes (ADR-022)
├── notes/
│   ├── proposed/       # drafts awaiting decision (all types)
│   └── filed/          # approved typed notes (not planning tasks)
└── archive/
```

`DraftNoteStore` gains a `tasks` stage with path confinement (refuse writes outside `vault/tasks/`).

## 7. Control / capability map

```text
INTERFACE   CLI --as task --project · Obsidian draft status · approvals · file
CONTROL     Orchestrator — pin validation, idempotency, approval gate, audit
CAPABILITY  capture (pin+parent) · classify (pin override) · propose/render · file router
PERSISTENCE evidence meta · SQLite intake/approval/audit · vault/tasks/
```

No new intake states. No second approval surface. Skills still return bounded results only (ADR-007). SQL stays in the data layer; provider SDK stays in the model adapter.

Audit `detail` JSON **must** include `effective_type`, `type_pin`, and `parent_project_id` for task filings (required by §1.7; not optional).

## 8. ADR-022 (required before code)

Title (draft): **Planning tasks are created through pinned capture under a project**

Header (required, verbatim):

> Amends ADR-014 (extends the uniqueness key for pinned captures). Supersedes the parent-conflict behavior established in the ADR-021 implementation, under which a differing parent produced `pin_conflict`.

The pointer lives in the header, not inside clause 9, so a reader arriving at ADR-021 alone finds the amendment instead of a stale rule.

Must record:

1. Hybrid CLI (`--as task --project <id>`) is the creation path for planning tasks.
2. Pin overrides classifier; parent is project-only and system-written as `project:`.
3. Provenance required on task notes.
4. Link rules: planning tasks may file with `links: []`; parent resolve-at-file.
5. File routing into `vault/tasks/`.
6. Disambiguation: typed-note `candidate_type: task` ≠ planning task entity.
7. Narrows ADR-021 (tasks were out of scope there).
8. Does not authorize outcomes, dependencies, decompose-project, agents, MCP, or external task managers.
9. **Intake uniqueness key (data-layer assertion — must land in this ADR before code).** The uniqueness key for **all** intakes is `content_hash + type_pin + parent_id`, where `parent_id` is the typed parent pin: the empty-string sentinel for unpinned captures **and for goals** (a goal has no parent), the goal id for projects, the project id for tasks. Identical text under two different parents is **two distinct captures**, not a replay. `pin_conflict` is reserved for identical `content_hash + parent_id` submitted under a differing `type_pin`. The constraint is a plain `UNIQUE(content_hash, type_pin, parent_id)`.

    `type_pin` and `parent_id` are `NOT NULL`; unpinned captures store the empty string in both. NULL is never used in the uniqueness key, because SQLite treats NULLs as distinct and a nullable column would silently disable replay protection for unpinned captures.

    Sentinels are chosen over a `COALESCE`/generated column deliberately: a generated column moves the semantics into an expression that no longer appears in the constraint a reader inspects, so `UNIQUE(content_hash, uniq_key)` cannot be understood without chasing the definition. The sentinel keeps the key legible where it is defined, and `NOT NULL` makes the hazard structurally impossible rather than handled — the same move as `notes/proposed/` over a status convention. It is also portable across the ADR-002 seam: Postgres `NULLS NOT DISTINCT` is version-gated and differs from SQLite's default, while a sentinel behaves identically on both.

    `intake` has no pin columns today, so the migration adds `type_pin` and `parent_id` as `NOT NULL DEFAULT ''`: the sentinel arrives as the column default and there is no backfill step at all. (SQLite cannot drop a column-level `UNIQUE`, so removing `UNIQUE(content_hash)` requires the drop/recreate/refill rebuild that migration 006 established; in that form existing rows take `''` from the refill `SELECT` rather than from the declared default. Either way the migration contains no backfill statement.) Verified against the current store: three intake rows, `content_hash` unique, zero duplicates and no parent-differing captures — so replacing the single-column constraint with `UNIQUE(content_hash, type_pin, parent_id)` requires no reconciliation.

    Putting the pin in a UNIQUE constraint promotes it from metadata to enforced state, so the ADR must bound what those columns are. The intake pin columns are a **derived projection** of the pin, written in the same transaction as the intake row; evidence meta remains the immutable record of what was captured (ADR-003). Divergence between the projection and evidence meta is a **fail-closed refusal**, never a silent preference for either side.

    The projection's scope is deliberately narrow: it exists for the uniqueness constraint and for consistency checks. **Every behavioral read of the pin — routing, rendering, parent resolution — takes it from evidence, never from the projection.** A migration runner reads `*.sql` only and cannot open evidence, so rows captured before ADR-022 carry the sentinel even where their evidence records a pin. Those rows therefore diverge by construction, and a replay that touches one fails closed rather than being read as a duplicate. Repairing a legacy row is a documented one-statement `UPDATE`, not a code path in this slice; no filed note is affected, because filing reads the pin from evidence.

    **The two divergences carry different reason codes**, because they call for different human responses and an operator should not have to guess which one they are looking at:

    | Row state versus evidence | Reason | Meaning |
    |---|---|---|
    | Both projection columns hold the sentinel while evidence records a pin | `intake_pin_unprojected` | A pre-ADR-022 row the migration could not project. Expected on any store that predates this ADR; repair with the documented `UPDATE`. |
    | The projection holds a pin that differs from evidence | `state_evidence_mismatch` | Corruption or a hand edit. Nothing about the migration produces this; investigate before repairing. |
    | The projection is populated in one column and sentinel in the other while evidence populates both | `state_evidence_mismatch` | **Deliberately classed as tampering.** The migration writes the sentinel to every column at once and the application writes both from evidence in one transaction, so no Metis code path produces a half-projected row. The likeliest cause is a repair `UPDATE` run halfway, which is exactly the state an operator should be told to investigate rather than re-run. |

    All three refuse and write nothing. The table is total over the reachable shapes: no combination of projection and evidence is left unassigned. Reusing one code for all of them would make a migration artifact indistinguishable from tampering.
10. **Planning status is lifecycle, not authorization.** On a filed task, `status: open` is a lifecycle field. Obsidian edits to it are **inert** in this slice (Metis does not treat them as approval, rejection, or progress). Approval remains the draft `status` flip recorded in SQLite + audit (ADR-005 / ADR-020). CLI transitions of planning status are deferred to a later ADR.
11. **Filing routes by the pin, not by the note type.** The vault stage is selected from `evidence.type_pin`: `goal` → `vault/goals/`, `project` → `vault/projects/`, `task` → `vault/tasks/`, no pin → `vault/notes/filed/`. This is the mechanical form of the §3.1 disambiguation, which otherwise exists only as prose: a classifier may legitimately return `candidate_type: task` for an ordinary note, so `note_type` cannot distinguish a typed task from a planning task and must not be the routing input. Audit detail for a filing takes `effective_type` from the same input.

    The pin also selects the parent contract, which is three-way and total — every pin has a rule, and a pin with no rule is a refusal, not a default:

    | Pin | Parent | Resolved against | Refusal reason |
    |---|---|---|---|
    | `goal` | none — a goal has nothing above it | — | a parent supplied with a goal pin is refused at capture (`pin_incomplete`) |
    | `project` | required | `vault/goals/` | `filing.parent_goal_unresolvable` |
    | `task` | required | `vault/projects/` | `filing.parent_project_unresolvable` |

    Resolving a task's parent only against `vault/projects/` is what makes a goal id an unresolvable parent rather than a silently accepted one.

**Migration detail (supports clause 9).** One migration adds `type_pin` and `parent_id` as `NOT NULL DEFAULT ''` and replaces `UNIQUE(content_hash)` with `UNIQUE(content_hash, type_pin, parent_id)`. Existing rows take the sentinel from the column default; no separate backfill statement is needed, and pre-existing planning captures keep their pin of record in evidence meta. New writes populate the projection from the pin in the intake transaction (clause 9).

Ship as **ADR-only PR** on `adr/022-planning-task-capture`, merge before `step/09-planning-tasks` (or equivalent) code. Clauses 9–10 (and the ADR header supersession line + migration paragraph) are constraints and **must** appear in that ADR-only PR; the §9 tests and the verification-exemption line in §3 are evidence and may ride with the implementation PR.

## 9. Tests (minimum)

- `capture_as_task_requires_project_flag`
- `capture_as_task_records_the_pin_and_parent_project`
- `task_pin_overrides_the_model_candidate_type`
- `a_pinned_task_proposes_a_draft_naming_its_parent_project`
- `editing_a_tasks_parent_project_is_refused`
- `a_pinned_task_files_under_vault_tasks_with_empty_links`
- `a_task_whose_parent_project_is_not_filed_is_refused`
- `a_task_may_not_name_a_goal_as_its_parent`
- `an_unapproved_planning_task_is_refused`
- `duplicate_replay_creates_one_planning_task`
- `a_planning_task_without_provenance_fails_validation`
- `same_text_under_two_projects_creates_two_tasks` — proves §1.5/§1.6 uniqueness (distinct parents ⇒ distinct captures)
- `conflicting_pin_on_identical_text_is_refused` — proves §1.6 `pin_conflict` (same hash + parent, differing `type_pin`)
- `project_flag_without_task_pin_writes_no_evidence` — proves §4 usage error (no evidence write)
- `status_edit_on_filed_task_is_inert` — proves §8 clause 10 (lifecycle edit does not authorize or transition intake)
- `planning_task_and_typed_note_task_are_distinguishable` — proves §3.1 / §8 clause 6
- `task_filing_emits_audit_with_effective_type_and_parent` — proves §1.7 audit detail (required fields)
- `duplicate_plain_capture_still_creates_one_capture` — regression: composite key must not break unpinned replay (NULL/sentinel hazard in §8 clause 9). It asserts one intake, not one note; the founding end-to-end `duplicate_replay_creates_one_note` already exists and must stay green as the filed half
- `intake_pin_columns_match_evidence_meta` — proves §8 clause 9: the projection is written in the intake transaction and a projection that contradicts evidence is refused as `state_evidence_mismatch`, not preferred away
- `v2_evidence_reads_its_parent_goal_as_parent_id` — proves the single read-time mapping site: immutable v2 meta (`parent_goal_id`) is read as `parent_id` without being rewritten (ADR-003)
- `a_legacy_unprojected_planning_row_fails_closed` — proves §8 clause 9's migration consequence: a pre-ADR-022 row carrying the sentinel while its evidence records a pin is refused as `intake_pin_unprojected`, never read as a duplicate and never confused with tampering
- Existing goal/project and step 5–7 suites stay green (update parent-differing replay tests to match §8 clause 9; keep founding `duplicate_replay_creates_one_note` green)

These ten additions (and the verification-exemption line in §3) may land in the implementation PR as evidence. The uniqueness key, NULL/sentinel rule, projection-vs-evidence rule, pin-driven routing (clause 11), ADR header supersession line, and migration paragraph in §8 may not — they must be asserted by ADR-022 first.

## 10. Ledger / docs updates (same implementation PR)

- Amend `METIS-SCHEMAS.md`: new § for Task; vault layout; filing routes; disambiguation vs typed `task`.
- Update `AGENTS.md` commands + build order step 9 progress.
- Requirement rows: extend REQ-VLT-002 (and related intake/link rows) to Verified only when named tests prove task filing; never in a follow-up-only commit.

## 11. Explicit non-goals

- Outcomes as first-class notes
- Task↔task dependency tables or enforced graphs (ADR-018: markdown links sufficient until proven otherwise)
- `decompose-project` proposing multiple tasks in one approval
- Auto-creating a parent project when missing
- Calendar / Todoist / external writes
- Runtime agents executing tasks
- Treating Obsidian edits to filed planning `status` as Metis approval or progress (inert this slice; §8 clause 10)
- Declaring planning-status values this slice cannot produce (`in_progress` / `done` / `cancelled`)
- CLI transitions of planning status (later ADR)
- Requiring `verification:` on planning entities (exempt; §3)
- Inventing NULL in the uniqueness key (breaks unpinned replay; §8 clause 9)
- Routing on `proposal.note_type` (collapses typed tasks into planning tasks; §8 clause 11)
- Rewriting v2 evidence meta to the new key name (ADR-003: evidence is never rewritten — map it on read)
- Building repair tooling for legacy unprojected rows (documented `UPDATE`; later slice if it ever recurs)
- Leaving audit `detail` optional for task filings (§7 / §1.7: required)

## 12. Implementation order (after ADR-022 merges)

1. Failing tests for CLI flags + pin + parent persistence + uniqueness cases in §9
2. Data-layer uniqueness for pinned captures per ADR-022 §8 clause 9 (migration; cannot invent in app-only logic)
3. Capture metadata + classify pin override for `task`
4. Propose/render type-aware frontmatter (`project:` system field; `status: open` only; no `verification` on planning notes)
5. File router + id allocation + parent resolve + provenance validation + `tasks` store stage + audit detail
6. Ledger + schema + AGENTS commands
7. Draft PR; prefer merge commit; tag `step-09-planning-tasks-verified` only after acceptance tests pass on `main`

## 13. Resolved design points

1. **Parent:** project only (`--project`); not goal-or-project; not soft links.
2. **Directory:** `vault/tasks/`.
3. **Planning status:** `open` only in this slice; lifecycle not authorization; Obsidian edits inert; CLI transitions deferred.
4. **Classification:** still runs under pin; pin forces type only.
5. **Parent visibility on draft:** yes — system-written `project:` on the proposed note; not editable.
6. **Slug:** title-slug + 8 hex of capture_id (machine uniqueness, same family as goals/projects).
7. **Intake uniqueness:** plain `UNIQUE(content_hash, type_pin, parent_id)`; both pin columns `NOT NULL DEFAULT ''` (never NULL, never a generated column); one migration, no backfill, no reconciliation; pin columns are a derived projection with evidence meta as the record of truth and divergence refused; amends ADR-014 and supersedes the ADR-021 implementation's parent-conflict behavior.
8. **Verification:** planning entities exempt from REQ-DATA-005's `verification` field.
9. **Projection scope:** the intake pin columns serve uniqueness and consistency only; routing, rendering, and parent resolution read the pin from evidence (ADR-022 clause 9).
10. **Routing input:** the pin, never `note_type` (ADR-022 clause 11) — this is what keeps a classifier-typed `task` out of `vault/tasks/`.

## 14. Later slices (not this PR)

- **B2:** Outcomes (schema + pin or other creation path — separate ADR).
- **B3:** Dependencies / next-actions views once a real task corpus exists.
- **B4:** Decompose-project (multi-note proposals under one authority decision — new ADR).
- Phase 7 review tooling when the corpus justifies it.
- Execution-spine ADR only when durable approved runs are needed; agents remain Layer C workers.
