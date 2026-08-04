# Metis — Repository Instructions

Metis is a personal knowledge, agent, and execution operating system. It captures information, turns it into
reviewed knowledge, connects it to goals and projects, and executes repeatable workflows — without ever
writing permanently to knowledge or acting externally without human approval.

This file governs all work in this repository, whatever tool is driving.

## Read before building

- `METIS-MASTER-PROMPT.md` — the governing product source
- `METIS-EXECUTION-BLUEPRINT.md` — execution strategy and phased roadmap
- `METIS-DECISIONS.md` — architecture decisions (ADR-001 … ADR-021). **Binding.**
- `METIS-SCHEMAS.md` — the information and state model
- `METIS-REQUIREMENT-LEDGER.md` — what is required, what is missing, what is deferred

(If these later move into a `docs/` directory when the code repository is created, update these paths.)

If this file and the master prompt appear to conflict, stop and ask. Do not harmonize silently.

## Current phase

MVP loop (build-order steps 1–7) is complete and tagged through `step-07-audit-verified`.

**Phase 6 slice A — planning notes (hybrid)** is complete and tagged `step-08-planning-notes-verified`
(ADR-021): `metis capture --as goal|project` pins planning intent before any model call; the same
propose → approve → file → audit loop files under `vault/goals/` or `vault/projects/` with provenance.

Next product work is **Phase 6 slice B** (outcomes, tasks, dependencies) — design before code. Prefer
real vault use and a growing corpus before Phase 7 review tooling. No containers. No external
integrations. No agents. No execution-spine code until an adopted ADR.

MVP acceptance (still the floor for every change):

> A typed idea is preserved immutably, classified with visible confidence, turned into a schema-valid
> proposal, surfaced in Obsidian as a draft with `status: proposed`, and — only after a human changes that to
> `approved` — filed as a typed note with provenance, linked to an existing goal or project, and recorded in
> the audit log. Replaying the identical input creates no second note.

## Non-negotiable rules

1. **Never write permanent knowledge without a recorded human approval.** No confidence threshold, no
   convenience path, no exception. (ADR-004)
2. **Write evidence before interpreting it.** The raw input is stored and hashed before classification runs,
   and is never modified afterward. (ADR-003)
3. **Fail closed.** If permission, provenance, approval state, or the write outcome is undetermined, stop and
   create a visible review item. Silence is never consent.
4. **Never report success that did not happen.** A partial, failed, or unverified operation is recorded as
   such. `refused` is a valid, successful outcome — record it, do not treat it as an error.
5. **The orchestrator owns every state transition.** Skills return bounded results. Skills never call other
   skills, never touch persistence directly, and never decide their own authority. (ADR-007)
6. **Only the data-access layer contains SQL.** Everything else talks to its interface. (ADR-002)
7. **Only the model adapter talks to a provider.** Exactly one module imports a provider SDK. (ADR-008)
8. **Secrets never enter the repository, the vault, logs, or the state database.** Environment or keychain
   only. (ADR-017)
9. **Obsidian is the sole approval surface.** Do not build a second way to approve anything. (ADR-005)
10. **Never claim a test passed unless it was run.** If tests could not run, say so, say what is unverified,
    and give the exact command that would verify it.

## Architecture in one screen

```
INTERFACE     CLI capture  ·  Obsidian vault (knowledge + approval)  ·  approval command
                                        │
CONTROL       Deterministic orchestrator — routes, checks idempotency, enforces
              permissions, gates on approval, emits audit events, fails closed
                                        │
CAPABILITY    capture · classify · propose · validate · write-note   ·   model adapter
                                        │
PERSISTENCE   evidence/ (immutable files)  ·  SQLite (via data layer)  ·  git
```

Control flows down. The only path back up runs through a human.

## Coding standard

Condensed from master prompt §33. It applies to every change.

**Before writing code.** Inspect the relevant files. Find existing conventions. State material assumptions.
Ask one focused question only if missing information would change the implementation. Do not invent context.

**Simplicity.** Write the minimum that satisfies the request. No speculative abstraction, no single-use
abstraction layers, no premature extensibility, no error handling for impossible conditions. If a smaller
implementation works, use it.

**Surgical changes.** Touch only what is required. Do not refactor, reformat, or clean up unrelated code.
Mention unrelated problems rather than fixing them. Every changed line should trace to the request, a required
test, a required dependency, or cleanup caused by your own change.

**Testing.** Reproduce bugs with a failing test first. Add tests for new behavior. Test observable behavior,
not implementation details. Run the narrowest relevant suite first.

**Verification before claiming done.** Requested behavior exists · acceptance criteria met · tests pass ·
schemas validate · no unintended files changed · no secrets introduced.

**Dependencies.** Confirm the existing stack cannot solve it. Explain why the dependency is necessary. Do not
add a framework for a small feature.

**Completion report.** What changed, why, files affected, checks run, results, remaining limitations,
unverified items. Do not describe work that was not performed.

## Git workflow

Git is the approval gate and audit trail for code, exactly as Obsidian is for knowledge. (ADR-019)

**Branches.** `main` is protected — never push to it directly. One task, one branch, one tool at a time.

```
step/NN-name      work from the build order below
adr/NNN-name      a new architecture decision — ADR only, no code
fix/short-name    a defect
docs/short-name   documentation
```

For two tools at once use `git worktree add ../metis-<branch> <branch>`. Never run two tools in one working tree.

**Commits carry the trace.** Any commit implementing a requirement includes trailers:

```
feat(capture): write evidence before classification runs

Evidence and meta.json are written and hashed before any model call,
so a downstream failure cannot lose the input.

Requirement: REQ-INTK-001
Decision: ADR-003
Test: test_evidence_written_before_classify
Co-Authored-By: Codex <codex@openai.com>
```

This makes `git log --grep=REQ-INTK-001` the full history of that requirement, and tells a future session —
which has no memory of this one — which tool wrote what and why.

**A new architecture decision arrives as its own pull request containing only the ADR.** It is merged before
any code implementing it is written. Never implement a decision that has not been recorded and merged.

**A pull request that satisfies a requirement updates `METIS-REQUIREMENT-LEDGER.md` in the same pull request** —
moving the row to Verified and naming the test that proves it. Never mark a requirement Verified in a
follow-up commit.

**Never commit:** the state database, the evidence store, `.env`, credentials, vault content, or generated
artifacts. Add ignore rules before those files exist, not after.

**Tag a build step when its acceptance test passes:**
`git tag -a step-NN-<name>-verified -m "<REQ IDs> verified"`.

**Governance files** — `AGENTS.md`, `METIS-DECISIONS.md`, `METIS-MASTER-PROMPT.md` — are covered by
`CODEOWNERS`. Changing a non-negotiable rule requires a merged ADR first.

## Required tests

No capability ships without these. They are the reason this system can be trusted.

- `unapproved_write_is_refused` — the note writer refuses without an approval record
- `duplicate_replay_creates_one_note` — identical input twice produces one permanent note
- `source_survives_classification_failure` — evidence intact when downstream fails
- `illegal_state_transition_is_rejected` — one test per illegal edge in the state machine
- `note_without_provenance_fails_validation` — no `capture_id` or `evidence` means invalid
- `unresolvable_link_blocks_commit` — no orphan links
- `secret_never_appears_in_logs_or_notes`
- `sql_appears_only_in_data_layer`
- `provider_sdk_imported_only_by_adapter`

## Build order

Do not skip ahead. Each step ships with its tests before the next begins.

1. Repository skeleton, data-access layer, schema migrations, test harness — done
2. Capture — evidence store, hashing, capture ID, replay protection — done
3. Classify — model adapter, prompt versioning, confidence, raw-response preservation — done
4. Propose — proposal record, draft note written to `vault/notes/proposed/` — done
5. Approve — the approval command reads status, records the decision — done
6. File — note committed to `vault/notes/filed/` with provenance and links — done
7. Audit — every transition emits an event; end-to-end acceptance test — done
8. Planning notes — `capture --as goal|project`, type-aware filing to `vault/goals/` / `vault/projects/` (ADR-021) — done
9. Outcomes / tasks / dependencies — Phase 6 slice B; design before code — not started

## Do not build yet

Containers · Postgres · OpenWebUI or any second interface · MCP or external integrations · runtime agents ·
agent or skill registries · vector or graph databases · a background file-watcher · autonomous anything ·
outcomes, tasks, dependencies, or decompose-project.

Each is deferred by a recorded decision with a stated trigger. If you believe a trigger has fired, say so and
stop — do not build it.

## Commands

Keep this section accurate; it is the first thing a new session reads.

```
metis capture "<text>"                              # immutable typed capture with exact replay protection
metis capture --as goal "<text>"                    # pin a Goal; files under vault/goals/ after approval
metis capture --as project --goal <goal-id> "<text>" # pin a Project; parent must resolve at file time
metis classify <capture_id>                         # classify one preserved capture (pin overrides type)
metis propose "<capture-id>"                        # create or resume one proposal and proposed Obsidian draft
metis approvals                                     # read the vault status field and record each human decision
metis file <capture-id>                             # file one approved note (typed / goal / project by type)
metis status                                        # not yet implemented
```
