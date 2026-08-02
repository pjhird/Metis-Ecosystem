"""Thin Anthropic adapter for the bounded classification request."""

from __future__ import annotations

import os
from typing import Callable, Mapping, Optional

import anthropic

from .contracts import (
    ModelConfigurationError,
    ModelRequestError,
    ModelResponse,
    ModelResponseRefused,
    ModelResponseTruncated,
    UnsupportedModelResponse,
)


DEFAULT_MODEL = "claude-sonnet-4-6"
CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_type": {
            "type": "string",
            "enum": ["idea", "reference", "decision", "question", "task"],
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
}


class ClaudeModelAdapter:
    def __init__(
        self,
        *,
        environment: Optional[Mapping[str, str]] = None,
        client_factory: Callable[..., object] = anthropic.Anthropic,
    ) -> None:
        self._environment = os.environ if environment is None else environment
        self._client_factory = client_factory
        self._model_id = self._environment.get(
            "METIS_CLASSIFICATION_MODEL",
            DEFAULT_MODEL,
        )

    def classify(self, prompt: str) -> ModelResponse:
        api_key = self._environment.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key or not self._model_id.strip():
            raise ModelConfigurationError(
                "model_configuration_failed",
                "Anthropic API key is not configured",
            )

        try:
            client = self._client_factory(api_key=api_key)
            response = client.messages.create(
                model=self._model_id,
                max_tokens=128,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": CLASSIFICATION_SCHEMA,
                    }
                },
            )
        except anthropic.APIError as error:
            raise ModelRequestError(
                "model_request_failed",
                "Anthropic request failed",
            ) from error

        try:
            model_id = response.model
            content = response.content
            stop_reason = response.stop_reason
        except AttributeError as error:
            raise UnsupportedModelResponse(
                "model_response_invalid",
                "Anthropic response shape is unsupported",
            ) from error

        raw_text, valid_content = self._response_text(content)
        if (
            not isinstance(model_id, str)
            or not model_id
            or not valid_content
            or not isinstance(stop_reason, str)
        ):
            raise UnsupportedModelResponse(
                "model_response_invalid",
                "Anthropic response shape is unsupported",
                model_id=model_id if isinstance(model_id, str) else None,
                raw_text=raw_text,
            )
        if stop_reason == "refusal":
            raise ModelResponseRefused(
                "model_response_refused",
                "Anthropic response was refused",
                model_id=model_id,
                raw_text=raw_text,
            )
        if stop_reason == "max_tokens":
            raise ModelResponseTruncated(
                "model_response_truncated",
                "Anthropic response was truncated",
                model_id=model_id,
                raw_text=raw_text,
            )
        if stop_reason not in {"end_turn", "stop_sequence"}:
            raise UnsupportedModelResponse(
                "model_response_invalid",
                "Anthropic response shape is unsupported",
                model_id=model_id,
                raw_text=raw_text,
            )
        return ModelResponse(model_id=model_id, raw_text=raw_text)

    def _response_text(self, content: object) -> tuple[Optional[str], bool]:
        if not isinstance(content, list):
            return None, False
        text_blocks = [
            getattr(block, "text", None)
            for block in content
            if getattr(block, "type", None) == "text"
            and isinstance(getattr(block, "text", None), str)
        ]
        raw_text = text_blocks[0] if len(text_blocks) == 1 else None
        return raw_text, len(content) == 1 and raw_text is not None
