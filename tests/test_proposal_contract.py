from __future__ import annotations

import json
import unittest

from metis.proposal_contract import (
    ProposalContentError,
    SemanticProposal,
    parse_proposal_response,
    render_proposal_body,
    risk_for_sensitivity,
)


VALID = {
    "title": "Review this idea",
    "body": "A reviewable proposal body.",
    "reason": "It follows directly from the captured idea.",
    "uncertainties": ["The delivery date is unknown."],
}


def encoded(**changes: object) -> str:
    payload = dict(VALID)
    payload.update(changes)
    return json.dumps(payload, ensure_ascii=False)


class ProposalContractTests(unittest.TestCase):
    def test_valid_response_returns_frozen_semantic_content(self) -> None:
        result = parse_proposal_response(encoded())

        self.assertEqual(
            result,
            SemanticProposal(
                title="Review this idea",
                body="A reviewable proposal body.",
                reason="It follows directly from the captured idea.",
                uncertainties=("The delivery date is unknown.",),
            ),
        )
        with self.assertRaises(AttributeError):
            result.title = "changed"

    def test_exact_keys_and_duplicate_keys_are_required(self) -> None:
        cases = (
            json.dumps({key: value for key, value in VALID.items() if key != "reason"}),
            encoded(extra="not allowed"),
            (
                '{"title":"First","title":"Second","body":"Body",'
                '"reason":"Reason","uncertainties":[]}'
            ),
            "[]",
        )
        for raw_text in cases:
            with self.subTest(raw_text=raw_text):
                with self.assertRaises(ProposalContentError):
                    parse_proposal_response(raw_text)

    def test_fields_reject_wrong_json_types(self) -> None:
        cases = (
            {"title": None},
            {"body": True},
            {"reason": 1},
            {"uncertainties": {}},
            {"uncertainties": [1]},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ProposalContentError):
                    parse_proposal_response(encoded(**changes))

    def test_title_boundaries_whitespace_lines_and_controls_are_enforced(self) -> None:
        self.assertEqual(
            parse_proposal_response(encoded(title="t" * 160)).title,
            "t" * 160,
        )
        for title in ("", "t" * 161, " padded", "padded ", "two\nlines", "bad\tcontrol"):
            with self.subTest(title=title):
                with self.assertRaises(ProposalContentError):
                    parse_proposal_response(encoded(title=title))

    def test_body_utf8_boundaries_and_controls_are_enforced(self) -> None:
        self.assertEqual(
            len(parse_proposal_response(encoded(body="é" * 10_000)).body.encode("utf-8")),
            20_000,
        )
        for body in (
            "",
            "é" * 10_001,
            "\nleading",
            "trailing\n",
            "carriage\rreturn",
            "nul\x00byte",
            "tab\tcontrol",
            "invalid surrogate \ud800",
        ):
            with self.subTest(body=body[:20]):
                with self.assertRaises(ProposalContentError):
                    parse_proposal_response(encoded(body=body))

    def test_reason_and_uncertainty_bounds_are_enforced(self) -> None:
        self.assertEqual(
            len(parse_proposal_response(encoded(reason="r" * 1000)).reason),
            1000,
        )
        self.assertEqual(
            len(
                parse_proposal_response(
                    encoded(uncertainties=[str(index) for index in range(10)])
                ).uncertainties
            ),
            10,
        )
        cases = (
            {"reason": ""},
            {"reason": "r" * 1001},
            {"reason": " padded"},
            {"reason": "bad\ncontrol"},
            {"uncertainties": [str(index) for index in range(11)]},
            {"uncertainties": [""]},
            {"uncertainties": ["u" * 501]},
            {"uncertainties": [" padded"]},
            {"uncertainties": ["same", "same"]},
            {"uncertainties": ["bad\tcontrol"]},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ProposalContentError):
                    parse_proposal_response(encoded(**changes))

    def test_credential_patterns_are_policy_refusals(self) -> None:
        secrets = (
            "-----BEGIN PRIVATE KEY-----",
            "sk-1234567890abcdef",
            "ghp_12345678901234567890",
            "github_pat_12345678901234567890",
            "xoxb-1234567890",
            "AKIA1234567890ABCDEF",
            "api_key=12345678",
            "password:12345678",
        )
        for secret in secrets:
            with self.subTest(secret=secret[:12]):
                with self.assertRaises(ProposalContentError):
                    parse_proposal_response(encoded(body=f"Contains {secret}"))

    def test_unsafe_markdown_forms_are_policy_refusals(self) -> None:
        bodies = (
            "---\nfrontmatter-like body",
            "<script>alert(1)</script>",
            "![image](https://example.test/image.png)",
            "[link](https://example.test)",
            "[[Goal]]",
            "![[embedded-note]]",
            "DATA:text/plain,unsafe",
        )
        for body in bodies:
            with self.subTest(body=body):
                with self.assertRaises(ProposalContentError):
                    parse_proposal_response(encoded(body=body))

    def test_canonical_body_rendering_is_exact(self) -> None:
        result = parse_proposal_response(encoded())

        self.assertEqual(
            render_proposal_body(result),
            (
                "A reviewable proposal body.\n\n"
                "## Proposal rationale\n"
                "It follows directly from the captured idea.\n\n"
                "## Uncertainties\n"
                "- The delivery date is unknown.\n"
            ).encode("utf-8"),
        )
        no_uncertainties = parse_proposal_response(encoded(uncertainties=[]))
        self.assertTrue(
            render_proposal_body(no_uncertainties).endswith(
                b"## Uncertainties\nNone identified by the proposal model.\n"
            )
        )

    def test_risk_is_derived_only_from_sensitivity(self) -> None:
        self.assertEqual(risk_for_sensitivity("normal"), "low")
        self.assertEqual(risk_for_sensitivity("sensitive"), "high")
        for value in ("public", "", None, True):
            with self.subTest(value=value):
                with self.assertRaises(ProposalContentError):
                    risk_for_sensitivity(value)


if __name__ == "__main__":
    unittest.main()
