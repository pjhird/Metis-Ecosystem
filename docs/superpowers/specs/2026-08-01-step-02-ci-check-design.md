# Build-Order Step 2: Pull-Request CI Check Design

**Status:** Approved design recorded for written review

**Branch:** `step/02-capture`

**Build-order scope:** Step 2 delivery governance only; no Step 3 behavior

## 1. Purpose

PR #2 is blocked because `main` requires the status context `metis/step-1-tests`, but neither `main` nor
`step/02-capture` contains a workflow that can report it. This change adds a real, repeatable pull-request test
check and replaces the stale step-specific protection requirement only after the new check succeeds.

The workflow is evidence for the existing test suite, not evidence that later Metis build steps exist. It does
not change any requirement-ledger status.

## 2. Scope

### Included

- Add `.github/workflows/metis-tests.yml`.
- Run the complete standard-library test suite for pull requests targeting `main`.
- Publish one stable job context named `metis/tests`.
- Grant the workflow read-only repository contents permission.
- Install the existing `setuptools` build-system requirement before running the suite.
- Add a repository-contract test before the workflow file so the change follows red-green TDD.
- Push the verified change to the existing `step/02-capture` branch and observe the real GitHub Actions result.
- After `metis/tests` succeeds, replace `metis/step-1-tests` with `metis/tests` in the `main` protection rule.

### Excluded

- No Step 3 classification, model adapter, prompt, confidence, or raw-response behavior.
- No change to PR draft state, reviews, merge method, auto-merge, or merge state.
- No removal or weakening of pull-request, up-to-date-branch, conversation-resolution, administrator,
  force-push, or deletion protections.
- No new Metis runtime dependency, test framework, secret, token, artifact upload, cache, matrix, scheduled
  run, push run, or deployment. The workflow may install only the `setuptools` build-system requirement already
  declared by `pyproject.toml`.
- No requirement-ledger status change.
- `METIS-EXECUTION-SPINE.md` remains uninspected, unmodified, unstaged, and uncommitted.

## 3. Approaches considered

### Selected: stable `metis/tests` context

The workflow reports `metis/tests`, and branch protection is updated only after that context has completed
successfully on PR #2. The name remains accurate as later build steps extend the same complete suite.

Trade-off: this requires one explicit protected-branch settings change after CI is proven.

### Rejected: preserve `metis/step-1-tests`

A workflow could emit the already-required context without changing protection. This is mechanically smaller,
but the name would remain misleading for Step 2 and every later step.

### Rejected: remove the required check

Removing the required context would unblock the PR without establishing automated evidence. That would weaken
ADR-019's fail-closed code-governance gate and is not acceptable.

## 4. Workflow contract

`.github/workflows/metis-tests.yml` will have this behavior:

- Trigger only on `pull_request` events whose base branch is `main`.
- Set top-level `permissions: contents: read`.
- Define one job with display name `metis/tests`.
- Run on GitHub's Ubuntu hosted runner.
- Set up Python 3.13, matching the recorded Step-2 verification version family.
- Check out the pull-request source.
- Run `python -m pip install setuptools` to supply the declared build backend absent from the clean runner.
- Run `python -m unittest discover -s tests -v` from the repository root.
- Report failure honestly through the job result; do not retry, suppress, or convert failures to success.

The workflow does not install Metis or any runtime dependency. It installs `setuptools` because
`pyproject.toml` declares `setuptools.build_meta` as the build backend and the packaging test deliberately runs
offline with build isolation disabled. GitHub's clean Python 3.13 runner does not otherwise provide that
backend.

## 5. Test and verification design

Implementation follows this sequence:

1. Add `test_pull_request_ci_workflow_contract` to `tests/test_repository_skeleton.py`.
2. Run that focused test and confirm it fails because `.github/workflows/metis-tests.yml` does not exist.
3. Add the minimal workflow file.
4. Re-run the focused repository-skeleton module and confirm it passes.
5. Run `python3 -m unittest discover -s tests -v` and record the exact result, including existing warnings.
6. Run `git diff --check`, inspect the complete diff, and confirm only the design, plan, test, and workflow are
   changed.
7. Commit with `REQ-TEST-002` and `ADR-019` traceability trailers, then push to `step/02-capture`.
8. Observe the GitHub Actions run on PR #2. A local pass is not a substitute for this remote result.

The repository-contract test will assert the durable observable contract: file location, pull-request trigger,
`main` target, read-only contents permission, stable job name, Python 3.13 selection, declared build-backend
installation, and exact full-suite command. GitHub's workflow parser and runner remain the authoritative
validation of YAML syntax and execution.

The first remote run proved the original workflow incomplete: 77 tests passed, but the packaging test failed
with `BackendUnavailable: Cannot import 'setuptools.build_meta'`. The human owner approved the narrow
build-backend installation amendment on 2026-08-01. Node runtime deprecation messages from the two official
actions were non-failing and remain outside this fix.

## 6. Protection transition

The protection rule remains unchanged while the new workflow is unproven. After `metis/tests` reports success:

1. Open the existing classic branch-protection rule for `main`.
2. Remove only the stale required context `metis/step-1-tests`.
3. Add only the successful context `metis/tests` as required from any source.
4. Preserve every other protection value exactly.
5. Save only after an action-time confirmation because this changes repository governance.
6. Reopen PR #2 and verify the required check is satisfied while the PR remains draft and unmerged.

If the new workflow fails, branch protection is not changed. The failure is investigated as Step-2 CI work;
no check is bypassed or manually marked successful.

## 7. Acceptance criteria

- The new repository-contract test is observed failing before the workflow exists and passing afterward.
- The complete local suite passes, with warnings reported accurately.
- PR #2 receives a successful `metis/tests` GitHub Actions result for the published head.
- `main` requires `metis/tests` and no longer waits for `metis/step-1-tests`.
- All other branch protections remain unchanged.
- PR #2 remains draft, open, and unmerged.
- The Step-2 branch contains no Step-3 implementation and no execution-spine change.
