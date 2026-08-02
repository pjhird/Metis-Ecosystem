# Build-Order Step 2 Pull-Request CI Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real `metis/tests` pull-request check, prove it locally and on PR #2, and replace the orphaned `metis/step-1-tests` branch-protection requirement without weakening any other gate.

**Architecture:** A single read-only GitHub Actions workflow runs the existing complete `unittest` suite for pull requests targeting `main`. The workflow is written as JSON-compatible YAML so Python's standard library can parse and validate its complete configuration during TDD; GitHub remains the authoritative execution environment. Branch protection changes only after the new context succeeds.

**Tech Stack:** GitHub Actions, JSON-compatible YAML, Python 3.13, Python standard library (`json`, `pathlib`, `unittest`), Git, GitHub connector/browser

## Global Constraints

- Work only from the published `step/02-capture` history and push only to `step/02-capture`; never push to `main`.
- Keep PR #2 draft, open, and unmerged; do not enable auto-merge or request reviews.
- Add no Step 3 classification, model, prompt, confidence, or raw-response behavior.
- Add no project dependency, secret, token, cache, artifact upload, matrix, scheduled trigger, push trigger, or deployment.
- The workflow may install only `setuptools`, the build-system requirement already declared by `pyproject.toml`.
- The workflow grants only `contents: read` and runs only for pull requests targeting `main`.
- The required check context is exactly `metis/tests`.
- Preserve every existing branch-protection value except replacing `metis/step-1-tests` with `metis/tests` after the new check succeeds.
- Do not update requirement-ledger statuses.
- Do not inspect, modify, stage, rename, or commit `METIS-EXECUTION-SPINE.md`.
- Use red-green TDD for the repository workflow contract and fresh verification before every success claim.

---

### Task 1: Add the Pull-Request Test Workflow With Red-Green TDD

**Files:**
- Modify: `tests/test_repository_skeleton.py:1-50`
- Create: `.github/workflows/metis-tests.yml`

**Interfaces:**
- Consumes: `REPOSITORY_ROOT`, the existing standard-library test suite, and the `>=3.11` Python contract in `pyproject.toml`.
- Produces: a parseable workflow object whose sole job reports the exact status context `metis/tests` and runs `python -m unittest discover -s tests -v` on pull requests targeting `main`.

- [ ] **Step 1: Add the failing repository workflow contract test**

Add `import json` before `import subprocess` in `tests/test_repository_skeleton.py`, then add this method after `test_governance_entrypoints_exist`:

```python
    def test_pull_request_ci_workflow_contract(self) -> None:
        workflow_path = (
            REPOSITORY_ROOT / ".github" / "workflows" / "metis-tests.yml"
        )
        self.assertTrue(workflow_path.is_file(), "pull-request CI workflow is missing")

        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

        self.assertEqual(
            workflow,
            {
                "name": "Metis tests",
                "on": {"pull_request": {"branches": ["main"]}},
                "permissions": {"contents": "read"},
                "jobs": {
                    "tests": {
                        "name": "metis/tests",
                        "runs-on": "ubuntu-latest",
                        "steps": [
                            {
                                "name": "Check out repository",
                                "uses": "actions/checkout@v4",
                            },
                            {
                                "name": "Set up Python",
                                "uses": "actions/setup-python@v5",
                                "with": {"python-version": "3.13"},
                            },
                            {
                                "name": "Install build backend",
                                "run": "python -m pip install setuptools",
                            },
                            {
                                "name": "Run test suite",
                                "run": "python -m unittest discover -s tests -v",
                            },
                        ],
                    }
                },
            },
        )
```

This test catches a missing workflow, a wrong trigger or target branch, expanded permissions, a renamed required context, a changed Python version, an added step, or a test command that no longer runs the complete suite. Its expected object is a hand-written literal and does not reuse workflow implementation logic.

- [ ] **Step 2: Run the focused test and verify the RED state**

Run:

```bash
python3 -m unittest tests.test_repository_skeleton.RepositorySkeletonTests.test_pull_request_ci_workflow_contract -v
```

Expected: `FAIL`, with `AssertionError: False is not true : pull-request CI workflow is missing`. If the test errors for another reason or passes, fix the test setup before continuing.

- [ ] **Step 3: Add the minimal workflow**

Create `.github/workflows/metis-tests.yml` with exactly this JSON-compatible YAML document:

```json
{
  "name": "Metis tests",
  "on": {
    "pull_request": {
      "branches": ["main"]
    }
  },
  "permissions": {
    "contents": "read"
  },
  "jobs": {
    "tests": {
      "name": "metis/tests",
      "runs-on": "ubuntu-latest",
      "steps": [
        {
          "name": "Check out repository",
          "uses": "actions/checkout@v4"
        },
        {
          "name": "Set up Python",
          "uses": "actions/setup-python@v5",
          "with": {
            "python-version": "3.13"
          }
        },
        {
          "name": "Install build backend",
          "run": "python -m pip install setuptools"
        },
        {
          "name": "Run test suite",
          "run": "python -m unittest discover -s tests -v"
        }
      ]
    }
  }
}
```

Do not add dependency installation beyond the declared `setuptools` build backend, caching, matrices, extra
events, retries, `continue-on-error`, or write permissions.

- [ ] **Step 4: Run the focused test and verify the GREEN state**

Run:

```bash
python3 -m unittest tests.test_repository_skeleton.RepositorySkeletonTests.test_pull_request_ci_workflow_contract -v
```

Expected: one test, `OK`.

- [ ] **Step 5: Run the repository-skeleton module**

Run:

```bash
python3 -m unittest tests.test_repository_skeleton -v
```

Expected: three tests, `OK`.

- [ ] **Step 6: Run the complete Step-2 suite**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/metis-step-02-ci-pycache python3 -m unittest discover -s tests -v
```

Expected: 78 tests, `OK`. Record the exact count and all warnings. The known SQLite `ResourceWarning` messages are non-failing but must not be described as absent if they appear.

- [ ] **Step 7: Verify the local change boundary**

Run:

```bash
git diff --check
git status --short
git diff --name-only HEAD
git diff -- tests/test_repository_skeleton.py .github/workflows/metis-tests.yml
```

Expected: no whitespace errors; only `tests/test_repository_skeleton.py` and `.github/workflows/metis-tests.yml` are uncommitted implementation files. The already committed design and plan appear only in commit history. Do not run a broad command that reads `METIS-EXECUTION-SPINE.md`.

- [ ] **Step 8: Commit the verified workflow and test**

Stage only the two implementation files:

```bash
git add tests/test_repository_skeleton.py .github/workflows/metis-tests.yml
git diff --cached --check
git diff --cached --name-only
```

Expected staged paths:

```text
.github/workflows/metis-tests.yml
tests/test_repository_skeleton.py
```

Commit:

```bash
git commit -m "ci: run the Metis suite on pull requests" \
  -m "Add a read-only PR check with a stable metis/tests context so protected main requires real test evidence." \
  -m $'Requirement: REQ-TEST-002\nDecision: ADR-019\nTest: test_pull_request_ci_workflow_contract\nCo-Authored-By: Codex <codex@openai.com>'
```

Verify the trailers:

```bash
git show -s --format=%B HEAD | git interpret-trailers --parse
```

Expected: all four contiguous trailers parse.

---

### Task 2: Publish and Verify the New GitHub Actions Context

**Files:**
- No file changes

**Interfaces:**
- Consumes: the locally verified commits descending from published Step-2 head `390b561da19078d42f4e28ac154f8783d5c46918`.
- Produces: an updated remote `step/02-capture` head and an actual GitHub Actions result named `metis/tests` on PR #2.

- [ ] **Step 1: Verify the exact history to publish**

Run:

```bash
git merge-base --is-ancestor 390b561da19078d42f4e28ac154f8783d5c46918 HEAD
git log --oneline --decorate 390b561da19078d42f4e28ac154f8783d5c46918..HEAD
git status --short --branch
```

Expected: the ancestry command exits `0`; the log contains only the CI design, CI implementation plan, and CI workflow/test commits; the worktree is clean and may remain detached.

- [ ] **Step 2: Push only to the Step-2 branch**

Run:

```bash
git push origin HEAD:step/02-capture
```

Expected: `origin/step/02-capture` advances from `390b561...` to the verified implementation commit. Do not push a tag or any ref named `main`.

- [ ] **Step 3: Verify PR identity and workflow discovery**

Using the GitHub connector, fetch PR #2 and the workflow runs for the new head SHA.

Expected:

- PR #2 remains `open`, `draft`, base `main`, head `step/02-capture`.
- The PR head SHA equals the pushed implementation commit.
- A pull-request-triggered workflow run appears for the new head.
- The reported job/check name is `metis/tests`.

- [ ] **Step 4: Wait for the workflow result**

Use bounded GitHub status/workflow polling until the run reaches a terminal state.

Expected success: `metis/tests` completes successfully for the new head SHA.

If it fails, do not touch branch protection. Record the run URL and failure evidence. Because the local `gh` credential was invalid during inspection, first request `gh auth login -h github.com` if the GitHub Actions CI-fix workflow requires CLI logs. Fix only a demonstrated Step-2 CI issue through another red-green-review cycle.

- [ ] **Step 5: Re-run local completion checks after publication**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse origin/step/02-capture
```

Expected: clean worktree; local HEAD and `origin/step/02-capture` resolve to the same implementation commit.

---

### Task 3: Replace the Orphaned Protected-Branch Check

**Files:**
- No file changes

**Interfaces:**
- Consumes: a successful `metis/tests` result on the current PR #2 head and the existing classic protection rule for `main`.
- Produces: the same protection rule with only its required status context changed from `metis/step-1-tests` to `metis/tests`.

- [ ] **Step 1: Re-inspect the protection rule before mutation**

Open the classic branch-protection rule for `main` read-only and confirm:

- pull request required;
- zero approvals required;
- CODEOWNERS approval not required;
- status checks required;
- branch must be up to date;
- current required context is only `metis/step-1-tests` from any source;
- conversation resolution required;
- administrator bypass disabled;
- force pushes disabled;
- deletion disabled;
- no repository ruleset adds another requirement.

If any value differs, stop and report the drift rather than harmonizing it silently.

- [ ] **Step 2: Obtain action-time confirmation**

Immediately before saving the settings form, ask the human owner to authorize this exact mutation:

```text
Replace only the required status context metis/step-1-tests with the already-successful metis/tests context on main; preserve every other protection value.
```

Do not treat earlier design or implementation approval as a substitute for this action-time permission change confirmation.

- [ ] **Step 3: Apply only the confirmed status-context replacement**

In the existing `main` protection rule:

1. Add `metis/tests` as a required check from any source.
2. Remove `metis/step-1-tests`.
3. Reconfirm every other displayed control matches Step 1.
4. Save once.

Do not mark PR #2 ready, approve it, merge it, enable auto-merge, change review requirements, or alter any other repository setting.

- [ ] **Step 4: Verify the protection result and PR state**

Reopen the branch-protection rule and PR #2.

Expected:

- required status context is only `metis/tests`;
- the successful current-head `metis/tests` result satisfies the check gate;
- conversation count remains zero;
- PR #2 remains open, draft, and unmerged;
- the merge button remains disabled because draft state is intentionally unchanged;
- no files or commits were created by the settings transition.

- [ ] **Step 5: Produce the completion report**

Report:

- design, plan, test, and workflow commits and pushed head SHA;
- RED and GREEN focused-test evidence;
- complete local suite count and warnings;
- GitHub Actions run result and URL;
- exact protection setting changed and settings preserved;
- PR draft/open/unmerged state;
- no Step 3 work and no execution-spine access or change;
- any residual limitation, including the SQLite warnings or CLI-authentication state.
