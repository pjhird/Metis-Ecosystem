# Metis Ecosystem

Metis is a documentation and code foundation for a personal AI knowledge, agent, and execution operating system.

It contains the original governing prompt in readable Markdown, an execution blueprint for implementing it safely, the settled design artifacts, and two implementation layers: an engine-neutral state-store contract with versioned SQLite migrations, plus immutable typed CLI capture with exact replay protection. It remains intentionally separate from any existing Obsidian vault or software project.

## Start Here

Read the documents in this order:

1. [Metis Master Prompt](METIS-MASTER-PROMPT.md) — the governing source, translated from the original 61-page PDF.
2. [Metis Execution Blueprint](METIS-EXECUTION-BLUEPRINT.md) — implementation analysis, boundaries, Claude Code mapping, phased roadmap, and MVP acceptance test.
3. [Metis Decisions](METIS-DECISIONS.md) — the eighteen architecture decisions that have actually been made, with reversal paths and revisit triggers.
4. [Metis Schemas](METIS-SCHEMAS.md) — the evidence store, SQLite tables, intake state machine, and Obsidian note schemas.
5. [Metis Requirement Ledger](METIS-REQUIREMENT-LEDGER.md) — every requirement with an ID, its status, and the evidence needed to prove it.
6. [AGENTS.md](AGENTS.md) — the repository ground rules, read first by whichever coding tool is driving.

The master prompt says **what the ecosystem is intended to become**. The blueprint explains **how to begin building it without pretending the whole system already exists**. The decisions, schemas, and ledger record **what has actually been settled and which foundation requirements have test evidence**.

## What Exists Today

This folder contains the documentation package and build-order steps 1 and 2. Step 2 accepts one typed CLI input, preserves its UTF-8 bytes and metadata/hash as immutable evidence, and registers it only after evidence finalization. Exact replays return the existing capture rather than creating another intake row or evidence directory. It does not implement classification, proposal creation, an Obsidian vault, approval, filing, linking, audit behavior, agents, integrations, or workflows.

| Artifact | Status |
|---|---|
| Governing prompt | Complete Markdown edition |
| Execution strategy | Complete initial blueprint |
| Architecture decisions | 18 recorded — 10 adopted, 8 deferred with triggers |
| Information and state model | Phase 1 design complete |
| Requirement ledger | Populated; foundation and partial capture evidence recorded, later capabilities still Missing or Deferred |
| Repository ground rules | `AGENTS.md`, `CLAUDE.md`, ignore rules, and `CODEOWNERS` present |
| Metis application code | Steps 1–2: data-access contract, versioned SQLite schema migrations, immutable typed capture, and CLI entry point |
| Test harness | Standard-library `unittest`; schema, migration, SQL-boundary, capture, evidence, replay, and CLI coverage |
| Obsidian vault | Not created or modified by this package |
| Runtime agents and integrations | Not yet implemented |

## Settled Architecture

The design phase is complete. In brief:

- **Obsidian Markdown** is the durable knowledge layer and the *sole* approval surface. A proposal becomes permanent only when a human changes a note's `status` field from `proposed` to `approved`.
- **SQLite** holds workflow state, proposals, approvals, and audit events, reached only through a thin data-access layer so the engine can later be swapped for Postgres.
- **Source evidence** is written immutably before anything interprets it, and is never replaced by a summary.
- **A deterministic orchestrator** owns every state transition. Skills return bounded results and never decide their own authority.
- **Claude** is the runtime reasoning engine, behind a thin adapter so that exactly one module in the codebase talks to a provider.
- **Codex** builds and maintains the codebase, governed by `AGENTS.md`.

Deliberately deferred, each with a stated unlock trigger: per-project Docker containers, Postgres, OpenWebUI as a read-only observation surface, MCP and external integrations, runtime agents, vector and graph databases, and any background file-watcher.

## First Build Target

The initial MVP is one safe intake loop:

> A typed idea is preserved immutably, classified with visible confidence, turned into a schema-valid proposal, surfaced in Obsidian as a draft with `status: proposed`, and — only after a human changes that to `approved` — filed as a typed note with provenance, linked to an existing goal or project, and recorded in the audit log. Replaying the identical input creates no second note.

Build and prove this path before expanding into a broad agent ecosystem. The build order is in [AGENTS.md](AGENTS.md).

## Development Check

The step-2 application and tests use only Python's standard library and require Python 3.11 or later. From the repository root, run:

```bash
python3 -m unittest discover -s tests -v
```

The recorded Step-2 verification used Python 3.13.13 (`PATH=/opt/miniconda3/bin:/usr/bin:/bin python3`) and ran 69 tests with final `OK`. Tests create SQLite databases only in temporary directories. Runtime databases, evidence, vault content, environment files, and generated Python artifacts are ignored by Git.

## Typed Capture

`metis capture "<text>"` is implemented. It writes the input's UTF-8 bytes without modification to immutable evidence together with exact metadata and a SHA-256 content hash, validates that evidence, then registers the intake record. On an exact replay with matching state and evidence, it returns the existing capture rather than creating a second intake row or evidence directory.

The command emits one stable JSON object. `captured`, `duplicate`, and `refused` are written to standard output with exit status 0; `failed` is written to standard error with exit status 1. `metis approvals` and `metis status` are not implemented.

Typed capture is only the second build-order step. It does not classify input, produce a proposal or draft, create or modify a vault, accept an approval, file a permanent note, link a goal or project, or emit audit events.

## How Claude Code and Codex Get the Information

A coding tool does not automatically know the contents of any chat, the source PDF, or files outside its working directory.

To use Metis:

1. Open a terminal in this folder.
2. Start your coding tool from here.
3. Codex and Cursor read `AGENTS.md`; Claude Code loads `CLAUDE.md`, which imports the same file.
4. Review its plan before authorizing any implementation.

Because all Metis documents are inside the working folder, the tool can read them when instructed. Keep the governing documents separate from the instruction file; `AGENTS.md` should stay short and link outward rather than embedding strategy.

If you want a fresh read-only assessment rather than implementation, use the audit prompt in [Section 17 of the execution blueprint](METIS-EXECUTION-BLUEPRINT.md#17-first-safe-claude-code-audit-prompt).

## How to Handle Coding-Tool Results

Save useful output as a dated proposal or audit. Treat it as a recommendation until its claims have been checked against files and tests.

You may also bring the result into a later conversation for an independent review. When doing so, include the exact prompt given, the complete output, relevant error messages, the files created or changed, and any decisions you approved.

## Safety Rules

- Preserve original source material.
- Do not silently convert an AI summary into verified knowledge.
- Require human approval for permanent knowledge changes and consequential actions.
- Begin integrations with the least privilege possible.
- Keep secrets outside the vault and repository.
- Do not call a capability complete without test evidence.
- Add infrastructure only when a real, measured need justifies it.

## Maintaining These Documents

Different files change at different rates, by design:

| Document | Expected churn | What changes |
|---|---|---|
| Master prompt | None | Only if the vision itself changes |
| Execution blueprint | Minimal | Phases marked complete |
| Decisions | Append-only | New ADRs supersede old ones; existing records are never rewritten |
| Schemas | Moderate | Design meets reality — expect added fields and states |
| Requirement ledger | Continuous | Status moves from Missing to Verified as tests land |
| AGENTS.md | Moderate | Commands, current phase, and build order — but never the ten non-negotiable rules |

A requirement moves to **Verified** only when a test run or observed behavior proves it, never because a document mentions it. Changing a non-negotiable rule in `AGENTS.md` is a governance change and requires its own decision record.

## Naming

Metis is named for the Greek figure associated with wisdom, skill, and prudent counsel. The name is intended to serve as an umbrella for a growing ecosystem while keeping judgment and deliberate action at its center.
