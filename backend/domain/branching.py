"""Which line of the story a request works in (ADR-020).

The main story is the default. A request that names a what-if carries its branch
id here for the whole request, so every story read is filtered by it without each
caller passing it along. Only the API layer sets it, and only after checking the
branch belongs to the project in scope.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_WORKING_BRANCH: ContextVar[str | None] = ContextVar("novel_engine_working_branch", default=None)


def working_branch_id() -> str | None:
    """The what-if this request works in, or ``None`` for the main story."""
    return _WORKING_BRANCH.get()


def set_working_branch(branch_id: str | None) -> None:
    _WORKING_BRANCH.set(branch_id)


@contextmanager
def working_in(branch_id: str | None) -> Iterator[None]:
    """Work in one line of the story for the duration of a block."""
    token = _WORKING_BRANCH.set(branch_id)
    try:
        yield
    finally:
        _WORKING_BRANCH.reset(token)


__all__ = ["set_working_branch", "working_branch_id", "working_in"]
