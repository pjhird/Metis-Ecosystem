"""Identifier generation and validation."""

from __future__ import annotations

import os
import time
from typing import Callable


ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ALPHABET_SET = frozenset(ALPHABET)


def new_ulid(
    *,
    clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    random_bytes: Callable[[int], bytes] = os.urandom,
) -> str:
    timestamp = clock_ms()
    randomness = random_bytes(10)
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        raise ValueError("ULID timestamp must be an integer")
    if not 0 <= timestamp < 2**48:
        raise ValueError("ULID timestamp is outside the 48-bit range")
    if not isinstance(randomness, bytes) or len(randomness) != 10:
        raise ValueError("ULID randomness must contain exactly ten bytes")
    value = (timestamp << 80) | int.from_bytes(randomness, "big")
    return "".join(
        ALPHABET[(value >> shift) & 31] for shift in range(125, -1, -5)
    )


def is_ulid(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 26
        and value[0] in "01234567"
        and all(character in ALPHABET_SET for character in value)
    )
