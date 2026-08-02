"""Command-line entry point for typed capture."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Sequence

from .capture import CaptureResult, CaptureService, CaptureStatus
from .data_access import SQLiteStateStore
from .evidence import EvidenceStore


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    runtime_root: Optional[Path] = None,
) -> int:
    parser = argparse.ArgumentParser(prog="metis")
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("text")
    arguments = parser.parse_args(argv)

    root = Path.cwd() if runtime_root is None else Path(runtime_root)
    initialized = False
    try:
        with SQLiteStateStore(root / "state" / "metis.db") as state_store:
            state_store.initialize()
            initialized = True
            result = CaptureService(state_store, EvidenceStore(root)).capture(
                arguments.text
            )
    except Exception as error:
        if initialized:
            raise
        result = CaptureResult(
            CaptureStatus.FAILED,
            None,
            None,
            "state_initialization_failed",
            str(error),
        )

    payload = asdict(result)
    payload["status"] = result.status.value
    output = sys.stderr if result.status is CaptureStatus.FAILED else sys.stdout
    print(json.dumps(payload, sort_keys=True), file=output)
    return 1 if result.status is CaptureStatus.FAILED else 0
