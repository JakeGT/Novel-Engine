"""How someone looks in the prose, against how the story says they look (CON-004, CON-014).

An accepted appearance detail is a ``HAS_DETAIL`` fact (written by the author, or
suggested from a picture and accepted), or an appearance a model saw in a picture
that the author accepted or corrected. This compares the colour words the prose
gives a person's eyes, hair or beard with the colour words of those details, with
no model.

It is deliberately narrow, because a wrong note costs the author's trust:

* Only eyes, hair and beard, the details writing guides list first for a
  continuity pass (eye and hair colour are the ones readers notice at once). Skin
  is left out: its words are ambiguous and personal.
* Colour words are grouped by the basic colour categories (Berlin and Kay, 1969) and
  the words fiction uses for hair and eyes ("auburn", "flaxen", "hazel"). A word
  that sits between two groups ("hazel", "dark", "silver") is compatible with
  both; only words with no group in common differ.
* The prose must tie the colour to the person: in a sentence that names them and
  no one else in the scene, or with their name in the possessive owning the trait
  itself ("Wren's grey eyes", not "Wren's mother had green eyes"), or as "Wren,
  blue-eyed". A pronoun alone is never read. Nor is a colour
  that belongs to someone else in the same sentence: "the green-eyed boy", "Osric's
  grey eyes", or "his grey eyes" after a word like "into" or "met" ("Wren stared
  into his grey eyes").
* Words that are not eye or hair colour are left alone: a black eye, red-eyed or
  eyes red and swollen from crying, a red hair ribbon, and a passing look such as
  hair that gleamed gold or was white with frost.
* A sentence that says the colour is not so, was dyed, a wig, a disguise, or how
  it used to be, is left alone.

Each difference is a note, never a warning: the author may have changed their mind,
and either side may be the one to fix.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

FEATURES: dict[str, str] = {
    "eye": "eyes",
    "eyes": "eyes",
    "eyed": "eyes",
    "hair": "hair",
    "haired": "hair",
    "beard": "beard",
    "bearded": "beard",
}

# Each word, and the colour groups it can belong to. A word in two groups is
# compatible with either.
_COLOURS: dict[str, frozenset[str]] = {}


def _group(names: Iterable[str], words: Iterable[str]) -> None:
    for word in words:
        _COLOURS[word] = _COLOURS.get(word, frozenset()) | frozenset(names)


_group(["blue"], ["blue", "azure", "sapphire", "cornflower", "sky-blue", "ice-blue"])
_group(["green"], ["green", "emerald", "jade", "sea-green"])
_group(["brown"], ["brown", "chestnut", "brunette", "mousy", "mousey", "walnut", "chocolate"])
_group(["grey"], ["grey", "gray", "slate", "ashen", "steel-grey", "steel-gray"])
_group(["black"], ["black", "jet", "jet-black", "raven", "ebony", "inky", "coal-black"])
_group(["blond"], ["blond", "blonde", "flaxen", "straw", "tow", "platinum", "sandy"])
_group(["red"], ["red", "ginger", "copper", "russet", "titian", "flame", "carrot", "scarlet"])
_group(["white"], ["white", "snowy", "snow-white"])
_group(["violet"], ["violet", "purple", "lilac"])
_group(["amber"], ["amber", "tawny"])
# Between two groups.
_group(["red", "brown"], ["auburn", "mahogany"])
_group(["brown", "green", "amber"], ["hazel"])
_group(["brown", "black"], ["dark"])
_group(["grey", "white"], ["silver", "silvery", "pewter"])
_group(["blond", "amber"], ["golden", "gold", "honey", "yellow"])
_group(["blond", "grey", "blue"], ["pale", "fair", "light"])

_COLOUR_WORDS = sorted(_COLOURS, key=len, reverse=True)
_COLOUR = r"(?:" + "|".join(re.escape(word) for word in _COLOUR_WORDS) + r")"
# "grey eyes", "dark curly hair", "red-haired", "blue-eyed".
# Zero-width, so "dark brown hair" is also read from "brown".
# Things worn in the hair: "a red hair ribbon" says nothing of the hair.
_WORN = r"(?:ribbons?|ties?|pins?|bands?|clips?|nets?|combs?|brush|clasps?|slides?|grips?)"
_BEFORE = re.compile(
    rf"(?<![\w-])(?=(?P<colour>{_COLOUR})(?:-ish|ish)?"
    r"(?:(?:,?\s+(?P<between>[a-z]+))?\s+(?P<feature>eyes|eye|hair|beard)"
    rf"(?!\s+{_WORN}(?![\w-]))"
    r"|-(?P<compound>eyed|haired|bearded))(?![\w-]))",
    re.IGNORECASE,
)
# "her eyes were grey", "his hair is black". Not a passing look: "hair gleamed gold",
# "hair was white with frost", "eyes looked grey in that light".
_AFTER = re.compile(
    rf"(?<![\w-])(?P<feature>eyes|hair|beard)\s+(?:is|are|was|were|had\s+gone|had\s+turned|"
    rf"turned|looked|seemed)\s+(?:a\s+|an\s+)?(?:\w+\s+)?(?P<colour>{_COLOUR})"
    r"(?![\w-])(?!\s+(?:with|in|under|beneath|from|against|and\s+(?:swollen|puffy|raw|sore))"
    r"(?![\w-]))",
    re.IGNORECASE,
)
# Red eyes from crying, smoke or no sleep, and a bruised eye, are not eye colour.
_SORE_EYES = re.compile(
    r"(?<![\w])(?:swollen|puffy|raw|sore|tears?|tearful|crying|cried|wept|weeping|"
    r"bloodshot|rimmed|sleepless|smoke)(?![\w])",
    re.IGNORECASE,
)
# Words that say whose eyes or hair these are, just before them.
_ARTICLES = frozenset({"the", "a", "an", "this", "that", "these", "those"})
_PRONOUNS = frozenset({"his", "her", "their", "its"})
_NOT_THEIRS = frozenset({"my", "your", "our"})
# "into his grey eyes", "met her blue eyes": someone else's eyes.
_TOWARD = frozenset(
    {
        "into",
        "at",
        "to",
        "toward",
        "towards",
        "from",
        "with",
        "on",
        "upon",
        "of",
        "in",
        "met",
        "meet",
        "meets",
        "meeting",
        "held",
        "holding",
        "searched",
        "searching",
        "studied",
        "studying",
        "caught",
        "watched",
        "watching",
    }
)
_WORD = re.compile(r"[\w’'-]+|[,;:—–()]")
# Words that cannot sit between a colour and the feature it would describe ("blue coat and
# grey eyes" must not read as blue eyes).
_NOT_BETWEEN = frozenset(
    {"and", "or", "with", "of", "the", "a", "an", "but", "her", "his", "their", "its", "my"}
)
# A sentence that says it is not so, or not now, or not real.
_HEDGE = re.compile(
    r"(?<![\w])(not|never|no|n[’']t|dyed|dye|wig|disguise|disguised|lenses|used\s+to|once|"
    r"had\s+been|would\s+be|might|imagined|dreamed|dreamt|painted|portrait|like)(?![\w])",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+[\"”’']?|$)")
# Someone or something a person has that has eyes or hair of its own: "Wren's mother
# had green eyes" says nothing of Wren's.
_OWNERS = frozenset(
    {
        "mother", "father", "mum", "mam", "ma", "pa", "dad", "parents", "brother",
        "sister", "son", "daughter", "wife", "husband", "aunt", "uncle", "cousin", "niece",
        "nephew", "grandmother", "grandfather", "gran", "granny", "grandson",
        "granddaughter", "child", "children", "baby", "twin", "kin", "family", "friend",
        "friends", "lover", "sweetheart", "betrothed", "master", "mistress", "servant",
        "maid", "captain", "crew", "companion", "partner", "rival", "enemy", "teacher",
        "mentor", "pupil", "apprentice", "guard", "guest", "man", "woman", "boy", "girl",
        "men", "women", "people", "horse", "pony", "mare", "stallion", "dog", "hound",
        "puppy", "cat", "kitten", "bird", "falcon", "hawk", "owl", "mule", "goat", "ox",
        "doll", "portrait", "painting", "statue",
    }
)  # fmt: skip
# Things a person has: "Wren's coat" is not a describing word for her eyes.
_THINGS = frozenset(
    {
        "coat", "cloak", "scarf", "hat", "hood", "dress", "shawl", "gloves", "boots",
        "shirt", "jacket", "ribbon", "hand", "hands", "face", "voice", "gaze", "smile",
    }
)  # fmt: skip
# Words that never sit between a possessive and the trait it owns: "Wren's eyes" and
# "Wren's tired grey eyes" are hers; "Wren's horse had black hair" is not.
_NOT_DIRECT = (
    _NOT_BETWEEN
    | _ARTICLES
    | _PRONOUNS
    | _NOT_THEIRS
    | _TOWARD
    | _OWNERS
    | _THINGS
    | frozenset(
        {
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "had",
            "has",
            "have",
            "did",
            "does",
            "looked",
            "looks",
            "seemed",
            "seems",
            "turned",
            "gone",
            "wore",
            "wears",
            "who",
            "whose",
            "which",
            "that",
            "when",
            "while",
            "as",
            "than",
            "so",
            "then",
            "for",
            "by",
            "over",
            "under",
            "nor",
            "yet",
        }
    )  # fmt: skip
)


@dataclass(frozen=True)
class Trait:
    """One colour given to one feature: ("eyes", "grey")."""

    feature: str
    colour: str
    start: int = 0
    end: int = 0

    @property
    def groups(self) -> frozenset[str]:
        return _COLOURS.get(self.colour.lower(), frozenset())


@dataclass(frozen=True)
class AppearanceDetail:
    """An accepted detail of how someone looks, in the words it was accepted in."""

    # The person's name as the checks show it, and every name that means them here.
    who: str
    names: tuple[str, ...]
    words: str
    # Where it comes from, said plainly: "an accepted detail", "the picture “Wren”".
    source: str = "an accepted detail"
    # The story-bible fact or picture it comes from, so a dismissal can recognise it.
    key: str = ""


@dataclass(frozen=True)
class AppearanceClash:
    who: str
    feature: str
    # What the prose says, quoted, and where.
    quote: str
    start: int
    end: int
    prose_colour: str
    detail: AppearanceDetail
    detail_colour: str


def traits(text: str) -> list[Trait]:
    """Every colour given to eyes, hair or a beard in ``text``, in order."""
    found: list[Trait] = []
    for match in _BEFORE.finditer(text):
        between = (match.group("between") or "").lower()
        if between and (between in _NOT_BETWEEN or between in _COLOURS):
            continue
        named = "feature" if match.group("feature") else "compound"
        word = match.group(named).lower()
        colour = match.group("colour").lower()
        if _not_colour(text, colour, word, match.start()):
            continue
        found.append(Trait(FEATURES[word], colour, match.start(), match.end(named)))
    for match in _AFTER.finditer(text):
        word = match.group("feature").lower()
        colour = match.group("colour").lower()
        if _not_colour(text, colour, word, match.start()):
            continue
        found.append(Trait(FEATURES[word], colour, match.start(), match.end()))
    found.sort(key=lambda trait: trait.start)
    return found


def _not_colour(text: str, colour: str, word: str, at: int) -> bool:
    """A black eye (one, bruised), or red eyes from crying: not the colour of the eyes."""
    groups = _COLOURS.get(colour, frozenset())
    if word == "eye" and groups & {"black", "violet"}:
        return True
    if word in ("eye", "eyes", "eyed") and "red" in groups:
        if word == "eyed":
            return True
        sentence = _sentence_at(text, at)
        return bool(_SORE_EYES.search(sentence))
    return False


def _sentence_at(text: str, at: int) -> str:
    for match in _SENTENCE.finditer(text):
        if match.start() <= at < match.end():
            return match.group(0)
    return text


def differs(a: Trait, b: Trait) -> bool:
    """Two colours for the same feature that have no colour group in common."""
    return a.feature == b.feature and bool(a.groups) and bool(b.groups) and not a.groups & b.groups


def _named(text: str, names: Iterable[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for name in names:
        if not name:
            continue
        for match in re.finditer(rf"(?<![\w]){re.escape(name)}(?![\w])", text, re.IGNORECASE):
            spans.append((match.start(), match.end()))
    return spans


def _direct(words: Sequence[str]) -> bool:
    """Whether the words between a possessive and a trait leave the trait the possessed
    thing: none, or a few describing words ("tired", "long, tangled"). A noun such as
    "mother" or "horse", a verb, another possessive or another name says the trait
    belongs to something else."""
    if len([word for word in words if word != ","]) > 3:
        return False
    for index, word in enumerate(words):
        if word == ",":
            if index == 0 or index == len(words) - 1 or words[index - 1] == ",":
                return False
            continue
        if word in ";:—–()" or word.endswith(("'s", "’s", "'", "’")):
            return False
        if word.lower() in _NOT_DIRECT or word[:1].isupper():
            return False
    return True


def _possessive_before(sentence: str, names: Iterable[str], at: int) -> bool:
    """``Name's`` (or ``Name’s``) owning the trait at ``at``: "Wren's tired grey eyes",
    "Wren's eyes were grey", but not "Wren's mother had green eyes"."""
    for _start, end in _named(sentence, names):
        tail = sentence[end : end + 2]
        if tail[:2] in ("'s", "’s") and 0 <= at - (end + 2) <= 30:
            if _direct(_WORD.findall(sentence[end + 2 : at])):
                return True
    return False


def _about_their_things(sentence: str, names: Iterable[str], trait: Trait) -> bool:
    """Whether the trait sits in words about something the person has, named last in
    the sentence through a possessive: "Wren's mother, green-eyed and silent," or
    "Wren's old horse had black hair"."""
    spans = [span for span in _named(sentence, names) if span[1] <= trait.start]
    if not spans:
        return False
    _start, end = max(spans)
    if sentence[end : end + 2] not in ("'s", "’s"):
        return False
    words = [word.lower() for word in _WORD.findall(sentence[end + 2 : trait.start])]
    if _direct(words):
        return False
    # The first word that is not a describing one is a thing they have.
    for word in words:
        if word in _OWNERS:
            return True
        if word in _NOT_DIRECT or word in ",;:—–()":
            return False
    return False


def _apposition(sentence: str, names: Iterable[str], trait: Trait) -> bool:
    """ "Wren, grey-eyed and tired," — the name, a comma, then the trait."""
    for _start, end in _named(sentence, names):
        if re.fullmatch(r",\s*", sentence[end : trait.start]):
            return True
    return False


def _is_name(word: str, names: Iterable[str]) -> bool:
    folded = word.casefold()
    return any(folded == part.casefold() for name in names if name for part in name.split())


def _theirs(sentence: str, names: Iterable[str], trait: Trait) -> bool:
    """Whether nothing just before the trait says it is someone else's. "Wren rubbed her
    grey eyes" and "Wren had grey eyes" are hers; "the green-eyed boy", "Osric's grey
    eyes", "stared into his grey eyes" and a pronoun before her name are not."""
    names = list(names)
    words = _WORD.findall(sentence[: trait.start])
    # Only the clause the trait is in: "Wren squinted, her blue eyes stinging".
    clause: list[str] = []
    for word in reversed(words):
        if word in ",;:—–()":
            break
        clause.append(word.lower())
    if _about_their_things(sentence, names, trait):
        return False
    for index, word in enumerate(clause[:4]):
        if word.endswith(("'s", "’s")):
            # "Wren's tired grey eyes", but not "Wren's horse had black hair".
            return _is_name(word[:-2], names) and _direct(list(reversed(clause[:index])))
        if word in _ARTICLES or word in _NOT_THEIRS:
            return False
        if word in _PRONOUNS:
            before = clause[index + 1] if index + 1 < len(clause) else ""
            if before in _TOWARD:
                return False
            # The person is named before the pronoun, as the one doing it.
            return any(start < trait.start for start, _end in _named(sentence, names))
        if word in _TOWARD:
            return True
    return True


def clashes(
    text: str, details: Sequence[AppearanceDetail], others: Iterable[str] = ()
) -> list[AppearanceClash]:
    """Where the prose gives someone a colour their accepted details contradict.

    ``others`` are the names of everyone else in the scene: a sentence naming one of
    them as well is read only through a possessive or an apposition.
    """
    if not details:
        return []
    by_person: dict[str, list[tuple[AppearanceDetail, Trait]]] = {}
    for detail in details:
        for trait in traits(detail.words):
            by_person.setdefault(detail.who, []).append((detail, trait))
    names_of = {detail.who: detail.names for detail in details}
    other_names = [name for name in others if name]
    found: list[AppearanceClash] = []
    seen: set[tuple[str, str]] = set()
    for sentence_match in _SENTENCE.finditer(text):
        sentence = sentence_match.group(0)
        if not sentence.strip() or _HEDGE.search(sentence):
            continue
        offset = sentence_match.start()
        here = traits(sentence)
        if not here:
            continue
        for who, accepted in by_person.items():
            names = names_of[who]
            if not _named(sentence, names):
                continue
            crowded = bool(_named(sentence, [n for n in other_names if n not in names])) or any(
                _named(sentence, names_of[other]) for other in names_of if other != who
            )
            for trait in here:
                tied = _possessive_before(sentence, names, trait.start) or _apposition(
                    sentence, names, trait
                )
                if not tied and (crowded or not _theirs(sentence, names, trait)):
                    continue
                if (who, trait.feature) in seen:
                    continue
                against = next(
                    ((detail, kept) for detail, kept in accepted if differs(trait, kept)), None
                )
                if against is None:
                    continue
                # Agreeing with any accepted detail for that feature is agreeing.
                if any(
                    kept.feature == trait.feature and not differs(trait, kept)
                    for _detail, kept in accepted
                ):
                    continue
                seen.add((who, trait.feature))
                detail, kept = against
                found.append(
                    AppearanceClash(
                        who=who,
                        feature=trait.feature,
                        quote=sentence[trait.start : trait.end],
                        start=offset + trait.start,
                        end=offset + trait.end,
                        prose_colour=trait.colour,
                        detail=detail,
                        detail_colour=kept.colour,
                    )
                )
    return found


def detail_words(value: object) -> str:
    """The words of a stored ``HAS_DETAIL`` value: its ``appearance``, or every string in it."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        first = value.get("appearance")
        if isinstance(first, str):
            return first
        return ". ".join(str(item) for item in value.values() if isinstance(item, str))
    return ""


__all__ = [
    "FEATURES",
    "AppearanceClash",
    "AppearanceDetail",
    "Trait",
    "clashes",
    "detail_words",
    "differs",
    "traits",
]
