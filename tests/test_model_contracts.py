from __future__ import annotations

import unittest

from metis.model_adapters import (
    ModelAdapter,
    ModelConfigurationError,
    ModelRequestError,
    ModelResponse,
    ModelResponseRefused,
    ModelResponseTruncated,
    UnsupportedModelResponse,
)
from metis.prompts import (
    PROMPT_VERSION,
    PROPOSAL_PROMPT_VERSION,
    load_classification_prompt,
    load_proposal_prompt,
)


class FakeAdapter:
    def classify(self, prompt: str) -> ModelResponse:
        return ModelResponse("test-model", '{"candidate_type":"idea"}')

    def propose(self, prompt: str) -> ModelResponse:
        return ModelResponse("test-model", '{"title":"Review me"}')


class ModelContractTests(unittest.TestCase):
    def test_model_adapter_contract_is_provider_neutral(self) -> None:
        adapter = FakeAdapter()

        self.assertIsInstance(adapter, ModelAdapter)
        self.assertEqual(
            adapter.classify("prompt"),
            ModelResponse("test-model", '{"candidate_type":"idea"}'),
        )

    def test_model_adapter_contract_includes_propose(self) -> None:
        adapter = FakeAdapter()

        self.assertIsInstance(adapter, ModelAdapter)
        self.assertEqual(
            adapter.propose("prompt"),
            ModelResponse("test-model", '{"title":"Review me"}'),
        )

    def test_adapter_errors_carry_only_bounded_response_context(self) -> None:
        error_types = (
            ModelConfigurationError,
            ModelRequestError,
            ModelResponseRefused,
            ModelResponseTruncated,
            UnsupportedModelResponse,
        )

        for error_type in error_types:
            with self.subTest(error_type=error_type.__name__):
                error = error_type(
                    "bounded_reason",
                    "safe message",
                    model_id="test-model",
                    raw_text="raw response",
                )

                self.assertEqual(str(error), "safe message")
                self.assertEqual(error.reason, "bounded_reason")
                self.assertEqual(error.model_id, "test-model")
                self.assertEqual(error.raw_text, "raw response")

    def test_classification_prompt_is_immutable_version_one(self) -> None:
        self.assertEqual(PROMPT_VERSION, "classify-v1")

        prompt = load_classification_prompt()

        self.assertIn("{{CAPTURE_JSON}}", prompt)
        self.assertIn("candidate_type", prompt)
        self.assertIn("sensitivity", prompt)
        self.assertIn("confidence", prompt)
        self.assertNotIn("routing", prompt)

    def test_proposal_prompt_is_immutable_version_one(self) -> None:
        self.assertEqual(PROPOSAL_PROMPT_VERSION, "propose-v1")

        prompt = load_proposal_prompt()

        self.assertIn("{{CAPTURE_JSON}}", prompt)
        self.assertIn("{{CLASSIFICATION_JSON}}", prompt)
        self.assertIn("title", prompt)
        self.assertIn("body", prompt)
        self.assertIn("reason", prompt)
        self.assertIn("uncertainties", prompt)
        for owned_field in (
            "proposal_id",
            "status",
            "risk_level",
            "links",
            "draft_path",
        ):
            with self.subTest(owned_field=owned_field):
                self.assertNotIn(owned_field, prompt)


if __name__ == "__main__":
    unittest.main()
