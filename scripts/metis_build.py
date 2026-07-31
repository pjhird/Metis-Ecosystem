#!/usr/bin/env python3
"""Run one Metis build-order step as a Cursor agent.

Usage:
    python scripts/metis_build.py 1                 # run build-order step 1
    python scripts/metis_build.py 1 --follow-up "also add a Makefile"
    python scripts/metis_build.py --list

One step per invocation, on that step's branch, with the human reviewing and
merging before the next step runs. This mirrors the governance in AGENTS.md:
the orchestrator sequences work, a human owns every gate.

Exit codes: 0 = run finished, 1 = agent never started (auth/config/network),
2 = run started but failed.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

REPO = Path(__file__).resolve().parent.parent
STATE_FILE = REPO / ".metis-build" / "agents.json"

# Build order from AGENTS.md. Do not skip ahead; each step ships with its
# tests before the next begins.
STEPS = {
    1: ("step/01-skeleton", "Repository skeleton, data-access layer, schema migrations, test harness"),
    2: ("step/02-capture", "Capture - evidence store, hashing, capture ID, replay protection"),
    3: ("step/03-classify", "Classify - model adapter, prompt versioning, confidence, raw-response preservation"),
    4: ("step/04-propose", "Propose - proposal record, draft note written to vault/notes/proposed/"),
    5: ("step/05-approve", "Approve - the approval command reads status, records the decision"),
    6: ("step/06-file", "File - note committed to vault/notes/filed/ with provenance and links"),
    7: ("step/07-audit", "Audit - every transition emits an event; end-to-end acceptance test"),
}

PROMPT_TEMPLATE = """\
You are implementing one step of the Metis build inside this repository.

Before writing anything, read AGENTS.md in the repository root and obey every
rule in it, especially the non-negotiable rules, the coding standard, the git
workflow, and the required tests. Also consult METIS-DECISIONS.md,
METIS-SCHEMAS.md, and METIS-REQUIREMENT-LEDGER.md where they apply.

Implement build-order step {number} ONLY:

    {title}

Constraints:
- You are already on branch {branch}. Commit to this branch only. Never touch main.
- Ship the step's tests in the same change and run them before claiming done.
- Use commit trailers (Requirement:, Decision:, Test:) as AGENTS.md specifies.
- Do not implement later steps, deferred capabilities, or anything in the
  "Do not build yet" list.
- If a rule conflict or missing decision blocks you, stop and report it
  instead of improvising.

Finish with the completion report format required by AGENTS.md.
"""


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def stream_run(run) -> int:
    print(f"agent run started: run_id={run.id}", flush=True)
    for chunk in run.iter_text():
        print(chunk, end="", flush=True)
    result = run.wait()
    print(f"\n\nrun {result.id} finished with status: {result.status}")
    if result.status == "error":
        print("The run started but failed. Inspect the branch and transcript.", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Metis build step as a Cursor agent.")
    parser.add_argument("step", type=int, nargs="?", help="build-order step number (1-7)")
    parser.add_argument("--follow-up", metavar="MSG", help="send a follow-up to the step's existing agent")
    parser.add_argument("--model", default="composer-2.5", help="model id (default: composer-2.5)")
    parser.add_argument("--list", action="store_true", help="list steps and any recorded agent ids")
    args = parser.parse_args()

    state = load_state()

    if args.list:
        for n, (branch, title) in STEPS.items():
            agent_id = state.get(str(n), {}).get("agent_id", "-")
            print(f"{n}. [{branch}] {title}\n   agent: {agent_id}")
        return 0

    if args.step not in STEPS:
        parser.error("step must be 1-7 (or use --list)")

    api_key = os.environ.get("CURSOR_API_KEY")
    if not api_key:
        print("CURSOR_API_KEY is not set. Create a key at cursor.com/dashboard -> Integrations.", file=sys.stderr)
        return 1

    branch, title = STEPS[args.step]

    try:
        if args.follow_up:
            agent_id = state.get(str(args.step), {}).get("agent_id")
            if not agent_id:
                print(f"No recorded agent for step {args.step}; run the step first.", file=sys.stderr)
                return 1
            with Agent.resume(agent_id, AgentOptions(api_key=api_key)) as agent:
                run = agent.send(args.follow_up)
                return stream_run(run)

        if git("status", "--porcelain"):
            print("Working tree is not clean. Commit or stash before starting a step.", file=sys.stderr)
            return 1
        existing = git("branch", "--list", branch)
        git("switch", branch) if existing else git("switch", "-c", branch)
        print(f"on branch {branch}")

        with Agent.create(
            model=args.model,
            api_key=api_key,
            local=LocalAgentOptions(cwd=str(REPO)),
        ) as agent:
            print(f"agent created: agent_id={agent.agent_id}")
            state[str(args.step)] = {"agent_id": agent.agent_id, "branch": branch}
            save_state(state)
            run = agent.send(PROMPT_TEMPLATE.format(number=args.step, title=title, branch=branch))
            code = stream_run(run)

        if code == 0:
            print(
                f"\nNext: review branch {branch}, run its tests, merge it, "
                f"then run step {args.step + 1}." if args.step < 7 else
                f"\nNext: review branch {branch}, run the end-to-end acceptance test, and merge."
            )
        return code

    except CursorAgentError as err:
        print(f"agent failed to start: {err} (retryable={err.is_retryable})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
