"""Exact, explainable term normalization used for vocabulary lookup."""
from __future__ import annotations

import re
import unicodedata


def normalize_term(value: str) -> str:
    """NFKC/casefold and collapse whitespace; punctuation deliberately remains."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()
