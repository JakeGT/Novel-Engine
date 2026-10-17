"""Writing an author brings in: source texts, the names found in them, and name memory.

A source text is the author's own prose, kept exactly as given. Each change to it
is a new immutable version, so evidence that points at characters 120–180 of a
version always points at the same words. A source text is not canon: nothing in
it is true in the story until the author keeps a change that cites it.

Finding names is deterministic. An explicit mark (``@Mara``, ``@"the Fox"``,
``[[the Old Mill]]``) is the strongest signal; a plain occurrence of a known name
or alias is the next. No model is involved, so the same text and the same story
bible always give the same answer.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.naming import normalize_name

MAX_SOURCE_LENGTH = 400_000
MAX_TITLE_LENGTH = 200
MAX_SOURCES_PER_PROJECT = 500
MAX_VERSIONS_PER_SOURCE = 200
MAX_SECTIONS = 400
MAX_MENTIONS = 5000
MAX_PHRASE_LENGTH = 120
MAX_QUESTIONS = 200
EXCERPT_MARGIN = 80


class SourceKind(StrEnum):
    PASTE = "paste"
    MARKDOWN = "markdown"
    # A planned scene's prose, written or kept in the scene studio (ADR-022).
    SCENE = "scene"


class VersionOrigin(StrEnum):
    AUTHOR = "author"
    # A draft from the assistant that the author kept.
    ASSISTANT = "assistant"


class Verdict(StrEnum):
    # The phrase means this entry.
    IS = "is"
    # The phrase does not mean this entry.
    IS_NOT = "is_not"
    # The phrase is not a name at all; stop asking.
    IGNORE = "ignore"
    # A model's guess, waiting for the author. Never used to resolve anything.
    SUGGESTED = "suggested"


@dataclass(frozen=True)
class SourceText:
    workspace_id: str
    project_id: str
    id: str
    title: str
    kind: SourceKind
    created_by: str | None
    created_at: datetime
    removed_at: datetime | None = None
    versions: int = 0
    latest_version_id: str | None = None
    length: int = 0


@dataclass(frozen=True)
class SourceVersion:
    workspace_id: str
    project_id: str
    id: str
    source_id: str
    number: int
    content: str
    content_hash: str
    created_by: str | None
    created_at: datetime
    # Where these words came from (ADR-022). Texts brought in are the author's.
    origin: VersionOrigin = VersionOrigin.AUTHOR
    parent_version_id: str | None = None
    draft_id: str | None = None
    manifest_id: str | None = None


@dataclass(frozen=True)
class AliasBinding:
    """What an author decided a phrase means, or does not mean."""

    workspace_id: str
    project_id: str
    id: str
    phrase: str
    normalized: str
    verdict: Verdict
    entity_id: str | None
    # Set when the decision holds only inside one text; None means everywhere.
    source_id: str | None
    decided_by: str | None
    decided_at: datetime


@dataclass(frozen=True)
class Section:
    index: int
    title: str
    start: int
    end: int


class MentionStatus(StrEnum):
    KNOWN = "known"
    # More than one entry answers to this phrase.
    UNCLEAR = "unclear"
    # Marked by the author, but nothing in the story bible answers to it.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Mention:
    start: int
    end: int
    # The words as written, without any mark around them.
    phrase: str
    # True when the author marked it; False for a plain occurrence of a known name.
    marked: bool
    status: MentionStatus
    entity_ids: tuple[str, ...] = ()


@dataclass
class NameQuestion:
    """One thing to ask the author about a phrase: who is this?"""

    phrase: str
    normalized: str
    count: int
    first_start: int
    first_end: int
    # Entries it could be, most likely first. Empty when nothing answers to it.
    candidates: list[str] = field(default_factory=list)
    # True when the candidates are a model's guess rather than a matching name.
    guessed: bool = False


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def clean_content(content: str) -> str:
    """Normalise line endings only. The words are never touched."""
    return content.replace("\r\n", "\n").replace("\r", "\n")


_HEADING = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t#]*$", re.MULTILINE)
HEADING = _HEADING


def split_sections(content: str) -> list[Section]:
    """Split on Markdown headings (levels 1–3). Text with none is one section.

    Derived on read from an immutable version, so it needs no storage and cannot
    drift from the words it describes.
    """
    headings = list(_HEADING.finditer(content))[: MAX_SECTIONS - 1]
    if not headings:
        return [Section(0, "", 0, len(content))] if content.strip() else []
    sections: list[Section] = []
    if content[: headings[0].start()].strip():
        sections.append(Section(0, "", 0, headings[0].start()))
    for position, heading in enumerate(headings):
        end = headings[position + 1].start() if position + 1 < len(headings) else len(content)
        sections.append(
            Section(
                len(sections), heading.group(2).strip()[:MAX_TITLE_LENGTH], heading.start(), end
            )
        )
    return sections


# @Mara · @"the Fox" · [[the Old Mill]]
_MARK = re.compile(
    r"@\"(?P<quoted>[^\"\n]{1,120})\""
    r"|\[\[(?P<bracket>[^\[\]\n]{1,120})\]\]"
    r"|(?<![\w@])@(?P<word>[^\W\d_][\w'’\-]{0,60})",
    re.UNICODE,
)


def _phrase_pattern(labels: Iterable[str]) -> re.Pattern[str] | None:
    ordered = sorted({label for label in labels if label.strip()}, key=len, reverse=True)
    if not ordered:
        return None
    body = "|".join(re.escape(label).replace(r"\ ", r"\s+") for label in ordered)
    return re.compile(rf"(?<![\w@])(?:{body})(?!\w)", re.IGNORECASE | re.UNICODE)


@dataclass
class NameIndex:
    """Everything that answers to a phrase, for one text."""

    # normalized phrase → entity ids, in a stable order
    answers: dict[str, list[str]]
    # normalized phrase → ids the author ruled out
    ruled_out: dict[str, set[str]]
    # normalized phrases the author said are not names
    ignored: set[str]
    # normalized phrase → the label as written in the story bible
    labels: dict[str, str]

    def resolve(self, phrase: str) -> list[str]:
        key = normalize_name(phrase)
        out = self.ruled_out.get(key, set())
        return [entity_id for entity_id in self.answers.get(key, []) if entity_id not in out]


def build_index(
    entities: Sequence[CanonEntity],
    bindings: Sequence[AliasBinding],
    *,
    source_id: str | None,
) -> NameIndex:
    answers: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    ruled_out: dict[str, set[str]] = {}
    ignored: set[str] = set()
    active = {entity.id for entity in entities if entity.is_active}

    def add(label: str, entity_id: str) -> None:
        key = normalize_name(label)
        if not key:
            return
        labels.setdefault(key, label)
        found = answers.setdefault(key, [])
        if entity_id not in found:
            found.append(entity_id)

    for entity in sorted(entities, key=lambda item: item.id):
        if not entity.is_active:
            continue
        for label in (entity.name, *entity.aliases):
            add(label, entity.id)

    # A decision about one text beats a decision made everywhere.
    scoped = [item for item in bindings if item.source_id in (None, source_id)]
    for binding in sorted(scoped, key=lambda item: (item.source_id is not None, item.decided_at)):
        key = binding.normalized
        if binding.verdict is Verdict.IGNORE:
            ignored.add(key)
        elif binding.entity_id is None or binding.entity_id not in active:
            continue
        elif binding.verdict is Verdict.IS:
            ignored.discard(key)
            ruled_out.get(key, set()).discard(binding.entity_id)
            labels.setdefault(key, binding.phrase)
            # The author named one entry for this phrase: it stands alone.
            answers[key] = [binding.entity_id]
        elif binding.verdict is Verdict.IS_NOT:
            ruled_out.setdefault(key, set()).add(binding.entity_id)
    return NameIndex(answers=answers, ruled_out=ruled_out, ignored=ignored, labels=labels)


def _capital_ok(label: str, written: str) -> bool:
    """A one-word name is only found with its capital: "Hope" the person, not "hope"."""
    if " " in label.strip() or not label[:1].isupper():
        return True
    return written[:1].isupper()


def find_mentions(content: str, index: NameIndex) -> list[Mention]:
    """Every marked phrase, and every plain occurrence of a known name. Deterministic."""
    found: list[Mention] = []
    taken: list[tuple[int, int]] = []

    for match in _MARK.finditer(content):
        phrase = (match.group("quoted") or match.group("bracket") or match.group("word")).strip()
        if not phrase or normalize_name(phrase) in index.ignored:
            continue
        ids = index.resolve(phrase)
        status = (
            MentionStatus.KNOWN
            if len(ids) == 1
            else MentionStatus.UNCLEAR
            if ids
            else MentionStatus.UNKNOWN
        )
        found.append(Mention(match.start(), match.end(), phrase, True, status, tuple(ids)))
        taken.append((match.start(), match.end()))
        if len(found) >= MAX_MENTIONS:
            return found

    pattern = _phrase_pattern(
        label for key, label in index.labels.items() if key not in index.ignored
    )
    if pattern is None:
        return sorted(found, key=lambda item: item.start)
    for match in pattern.finditer(content):
        start, end = match.start(), match.end()
        if any(start < other_end and other_start < end for other_start, other_end in taken):
            continue
        written = match.group(0)
        key = normalize_name(written)
        label = index.labels.get(key)
        if label is None or not _capital_ok(label, written):
            continue
        ids = index.resolve(written)
        if not ids:
            continue
        status = MentionStatus.KNOWN if len(ids) == 1 else MentionStatus.UNCLEAR
        found.append(Mention(start, end, written, False, status, tuple(ids)))
        if len(found) >= MAX_MENTIONS:
            break
    return sorted(found, key=lambda item: item.start)


def name_questions(
    mentions: Sequence[Mention],
    suggestions: Sequence[AliasBinding] = (),
    *,
    content: str = "",
    index: NameIndex | None = None,
) -> list[NameQuestion]:
    """What is worth asking, most frequent first. One question per phrase."""
    questions: dict[str, NameQuestion] = {}
    for mention in mentions:
        if mention.status is MentionStatus.KNOWN:
            continue
        key = normalize_name(mention.phrase)
        question = questions.get(key)
        if question is None:
            questions[key] = NameQuestion(
                phrase=mention.phrase,
                normalized=key,
                count=1,
                first_start=mention.start,
                first_end=mention.end,
                candidates=list(mention.entity_ids),
            )
        else:
            question.count += 1

    # A model's guess about an epithet is only ever a question.
    for suggestion in suggestions:
        if suggestion.verdict is not Verdict.SUGGESTED or suggestion.entity_id is None:
            continue
        key = suggestion.normalized
        if index is not None and (key in index.ignored or index.resolve(suggestion.phrase)):
            continue
        if index is not None and suggestion.entity_id in index.ruled_out.get(key, set()):
            continue
        pattern = _phrase_pattern([suggestion.phrase])
        hits = list(pattern.finditer(content)) if pattern is not None else []
        if not hits:
            continue
        question = questions.get(key)
        if question is None:
            questions[key] = NameQuestion(
                phrase=suggestion.phrase,
                normalized=key,
                count=len(hits),
                first_start=hits[0].start(),
                first_end=hits[0].end(),
                candidates=[suggestion.entity_id],
                guessed=True,
            )
        elif suggestion.entity_id not in question.candidates:
            question.candidates.append(suggestion.entity_id)
    ordered = sorted(questions.values(), key=lambda item: (-item.count, item.first_start))
    return ordered[:MAX_QUESTIONS]


_LOCATION = re.compile(r"^chars:(\d{1,9})-(\d{1,9})(,implied)?$")


def location_for(start: int, end: int, *, implied: bool = False) -> str:
    """A pointer at a passage. ``implied`` records that the words suggest it, not state it."""
    return f"chars:{start}-{end}" + (",implied" if implied else "")


def is_implied(location: str | None) -> bool:
    match = _LOCATION.match(location or "")
    return match is not None and match.group(3) is not None


def parse_location(location: str | None) -> tuple[int, int] | None:
    match = _LOCATION.match(location or "")
    if match is None:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    return (start, end) if start < end else None


@dataclass(frozen=True)
class Excerpt:
    before: str
    quote: str
    after: str


def excerpt(content: str, start: int, end: int, *, margin: int = EXCERPT_MARGIN) -> Excerpt:
    start = max(0, min(start, len(content)))
    end = max(start, min(end, len(content)))
    return Excerpt(
        before=content[max(0, start - margin) : start],
        quote=content[start:end],
        after=content[end : end + margin],
    )


def passage_bounds(content: str, start: int, end: int | None, *, limit: int) -> tuple[int, int]:
    """At most ``limit`` characters from ``start``, ending at a paragraph or sentence if cut."""
    start = max(0, min(start, len(content)))
    wanted = len(content) if end is None else max(start, min(end, len(content)))
    if wanted - start <= limit:
        return start, wanted
    hard = start + limit
    floor = start + limit // 2
    for breaker in ("\n\n", "\n", ". "):
        cut = content.rfind(breaker, floor, hard)
        if cut != -1:
            return start, cut + len(breaker)
    return start, hard


def find_quote(
    content: str, quote: str, *, within: tuple[int, int] | None = None
) -> tuple[int, int] | None:
    """Where a quoted passage sits in the text, tolerant of spacing; None if it is not there."""
    words = quote.split()
    if not words:
        return None
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
    lo, hi = within if within is not None else (0, len(content))
    match = pattern.search(content, lo, hi)
    return (match.start(), match.end()) if match is not None else None
