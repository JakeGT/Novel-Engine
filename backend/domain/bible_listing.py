"""Which part of the story bible a request is shown, and why (CTX-002, ADR-021 amendment).

Writing a scene has its own policy (``context.py``, ``scene-draft/2``). The other requests
that read the story bible — changing anything by instruction, suggesting missing
connections, reading the author's text — used to list it up to fixed counts (200 entries,
300 facts) in whatever order the store returned, so in a large story the very entries a
request named could fall off the end of the list. Each now has a versioned listing
policy: a purpose, the sections it lists, and a word budget.

What is listed is chosen deterministically, in three tiers:

* **named**: the entries the request names, by name or alias (accents and case ignored),
  by id, the entry the author is looking at, or the names found in the passage being read;
* **near**: their neighbours, one fact, belief or event away, and the entries the author's
  own writing names in the same passages (the passage index);
* **rest**: everything else, the most important first (the author's importance mark, then
  how connected an entry is), then by name.

A fact, belief or event takes the best tier of the entries it touches. Named entries are
listed first (up to ``max_named``, and never past the budget); everything else fills what
is left of the budget tier by tier, and in
each tier section by section in the policy's order. What does not fit is counted per
section and said, to the model in the listing's last line and to the author with the
result. Rendering keeps the sections in the policy's order (the stable story bible before
anything request-specific, as in ``context.py``), and within a section the request's own
entries first, since models use the start and end of a long context best (Liu et al.,
"Lost in the Middle", TACL 2024).

The policy version is recorded with every proposal the request makes, so a reviewer can
tell which listing the assistant worked from. Nothing here calls a model.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum

from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.marks import IMPORTANCE_KEY, clean_importance


class Tier(IntEnum):
    NAMED = 0
    NEAR = 1
    REST = 2


SECTION_WORDS: dict[str, tuple[str, str]] = {
    "entries": ("entry", "entries"),
    "facts": ("fact", "facts"),
    "events": ("event", "events"),
    "beliefs": ("belief", "beliefs"),
    "threads": ("thread", "threads"),
}


@dataclass(frozen=True)
class ListingPolicy:
    """How one kind of request lists the story bible. Changing it means a new version."""

    version: str
    purpose: str
    sections: tuple[str, ...]
    # In words, as the scene policy counts them.
    budget: int
    max_named: int = 200
    # When set, each entry's details are cut to about this many words in the listing.
    detail_words: int | None = None


# About what the fixed counts used to allow (200 entries, 300 facts, 200 events, 200
# beliefs, a dozen words each), now spent on what the request is about first.
CHANGE_POLICY = ListingPolicy(
    version="change-story/1",
    purpose="Change anything in the story by one instruction.",
    sections=("entries", "facts", "events", "beliefs"),
    budget=9000,
)
# /2: entries whose details name others are near, not named (in a story where most
# details name someone, they were all listed in full over the budget), and each entry's
# details are cut short.
LINKS_POLICY = ListingPolicy(
    version="suggest-links/2",
    purpose="Find connections the entries' own words describe but no fact records.",
    sections=("entries", "facts"),
    budget=7000,
    detail_words=40,
)
READ_POLICY = ListingPolicy(
    version="read-text/1",
    purpose="Read a passage of the author's writing into suggested changes.",
    sections=("entries", "events", "threads"),
    budget=3000,
)

POLICIES: tuple[ListingPolicy, ...] = (CHANGE_POLICY, LINKS_POLICY, READ_POLICY)


@dataclass(frozen=True)
class Line:
    """One listed record: its section, its id, the text the model reads, and its rank."""

    section: str
    record_id: str
    text: str
    tier: Tier = Tier.REST
    # Order inside a tier: lower first. Ties break on the record id.
    rank: tuple[object, ...] = ()
    # The entries a record touches, for ranking; an entry touches itself.
    touches: tuple[str, ...] = ()

    @property
    def words(self) -> int:
        return max(1, len(self.text.split()))


@dataclass
class Listing:
    policy: ListingPolicy
    lines: list[Line] = field(default_factory=list)
    # section → how many were listed / left out.
    listed: dict[str, int] = field(default_factory=dict)
    left_out: dict[str, int] = field(default_factory=dict)
    named: tuple[str, ...] = ()

    @property
    def policy_version(self) -> str:
        return self.policy.version

    @property
    def words(self) -> int:
        return sum(line.words for line in self.lines)

    def section(self, name: str) -> list[Line]:
        return [line for line in self.lines if line.section == name]

    def ids(self, name: str) -> set[str]:
        return {line.record_id for line in self.lines if line.section == name}

    @property
    def complete(self) -> bool:
        return not any(self.left_out.values())

    def counts(self, which: Mapping[str, int]) -> str:
        parts = []
        for name in self.policy.sections:
            count = which.get(name, 0)
            if count:
                one, many = SECTION_WORDS.get(name, (name, name))
                parts.append(f"{count} {one if count == 1 else many}")
        if len(parts) > 1:
            return ", ".join(parts[:-1]) + " and " + parts[-1]
        return parts[0] if parts else ""

    def left_out_line(self) -> str | None:
        """The listing's last line for the model, when something was left out."""
        if self.complete:
            return None
        return (
            f"(The story is larger than this list: {self.counts(self.left_out)} less "
            "connected to this request are not listed. Ask if something seems missing.)"
        )

    def note(self) -> str | None:
        """What the author is told when something was left out, in plain words."""
        if self.complete:
            return None
        return (
            f"Your story is larger than the assistant reads at once, so it was shown what "
            f"this is about first. Not shown: {self.counts(self.left_out)}."
        )

    def provenance(self) -> str:
        """A short record of the listing, kept with the proposal: never any of the story."""
        listed = self.counts(self.listed) or "nothing"
        text = f"context {self.policy.version}: {listed} listed"
        if not self.complete:
            text += f"; {self.counts(self.left_out)} left out"
        return text


def clip_words(text: str, limit: int | None) -> str:
    """``text`` cut to ``limit`` words, marked where it was cut; as it is without a limit."""
    if limit is None:
        return text
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]) + " …"


def importance_rank(entity: CanonEntity) -> int:
    """Main first, then supporting, then unmarked, then minor and background."""
    level = clean_importance(entity.attributes.get(IMPORTANCE_KEY))
    return {"main": 0, "supporting": 1, None: 2, "minor": 3, "background": 4}[level]


_NOT_WORD = re.compile(r"[^\w]+", re.UNICODE)


def fold(text: str) -> str:
    """Words without accents, case or punctuation: "Mara's" and "MARA" both read "mara s"."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(_NOT_WORD.sub(" ", plain.casefold()).split())


_ID = re.compile(r"[A-Za-z]+_[A-Za-z0-9_\-]+")
# Names longer than this many words are not looked for in free text.
MAX_NAME_WORDS = 8


class Names:
    """Every active entry's name and aliases, folded, for finding them in free text.

    Built once per request; finding is a lookup of each run of up to eight words, so it
    stays fast for a story of thousands of entries. Whole words only: "Mara" is found in
    "Mara's boat" but not in "Maranta".
    """

    def __init__(self, entities: Iterable[CanonEntity]) -> None:
        self._labels: dict[str, list[str]] = {}
        self._ids: set[str] = set()
        longest = 1
        for entity in sorted(entities, key=lambda item: item.id):
            if not entity.is_active:
                continue
            self._ids.add(entity.id)
            for label in (entity.name, *entity.aliases):
                key = fold(label)
                if not key:
                    continue
                found = self._labels.setdefault(key, [])
                if entity.id not in found:
                    found.append(entity.id)
                longest = max(longest, len(key.split()))
        self._longest = min(longest, MAX_NAME_WORDS)

    def find(self, text: str) -> list[str]:
        """Entries the text names, by id or by name or alias, in order of first mention."""
        found: dict[str, None] = {}
        for match in _ID.finditer(text):
            if match.group(0) in self._ids:
                found.setdefault(match.group(0), None)
        words = fold(text).split()
        for start in range(len(words)):
            for size in range(min(self._longest, len(words) - start), 0, -1):
                ids = self._labels.get(" ".join(words[start : start + size]))
                if ids:
                    for entity_id in ids:
                        found.setdefault(entity_id, None)
                    break
        return list(found)


def named_in(text: str, entities: Iterable[CanonEntity]) -> list[str]:
    """Entries a request's own words name: by id, or by name or alias (accents, case and
    punctuation ignored). Deterministic and model-free."""
    if not text.strip():
        return []
    return Names(entities).find(text)


def tiers(
    entity_ids: Iterable[str],
    *,
    named: Iterable[str],
    links: Iterable[tuple[str, str]],
    near: Iterable[str] = (),
) -> dict[str, Tier]:
    """Each entry's tier: named, one link from a named entry (or in ``near``), or the rest."""
    chosen = set(named)
    result = {entity_id: Tier.REST for entity_id in entity_ids}
    near = set(near) - chosen
    for left, right in links:
        if left in chosen and right not in chosen:
            near.add(right)
        elif right in chosen and left not in chosen:
            near.add(left)
    for entity_id in result:
        if entity_id in chosen:
            result[entity_id] = Tier.NAMED
        elif entity_id in near:
            result[entity_id] = Tier.NEAR
    return result


def degree(links: Iterable[tuple[str, str]]) -> dict[str, int]:
    """How many links each entry has: a tie-breaker among equally important entries."""
    counts: dict[str, int] = {}
    for left, right in links:
        counts[left] = counts.get(left, 0) + 1
        counts[right] = counts.get(right, 0) + 1
    return counts


def ranked(lines: Iterable[Line], tier_of: Mapping[str, Tier]) -> list[Line]:
    """Lines with their tier set from the entries they touch (the best one wins).

    Within a tier, a record touching more named entries comes first: a fact between two
    entries the request names says more about it than one that reaches outside. (Graph
    retrieval ranks edges between two seed entities above the rest the same way: KET-RAG,
    arXiv 2502.09304; Microsoft GraphRAG's local search keeps in-network relationships
    apart from out-network ones.)
    """
    out: list[Line] = []
    for line in lines:
        tiers_touched = [tier_of.get(entity_id, Tier.REST) for entity_id in line.touches]
        best = min(tiers_touched, default=None)
        named = sum(1 for tier in tiers_touched if tier is Tier.NAMED)
        out.append(
            Line(
                section=line.section,
                record_id=line.record_id,
                text=line.text,
                tier=best if best is not None else line.tier,
                rank=(-named, *line.rank),
                touches=line.touches,
            )
        )
    return out


def choose(policy: ListingPolicy, lines: Iterable[Line], *, named: Iterable[str] = ()) -> Listing:
    """What to list within the policy's budget, and how much of each section was left out.

    Lines of a section the policy does not list are ignored. Named entries come first, as
    many as fit the budget; everything else is taken tier by tier, section by section.
    """
    order = {name: index for index, name in enumerate(policy.sections)}
    candidates: dict[tuple[str, str], Line] = {}
    for line in lines:
        if line.section in order:
            candidates.setdefault((line.section, line.record_id), line)
    pool = sorted(
        candidates.values(),
        key=lambda line: (line.tier, order[line.section], line.rank, line.record_id),
    )
    must: list[Line] = []
    used = 0
    for line in pool:
        if line.section != "entries" or line.tier is not Tier.NAMED:
            continue
        if len(must) >= policy.max_named:
            break
        if used + line.words <= policy.budget:
            must.append(line)
            used += line.words
    taken = {(line.section, line.record_id) for line in must}
    kept = list(must)
    left_out: dict[str, int] = {}
    for line in pool:
        if (line.section, line.record_id) in taken:
            continue
        if used + line.words <= policy.budget:
            kept.append(line)
            used += line.words
        else:
            left_out[line.section] = left_out.get(line.section, 0) + 1
    kept.sort(key=lambda line: (order[line.section], line.tier, line.rank, line.record_id))
    listed: dict[str, int] = {}
    for line in kept:
        listed[line.section] = listed.get(line.section, 0) + 1
    return Listing(
        policy=policy,
        lines=kept,
        listed=listed,
        left_out=left_out,
        named=tuple(dict.fromkeys(named)),
    )


__all__ = [
    "CHANGE_POLICY",
    "LINKS_POLICY",
    "POLICIES",
    "READ_POLICY",
    "Line",
    "Listing",
    "ListingPolicy",
    "Names",
    "Tier",
    "choose",
    "clip_words",
    "degree",
    "fold",
    "importance_rank",
    "named_in",
    "ranked",
    "tiers",
]
