# Phase 6 Slice B — First-Class Planning Tasks

## Status

- Blueprint phase: 6 — Connect Goals, Projects, and Tasks (second slice)
- Implementation status: **design — owner approved direction 2026-08-04; awaiting formal spec review**
- Base: `main` through `step-08-planning-notes-verified` (ADR-021 merged; docs honesty PR #13 merged)
- Approach chosen: **Hybrid pin** — `metis capture --as task --project <proj.id>`; same capture → classify → propose → approve → file → audit loop
- Parent contract: **project only** (mirrors project → goal)
- Governing decisions today: ADR-001 … ADR-021 (esp. ADR-003, ADR-004, ADR-005, ADR-007, ADR-018, ADR-020, ADR-021)
- **Blocked on:** a new ADR (proposed number **ADR-022**) recording planning-task creation rules before any code
- Out of scope for this slice: outcomes, task↔task dependencies, decompose-project, calendar/task-manager writes, agents, execution spine, CLI-driven planning-status transitions

## 1. Objective

Give Metis a governed way to create **Task** notes that hang under an existing **Project**, completing the usable planning spine `goal → project → task` without a second approval surface or trusting the classifier to invent planning identity.

Acceptance for this slice:

1. `metis capture --as task --project <project-id> "…"` (after classify → propose → human `status: approved` → `metis file`) writes exactly one note under `vault/tasks/` whose frontmatter matches §3 below **plus** provenance (`capture_id`, `evidence`).
2. The parent is system-written as `project: "[[…]]"` from the capture pin and must resolve at file time to an existing note in `vault/projects/` with `type: project`.
3. Missing parent, wrong-type parent (e.g. a goal id), or unapproved write → `refused`; vault unchanged.
4. Plain `metis capture` (no `--as`) and goal/project pins remain unchanged.
5. Replay of the identical capture (same text + same pin) creates no second task note.
6. Same text with a conflicting pin → `pin_conflict` (same rule as ADR-021 goals/projects).
7. Audit continues to emit per step 7; unapproved write remains refused.

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

Created through `metis capture --as task --project <project-id>` → classify → propose → approve → `metis file`. Filed under `vault/tasks/` with deterministic id `task.<title-slug>-<8 hex of capture_id>` (same uniqueness strategy as goals/projects). Planning `status` defaults to `open` at file time; the approval decision lives in SQLite + audit, not in this field.

```yaml
---
id: task.weekly-weigh-in-7d4e8eb8
type: task
title: Do the weekly weigh-in
status: open              # open · in_progress · done · cancelled
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
- Editing planning `status` (`open` / `done` / …) through Obsidian is **not** an approval signal in this slice; approval remains draft `status: proposed|approved|rejected`.

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

Ship as **ADR-only PR** on `adr/022-planning-task-capture`, merge before `step/09-planning-tasks` (or equivalent) code.

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
- Existing goal/project and step 5–7 suites stay green

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
- Treating Obsidian edits to planning `status` (`open`→`done`) as Metis approval
- Changing plain-capture behavior or goal/project contracts

## 12. Implementation order (after ADR-022 merges)

1. Failing tests for CLI flags + pin + parent persistence
2. Capture metadata + classify pin override for `task`
3. Propose/render type-aware frontmatter (`project:` system field)
4. File router + id allocation + parent resolve + provenance validation + `tasks` store stage
5. Ledger + schema + AGENTS commands
6. Draft PR; prefer merge commit; tag `step-09-planning-tasks-verified` only after acceptance tests pass on `main`

## 13. Resolved design points

1. **Parent:** project only (`--project`); not goal-or-project; not soft links.
2. **Directory:** `vault/tasks/`.
3. **Planning status default:** `open` (vocabulary: `open · in_progress · done · cancelled`).
4. **Classification:** still runs under pin; pin forces type only.
5. **Parent visibility on draft:** yes — system-written `project:` on the proposed note; not editable.
6. **Slug:** title-slug + 8 hex of capture_id (machine uniqueness, same family as goals/projects).

## 14. Later slices (not this PR)

- **B2:** Outcomes (schema + pin or other creation path — separate ADR).
- **B3:** Dependencies / next-actions views once a real task corpus exists.
- **B4:** Decompose-project (multi-note proposals under one authority decision — new ADR).
- Phase 7 review tooling when the corpus justifies it.
- Execution-spine ADR only when durable approved runs are needed; agents remain Layer C workers.
