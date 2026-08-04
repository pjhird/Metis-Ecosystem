# Phase 6 Slice A — Planning Notes (Hybrid Capture)

## Status

- Blueprint phase: 6 — Connect Goals, Projects, and Tasks (first slice only)
- Implementation status: **design — awaiting owner approval**
- Base: verified `main` at merge commit `8fab433` (step 7 audit merged; tag `step-07-audit-verified`)
- Approach chosen: **Hybrid** — one intake/approve/file pipeline; human pins `goal` / `project` at capture via `--as`
- Governing decisions today: ADR-001 … ADR-020 (esp. ADR-003, ADR-004, ADR-005, ADR-007, ADR-013, ADR-015, ADR-020)
- **Blocked on:** a new ADR (proposed number **ADR-021**) recording planning-note creation rules before any code
- Out of scope for this slice: outcomes, tasks, dependencies, decompose-project, weekly review, calendar writes

## 1. Objective

Give Metis a governed way to create **Goal** and **Project** notes so a clean vault is not permanently dependent on hand-authored planning files, while keeping a single approval surface and a single orchestrated loop.

Acceptance for this slice:

1. `metis capture --as goal "…"` (after classify → propose → human `status: approved` → `metis file`) writes exactly one note under `vault/goals/` whose `id` / frontmatter match `METIS-SCHEMAS` §4.1 **plus** provenance (`capture_id`, `evidence`).
2. `metis capture --as project --goal <goal.id> "…"` files exactly one note under `vault/projects/` matching §4.2 plus provenance, with `goal: "[[…]]"` resolving to an existing goal.
3. Plain `metis capture "…"` (no `--as`) still produces typed notes under `vault/notes/filed/` and still requires ≥1 resolvable link (unchanged).
4. Replay of the identical capture creates no second planning note.
5. Unapproved write is refused; audit events continue to emit per step 7.

## 2. Why hybrid (not pure loop, not dedicated commands)

| Approach | Verdict for this slice |
|---|---|
| (1) Classifier invents `goal`/`project` | Rejected — model must not choose planning identity |
| (2) `metis propose-goal` / `propose-project` | Deferred — useful aliases later; more CLI surface now |
| (3) **Hybrid: `capture --as` pin** | **Chosen** — one pipe, human declares planning intent at the door |

Optional later: thin aliases `metis propose-goal` → `capture --as goal` without a second pipeline.

## 3. CLI contract

```text
metis capture "<text>"
metis capture --as goal "<text>"
metis capture --as project --goal <goal-id> "<text>"
```

Rules:

- `--as` accepts only `goal` or `project` in this slice (not `idea` / etc. — those stay classifier-owned).
- `--as project` **requires** `--goal <id>` where `<id>` matches `[A-Za-z0-9._-]+` and, at **file** time, resolves to an existing note in `vault/goals/`.
- `--goal` without `--as project` is a usage error (exit non-zero, no evidence write).
- `--as` is recorded in capture metadata / evidence `meta.json` before any model call (ADR-003 ordering preserved).
- Classification still runs for sensitivity/confidence **unless** a later ADR skips it; it **must not override** a pinned type. If the model returns a conflicting `candidate_type`, the pin wins and the raw model response is preserved as today.

## 4. Type pin and classification

New durable field (name in ADR/schema): `type_pin` on the intake or capture metadata:

| Capture form | `type_pin` | `classification.candidate_type` used downstream |
|---|---|---|
| plain | `null` | model output (existing `ROUTING`) |
| `--as goal` | `goal` | forced `goal` |
| `--as project` | `project` | forced `project` |

`ROUTING` gains `goal` and `project` entries (routing labels TBD in implementation; must be stable and tested).

Propose reads the effective note type = pin if present else classification.candidate_type.

## 5. Draft and human edits (ADR-020 unchanged)

Drafts remain in `vault/notes/proposed/note.<capture_id>.md` for all types (one approval inbox).

Human-editable fields remain **only** `status` and `links` (ADR-020).

| Effective type | `links` at approve/file |
|---|---|
| typed note (idea, …) | ≥1 resolvable goal or project link (existing rule) |
| `goal` | may be `links: []` — first goals have no parent |
| `project` | must include the pinned `--goal` target as a resolvable `[[goal.…]]` link **or** carry an equivalent `goal:` field derived deterministically from `--goal` at propose time (byte-exact; not human-editable). Prefer **system-written `goal:`** from `--goal` so humans do not re-type the parent; `links` for projects may stay `[]` or also list the goal — pick one in implementation and test it; default: **`goal:` field from pin, `links: []` allowed for projects**. |

`status` remains the only authorizing field.

## 6. Filing routes by effective type

`metis file <capture-id>` keeps one command. Destination:

| Effective type | Path |
|---|---|
| `goal` | `vault/goals/<id>.md` |
| `project` | `vault/projects/<id>.md` |
| other | `vault/notes/filed/note.<capture_id>.md` (unchanged) |

Id generation (deterministic, tested):

- Goals: `goal.<slug-from-title-or-stable-suffix>` — must be unique; collision fails closed.
- Projects: `proj.<slug-…>` — same.
- Exact slug algorithm is an implementation detail but must be pure given title + capture_id (include capture_id or a hash fragment if needed to guarantee uniqueness without a probe loop that races).

Frontmatter:

- Goals/projects include §4.1 / §4.2 fields **and** `capture_id` + `evidence` (provenance). This narrows the current schema text that omits provenance on planning notes — recorded in ADR-021.
- `status` on filed goal/project uses planning vocabulary (`active`, …), not `approved`. The approval decision is recorded in SQLite + audit; the note’s planning `status` defaults to `active` at file time.
- Typed notes keep `status: approved` as today.

## 7. State machine and audit

No new intake states. Same path: `captured → … → approved → filed`.

Audit actions stay on the existing vocabulary; `detail` JSON may include `effective_type` and `type_pin` for planning files. No new approval surface.

## 8. ADR-021 (required before code)

Title (draft): **Planning notes are created through pinned capture, not by hand-only bootstrap**

Must record:

1. Hybrid CLI (`--as goal|project`) is the creation path for goals/projects.
2. Pin overrides classifier.
3. Provenance required on goal/project notes.
4. Link rules by type (goal may file with empty links; project requires existing parent goal via `--goal`).
5. File routing by type into `vault/goals/` / `vault/projects/`.
6. Narrows ADR-015 (capture remains the first input type; flags extend it) and the schema note “Metis does not create goal or project notes.”
7. Does not authorize outcomes/tasks/agents/MCP.

Ship as **ADR-only PR** on `adr/021-planning-note-capture`, merge before `step/08-planning-notes` (or equivalent) code.

## 9. Tests (minimum)

- `capture_as_goal_files_under_vault_goals`
- `capture_as_project_requires_goal_flag`
- `capture_as_project_files_under_vault_projects_with_parent_goal`
- `pin_overrides_classifier_candidate_type`
- `plain_capture_still_requires_links_to_file`
- `goal_may_file_with_empty_links`
- `unapproved_planning_note_is_refused`
- `duplicate_replay_creates_one_planning_note`
- `planning_note_without_provenance_fails_validation`
- Existing step 5–7 suite stays green

## 10. Ledger / docs updates (same implementation PR)

- Amend `METIS-SCHEMAS.md` §4.1, §4.2, §4.3 (remove “Metis does not create…”; add provenance + routing).
- Update `AGENTS.md` current phase: MVP complete; Phase 6 slice A in progress; commands list for `capture --as`.
- Move/add requirements as evidence lands (likely Partial → Verified for vault goal/project schema rows once tests exist).

## 11. Explicit non-goals

- `metis propose-goal` as a separate pipeline
- Classifier-selected goals without `--as`
- Outcomes, tasks, dependencies, decompose-project
- Auto-creating a parent goal when missing
- Watchers, UI, agents, external task managers
- Editing planning `status` (active/achieved/…) through Obsidian as an approval signal — out of scope; approval remains draft `status: proposed|approved|rejected`

## 12. Implementation order (after ADR-021 merges)

1. Failing tests for CLI flags + pin persistence
2. Capture metadata + classify pin override
3. Propose/render type-aware frontmatter
4. File router + id allocation + provenance validation
5. Ledger + schema + AGENTS commands
6. Draft PR; tag only after merge when acceptance tests pass

## 13. Open points (resolve in ADR or first implementation PR, not silently)

1. Slug algorithm specifics (title-only vs title+capture fragment).
2. Whether classification is skipped entirely for `--as` pins (recommend: **still run** for sensitivity/confidence; pin only forces type).
3. Whether project drafts expose `goal:` in the proposed note for human visibility (recommend: **yes**, system-written, not editable under ADR-020).
