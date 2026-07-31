# Metis Execution Blueprint

> This document interprets and operationalizes [METIS-MASTER-PROMPT.md](METIS-MASTER-PROMPT.md).
> It does not replace or silently revise the governing prompt.

## Document Status

- Type: execution strategy
- Initial scope: documentation and MVP guidance
- Existing implementation assumed: none
- Permanent actions: human-approved
- Governing source: `METIS-MASTER-PROMPT.md`

## 1. Purpose

Metis is a personal knowledge, agent, and execution operating system. Its purpose is to help one person capture information, convert it into useful knowledge, connect it to goals and projects, and execute repeatable workflows without surrendering control over permanent records or consequential actions.

This blueprint turns the master prompt into an implementation sequence suitable for Claude Code. It separates:

- requirements stated by the master prompt;
- implementation recommendations derived from those requirements;
- features that should be deliberately deferred;
- evidence needed before any capability is called complete.

The master prompt remains authoritative. If this blueprint and the master prompt appear to conflict, pause implementation, record the conflict, and ask the human owner to decide.

## 2. Product Boundaries

### Source-derived requirements

- Metis must support knowledge capture, organization, retrieval, planning, execution, review, and continuous improvement.
- Human approval must govern permanent knowledge changes and consequential actions.
- Information must retain provenance and must not silently become verified fact.
- The system must be modular, auditable, testable, and capable of growing over time.
- Obsidian is the human-readable knowledge layer.

### Implementation recommendations

- Use Obsidian Markdown for durable, human-readable knowledge.
- Use SQLite for workflow state, approvals, queues, execution history, and other operational data that should not live in notes.
- Use Git for version history of code, configuration, schemas, and non-secret knowledge artifacts.
- Use Claude Code as the development and controlled-operation interface during the early phases, not as the permanent application runtime.
- Keep integrations least-privileged. Begin read-only whenever useful work is possible without write access.
- Keep credentials and secrets outside the repository and vault.
- Treat every permanent write as a governed state transition, not an incidental side effect of a model response.

### Explicit non-goals for the first release

- Autonomous permanent memory writes
- A large multi-agent organization
- Cloud deployment
- Broad write access to third-party services
- A vector database before ordinary retrieval is proven inadequate
- A graph database before Markdown links and typed properties are proven inadequate
- A polished application interface before the core intake and approval loop works

## 3. The Four-Layer Data Model

Metis should not place every kind of information into the same storage system.

| Layer | Purpose | Recommended store | Examples |
|---|---|---|---|
| Source evidence | Preserve what was actually received | Immutable or append-only files | Original document, message export, transcript, URL snapshot |
| Durable knowledge | Human-readable, reviewed understanding | Obsidian Markdown | Person, project, goal, decision, concept, reference note |
| Operational state | Drive workflows and enforce transitions | SQLite | Queue state, proposal, approval, retry, execution event |
| Development system | Define and test system behavior | Git repository | Code, schemas, prompts, rules, fixtures, documentation |

Secrets are a separate concern and belong in environment variables, the operating system keychain, or a secrets manager. They do not belong in any of the four layers above.

This separation prevents several common failures:

- a model summary replacing the original evidence;
- workflow state being hidden inside prose notes;
- unverified claims being treated as durable facts;
- application secrets entering Git history;
- implementation files becoming mixed with the user’s working vault.

## 4. How the Master Prompt Maps to Claude Code

Do not paste the entire master prompt into `CLAUDE.md`. That would make every session carry a long strategic document, obscure the small set of instructions that must always apply, and make future changes difficult to review.

Use the master prompt as the governing product source. Convert only stable, universally applicable operating rules into concise Claude Code instructions.

| Master-prompt concept | Claude Code implementation | Durable source |
|---|---|---|
| Constitution and coding behavior | `CLAUDE.md` plus `.claude/rules/` | Repository |
| Reusable procedures | `.claude/skills/<name>/SKILL.md` | Repository |
| Isolated development workers | `.claude/agents/<name>.md` | Repository |
| Runtime agents | Application code plus agent manifests | Runtime repository |
| Deterministic enforcement | Hooks and permission rules | `.claude/settings.json` |
| External connections | MCP or narrow APIs | MCP and integration configuration |
| Permanent knowledge | Obsidian Markdown | Vault |
| Workflow state and approvals | SQLite | Local application data |
| Audit evidence | SQLite events plus Git history | State database and repository |
| Secrets | Environment or secrets manager | Outside repository and vault |

### Recommended instruction hierarchy

1. `CLAUDE.md`: concise repository purpose, non-negotiable safety rules, key commands, and navigation.
2. `.claude/rules/`: scoped instructions that apply only to relevant file types or directories.
3. `.claude/skills/`: repeatable, user-invocable procedures with clear inputs, outputs, checks, and stopping conditions.
4. `.claude/agents/`: optional Claude Code subagents for bounded development or read-only analysis.
5. Application code and manifests: the actual Metis runtime behavior.
6. Hooks, permissions, schemas, and tests: deterministic constraints that do not depend on the model remembering prose.

Keep `CLAUDE.md` compact—preferably below roughly 200 lines—and link to deeper product and architecture documents instead of embedding them.

## 5. Four Concepts That Must Not Be Confused

| Concept | What it is | Appropriate use | What it is not |
|---|---|---|---|
| Claude Code main session | The interactive development and controlled-operation session | Planning, implementation, review, approved operations | The permanent Metis runtime |
| Claude Code subagent | A bounded helper defined in `.claude/agents/<name>.md` | Read-only research, verification, focused development analysis | A business-domain agent automatically running in production |
| Metis runtime agent | Application behavior with a manifest, permissions, tools, and tests | Intake, classification, librarian, project/task workflows | Merely a Claude Code configuration file |
| Skill | A repeatable procedure with defined inputs and outputs | Capture, classify, validate, propose, review | An autonomous identity with broad authority |

Files in `.claude/agents/` configure Claude Code subagents. They should not be treated as the master prompt’s runtime `AGENT.md` objects. Runtime agents belong to the application architecture and require their own schemas, permissions, tests, and audit records.

## 6. Recommended Future Repository Shape

This is a target map, not an instruction to create every directory immediately.

```text
metis/
├── CLAUDE.md
├── .claude/
│   ├── rules/
│   ├── skills/
│   │   └── <skill-name>/
│   │       └── SKILL.md
│   ├── agents/
│   │   └── <subagent-name>.md
│   └── settings.json
├── docs/
│   ├── product/
│   ├── architecture/
│   ├── decisions/
│   └── reference/
├── vault/
├── runtime/
│   ├── agents/
│   ├── skills/
│   ├── workflows/
│   ├── policies/
│   └── schemas/
├── tests/
├── scripts/
└── state/
```

Create only the directories needed by the active phase. Empty architecture is not progress.

The local state database should normally be excluded from Git. A schema, migrations, and sanitized fixtures should be versioned instead.

## 7. Governance and Human Approval

### Read boundaries

Every operation should declare:

- the data it may read;
- the data it may propose changing;
- the actions it may execute;
- the conditions that require human approval;
- the evidence it must retain;
- the behavior required when a permission or dependency is missing.

### Proposal before mutation

A model-generated result is a proposal until a governed transition accepts it. A proposal record should include:

- proposal ID;
- source evidence and provenance;
- proposed change;
- reason and supporting evidence;
- confidence and unresolved uncertainty;
- affected records;
- risk classification;
- required action;
- approver;
- decision;
- timestamp;
- resulting state or artifact.

### Actions requiring explicit approval

At minimum, require approval before:

- creating, changing, merging, moving, or deleting permanent knowledge;
- deleting source evidence;
- publishing or sending content externally;
- purchasing or committing funds;
- granting or expanding permissions;
- writing to external systems;
- acting on sensitive personal information;
- performing an irreversible or difficult-to-reverse action.

Approval must be enforced through schemas, workflow state transitions, permission rules, and hooks where available. A prose instruction such as “ask first” is useful context but is not sufficient enforcement.

### Fail-closed behavior

If the system cannot determine permission, provenance, approval state, target, or write outcome, it must stop and produce a clear review item. It must not infer approval from silence or continue with a partial permanent write.

## 8. Memory and Knowledge Lifecycle

### Knowledge states

- **Immediate:** temporary context needed for the current interaction.
- **Working:** active context for a current project or workflow.
- **Episodic:** evidence of an event, interaction, or completed execution.
- **Unverified:** extracted or proposed information awaiting review or corroboration.
- **Verified:** approved information supported by known provenance.
- **Preferences:** user choices that are stable enough to influence future behavior.
- **Archive:** retained material no longer active but preserved for history.

### Lifecycle

```text
raw input
  → immutable capture
  → classified proposal
  → human review
  → approved typed note
  → periodic verification
  → superseded or archived
```

The raw capture is never silently replaced by a summary. Corrections should supersede earlier knowledge while preserving the evidence and audit history that explain the change.

## 9. Universal Intake Workflow

All input types should enter through a common governed sequence:

```text
capture
  → preserve source
  → screen for sensitive content
  → classify
  → check duplicates
  → extract candidate entities
  → identify relationships
  → generate proposal
  → request approval
  → commit approved change
  → record evidence and outcome
```

Each step must have defined inputs, outputs, error states, and an idempotency strategy. Re-running the same capture must not silently create duplicate permanent records.

If any step fails, the source remains preserved and the workflow enters a visible review or retry state. A failure must not leave a misleading “complete” record.

## 10. Initial Skills and Runtime Components

### Initial skills

Begin with six narrow procedures:

1. **Capture Intake** — preserve an input and create a traceable intake record.
2. **Classify Intake** — assign candidate types, sensitivity, and routing.
3. **Propose Knowledge Update** — produce a structured, reviewable change without applying it.
4. **Decompose Project** — turn an approved goal or project into bounded outcomes and tasks.
5. **Generate Weekly Review** — summarize evidence, open commitments, risks, and decisions.
6. **Validate Proposed Change** — check schema, provenance, duplication, permissions, and approval requirements.

Each skill should state its purpose, allowed tools, input schema, output schema, failure behavior, and test fixtures.

### Initial runtime

The smallest useful runtime consists of:

- a deterministic orchestrator;
- an Intake/Classify capability;
- a Librarian capability for typed-note proposals and link validation;
- a Project/Task capability for approved planning records.

The orchestrator owns state transitions. Runtime agents or model calls produce bounded results; they do not decide their own authority.

### Appropriate Claude Code subagents

Early Claude Code subagents should be read-only researchers or verifiers. Examples:

- trace a master-prompt requirement to design artifacts;
- review schemas for missing states;
- compare implementation with acceptance criteria;
- inspect documentation consistency.

Add another runtime agent only when a repeated workflow has a distinct responsibility, tool boundary, permission boundary, and testable contract. An organizational chart is not a reason to add agents.

## 11. Testing and Evidence Strategy

Build verification into each phase.

### Required test layers

- Schema validation for every structured artifact
- Unit tests for pure classification, routing, and transition logic
- Prompt fixtures for expected model outputs and failure cases
- Permission tests that prove disallowed actions are blocked
- Approval-state tests that prove no permanent write occurs before approval
- Integration tests using disposable content and least-privileged credentials
- Recovery tests for timeouts, partial writes, and interrupted execution
- End-to-end intake tests from raw evidence through approved note and audit event

### Evidence rule

No capability is “working” merely because a prompt produced a plausible answer. A completion claim requires a recorded test run, the expected result, the actual result, and any remaining limitations.

### Critical negative tests

Metis must prove that it:

- refuses an unapproved permanent write;
- preserves the source when classification fails;
- prevents a duplicate replay from duplicating the permanent note;
- records a failed or partial external action accurately;
- keeps unverified content visibly unverified;
- cannot expose a stored secret through ordinary logs or notes.

## 12. Phased Roadmap

Each phase has an entry condition, a concrete output, acceptance criteria, and explicit deferrals.

### Phase 0 — Ratify the Constitution

- **Objective:** establish the governing requirements and resolve contradictions.
- **Entry:** the master prompt and this blueprint are available.
- **Output:** requirement ledger, terminology, risk classes, and decision log.
- **Acceptance:** every major requirement has an ID, source reference, status, and owner; conflicts are recorded rather than silently harmonized.
- **Deferred:** implementation code and integrations.

### Phase 1 — Define Information and State Models

- **Objective:** separate evidence, knowledge, workflow state, and audit history.
- **Entry:** Phase 0 requirements are traceable.
- **Output:** note types, SQLite entities, lifecycle states, schemas, and provenance rules.
- **Acceptance:** representative captures can be modeled without placing workflow state in Markdown or permanent knowledge in a queue record.
- **Deferred:** model-driven automation.

### Phase 2 — Establish Repository and Claude Code Controls

- **Objective:** create the smallest maintainable development environment.
- **Entry:** core schemas and boundaries are known.
- **Output:** concise `CLAUDE.md`, scoped rules, permissions, hooks, tests, and decision records.
- **Acceptance:** setup instructions work from a clean checkout; prohibited writes are blocked by a test.
- **Deferred:** broad subagent use and external writes.

### Phase 3 — Build Immutable Capture

- **Objective:** accept a real input without losing or rewriting the source.
- **Entry:** source and intake schemas pass validation.
- **Output:** capture command, source store, checksum, provenance record, and retry behavior.
- **Acceptance:** the same input can be captured safely, detected on replay, and recovered after an interrupted run.
- **Deferred:** permanent knowledge creation.

### Phase 4 — Add Classification and Proposal

- **Objective:** convert a preserved capture into a structured proposed change.
- **Entry:** Phase 3 capture is reliable.
- **Output:** classifier, duplicate check, entity candidates, relationships, confidence, and proposal record.
- **Acceptance:** fixtures produce schema-valid proposals; uncertainty remains visible; no proposal directly writes a permanent note.
- **Deferred:** autonomous acceptance.

### Phase 5 — Add Human Review and Approved Commit

- **Objective:** make approval a real, auditable state transition.
- **Entry:** proposal records are deterministic enough to review.
- **Output:** approval queue, approve/reject/edit decisions, typed-note writer, and audit event.
- **Acceptance:** only an approved proposal can produce a permanent note; the note links to provenance and the audit record identifies the approver and outcome.
- **Deferred:** third-party write integrations.

### Phase 6 — Connect Goals, Projects, and Tasks

- **Objective:** turn approved knowledge into executable planning relationships.
- **Entry:** typed notes and approval flow are stable.
- **Output:** goal, project, outcome, task, dependency, and status models.
- **Acceptance:** an approved intake can link to an existing goal or project without creating duplicate or orphan planning records.
- **Deferred:** calendar and task-manager writes.

### Phase 7 — Deliver Review and Retrieval

- **Objective:** make stored knowledge useful in daily and weekly decisions.
- **Entry:** a realistic local corpus exists.
- **Output:** metadata/full-text retrieval, daily view, weekly review, stale-item checks, and source navigation.
- **Acceptance:** review outputs trace every material statement to current records and clearly flag missing or stale evidence.
- **Deferred:** vector and graph databases unless measured retrieval failures justify them.

### Phase 8 — Introduce Controlled Runtime Agents

- **Objective:** automate stable workflows behind explicit contracts.
- **Entry:** the manual or deterministic workflow has repeated successfully and has test coverage.
- **Output:** versioned manifests, narrow permissions, tool boundaries, evaluation fixtures, and orchestrated execution.
- **Acceptance:** each agent can be disabled independently; its allowed actions and failure states are tested; the orchestrator retains authority.
- **Deferred:** agent proliferation and self-modifying authority.

### Phase 9 — Add Read-Only Integrations

- **Objective:** increase useful context without creating external side effects.
- **Entry:** local workflows and permission model are stable.
- **Output:** one narrow read-only integration, sync cursor, provenance, rate-limit handling, and revocation procedure.
- **Acceptance:** disconnecting the integration does not corrupt Metis; imported data retains source attribution; permissions match the documented minimum.
- **Deferred:** external writes.

### Phase 10 — Evaluate Expansion

- **Objective:** decide whether advanced infrastructure is justified by evidence.
- **Entry:** measured usage, failures, retrieval quality, and maintenance costs are available.
- **Output:** decision records for external writes, cloud availability, vector search, graph storage, or additional runtime agents.
- **Acceptance:** each addition solves a documented recurring problem, has an owner, threat model, rollback path, and acceptance test.
- **Deferred:** any feature that cannot meet those conditions.

## 13. MVP Definition

The MVP is not a general-purpose autonomous assistant. It is one complete, safe knowledge-to-action loop.

### Included

- one raw input type;
- immutable source preservation;
- structured intake and provenance;
- classification and duplicate detection;
- typed-note proposal;
- human review and approval;
- approved Obsidian note creation;
- link to an existing goal or project;
- SQLite workflow and audit records;
- replay protection and failure recovery;
- automated tests for the critical path and prohibited writes.

### Exact acceptance test

> A real raw capture is preserved, classified, proposed, reviewed, approved, filed as a typed note with provenance, linked to an existing goal or project, and recorded in the audit history without any silent permanent write.

If this single test cannot be demonstrated end to end, the MVP is not complete.

## 14. Deliberate Deferrals and Activation Triggers

| Capability | Defer until |
|---|---|
| Vector database | Full-text search plus metadata filters fail on the real corpus, with documented examples and a measurable retrieval target |
| Graph database | Markdown links and typed properties cannot answer recurring relationship queries within acceptable complexity or performance |
| Formal multi-agent registries | At least five stable runtime agents exist, or real routing/version conflicts require centralized registration |
| Cloud runtime | Local availability, backup, or remote-access limitations block a real workflow |
| External write integrations | A read-only integration has delivered value, the write action has explicit approvals and rollback, and permission tests pass |
| Autonomous permanent memory | Indefinitely, unless the owner explicitly changes governance and accepts a documented risk model |

Deferral is not rejection. It protects the core from premature infrastructure and preserves clear evidence for later decisions.

## 15. Requirement Traceability

Create a living ledger during Phase 0.

| Field | Meaning |
|---|---|
| Requirement ID | Stable identifier such as `REQ-GOV-001` |
| Source | Master-prompt section and exact statement |
| Interpretation | Testable meaning adopted by Metis |
| Status | Verified, partial, deferred, superseded, or missing |
| Design artifact | Schema, rule, decision, workflow, or module |
| Test evidence | Test or review record proving the behavior |
| Decision reference | Explanation of any narrowing, conflict, or deferral |

Never mark a requirement verified solely because a document mentions it. Verification requires working evidence appropriate to the requirement.

## 16. Architecture Decisions to Record Early

Create decision records for at least:

1. Obsidian Markdown as the durable knowledge layer.
2. SQLite as the operational state and approval store.
3. Source evidence preserved separately from derived notes.
4. Human approval before permanent knowledge mutation.
5. Claude Code as a development and controlled-operation interface, not the permanent runtime.
6. Deterministic orchestrator ownership of workflow transitions.
7. Read-only integrations before write access.
8. Secrets outside Git and the vault.
9. Deferred vector and graph databases.
10. The distinction between Claude Code subagents and Metis runtime agents.

Each record should state context, decision, alternatives, consequences, reversal path, and the evidence that would justify revisiting it.

## 17. First Safe Claude Code Audit Prompt

Run this before asking Claude Code to build anything:

```text
You are auditing a proposed personal knowledge, agent, and execution operating
system. Do not modify files, install packages, create integrations, or perform
external actions.

Read:
1. METIS-MASTER-PROMPT.md
2. METIS-EXECUTION-BLUEPRINT.md
3. The current repository documentation, schemas, configuration, and tests

Create a requirement traceability report. For each material requirement:
- assign a stable requirement ID;
- cite its source section;
- identify any existing implementation evidence;
- classify the status as verified, partial, deferred, superseded, or missing;
- explain the evidence or gap;
- identify contradictions, ambiguous terms, hidden assumptions, and unsafe
  permissions;
- recommend the smallest next step and the test that would prove it.

Do not equate a filename, prompt, or design statement with a working feature.
Do not claim an integration is available unless its authentication and a
read-only verification have been demonstrated.

End with:
1. the ten highest-risk gaps;
2. the smallest defensible MVP boundary;
3. the exact acceptance test for the next phase;
4. a list of decisions that require human approval.

Stop after producing the report. Wait for explicit approval before making any
change.
```

This prompt gives Claude Code access only to the information present in the files it can read. Claude Code does not automatically know the contents of this chat or any document that has not been placed in its working directory or explicitly provided.

## 18. Using Claude Code Results

Claude Code’s output should be treated as evidence or a proposal, not as an automatic update to the governing documents.

Recommended review loop:

1. Save the result as a dated audit or proposal inside the future repository.
2. Check every completion claim against files, tests, commands, and observed behavior.
3. Compare proposed interpretations with the master prompt.
4. Record approved deviations as architecture decisions.
5. Update the blueprint only through a visible, reviewed edit.
6. Bring disputed findings or major decisions back to the human owner for resolution.

You may paste a Claude Code result into a later conversation for independent review, but the durable repository copy should remain the source used for implementation.

## 19. Official Claude Code References

Claude Code changes over time. Confirm current behavior against the official documentation before relying on a feature:

- [Memory and `CLAUDE.md`](https://code.claude.com/docs/en/memory)
- [Skills and slash commands](https://code.claude.com/docs/en/slash-commands)
- [Subagents](https://code.claude.com/docs/en/sub-agents)
- [Hooks](https://code.claude.com/docs/en/hooks-guide)
- [Permissions](https://code.claude.com/docs/en/permissions)
- [MCP integrations](https://code.claude.com/docs/en/mcp)
- [Features overview](https://code.claude.com/docs/en/features-overview)

These references explain Claude Code mechanisms. They do not supersede Metis governance or authorize external actions.

## 20. Completion Standard

An implementation phase is complete only when:

- its entry conditions were satisfied;
- its output artifacts exist;
- its positive and negative acceptance tests passed;
- evidence is retained;
- limitations and deferrals are recorded;
- no unapproved permanent write occurred;
- the requirement ledger was updated;
- the human owner approved any decision that changed governance or scope.

Metis should grow through verified, reversible increments. The quality of its boundaries, provenance, and approvals matters more than the number of agents or integrations it contains.
