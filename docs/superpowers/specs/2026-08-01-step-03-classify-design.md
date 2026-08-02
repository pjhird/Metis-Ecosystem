# Step 3 Classification Design

## Status

- Build-order step: 3 — Classify
- Design approved by the human owner: 2026-08-01
- Implementation status: not started
- Base: public `origin/main` at merge commit `12bcc88e36872a23095e4a4e7e206ecd1884dab2`
- Governing decisions: ADR-002, ADR-003, ADR-007, ADR-008, ADR-014, ADR-016, ADR-017, ADR-019
- Primary requirements: REQ-INTK-001, REQ-INTK-003, REQ-INTK-005, REQ-MODEL-001,
  REQ-MODEL-002, REQ-MODEL-003, REQ-ORCH-001, REQ-TEST-003

## 1. Objective

Add one explicit classification operation for an existing immutable typed capture:

```text
metis classify <capture_id>
```

The operation revalidates the capture and its evidence, calls Claude through a provider-neutral model seam,
preserves the model's raw response before interpretation, validates a bounded classification, derives routing
deterministically, and records the result in SQLite. It never creates a proposal or writes to the vault.

Classification is separate from `metis capture`. Capture therefore remains local and independent of model
credentials, network availability, latency, and provider failures. A classification failure can be inspected
and retried without resubmitting or duplicating the source.

## 2. Scope

### Included

- One `metis classify <capture_id>` CLI subcommand.
- A deterministic `ClassificationService` that owns classification-related state transitions and routing.
- A provider-neutral `ModelAdapter` protocol and response/error contracts.
- One Claude adapter; it is the only module that imports the Anthropic SDK.
- One packaged, immutable prompt version, `classify-v1`.
- Exact raw assistant-response preservation in a separate append-only response-evidence store.
- Local response validation for candidate type, sensitivity, confidence, and the exact key set.
- SQLite classification persistence through the `StateStore` seam only.
- Mechanical one-classification-per-capture enforcement.
- Idempotent classification replay and bounded retry of recorded classification failures.
- Stable JSON CLI results and honest shell outcomes.
- Focused, integration, packaging, boundary, and full-suite tests.
- Conservative requirement-ledger and command documentation updates backed by fresh test evidence.

### Excluded

- Automatic classification during capture.
- Confidence thresholds, confidence-based routing, or auto-approval.
- Proposal generation, draft notes, an Obsidian vault, approval, filing, linking, or audit events.
- Semantic duplicate detection.
- Background workers, file watchers, runtime agents, or agent/skill registries.
- Provider routing, fallback providers, model selection by a model, or an agent framework.
- Cost accounting, retry budgets, cloud deployment, containers, MCP, or other integrations.
- Any inspection or change to `METIS-EXECUTION-SPINE.md`.

## 3. Architecture

```text
CLI: metis classify <capture_id>
                |
                v
       ClassificationService
       - validates row/evidence agreement
       - owns state transitions
       - renders versioned prompt
       - validates model result
       - derives routing
          |          |          |
          v          v          v
     StateStore  ModelAdapter  ClassificationResponseStore
          |          |          |
          v          v          v
       SQLite      Claude     classification-evidence/
```

Control flows down from `ClassificationService`. The model adapter never reaches persistence, never calls
another capability, and never selects routing or authority. The response store never updates operational
state. SQL remains confined to the data-access layer.

## 4. Public contracts

### 4.1 Classification result

The classification service returns a frozen result with these fields:

- `status`: `classified`, `duplicate`, `refused`, or `failed`
- `capture_id`
- `classification_id`, nullable until an attempt ID is safely allocated or on preflight refusal
- `candidate_type`, nullable unless a valid classification exists
- `sensitivity`, nullable unless a valid classification exists
- `confidence`, nullable unless a valid classification exists
- `routing`, nullable unless a valid classification exists
- `raw_response_path`, nullable when no model response was received or safely finalized
- `reason`, a stable machine-readable reason code or null
- `message`, a safe human-readable description or null

The CLI always serializes the complete, stable key set as one sorted JSON object.

### 4.2 Model adapter

The provider-neutral interface accepts a rendered prompt and returns:

- `model_id`: the model actually used
- `raw_text`: the exact assistant text returned by the provider

The adapter has bounded exceptions for configuration failure, request failure, refusal, truncation, and an
unsupported response shape. A provider refusal or truncation exception carries the received model ID and raw
assistant text so the service can preserve it before returning failure. Network or authentication failures
that produce no assistant response carry no raw text.

Only `metis/model_adapters/claude.py` imports `anthropic`. The base `anthropic` package is the only new runtime
dependency; no Agent SDK, dotenv package, model router, or provider framework is added. The compatible SDK
range is `anthropic>=0.104,<1`.

### 4.3 Model configuration

- `ANTHROPIC_API_KEY` supplies the credential through the environment.
- `claude-sonnet-4-6` is the pinned default classification model.
- `METIS_CLASSIFICATION_MODEL` may override the model ID.
- The adapter records the model ID returned by the provider rather than assuming the requested value.
- Secrets are never written to Git, SQLite, evidence, CLI output, or exception messages.

The CLI sends the captured text to the configured Claude API because ADR-008 explicitly selects Claude as the
classification reasoning engine. No other external call is permitted in this step.

## 5. Prompt and output contract

The prompt is packaged at:

```text
metis/prompts/classify-v1.txt
```

The prompt treats the captured text as untrusted data, asks for classification rather than action, and requests
an exact JSON object. A substantive prompt change creates a new immutable prompt file and version identifier;
it never edits the meaning of `classify-v1` in place.

The requested model object is exactly:

```json
{
  "candidate_type": "idea",
  "sensitivity": "normal",
  "confidence": 0.82
}
```

Allowed values and constraints:

- `candidate_type`: `idea`, `reference`, `decision`, `question`, or `task`
- `sensitivity`: `normal` or `sensitive`
- `confidence`: a JSON number from `0.0` through `1.0`, excluding booleans and non-finite values
- no missing or additional keys

The Claude adapter requests structured JSON output. `ClassificationService` nevertheless parses and validates
the raw text locally because refusal, truncation, SDK drift, or a provider defect can fall outside the requested
schema.

Routing is deterministic code, never model output:

| Candidate type | Routing |
|---|---|
| `idea` | `proposal:idea` |
| `reference` | `proposal:reference` |
| `decision` | `proposal:decision` |
| `question` | `proposal:question` |
| `task` | `proposal:task` |

Confidence is recorded and returned without changing routing or authority. The open confidence-threshold
question remains open until real outputs can be calibrated.

## 6. Raw-response evidence

Model output is evidence of what the model said, not permanent knowledge. Every received raw assistant response
is exclusively created before parsing under:

```text
classification-evidence/
└── <classification_id>/
    ├── raw-response.txt
    └── meta.json
```

`raw-response.txt` contains exactly `raw_text.encode("utf-8")`. It is never trimmed, normalized, reparsed, or
rewritten before storage.

`meta.json` contains exactly:

```json
{
  "classification_id": "01K1...",
  "capture_id": "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70",
  "model_id": "claude-sonnet-4-6",
  "prompt_version": "classify-v1",
  "received_at": "2026-08-01T20:00:00Z",
  "byte_size": 86,
  "schema_version": 1
}
```

The response store validates directory name, exact file set, exact metadata key set and types, UUID4 capture
ID, ULID classification ID, UTC timestamp, byte size, and agreement with the raw bytes. It uses exclusive
creation and never deletes, quarantines, completes, or repairs partial/corrupt evidence automatically.

`classification-evidence/` is ignored by Git and remains separate from `evidence/<capture_id>/`, whose Step-2
capture files are already final and must never be modified.

A standards-compliant 26-character Crockford Base32 ULID is generated with standard-library time and randomness
behind an injected ID factory. No second identifier dependency is added.

## 7. Data-access and state design

The `StateStore` protocol gains operations to:

- find an intake row by capture ID;
- find a classification by capture ID;
- atomically begin classification from an allowed state;
- atomically insert a classification and complete the transition;
- record a known classification failure.

The SQLite implementation remains the only SQL-bearing module. A new migration creates a unique index on
`classification.capture_id`, mechanically enforcing at most one successful classification per capture.

The completion transaction inserts the classification row and updates `intake.state` from `classifying` to
`classified` together. If either operation fails or the expected prior state is absent, the transaction rolls
back and the service cannot report success.

### Allowed Step-3 transitions

```text
captured --------------------------> classifying
failed (classification reason only) -> classifying
classifying ------------------------> classified
classifying ------------------------> failed
```

Rules:

- A retry from `failed` is allowed only when `failure_reason` has the `classification.` namespace and no
  successful classification row exists.
- Starting a retry clears the prior failure reason only when the compare-and-swap transition succeeds.
- A valid `classified` intake row and matching classification return `duplicate` without a model call.
- A row/classification disagreement, evidence disagreement, or undetermined persistence outcome returns
  `failed` without speculative repair.
- `proposed`, `awaiting_approval`, `approved`, `filed`, and `rejected` refuse classification.
- `classifying` refuses another classification attempt because Metis cannot determine whether another process
  is active or a previous process was interrupted.

An abandoned `classifying` state therefore requires a future governed recovery mechanism. Step 3 records this
as a limitation rather than guessing that the prior execution is dead. The original source remains intact.

## 8. Orchestration and ordering

A new attempt executes in this order:

1. Parse and validate the UUID4 capture ID.
2. Load the intake row and any classification row through `StateStore`.
3. Validate row/classification agreement and handle an idempotent replay.
4. Validate the referenced immutable capture directory and require full row/evidence agreement.
5. Allocate a ULID classification ID and whole-second UTC timestamp.
6. Atomically transition the intake to `classifying`.
7. Read the exact captured UTF-8 text and render `classify-v1` with the content clearly delimited as data.
8. Call the model adapter.
9. If any assistant text was received, exclusively write and validate response evidence before parsing.
10. Parse and validate the exact JSON contract.
11. Derive routing from the validated candidate type.
12. Atomically insert the classification and transition to `classified`.
13. Return `classified` only after the transaction commits.

A known failure after entering `classifying` attempts to record `state = failed` and a bounded
`classification.<reason>` value. If recording that failure also fails, the CLI returns `failed` with an
undetermined-state reason; it never claims the row reached `failed`.

## 9. Failure and shell contracts

Representative stable reasons:

| Condition | Status | Shell | Reason |
|---|---|---:|---|
| New valid classification | `classified` | 0 | null |
| Existing valid classification | `duplicate` | 0 | `already_classified` |
| Unknown capture ID | `refused` | 0 | `capture_not_found` |
| Determined illegal intake state | `refused` | 0 | `illegal_intake_state` |
| Existing `classifying` state | `refused` | 0 | `classification_in_progress` |
| Row/evidence or row/classification disagreement | `failed` | 1 | `classification_consistency_failed` |
| Missing or invalid provider configuration | `failed` | 1 | `model_configuration_failed` |
| Provider/network/authentication failure | `failed` | 1 | `model_request_failed` |
| Provider refusal | `failed` | 1 | `model_response_refused` |
| Truncated provider response | `failed` | 1 | `model_response_truncated` |
| Response evidence cannot be finalized | `failed` | 1 | `response_evidence_failed` |
| Raw response violates the local contract | `failed` | 1 | `model_response_invalid` |
| Classification transaction fails | `failed` | 1 | `classification_persistence_failed` |
| Failure-state recording is undetermined | `failed` | 1 | `classification_state_undetermined` |

`classified`, `duplicate`, and policy `refused` write one JSON object to stdout and exit zero. Operational
`failed` writes one JSON object to stderr and exits one. Safe messages may identify the failure category and
capture/classification IDs; they must not contain the API key, captured text, prompt text, or raw response.

## 10. Verification design

Tests use an injected deterministic fake `ModelAdapter`. Claude adapter tests inject a fake SDK client. The
ordinary suite and GitHub Actions make no paid provider call and require no API secret.

The Step-3 suite must prove:

1. A valid fixture produces the expected candidate type, sensitivity, routing, and bounded confidence.
2. The actual model ID and exact prompt version are persisted.
3. The exact assistant bytes are finalized before parsing or classification persistence.
4. Malformed, refused, truncated, and schema-invalid responses are preserved and fail explicitly.
5. `source_survives_classification_failure` preserves byte-identical capture evidence.
6. Replay returns the existing classification, makes no second model call, and leaves one classification row.
7. Response-evidence collisions and corrupt directories never overwrite or silently repair files.
8. Every classification-related illegal state edge is refused.
9. Classification insertion and the final state transition are atomic.
10. Persistence failure never reports `classified`.
11. `provider_sdk_imported_only_by_adapter` finds no Anthropic import outside the Claude adapter.
12. CLI success, duplicate, refusal, and failure produce the stable JSON and shell contracts.
13. Failure paths do not expose credentials, captured text, prompt text, or raw model output.
14. Package data includes the migration and versioned prompt, and an installed console script exposes the
    classify command and fails safely when provider configuration is absent.
15. Existing capture, evidence, migration, packaging, and boundary tests remain green.
16. The complete suite and the protected GitHub `metis/tests` check pass.

An opt-in manual live smoke test may run only when `ANTHROPIC_API_KEY` is already available in the environment.
It must use disposable state/evidence, must not print the credential or captured text, and is never a CI
requirement. If it is not run, the completion report states that real authentication, network execution, model
availability, cost, and live response behavior remain unverified.

## 11. Conservative requirement-ledger treatment

After fresh test evidence:

- REQ-INTK-001 may move to **Verified** when classification failure proves the original source survives.
- REQ-INTK-003 may move to **Verified** when the full classification contract passes fixture tests.
- REQ-INTK-005 remains **Partial** because later proposal, approval, filing, and review failures are unbuilt.
- REQ-MODEL-001 may move to **Verified** when the provider seam and import boundary pass.
- REQ-MODEL-002 remains **Partial** until proposal and vault boundaries can prove model output cannot become
  permanent knowledge without approval.
- REQ-MODEL-003 may move to **Verified** when every stored classification records `classify-v1`.
- REQ-ORCH-001 remains **Partial** because only classification-related transitions are implemented.
- REQ-TEST-003 remains **Partial** because proposal and Obsidian-note artifacts remain unimplemented.

No live-provider claim becomes Verified without an observed live smoke result. Documentation must distinguish
deterministic adapter/fixture verification from an actual Claude API call.

## 12. Completion gate

Step 3 is ready for review only when:

- the written implementation plan has been separately reviewed and approved;
- every implementation task follows red-green TDD;
- focused, module, integration, packaging, and full-suite checks pass freshly;
- GitHub `metis/tests` passes on the Step-3 pull request;
- ledger changes match only observed evidence;
- the worktree contains no runtime database, capture evidence, classification evidence, vault content, secret,
  or unrelated file;
- `METIS-EXECUTION-SPINE.md` remains uninspected, unmodified, unstaged, and uncommitted;
- the pull request remains unmerged until the human owner explicitly authorizes the merge; and
- proposal work from Step 4 has not begun.
