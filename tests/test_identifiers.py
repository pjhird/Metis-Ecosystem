from __future__ import annotations

import unittest

from metis.identifiers import is_ulid, new_ulid


class IdentifierTests(unittest.TestCase):
    def test_zero_ulid_is_canonical(self) -> None:
        self.assertEqual(
            new_ulid(
                clock_ms=lambda: 0,
                random_bytes=lambda size: b"\0" * size,
            ),
            "00000000000000000000000000",
        )

    def test_one_millisecond_uses_the_timestamp_prefix(self) -> None:
        self.assertEqual(
            new_ulid(
                clock_ms=lambda: 1,
                random_bytes=lambda size: b"\0" * size,
            ),
            "00000000010000000000000000",
        )

    def test_generated_value_is_canonical(self) -> None:
        value = new_ulid()

        self.assertTrue(is_ulid(value))

    def test_validation_rejects_noncanonical_values(self) -> None:
        values = (None, "", "0" * 25, "8" + "0" * 25, "i" * 26, "I" * 26)

        for value in values:
            with self.subTest(value=value):
                self.assertFalse(is_ulid(value))

    def test_generation_rejects_invalid_sources(self) -> None:
        cases = (
            {"clock_ms": lambda: True, "random_bytes": lambda size: b"\0" * size},
            {"clock_ms": lambda: -1, "random_bytes": lambda size: b"\0" * size},
            {
                "clock_ms": lambda: 2**48,
                "random_bytes": lambda size: b"\0" * size,
            },
            {"clock_ms": lambda: 0, "random_bytes": lambda size: b"\0" * 9},
        )

        for factories in cases:
            with self.subTest(factories=factories):
                with self.assertRaises(ValueError):
                    new_ulid(**factories)


if __name__ == "__main__":
    unittest.main()
