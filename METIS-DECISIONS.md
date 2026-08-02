# Metis Architecture Decision Records

> Companion to [METIS-MASTER-PROMPT.md](METIS-MASTER-PROMPT.md) (governing source) and
> [METIS-EXECUTION-BLUEPRINT.md](METIS-EXECUTION-BLUEPRINT.md) (execution strategy).
>
> This document records decisions that were **made**, not decisions that were merely proposed.
> Blueprint §16 requires these records; this file satisfies that requirement.

## Status

- Date settled: 2026-07-28
- Decided by: human owner (Philly), in an architecture session
- Implementation status: **none** — every decision below is unbuilt
- Supersedes: nothing
- Superseded by: nothing

Each record states context, the decision, alternatives considered, consequences, the reversal path, and the
evidence that would justify revisiting it. A decision recorded here is not a claim that it works — it is a
claim that it was chosen deliberately and can be re-opened deliberately.

---

## ADR-001 — Obsidian Markdown is the durable knowledge layer

**Context.** The master prompt (§2) warns against forcing Obsidian to become an operational database, while
still treating it as the primary human knowledge and interaction layer.

**Decision.** Approved, human-readable knowledge lives in Obsidian Markdown with YAML frontmatter. Workflow
state does not.

**Alternatives.** A database-backed knowledge store with Obsidian as a rendered view; a plain-files-only
system with no Obsidian.

**Consequences.** Knowledge stays portable, greppable, and readable without Metis running. Anything requiring
transactions, queues, or concurrent updates must live elsewhere.

**Reversal path.** Notes are plain files; migration to another store is a parsing exercise, not a rewrite.

**Revisit if.** Markdown frontmatter proves unable to express relationships the system repeatedly needs.

---

## ADR-002 — SQLite is the operational state store, reached only through a data-access seam

**Context.** Workflow state, proposals, approvals, retries, and audit events need transactional integrity that
Markdown cannot provide. The first loop is single-process with exactly one writer.

**Decision.** SQLite holds operational state. The orchestrator never issues SQL directly — all access passes
through a thin data-access layer whose interface is engine-agnostic.

**Alternatives.** Postgres from the start (rejected: no concurrent writers exist yet, adds a service to run);
JSON files (rejected: no transactions, no integrity constraints); Markdown (rejected by ADR-001).

**Consequences.** No server to run, trivial backup, easy inspection. The seam costs a small amount of
indirection now and buys a migration that is a swap rather than a rewrite.

**Reversal path.** ADR-012 — replace the data-layer implementation, leave orchestration untouched.

**Revisit if.** Concurrent writers appear (see ADR-011).

---

## ADR-003 — Source evidence is preserved separately and immutably

**Context.** Blueprint §3 and §8 both require that a model summary never silently replaces the original input.

**Decision.** Raw input is written to an append-only evidence store, hashed, and never modified. Every derived
artifact references it. Evidence is written **before** any interpretation runs.

**Alternatives.** Storing raw text inside the note (rejected: summaries overwrite it); storing raw text in the
database (rejected: mixes evidence with operational state).

**Consequences.** A classification failure, crashed process, or bad model response can never cost the original
input. Storage grows monotonically.

**Reversal path.** None needed — this is additive.

**Revisit if.** Never, without an explicit governance change.

---

## ADR-004 — Human approval is required before any permanent knowledge mutation

**Context.** This is the master prompt's central constraint (§21, §22) and the blueprint's fail-closed rule (§7).

**Decision.** No permanent note is created, changed, merged, moved, or deleted without a recorded human
approval. Enforcement is a state transition backed by schema and test, not a prompt instruction.

**Alternatives.** Confidence-threshold auto-approval (rejected: makes the model's self-assessment the
authority); post-hoc review (rejected: the write has already happened).

**Consequences.** Every capture requires a human decision. This is friction by design.

**Reversal path.** Would require an explicit, recorded governance change and an accepted risk model.

**Revisit if.** Deliberately never, per blueprint §14.

---

## ADR-005 — Obsidian is the sole approval surface

**Context.** Approval could plausibly live in a CLI, a web dashboard, or the vault. Multiple approval-capable
surfaces would create duplicate authority — a risk named in master prompt §38.

**Decision.** A proposal surfaces as a draft note in the vault with `status: proposed`. Changing that field to
`approved` is the only way to authorize a change. Exactly one surface holds authority.

**Alternatives.** Terminal review (rejected: ties approval to a tool session); local web dashboard (rejected
for now: more to build, and a second surface).

**Consequences.** Approval happens where knowledge is already read. Any future interface (see ADR-010) must be
read-only.

**Reversal path.** Additive — another surface could later write the same status field, but only by explicit decision.

**Revisit if.** Reviewing proposals inside the vault proves impractical at volume.

---

## ADR-006 — Approval is detected by a manual command before any automated watcher

**Context.** Master prompt §7: "manual before automated when learning is still required." It is not yet known
whether the approval step will feel like a useful pause or unnecessary friction.

**Decision.** A command the owner runs reads the vault, detects status changes, and hands decisions to the
orchestrator. No background process exists in the first loop.

**Alternatives.** Filesystem watcher (rejected for now: first continuously-running component, and editors/sync
tools write via temp-file-and-rename, which double-fires or misses events); Obsidian plugin (rejected for now:
new permission surface, plugin risk per master prompt §31, ongoing maintenance).

**Consequences.** Nothing happens until asked. Maximally testable and consistent with fail-closed.

**Reversal path.** A watcher can be added later; it invokes the same code path the manual command already calls.

**Revisit if.** The manual command has been used enough in real work to prove the friction is worth removing.

---

## ADR-007 — A deterministic orchestrator owns every state transition

**Context.** Master prompt §18 requires orchestration to enforce policy rather than forward prompts. Blueprint
§10 assigns transition ownership to the orchestrator.

**Decision.** All routing, permission checks, idempotency checks, approval gating, and audit emission happen in
deterministic code. Skills return bounded results and never call each other, never reach persistence directly,
and never decide their own authority.

**Alternatives.** A model-driven planner selecting its own next step (rejected: unauditable, non-reproducible);
a workflow engine (rejected: infrastructure ahead of demonstrated need).

**Consequences.** One place to audit, one place to halt. The orchestrator becomes the most test-covered
component in the system.

**Reversal path.** A workflow engine could later sit beneath the same interface.

**Revisit if.** Workflows become long-running or require durable timers the process model cannot provide.

---

## ADR-008 — Claude is the runtime reasoning engine, behind a thin model adapter

**Context.** Classification requires a model call from the moment it is built. Master prompt §30 requires
provider independence.

**Decision.** Claude is the chosen reasoning engine for classification and cross-project orchestration. Exactly
one component in the codebase talks to a provider API, behind a minimal interface.

**Alternatives.** Routing everything through OpenRouter from day one (rejected: builds multi-provider
infrastructure for a deliberate single-provider choice); calling the API directly from the classify skill
(rejected: hard-wires the provider into logic).

**Consequences.** The provider commitment is configuration, not architecture. Provider-specific features
reached through the adapter must be exposed deliberately.

**Reversal path.** Replace the adapter implementation. Nothing above it changes.

**Revisit if.** A second provider, local model, or router becomes genuinely useful — the adapter is the socket.

**Note.** The Claude that designed Metis (a planning session) and the Claude that Metis calls at runtime (an
API request in application code) are different roles. Blueprint §5 draws the same distinction for Claude Code
subagents versus Metis runtime agents; the same discipline applies here.

---

## ADR-009 — Codex is the primary builder; AGENTS.md is the governing instruction file

**Context.** The owner uses Claude, Codex, and Cursor. Claude Code reads `CLAUDE.md`; Codex and Cursor read
`AGENTS.md`. Repository rules must be readable by whichever tool is driving.

**Decision.** Codex writes and maintains the codebase. `AGENTS.md` is the primary instruction file, carrying
the constitution, approval rules, and coding standard. If a `CLAUDE.md` exists it points at `AGENTS.md` rather
than duplicating it.

**Alternatives.** Claude Code as sole builder (rejected: does not match how the owner works); duplicated
instruction files (rejected: they drift).

**Consequences.** Ground rules govern behavior regardless of tool. The file must stay concise — blueprint §4
suggests roughly 200 lines.

**Reversal path.** Trivial — instruction files are prose.

**Revisit if.** The tools' instruction-file conventions change.

---

## ADR-010 — OpenWebUI is a deferred, read-only observation surface

**Context.** The owner wants a way to watch orchestration activity. ADR-005 grants sole approval authority to
Obsidian.

**Decision.** OpenWebUI may display orchestration state and history. It may never approve, reject, or trigger
an action. It becomes a client of a small read API exposed after the core loop works.

**Alternatives.** Making it action-capable (rejected: duplicate authority); wiring it into the orchestrator
directly (rejected: couples an interface to internals).

**Consequences.** Observation and authority stay structurally separate. Requires designing the state and event
model to be queryable now, without building the query layer yet.

**Reversal path.** Not applicable while read-only.

**Revisit if.** Never for write access, without revisiting ADR-005.

---

## ADR-011 — Per-project Docker containers are deferred; the trigger is concurrent cross-project writes

**Context.** The owner wants each project to have a local host serving two purposes: a sandboxed environment
where that project's skills run, and a small local API/data layer Obsidian can query for richer views. These
containers would write to shared state concurrently.

**Decision.** Deferred until the core loop works as a single process. The unlock trigger is stated in advance:
concurrent cross-project state writes become real rather than hypothetical.

**Alternatives.** Building containers into the MVP (rejected: adds lifecycle, health checks, and orphaned-state
failure modes before there is anything to run in them).

**Consequences.** The MVP has exactly one writer, which is why ADR-002 is safe. Containers arriving forces
ADR-012 at the same time.

**Reversal path.** Additive. Containers connect at points the current design already exposes.

**Revisit if.** The trigger fires — which the owner expects it eventually will.

---

## ADR-012 — Postgres replaces SQLite when concurrent writers arrive

**Context.** SQLite permits many readers but serializes writers. Per-project containers (ADR-011) writing
independently would contend.

**Decision.** Postgres is the named upgrade path, swapped in behind the ADR-002 data-access seam. Not adopted
now, because there is currently one writer.

**Alternatives.** Postgres immediately (rejected: a service to run for a single-writer system); staying on
SQLite with write queuing (viable fallback if contention proves mild).

**Consequences.** The seam must be honored from the first line of code, or this becomes a rewrite.

**Reversal path.** The seam works in both directions.

**Revisit if.** ADR-011's trigger fires, or measured write contention appears sooner.

---

## ADR-013 — "Project" is one entity type with an optional runtime property

**Context.** "Project" was being used for two things: a knowledge-graph node an idea links to, and a container
host. Modelling these as separate types would create two entities that constantly reference each other.

**Decision.** One `Project` entity. Most projects are knowledge nodes with no runtime. A project that needs
active computation carries a `runtime: docker` property and a reference to its setup.

**Alternatives.** Two distinct entity types (rejected: duplication and constant cross-referencing).

**Consequences.** Linking, querying, and review treat all projects uniformly. Runtime is a capability a project
may have, not a different kind of thing.

**Reversal path.** Splitting later is a migration over a single property.

**Revisit if.** Runtime-bearing projects diverge so far from knowledge projects that shared schema becomes awkward.

---

## ADR-014 — Idempotency uses a content hash plus a capture ID

**Context.** Blueprint §11 requires a negative test proving a replayed capture cannot produce a duplicate
permanent record.

**Decision.** Every capture is assigned a UUID and hashed. The hash carries a uniqueness constraint in the
state store, so an identical resubmission is rejected at the data layer. The capture ID is the stable handle
every downstream artifact references.

**Alternatives.** Capture ID only (rejected: an accidental retry generating a fresh ID would duplicate);
hash only (rejected: no stable reference once content is edited).

**Consequences.** Exact replay is blocked mechanically rather than by application logic. Semantic duplicate
detection — two differently-worded captures about the same idea — is a separate, later problem.

**Reversal path.** Not applicable.

**Revisit if.** Never; extend rather than replace.

---

## ADR-015 — The first input type is typed capture at the CLI

**Context.** The MVP requires exactly one input type. Candidates included a watched folder, voice transcripts,
and forwarded email.

**Decision.** Typed capture via CLI. No external dependency, no transcription step, no integration.

**Alternatives.** Watched folder (deferred: closer to daily use, adds a watcher); voice (deferred: transcription
dependency); email (deferred: an integration, which ADR-016 blocks).

**Consequences.** The capture path is trivially testable. NotebookLM output is the expected second input type.

**Reversal path.** Additive — new input types feed the same intake pipeline.

**Revisit if.** The loop is proven and a second source is wanted.

---

## ADR-016 — No MCP or external integrations until the core loop is proven

**Context.** Blueprint Phase 9 places integrations after local workflows are stable. The owner's stack includes
several integration-capable tools.

**Decision.** The MVP makes no external calls except the model adapter (ADR-008). The first integration, when
it comes, is read-only and least-privilege.

**Alternatives.** Adding a useful read-only integration early (rejected: expands the failure surface before
there is a working core).

**Consequences.** The MVP is fully local and offline apart from one model call.

**Reversal path.** Additive.

**Revisit if.** The MVP acceptance test passes end to end.

---

## ADR-017 — Secrets live outside Git and outside the vault

**Context.** Master prompt §31 and blueprint §3 both require this. No secrets exist yet, which makes it the
cheapest possible moment to establish the rule.

**Decision.** Credentials live in environment variables, the OS keychain, or a secrets manager. Never in the
repository, the vault, skill files, or the state database. Enforced by ignore rules and a secret-scanning check.

**Alternatives.** An encrypted file in the repository (rejected: one mistake from exposure).

**Consequences.** Setup requires an explicit configuration step, documented from the start.

**Reversal path.** None wanted.

**Revisit if.** Never.

---

## ADR-018 — Vector and graph databases stay deferred

**Context.** Master prompt §26 lists many retrieval technologies. Blueprint §14 defers them pending evidence.

**Decision.** Full-text search plus metadata filtering over Markdown first. No vector store, no graph database.

**Alternatives.** Embedding everything from the start (rejected: stale-embedding management and infrastructure
before a corpus exists that could fail without them).

**Consequences.** Retrieval quality is bounded by search and metadata. That bound must be *measured* before it
is treated as a problem.

**Reversal path.** Additive — both sit alongside, not underneath.

**Revisit if.** Search plus metadata demonstrably fails on the real corpus, with documented examples and a
retrieval target to beat.

---

## ADR-019 — Git is the governance and audit layer for the development system

**Context.** Metis enforces proposal-before-mutation and an audit trail on the knowledge layer through the
Obsidian approval gate (ADR-004, ADR-005). The development system — the fourth layer in the blueprint's model
(§3) — has had no equivalent control. Code could change without review, without a record of why, and without a
reliable path back, while the system it implements refuses to write a single note ungoverned.

Two further pressures are specific to building with agents. Agents produce a large amount of change quickly and
non-deterministically, so reversibility (MP §7) needs a mechanism rather than an intention. And three tools —
Codex, Cursor, Claude — share this repository while sharing no memory with each other. Commit history is the
only common context between them.

**Decision.** Git is the governance and audit layer for code. Pull requests are the proposal-before-mutation
mechanism, branch protection is fail-closed enforcement, and commit trailers carrying requirement and decision
IDs are both the audit trail and the shared memory across tools.

- `main` is protected; all changes arrive by pull request.
- One task, one branch, one tool at a time. `git worktree` provides genuine parallelism without two tools
  sharing a working tree.
- Branches follow the `AGENTS.md` build order: `step/NN-name`, plus `adr/`, `fix/`, `docs/`.
- Commits carry `Requirement:`, `Decision:`, `Test:`, and `Co-Authored-By:` trailers.
- A new architecture decision arrives as a pull request containing **only** the ADR, merged before any code
  implementing it is written.
- A pull request that satisfies a requirement updates the requirement ledger in the same pull request.
- `CODEOWNERS` covers the governance files, so a non-negotiable rule cannot change without explicit review.
- The state database, evidence store, and secrets are never committed.
- A build step is tagged when its acceptance test passes.

**Alternatives.** A separate `METIS-GIT-WORKFLOW.md` (rejected: read only if a tool follows a link, while the
failure modes here — a committed secret, a bypassed check, a broken traceability chain — are the irreversible
kind that must stay in the always-read file). Trunk-based development with direct pushes (rejected: no gate at
all). A bespoke approval mechanism for code changes (rejected: git already implements exactly this pattern,
and a second mechanism would duplicate authority — the same objection as ADR-005).

**Consequences.** Every change is reviewable and revertible. `git log --grep=REQ-INTK-001` returns the complete
history of a requirement, which is what makes the ledger verifiable rather than trusted. A tool with no prior
context can reconstruct intent from history. The cost is that nothing reaches `main` without a human merging
it — friction that is the point, and the same friction ADR-004 accepts for knowledge.

The rules live in `AGENTS.md` because they must be in context every session; this record holds the reasoning so
that file stays within the ~200-line budget from blueprint §4. Same split used everywhere else: `AGENTS.md`
says what, this file says why.

**Reversal path.** Branch protection is a repository setting. Commit conventions are conventions. Nothing here
is load-bearing on application code.

**Revisit if.** Solo pull-request review becomes pure ceremony with no review value. The correction then is a
protected `main` with required status checks and self-merge — tightening the automated gate rather than
abandoning it.

**Related.** Whether the vault is its own repository remains open question 5 in the requirement ledger. This
record covers the development system only.

---

## ADR-020 — A proposed draft has two human-editable fields: `status` and `links`

**Context.** ADR-005 makes the vault the sole approval surface, and the schema doc's note-schema rules (§4.3)
recorded a stricter reading of it: `status` is the only field a human edits. Step 4 implemented exactly that.
The draft store validates an observed draft by rendering the three legal `status` variants of the expected
bytes and requiring the file to match one of them exactly, so any other edit is refused as inconsistent. That
byte-exactness is what makes the approval read trustworthy: the system knows precisely what the human saw.

Two requirements then collide with it. REQ-INTK-004 requires an approved intake to link to an existing goal or
project, and `unresolvable_link_blocks_commit` is a required negative test. But nothing proposes links —
`propose-v1` returns only title, body, reason, and uncertainties, and `proposal.proposed_links` is always `[]`.
The links have to come from the human, who authors goal and project notes by hand in Obsidian.

They also have to arrive *before* approval, not after. The owner's workflow is: write the goal note, open the
draft, add the link, then flip the status. Under the one-field contract the approval command refuses that
draft outright, so the workflow the MVP acceptance criterion describes cannot be completed at all.

**Decision.** A proposed draft has exactly two human-editable fields:

- `status` — the approval signal, one of `proposed` · `approved` · `rejected`. Unchanged by this record.
- `links` — a list of wikilinks to existing goal or project notes.

Every other byte of the draft remains system-owned and byte-exact, and the draft store continues to refuse a
draft that differs anywhere else. `links` supplies content; it does not authorize. Approval authority still
rests solely on `status`, so there is still exactly one approval surface and ADR-005 is unchanged.

Editing `links` does not make an approval decision, and an unresolvable link does not invalidate an approval
that was genuinely given. It blocks the permanent write and produces a visible review item — the fail-closed
behavior blueprint §7 requires, applied to filing rather than to approval.

**Alternatives.** Keeping one editable field and having the model propose links (rejected: a link is a
relationship assertion about the owner's own goals, and ADR-004's whole premise is that a model's assertion is
a proposal, not a fact — this would make the model author the graph and reduce the human to ratifying it).
A separate `metis link <capture_id> <target>` command (rejected: a second place to shape a proposal before
approval, and it moves work out of the vault where the human is already reading — the ADR-005 objection).
Collecting links after approval, at filing time (rejected: the human would approve a note whose relationships
they had not yet seen, so the approval would attest to less than the thing being filed). Deferring links and
filing orphans (rejected: REQ-INTK-004 exists precisely to prevent orphans, and deferring it defers the MVP).

**Consequences.** The draft contract becomes two variable regions instead of one, and the draft store must
report observed links alongside the observed status. The trust property survives in the same form it had
before — everything outside the editable fields is still byte-exact — but the invariant is now "two fields"
rather than "one", and every future reader of a draft has to honor both.

The step-6 note writer gains an obligation: resolve each link against an existing note's `id` in `vault/goals/`
or `vault/projects/`, and refuse to file on an unresolvable one. Goal and project notes are authored by hand;
Metis does not create them, and this record does not propose that it should.

`METIS-SCHEMAS.md` §4.3 is amended by this decision.

**Reversal path.** Narrowing back to one editable field is a validation change plus a decision about where
links come from instead. Nothing persisted depends on the second field.

**Revisit if.** Hand-authored links prove too laborious at volume, or a future step gives the model a
bounded, human-confirmed way to suggest links — which would be a new record, not a quiet widening of this one.

---

## Decision Summary

| ID | Decision | Status |
|---|---|---|
| ADR-001 | Obsidian Markdown as durable knowledge | Adopted |
| ADR-002 | SQLite behind a data-access seam | Adopted |
| ADR-003 | Evidence preserved separately and immutably | Adopted |
| ADR-004 | Human approval before permanent mutation | Adopted |
| ADR-005 | Obsidian is the sole approval surface | Adopted |
| ADR-006 | Manual approval command before any watcher | Adopted |
| ADR-007 | Deterministic orchestrator owns transitions | Adopted |
| ADR-008 | Claude behind a thin model adapter | Adopted |
| ADR-009 | Codex builds; AGENTS.md governs | Adopted |
| ADR-010 | OpenWebUI read-only, deferred | Deferred |
| ADR-011 | Per-project containers deferred | Deferred |
| ADR-012 | Postgres on concurrent writers | Deferred |
| ADR-013 | One Project entity, optional runtime | Adopted |
| ADR-014 | Content hash + capture ID | Adopted |
| ADR-015 | Typed CLI capture first | Adopted |
| ADR-016 | No integrations until the loop works | Deferred |
| ADR-017 | Secrets outside Git and vault | Adopted |
| ADR-018 | Vector and graph databases deferred | Deferred |
| ADR-019 | Git is the governance layer for code | Adopted |
| ADR-020 | Draft `status` and `links` are human-editable | Adopted |

Adopted means chosen and to be built. Deferred means chosen *not* to be built yet, with a stated trigger.
Neither means implemented.
