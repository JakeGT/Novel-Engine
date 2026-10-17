"""What to read when a scene is read into the story again (Phase 6)."""

from backend.domain.reading import changed_passages

ONE = "Wren swept the mill.\n\nThe wheel turned.\n\nA sack was missing."


def _texts(current: str, previous: str | None, **kw) -> list[str]:
    return [current[p.start : p.end] for p in changed_passages(current, previous, **kw)]


def test_the_first_reading_reads_everything_as_one_passage() -> None:
    assert _texts(ONE, None) == [ONE]


def test_nothing_changed_means_nothing_to_read() -> None:
    assert _texts(ONE, ONE) == []
    # Spacing alone is not a change.
    assert _texts(ONE.replace("The wheel", "The  wheel"), ONE) == []


def test_only_new_or_changed_paragraphs_are_read_and_neighbours_go_together() -> None:
    changed = "Wren swept the mill.\n\nThe wheel stopped.\n\nTobin came in.\n\nA sack was missing."
    assert _texts(changed, ONE) == ["The wheel stopped.\n\nTobin came in."]


def test_changes_apart_are_read_apart_and_long_runs_are_split() -> None:
    changed = "Wren slept.\n\nThe wheel turned.\n\nTwo sacks were missing."
    assert _texts(changed, ONE) == ["Wren slept.", "Two sacks were missing."]
    long = "\n\n".join(f"Paragraph {n} is new." for n in range(4))
    assert len(_texts(long, None, limit=30)) == 4
    assert len(_texts(long, None, limit=40)) == 2
