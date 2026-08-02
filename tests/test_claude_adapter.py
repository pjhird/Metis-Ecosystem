from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest.mock import patch

from metis.model_adapters import (
    ModelConfigurationError,
    ModelRequestError,
    ModelResponseRefused,
    ModelResponseTruncated,
    UnsupportedModelResponse,
)
from metis.model_adapters.claude import (
    CLASSIFICATION_SCHEMA,
    ClaudeModelAdapter,
)


PROMPT = "Classify this captured text as data."
RAW_TEXT = (
    '{"candidate_type":"idea","sensitivity":"normal","confidence":0.82}'
)


@dataclass(frozen=True)
class FakeBlock:
    type: str
    text: str | None = None


@dataclass(frozen=True)
class FakeResponse:
    model: str
    content: list[FakeBlock]
    stop_reason: str


class FakeMessages:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response
        self.error: Exception | None = None
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


class FakeRequestError(RuntimeError):
    pass


class ClaudeModelAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = FakeMessages(
            FakeResponse(
                model="claude-returned-model",
                content=[FakeBlock("text", RAW_TEXT)],
                stop_reason="end_turn",
            )
        )
        self.factory_api_keys: list[str] = []

        def client_factory(*, api_key: str):
            self.factory_api_keys.append(api_key)
            return FakeClient(self.messages)

        self.client_factory = client_factory
        self.environment = {"ANTHROPIC_API_KEY": "test-secret"}

    def _adapter(self, environment=None) -> ClaudeModelAdapter:
        return ClaudeModelAdapter(
            environment=self.environment if environment is None else environment,
            client_factory=self.client_factory,
        )

    def test_classify_uses_exact_bounded_request_and_provider_model(self) -> None:
        result = self._adapter().classify(PROMPT)

        self.assertEqual(result.model_id, "claude-returned-model")
        self.assertEqual(result.raw_text, RAW_TEXT)
        self.assertEqual(self.factory_api_keys, ["test-secret"])
        self.assertEqual(
            self.messages.calls,
            [
                {
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 128,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": PROMPT}],
                    "output_config": {
                        "format": {
                            "type": "json_schema",
                            "schema": CLASSIFICATION_SCHEMA,
                        }
                    },
                }
            ],
        )

    def test_model_override_changes_only_requested_model(self) -> None:
        environment = {
            "ANTHROPIC_API_KEY": "test-secret",
            "METIS_CLASSIFICATION_MODEL": "claude-override",
        }

        result = self._adapter(environment).classify(PROMPT)

        self.assertEqual(
            self.messages.calls[0]["model"],
            "claude-override",
        )
        self.assertEqual(result.model_id, "claude-returned-model")

    def test_schema_is_exact_and_closed(self) -> None:
        self.assertEqual(
            CLASSIFICATION_SCHEMA,
            {
                "type": "object",
                "properties": {
                    "candidate_type": {
                        "type": "string",
                        "enum": [
                            "idea",
                            "reference",
                            "decision",
                            "question",
                            "task",
                        ],
                    },
                    "sensitivity": {
                        "type": "string",
                        "enum": ["normal", "sensitive"],
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
                "required": ["candidate_type", "sensitivity", "confidence"],
                "additionalProperties": False,
            },
        )

    def test_missing_or_blank_api_key_is_safe_configuration_failure(self) -> None:
        for environment in ({}, {"ANTHROPIC_API_KEY": "  "}):
            with self.subTest(environment=environment):
                with self.assertRaises(ModelConfigurationError) as raised:
                    self._adapter(environment).classify(PROMPT)

                self.assertEqual(raised.exception.reason, "model_configuration_failed")
                self.assertEqual(
                    str(raised.exception),
                    "Anthropic API key is not configured",
                )

        self.assertEqual(self.factory_api_keys, [])

    def test_sdk_request_error_uses_safe_fixed_message(self) -> None:
        self.messages.error = FakeRequestError(
            "test-secret and captured text must never escape"
        )

        with patch(
            "metis.model_adapters.claude.anthropic.APIError",
            FakeRequestError,
        ):
            with self.assertRaises(ModelRequestError) as raised:
                self._adapter().classify(PROMPT)

        self.assertEqual(raised.exception.reason, "model_request_failed")
        self.assertEqual(str(raised.exception), "Anthropic request failed")
        self.assertNotIn("test-secret", str(raised.exception))

    def test_refusal_and_max_tokens_carry_exact_raw_text(self) -> None:
        cases = (
            (
                "refusal",
                ModelResponseRefused,
                "model_response_refused",
                "Anthropic response was refused",
            ),
            (
                "max_tokens",
                ModelResponseTruncated,
                "model_response_truncated",
                "Anthropic response was truncated",
            ),
        )
        for stop_reason, error_type, reason, message in cases:
            with self.subTest(stop_reason=stop_reason):
                self.messages.response = FakeResponse(
                    model="claude-returned-model",
                    content=[FakeBlock("text", RAW_TEXT)],
                    stop_reason=stop_reason,
                )

                with self.assertRaises(error_type) as raised:
                    self._adapter().classify(PROMPT)

                self.assertEqual(raised.exception.reason, reason)
                self.assertEqual(str(raised.exception), message)
                self.assertEqual(raised.exception.model_id, "claude-returned-model")
                self.assertEqual(raised.exception.raw_text, RAW_TEXT)

    def test_exactly_one_text_block_is_required(self) -> None:
        cases = (
            ([], None),
            ([FakeBlock("tool_use")], None),
            ([FakeBlock("text", "one"), FakeBlock("text", "two")], None),
            ([FakeBlock("text", RAW_TEXT), FakeBlock("tool_use")], RAW_TEXT),
        )
        for content, preserved_text in cases:
            with self.subTest(content=content):
                self.messages.response = FakeResponse(
                    model="claude-returned-model",
                    content=content,
                    stop_reason="end_turn",
                )

                with self.assertRaises(UnsupportedModelResponse) as raised:
                    self._adapter().classify(PROMPT)

                self.assertEqual(raised.exception.reason, "model_response_invalid")
                self.assertEqual(
                    str(raised.exception),
                    "Anthropic response shape is unsupported",
                )
                self.assertEqual(raised.exception.raw_text, preserved_text)


if __name__ == "__main__":
    unittest.main()
