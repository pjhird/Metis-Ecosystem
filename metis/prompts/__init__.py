"""Versioned prompts packaged with Metis."""

from importlib.resources import files


PROMPT_VERSION = "classify-v1"
PROPOSAL_PROMPT_VERSION = "propose-v1"


def load_classification_prompt() -> str:
    return (
        files("metis.prompts")
        .joinpath(f"{PROMPT_VERSION}.txt")
        .read_text(encoding="utf-8")
    )


def load_proposal_prompt() -> str:
    return (
        files("metis.prompts")
        .joinpath(f"{PROPOSAL_PROMPT_VERSION}.txt")
        .read_text(encoding="utf-8")
    )
