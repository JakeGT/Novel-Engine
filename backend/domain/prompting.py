"""Keeping an author's words apart from instructions in what is sent to a model."""

from __future__ import annotations

import re


def fence(label: str, *texts: str) -> tuple[str, str]:
    """Marker lines that appear in none of the texts, so nothing inside can fake an end.

    Deterministic: the first numbered marker absent from every text. Untrusted words go
    between them and the instructions say such words are material, never instructions
    (the delimiting form of "spotlighting", Hines et al., Microsoft, 2024).
    """
    # Every number already written after the label, found in one pass over each text
    # (looking again for each number took time growing with the square of the text).
    pattern = re.compile(re.escape(f"{label}-") + r"(\d+)>>>")
    used = {found for text in texts for found in pattern.findall(text)}
    number = 1
    while str(number) in used:
        number += 1
    return f"<<<{label}-{number}>>>", f"<<<END {label}-{number}>>>"


__all__ = ["fence"]
