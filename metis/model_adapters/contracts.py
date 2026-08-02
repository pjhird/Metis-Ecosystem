"""Provider-neutral model request and response contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class ModelResponse:
    model_id: str
    raw_text: str


class ModelAdapterError(RuntimeError):
    def __init__(
        self,
        reason: str,
        message: str,
        *,
        model_id: Optional[str] = None,
        raw_text: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.model_id = model_id
        self.raw_text = raw_text


class ModelConfigurationError(ModelAdapterError):
    """Raised when model configuration is absent or invalid."""


class ModelRequestError(ModelAdapterError):
    """Raised when the provider request does not return a response."""


class ModelResponseRefused(ModelAdapterError):
    """Raised when the provider refuses to classify the input."""


class ModelResponseTruncated(ModelAdapterError):
    """Raised when the provider stops before completing its response."""


class UnsupportedModelResponse(ModelAdapterError):
    """Raised when the provider response shape cannot be interpreted."""


@runtime_checkable
class ModelAdapter(Protocol):
    def classify(self, prompt: str) -> ModelResponse:
        """Return exact assistant text and the actual model ID."""
