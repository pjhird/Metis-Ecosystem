"""Command-line entry point for explicit Metis operations."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional, Sequence

from .approval import ApprovalRunResult, ApprovalRunStatus, ApprovalService
from .capture import CaptureResult, CaptureService, CaptureStatus
from .classification import (
    ClassificationResult,
    ClassificationService,
    ClassificationStatus,
)
from .classification_evidence import ClassificationEvidenceStore
from .data_access import SQLiteStateStore
from .draft_notes import LINK_TARGET, DraftNoteStore
from .evidence import EvidenceStore
from .filing import FilingResult, FilingService, FilingStatus
from .proposal import ProposalResult, ProposalService, ProposalStatus
from .proposal_content import ProposalContentStore
from .proposal_evidence import ProposalEvidenceStore


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    runtime_root: Optional[Path] = None,
    model_adapter_factory: Optional[Callable[[], object]] = None,
) -> int:
    parser = argparse.ArgumentParser(prog="metis")
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("text")
    # `--as` is a Python keyword, so the destination is named explicitly.
    capture_parser.add_argument("--as", dest="type_pin", choices=("goal", "project"))
    capture_parser.add_argument("--goal", dest="parent_goal_id")
    classify_parser = subparsers.add_parser("classify")
    classify_parser.add_argument("capture_id")
    propose_parser = subparsers.add_parser("propose")
    propose_parser.add_argument("capture_id")
    subparsers.add_parser("approvals")
    file_parser = subparsers.add_parser("file")
    file_parser.add_argument("capture_id")
    arguments = parser.parse_args(argv)

    if arguments.command == "capture":
        # Usage errors exit before any service is built, so no evidence is written.
        if arguments.type_pin == "project" and arguments.parent_goal_id is None:
            parser.error("--as project requires --goal <goal-id>")
        if arguments.parent_goal_id is not None and arguments.type_pin != "project":
            parser.error("--goal is only valid with --as project")
        if (
            arguments.parent_goal_id is not None
            and LINK_TARGET.fullmatch(arguments.parent_goal_id) is None
        ):
            parser.error("--goal must be a note id matching [A-Za-z0-9._-]+")

    root = Path.cwd() if runtime_root is None else Path(runtime_root)
    initialized = False
    try:
        with SQLiteStateStore(root / "state" / "metis.db") as state_store:
            state_store.initialize()
            initialized = True
            if arguments.command == "capture":
                result = CaptureService(state_store, EvidenceStore(root)).capture(
                    arguments.text,
                    type_pin=arguments.type_pin,
                    parent_goal_id=arguments.parent_goal_id,
                )
            elif arguments.command == "classify":
                if model_adapter_factory is None:
                    from .model_adapters.claude import ClaudeModelAdapter

                    model_adapter = ClaudeModelAdapter()
                else:
                    model_adapter = model_adapter_factory()
                result = ClassificationService(
                    state_store,
                    EvidenceStore(root),
                    ClassificationEvidenceStore(root),
                    model_adapter,
                    root,
                ).classify(arguments.capture_id)
            elif arguments.command == "approvals":
                result = ApprovalService(
                    state_store,
                    ProposalContentStore(root),
                    DraftNoteStore(root),
                    EvidenceStore(root),
                    root,
                ).review()
            elif arguments.command == "file":
                result = FilingService(
                    state_store,
                    EvidenceStore(root),
                    ClassificationEvidenceStore(root),
                    ProposalEvidenceStore(root),
                    ProposalContentStore(root),
                    DraftNoteStore(root),
                    DraftNoteStore(root, stage="filed"),
                    root,
                ).file(arguments.capture_id)
            else:
                if model_adapter_factory is None:
                    from .model_adapters.claude import ClaudeModelAdapter

                    model_adapter = ClaudeModelAdapter()
                else:
                    model_adapter = model_adapter_factory()
                result = ProposalService(
                    state_store,
                    EvidenceStore(root),
                    ClassificationEvidenceStore(root),
                    ProposalEvidenceStore(root),
                    ProposalContentStore(root),
                    DraftNoteStore(root),
                    model_adapter,
                    root,
                ).propose(arguments.capture_id)
    except Exception:
        if initialized:
            raise
        if arguments.command == "capture":
            result = CaptureResult(
                CaptureStatus.FAILED,
                None,
                None,
                "state_initialization_failed",
                "state initialization failed",
            )
        elif arguments.command == "classify":
            result = ClassificationResult(
                ClassificationStatus.FAILED,
                arguments.capture_id,
                None,
                None,
                None,
                None,
                None,
                None,
                "state_initialization_failed",
                "state initialization failed",
            )
        elif arguments.command == "approvals":
            result = ApprovalRunResult(
                status=ApprovalRunStatus.FAILED,
                decisions=(),
                reason="state_initialization_failed",
                message="state initialization failed",
            )
        elif arguments.command == "file":
            result = FilingResult(
                status=FilingStatus.FAILED,
                capture_id=arguments.capture_id,
                proposal_id=None,
                approval_id=None,
                filed_path=None,
                links=(),
                committed_at=None,
                intake_state=None,
                reason="state_initialization_failed",
                message="state initialization failed",
            )
        else:
            result = ProposalResult(
                status=ProposalStatus.FAILED,
                capture_id=arguments.capture_id,
                classification_id=None,
                proposal_id=None,
                note_type=None,
                title=None,
                confidence=None,
                sensitivity=None,
                risk_level=None,
                raw_response_path=None,
                content_path=None,
                draft_path=None,
                intake_state=None,
                reason="state_initialization_failed",
                message="state initialization failed",
            )

    payload = asdict(result)
    payload["status"] = result.status.value
    if isinstance(result, ApprovalRunResult):
        for entry, decision in zip(payload["decisions"], result.decisions):
            entry["status"] = decision.status.value
    output = sys.stderr if result.status.value == "failed" else sys.stdout
    print(json.dumps(payload, sort_keys=True), file=output)
    return 1 if result.status.value == "failed" else 0
