"""Deterministic validation and rendering for proposal semantics."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass


RESPONSE_KEYS = {"title", "body", "reason", "uncertainties"}
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
)
TOKEN_PATTERN = re.compile(
    r"(?:^|[^A-Za-z0-9])"
    r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16})"
    r"(?:$|[^A-Za-z0-9])"
)
ASSIGNMENT_PATTERN = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
    r"\b\s*[:=]\s*\S{8,}",
    re.IGNORECASE,
)
RAW_HTML_PATTERN = re.compile(r"<[A-Za-z!/][^>]*>")


class ProposalContentError(ValueError):
    """Raised when model-supplied proposal content is not safe and valid."""


class ProposalContentPolicyRefusal(ProposalContentError):
    """Raised when otherwise parseable content violates a safety policy."""


@dataclass(frozen=True)
class SemanticProposal:
    title: str
    body: str
    reason: str
    uncertainties: tuple[str, ...]


def parse_proposal_response(raw_text: str) -> SemanticProposal:
    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in pairs:
            if key in payload:
                raise ProposalContentError("proposal response keys are duplicated")
            payload[key] = value
        return payload

    try:
        payload = json.loads(raw_text, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, TypeError, UnicodeError) as error:
        raise ProposalContentError("proposal response is not valid JSON") from error
    if type(payload) is not dict or set(payload) != RESPONSE_KEYS:
        raise ProposalContentError("proposal response keys are invalid")

    title = _bounded_trimmed_text(payload["title"], 160, "title")
    body = _validated_body(payload["body"])
    reason = _bounded_trimmed_text(payload["reason"], 1000, "reason")
    uncertainties_value = payload["uncertainties"]
    if type(uncertainties_value) is not list or len(uncertainties_value) > 10:
        raise ProposalContentError("proposal uncertainties are invalid")
    uncertainties = tuple(
        _bounded_trimmed_text(value, 500, "uncertainty")
        for value in uncertainties_value
    )
    if len(set(uncertainties)) != len(uncertainties):
        raise ProposalContentError("proposal uncertainties are duplicated")

    for value in (title, body, reason, *uncertainties):
        _reject_credentials(value)
    _reject_unsafe_markdown(body)
    return SemanticProposal(title, body, reason, uncertainties)


def render_proposal_body(content: SemanticProposal) -> bytes:
    uncertainty_text = (
        "".join(f"- {value}\n" for value in content.uncertainties)
        if content.uncertainties
        else "None identified by the proposal model.\n"
    )
    rendered = (
        f"{content.body}\n\n"
        f"## Proposal rationale\n"
        f"{content.reason}\n\n"
        f"## Uncertainties\n"
        f"{uncertainty_text}"
    )
    return rendered.encode("utf-8")


def risk_for_sensitivity(sensitivity: str) -> str:
    if sensitivity == "normal":
        return "low"
    if sensitivity == "sensitive":
        return "high"
    raise ProposalContentError("proposal sensitivity is invalid")


def _bounded_trimmed_text(value: object, maximum: int, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ProposalContentError(f"proposal {field} is invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ProposalContentError(f"proposal {field} is invalid") from error
    if value != value.strip() or _contains_control(value):
        raise ProposalContentError(f"proposal {field} is invalid")
    return value


def _validated_body(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ProposalContentError("proposal body is invalid")
    try:
        body_bytes = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ProposalContentError("proposal body is invalid") from error
    if (
        len(body_bytes) > 20_000
        or value.startswith("\n")
        or value.endswith("\n")
        or _contains_control(value, allow_line_feed=True)
    ):
        raise ProposalContentError("proposal body is invalid")
    return value


def _contains_control(value: str, *, allow_line_feed: bool = False) -> bool:
    return any(
        unicodedata.category(character) == "Cc"
        and not (allow_line_feed and character == "\n")
        for character in value
    )


def _reject_credentials(value: str) -> None:
    if (
        PRIVATE_KEY_PATTERN.search(value)
        or TOKEN_PATTERN.search(value)
        or ASSIGNMENT_PATTERN.search(value)
    ):
        raise ProposalContentPolicyRefusal(
            "proposal content violates credential policy"
        )


def _reject_unsafe_markdown(body: str) -> None:
    if (
        body.split("\n", 1)[0] == "---"
        or RAW_HTML_PATTERN.search(body)
        or "![" in body
        or "](" in body
        or "[[" in body
        or "data:" in body.lower()
    ):
        raise ProposalContentPolicyRefusal(
            "proposal content violates Markdown policy"
        )
