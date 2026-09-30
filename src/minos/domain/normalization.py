"""Versioned search keys; never apply these transformations to source fields."""

import unicodedata

NORMALIZATION_VERSION = "1"


def normalize_description(value: str) -> str:
    # Keep punctuation and Polish letters; accent-insensitive matching is absent.
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())
