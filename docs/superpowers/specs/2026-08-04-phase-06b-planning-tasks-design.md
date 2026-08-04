# Phase 6 Slice B — First-Class Planning Tasks

## Status

- Blueprint phase: 6 — Connect Goals, Projects, and Tasks (second slice)
- Implementation status: **design — owner review amendments 2026-08-04 applied; awaiting formal spec approval**
- Base: `main` through `step-08-planning-notes-verified` (ADR-021 merged; docs honesty PR #13 merged)
- Approach chosen: **Hybrid pin** — `metis capture --as task --project <proj.id>`; same capture → classify → propose → approve → file → audit loop
- Parent contract: **project only** (mirrors project → goal)
- Pinned uniqueness: **`content_hash + type_pin + parent_id`** (ADR-022 clause 9; extends ADR-014)
- Governing decisions today: ADR-001 … ADR-021 (esp. ADR-003, ADR-004, ADR-005, ADR-007, ADR-018, ADR-020, ADR-021)
- **Blocked on:** a new ADR (proposed number **ADR-022**) recording planning-task creation rules before any code
- Out of scope for this slice: outcomes, task↔task dependencies, decompose-project, calendar/task-manager writes, agents, execution spine, CLI-driven planning-status transitions

## 1. Objective

Give Metis a governed way to create **Task** notes that hang under an existing **Project**, completing the usable planning spine `goal → project → task` without a second approval surface or trusting the classifier to invent planning identity.

Acceptance for this slice:

1. `metis capture --as task --project <project-id> "…"` (after classify → propose → human `status: approved` → `metis file`) writes exactly one note under `vault/tasks/` whose frontmatter matches §3 below **plus** provenance (`capture_id`, `evidence`).
2. The parent is system-written as `project: "[[…]]"` from the capture pin and must resolve at file time to an existing note in `vault/projects/` with `type: project`.
3. Missing parent, wrong-type parent (e.g. a goal id), or unapproved write → `refused`; vault unchanged.
4. Plain `metis capture` (no `--as`) remains unchanged for unpinned intake. Goal/project pins keep their creation paths; pinned-capture uniqueness is restated by ADR-022 §8 clause 9 (extends ADR-014; revises today's "different parent ⇒ pin_conflict" application behavior).
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

A **planning task** is a different entity: `type_pin=task`, files under `vault/tasks/`, carries `project:`, and is never created by plain capture. The ADR must state this disambiguation explicitly so schemas and routing do not collapse the two.

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

Audit `detail` JSON may include `effective_type`, `type_pin`, and `parent_project_id` for task filings.

## 8. ADR-022 (required before code)

Title (draft): **Planning tasks are created through pinned capture under a project**

Must record:

1. Hybrid CLI (`--as task --project <id>`) is the creation path for planning tasks.
2. Pin overrides classifier; parent is project-only and system-written as `project:`.
3. Provenance required on task notes.
4. Link rules: planning tasks may file with `links: []`; parent resolve-at-file.
5. File routing into `vault/tasks/`.
6. Disambiguation: typed-note `candidate_type: task` ≠ planning task entity.
7. Narrows ADR-021 (tasks were out of scope there).
8. Does not authorize outcomes, dependencies, decompose-project, agents, MCP, or external task managers.
9. **Pinned-capture uniqueness (extends ADR-014; must land in this ADR before code).** For pinned captures the intake uniqueness key is `content_hash + type_pin + parent_id` (where `parent_id` is the typed parent pin: none for goals, goal id for projects, project id for tasks). Identical text captured under two different parents is **two distinct captures**, not a replay. `pin_conflict` is reserved for identical `content_hash + parent_id` submitted under a differing `type_pin`. This is a data-layer schema assertion (ADR-014 places uniqueness in the state store); the implementation plan cannot invent or absorb it. It revises the ADR-021-era application behavior that treated a different parent as `pin_conflict`.
10. **Planning status is lifecycle, not authorization.** On a filed task, `status: open` is a lifecycle field. Obsidian edits to it are **inert** in this slice (Metis does not treat them as approval, rejection, or progress). Approval remains the draft `status` flip recorded in SQLite + audit (ADR-005 / ADR-020). CLI transitions of planning status are deferred to a later ADR.

Ship as **ADR-only PR** on `adr/022-planning-task-capture`, merge before `step/09-planning-tasks` (or equivalent) code. Clauses 9–10 are constraints and **must** appear in that ADR-only PR; the §9 tests and the verification-exemption line in §3 are evidence and may ride with the implementation PR.

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
- `task_filing_emits_audit_with_effective_type_and_parent` — proves §1.7 audit detail
- Existing goal/project and step 5–7 suites stay green (update parent-differing replay tests to match §8 clause 9)

These six added tests (and the verification-exemption line in §3) may land in the implementation PR as evidence. The uniqueness key in §8 clause 9 may not — it must be asserted by ADR-022 first.

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
- Changing plain-capture (unpinned) uniqueness — remains content-hash-only per ADR-014

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
7. **Pinned uniqueness:** `content_hash + type_pin + parent_id` (ADR-022 §8 clause 9; extends ADR-014).
8. **Verification:** planning entities exempt from REQ-DATA-005's `verification` field.

## 14. Later slices (not this PR)

- **B2:** Outcomes (schema + pin or other creation path — separate ADR).
- **B3:** Dependencies / next-actions views once a real task corpus exists.
- **B4:** Decompose-project (multi-note proposals under one authority decision — new ADR).
- Phase 7 review tooling when the corpus justifies it.
- Execution-spine ADR only when durable approved runs are needed; agents remain Layer C workers.
