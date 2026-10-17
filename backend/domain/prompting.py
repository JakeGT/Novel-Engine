"""Keeping an author's words apart from instructions in what is sent to a model."""

from __future__ import annotations


def fence(label: str, *texts: str) -> tuple[str, str]:
    """Marker lines that appear in none of the texts, so nothing inside can fake an end.

    Deterministic: the first numbered marker absent from every text. Untrusted words go
    between them and the instructions say such words are material, never instructions
    (the delimiting form of "spotlighting", Hines et al., Microsoft, 2024).
    """
    number = 1
    while any(f"{label}-{number}>>>" in text for text in texts):
        number += 1
    return f"<<<{label}-{number}>>>", f"<<<END {label}-{number}>>>"


__all__ = ["fence"]
