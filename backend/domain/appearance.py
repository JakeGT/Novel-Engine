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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import partial

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


# ===================================================================== identity details
#
# Height, scars and marks, and age as written (CON-004), the other identity details a
# continuity pass looks for after eye and hair colour. The same rules as the colours:
# the prose must tie the detail to the named person (their name in the possessive, an
# apposition, or their name as the one the sentence is about with no one else named),
# a pronoun alone is read only for a mark and only when it is the pronoun the prose
# gives that person elsewhere, a hedged sentence is left alone, and each difference is
# a note quoting both sides.

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip
_NUMBER = (
    r"(?:\d{1,3}|(?:"
    + "|".join(_TENS)
    + r")(?:[-\s](?:"
    + "|".join(word for word in _ONES if _ONES[word] and _ONES[word] < 10)
    + r"))?|"
    + "|".join(sorted(_ONES, key=len, reverse=True))
    + r")"
)


# Public: the same numbers are read for quantities spent (SCN-007).
NUMBER_PATTERN = _NUMBER


def number_of(words: str) -> int | None:
    """ "sixteen" → 16, "twenty-two" → 22, "40" → 40; None for anything else."""
    folded = " ".join(words.lower().replace("-", " ").split())
    if folded.isdigit():
        return int(folded)
    if folded in _ONES:
        return _ONES[folded]
    parts = folded.split()
    if parts and parts[0] in _TENS:
        if len(parts) == 1:
            return _TENS[parts[0]]
        if len(parts) == 2 and parts[1] in _ONES and 0 < _ONES[parts[1]] < 10:
            return _TENS[parts[0]] + _ONES[parts[1]]
    return None


@dataclass(frozen=True)
class AgeKnown:
    """How old someone can be at the scene's time, in whole years, worked out from the
    story bible (a birth placed in story time, or an age the author gave from a time).

    ``lo`` and ``hi`` are the least and most they can be; ``because`` says why, in a few
    plain words ("“Wren is born” is in year 5 and this scene is in year 21")."""

    who: str
    names: tuple[str, ...]
    lo: int
    hi: int
    because: str
    key: str = ""


@dataclass(frozen=True)
class IdentityClash:
    """The prose giving someone a height, a mark or an age the story bible says otherwise."""

    who: str
    # "height", "mark" or "age".
    kind: str
    quote: str
    start: int
    end: int
    # What the prose says, short and lower case, so a dismissal can recognise it.
    said: str
    # What the story says, in words: an accepted detail's words, or how old they can be.
    against: str
    source: str
    key: str = ""


def _names_pattern(names: Iterable[str]) -> str:
    unique = sorted({name for name in names if name}, key=len, reverse=True)
    return r"(?:" + "|".join(re.escape(name) for name in unique) + r")"


# ------------------------------------------------------------------ age as written

# Words that make an age not this scene's: "when she was sixteen", "looked sixteen",
# "would be sixteen", "sixteen years ago". ("Nearly sixteen", "at sixteen" and "sixteen
# or so" never match the patterns below, which want the number right after the name or verb
# and the end of the phrase right after it.)
_AGE_HEDGE = re.compile(
    r"(?<![\w])(not|never|no|n[’']t|used\s+to|once|would|might|could|will|when|since|until|"
    r"till|perhaps|maybe|ago|looked|looks|seemed|seems|felt|feels|like|if|claimed|claims|"
    r"said|says|pretended|lied|thought|believed|guessed|wished)(?![\w])",
    re.IGNORECASE,
)
# ("Wren was ten that summer" is another time, so "that" never ends an age.)
_AGE_END = r"(?=\s*[,;:—–.!?)]|\s+and\s|\s+(?:now|today|this)(?![\w])|\s*$)"
# A number in a list or a count is not an age: "Wren, two, three, and four ran".
_NOT_A_LIST = rf"(?!\s*,\s*(?:(?:and|or)\s+)?{_NUMBER}(?![\w-]))"
_YEARS = r"(?:\s+years?\s+old|\s+years?\s+of\s+age|-years?-old)?"


def _age_patterns(names: Iterable[str]) -> list[re.Pattern[str]]:
    name = _names_pattern(names)
    n = rf"(?P<n>{_NUMBER})"
    return [
        # "the sixteen-year-old Wren"
        re.compile(rf"(?<![\w-]){n}-years?-old\s+(?P<name>{name})(?![\w])", re.IGNORECASE),
        # "Wren, sixteen and furious," "Wren, aged sixteen," "Wren, a sixteen-year-old"
        re.compile(
            rf"(?<![\w])(?P<name>{name}),\s+(?:(?:aged|age|now)\s+)?{n}{_YEARS}{_NOT_A_LIST}"
            rf"{_AGE_END}",
            re.IGNORECASE,
        ),
        re.compile(rf"(?<![\w])(?P<name>{name}),\s+an?\s+{n}-years?-old(?![\w-])", re.IGNORECASE),
        # "Wren was sixteen." "Wren is twenty-two years old" "Wren had turned sixteen"
        re.compile(
            rf"(?<![\w])(?P<name>{name})\s+(?:is|was|had\s+turned|has\s+turned|turned|"
            rf"is\s+now|was\s+now)\s+(?:an?\s+(?={_NUMBER}-years?-old))?{n}{_YEARS}{_AGE_END}",
            re.IGNORECASE,
        ),
    ]


def written_ages(sentence: str, names: Iterable[str]) -> list[tuple[int, int, int]]:
    """(age, start, end) for each age the sentence gives the named person, or none when
    it is hedged."""
    names = [name for name in names if name]
    if not names or _AGE_HEDGE.search(sentence) or _HEDGE.search(sentence):
        return []
    found: list[tuple[int, int, int]] = []
    for pattern in _age_patterns(names):
        for match in pattern.finditer(sentence):
            value = number_of(match.group("n"))
            spelled = match.group(0)
            if value is None or value > 130:
                continue
            # An age of one is too easily a count ("Wren, one of the crew"): only with "years old".
            if value < 2 and "year" not in spelled.lower():
                continue
            found.append((value, match.start(), match.end()))
    found.sort(key=lambda item: item[1])
    return found


def _age_words(lo: int, hi: int) -> str:
    if lo == hi:
        return f"{lo}"
    if hi == lo + 1:
        return f"{lo} or {hi}"
    return f"between {lo} and {hi}"


# ------------------------------------------------------------------ height

_TALL = frozenset({"tall", "towering", "lanky", "gangly", "rangy", "statuesque", "lofty"})
_SHORT = frozenset({"short", "small", "tiny", "petite", "diminutive", "squat", "stocky"})
_HEIGHT_WORD = r"(?:" + "|".join(sorted(_TALL | _SHORT, key=len, reverse=True)) + r")"
# A height word is about the person only when no part of them follows it: "short black
# hair", "small hands", "tall boots" are not their height.
_PARTS = frozenset(
    {
        "hair", "beard", "eyes", "eye", "legs", "leg", "arms", "arm", "hands", "hand",
        "fingers", "nails", "neck", "nose", "ears", "feet", "foot", "stride", "steps",
        "breath", "temper", "fuse", "sleeves", "boots", "coat", "cloak", "hat", "sword",
        "knife", "blade", "stick", "staff", "glass", "cup", "pause", "laugh", "silence",
        "while", "time", "distance", "way", "walk", "trip", "notice", "supply", "bed",
        # Marks, clothes and the face: "a small scar", "a tiny tattoo", "a short skirt".
        "scar", "scars", "tattoo", "tattoos", "mark", "marks", "mole", "birthmark", "brand",
        "burn", "wound", "cut", "freckles", "skirt", "dress", "gown", "tunic", "jacket",
        "cape", "shirt", "trousers", "breeches", "gloves", "belt", "scarf", "hood", "cap",
        "shoes", "heels", "braid", "braids", "plait", "ponytail", "curls", "fringe",
        "moustache", "mustache", "stubble", "mouth", "lips", "chin", "jaw", "face",
        "forehead", "brow", "teeth", "smile", "earrings", "ring", "necklace", "pendant",
        "dagger", "bow", "bag", "pack",
    }
)  # fmt: skip
# Words that may follow a height word when a sentence says someone is tall or short:
# "Wren was tall enough", "a small frame". Any other word means something else is meant
# ("short with him", "a small part of the plan", "small comfort").
_HEIGHT_FOLLOWS = frozenset(
    {
        "enough", "now", "then", "too", "still", "already", "even", "as", "than", "again",
        "yet", "indeed", "herself", "himself", "themselves", "frame", "build", "stature",
        "body", "height",
    }
)  # fmt: skip
_PEOPLE_NOUNS = frozenset(
    {"woman", "man", "girl", "boy", "figure", "person", "child", "lad", "lass", "one", "fellow"}
)
_HEIGHT_NUMBER = re.compile(
    rf"(?<![\w])(?:(?P<ft>{_NUMBER})\s*(?:feet|foot|ft\.?)(?:\s*(?:and\s+)?(?P<inch>{_NUMBER})"
    r"(?:\s*(?:inches|inch|in\.?))?)?|(?P<cm>\d{2,3})\s*(?:cm|centimetres|centimeters)"
    r"|(?P<m>\d(?:\.\d{1,2})?)\s*(?:m|metres|meters))(?![\w])",
    re.IGNORECASE,
)


def _height_group(word: str) -> str | None:
    folded = word.lower()
    if folded in _TALL:
        return "tall"
    if folded in _SHORT:
        return "short"
    return None


def _number_height(match: re.Match[str]) -> float | None:
    """A height in centimetres."""
    if match.group("cm"):
        return float(match.group("cm"))
    if match.group("m"):
        return float(match.group("m")) * 100
    feet = number_of(match.group("ft") or "")
    if feet is None or not 3 <= feet <= 8:
        return None
    inches = number_of(match.group("inch")) if match.group("inch") else 0
    if inches is None or inches > 11:
        return None
    return feet * 30.48 + inches * 2.54


def _about_the_body(text: str, end: int, *, statement: bool = False) -> bool:
    """Whether the words after a height word say it is the person's height: nothing of
    them follows before "with", the end of the clause, or a word for a person. In a
    sentence saying what someone is (``statement``: "Wren was short"), the very next word
    must also end the thought or be one for a person or their build, so "short with him",
    "short on patience" and "a small part of the plan" are not heights."""
    for index, word in enumerate(_WORD.findall(text[end : end + 60])[:4]):
        lower = word.lower()
        if lower in (";", ":", "—", "–", "(", ")") or lower in ("and", "but", "or"):
            return True
        if lower in _PEOPLE_NOUNS:
            return True
        if lower in _PARTS or lower.endswith(("-haired", "-eyed", "-legged")):
            return False
        if statement and index == 0 and lower != "," and lower not in _HEIGHT_FOLLOWS:
            return False
        if lower in ("with", "for"):
            return True
    return True


def heights_in(text: str) -> list[tuple[str, float | None, int, int]]:
    """Each height a detail's words give: ("tall" | "short" | "number", centimetres or
    None, start, end)."""
    found: list[tuple[str, float | None, int, int]] = []
    for match in re.finditer(rf"(?<![\w-]){_HEIGHT_WORD}(?![\w-])", text, re.IGNORECASE):
        group = _height_group(match.group(0))
        if group and _about_the_body(text, match.end()):
            found.append((group, None, match.start(), match.end()))
    for match in _HEIGHT_NUMBER.finditer(text):
        value = _number_height(match)
        if value is not None:
            found.append(("number", value, match.start(), match.end()))
    return found


def _height_patterns(names: Iterable[str]) -> list[re.Pattern[str]]:
    name = _names_pattern(names)
    word = rf"(?P<word>{_HEIGHT_WORD})"
    degree = r"(?:(?:very|quite|so|rather|unusually|remarkably|terribly)\s+)?"
    return [
        # "Wren was tall." "Wren was a small woman" "Wren had grown tall" (not "stood tall")
        re.compile(
            rf"(?<![\w])(?P<name>{name})\s+(?:is|was|had\s+grown|has\s+grown|grew)\s+"
            rf"(?:an?\s+)?{degree}{word}(?![\w-])",
            re.IGNORECASE,
        ),
        # "Wren, tall and pale,"
        re.compile(rf"(?<![\w])(?P<name>{name}),\s+{degree}{word}(?![\w-])", re.IGNORECASE),
        # "the tall Wren", "tiny Wren"
        re.compile(rf"(?<![\w-]){word}\s+(?P<name>{name})(?![\w])", re.IGNORECASE),
    ]


def _height_number_patterns(names: Iterable[str]) -> re.Pattern[str]:
    name = _names_pattern(names)
    return re.compile(
        rf"(?<![\w])(?P<name>{name})\s+(?:is|was|stood|stands)\s+(?:(?:just|barely|only|a\s+full)\s+)?"
        rf"(?P<height>{_HEIGHT_NUMBER.pattern.removeprefix('(?<![\\w])')})",
        re.IGNORECASE,
    )


# ------------------------------------------------------------------ scars and marks

_MARKS = {
    "scar": "scar", "scars": "scar", "scarred": "scar", "tattoo": "tattoo",
    "tattoos": "tattoo", "tattooed": "tattoo", "birthmark": "birthmark",
    "birthmarks": "birthmark", "mole": "mole", "brand": "brand", "branded": "brand",
}  # fmt: skip
_BODY = {
    "cheek": "cheek", "hand": "hand", "palm": "hand", "knuckles": "hand", "arm": "arm",
    "forearm": "arm", "shoulder": "shoulder", "eye": "eye", "eyebrow": "brow",
    "brow": "brow", "temple": "temple", "jaw": "jaw", "leg": "leg", "thigh": "leg",
    "calf": "leg", "knee": "knee", "wrist": "wrist", "ear": "ear", "hip": "hip",
    "ankle": "ankle", "foot": "foot", "side": "side", "breast": "breast", "chin": "chin",
}  # fmt: skip
_MARK = r"(?P<mark>" + "|".join(sorted(_MARKS, key=len, reverse=True)) + r")"
_PART = r"(?P<part>" + "|".join(sorted(_BODY, key=len, reverse=True)) + r")"
_SIDE = r"(?P<side>left|right)"
# "her", "the", or someone's name in the possessive ("Wren's", "Isolde Varrow's").
_OWNER = r"(?P<owner>her|his|their|the|(?:[A-Z][\w’'-]*\s+){0,3}[\w’'-]+['’]s)"
# "a scar across her left cheek", "the tattoo on Wren's right forearm"
_MARK_ON = re.compile(
    rf"(?<![\w]){_MARK}(?![\w])(?:[^.;:!?]{{0,40}}?)(?<![\w])(?:on|across|along|over|down|above|"
    rf"below|under|beneath|through|at|upon)\s+{_OWNER}\s+{_SIDE}\s+{_PART}(?![\w])",
    re.IGNORECASE,
)
# "her scarred left cheek", "Wren's left cheek bore a scar"
_MARKED_PART = re.compile(
    rf"(?<![\w]){_OWNER}\s+(?:(?P<marked>scarred|tattooed|branded)\s+)?{_SIDE}\s+{_PART}"
    rf"(?:\s+(?:bore|bears|had|has|carried|carries|showed|shows|was\s+marked\s+with)\s+"
    rf"(?:an?|the|its)\s+(?:[\w-]+\s+){{0,2}}{_MARK})?(?![\w])",
    re.IGNORECASE,
)

_MARK_WORD = re.compile(rf"(?<![\w]){_MARK}(?![\w])", re.IGNORECASE)
# Sentences with a mark in them read for each person: past this, a text is not a scene.
_MAX_MARK_SENTENCES = 200


@dataclass(frozen=True)
class Mark:
    kind: str
    part: str
    side: str
    owner: str
    start: int
    end: int


def marks_in(text: str) -> list[Mark]:
    """Each mark with a side: ("scar", "cheek", "left")."""
    found: list[Mark] = []
    for match in _MARK_ON.finditer(text):
        found.append(
            Mark(
                _MARKS[match.group("mark").lower()],
                _BODY[match.group("part").lower()],
                match.group("side").lower(),
                match.group("owner"),
                match.start(),
                match.end(),
            )
        )
    for match in _MARKED_PART.finditer(text):
        word = match.group("marked") or match.group("mark")
        if not word:
            continue
        if any(m.start <= match.start() < m.end for m in found):
            continue
        found.append(
            Mark(
                _MARKS[word.lower()],
                _BODY[match.group("part").lower()],
                match.group("side").lower(),
                match.group("owner"),
                match.start(),
                match.end(),
            )
        )
    found.sort(key=lambda mark: mark.start)
    return found


_SUBJECT_PRONOUNS = {
    "she": "her", "her": "her", "hers": "her", "herself": "her",
    "he": "his", "his": "his", "him": "his", "himself": "his",
    "they": "their", "their": "their", "them": "their", "themselves": "their",
}  # fmt: skip


@dataclass(frozen=True)
class _PronounVotes:
    """Each sentence's vote for the pronoun the prose gives a person, read once a text."""

    votes: dict[str, int]
    by_sentence: dict[tuple[int, int], str]

    def leading(self, skip: tuple[int, int] = (0, 0)) -> str | None:
        votes = dict(self.votes)
        left_out = self.by_sentence.get(skip)
        if left_out is not None:
            votes[left_out] -= 1
            if not votes[left_out]:
                del votes[left_out]
        return _leading(votes)


def _pronoun_votes(text: str, names: Iterable[str], others: Iterable[str]) -> _PronounVotes:
    names = [name for name in names if name]
    others = [name for name in others if name]
    votes: dict[str, int] = {}
    by_sentence: dict[tuple[int, int], str] = {}
    for sentence_match in _SENTENCE.finditer(text):
        sentence = sentence_match.group(0)
        spans = _named(sentence, names)
        if not spans or _named(sentence, others):
            continue
        first = min(spans)
        for word in _WORD.findall(sentence[first[1] :]):
            folded = word.lower()
            if folded in _SUBJECT_PRONOUNS:
                pronoun = _SUBJECT_PRONOUNS[folded]
                votes[pronoun] = votes.get(pronoun, 0) + 1
                by_sentence[sentence_match.span()] = pronoun
                break
    return _PronounVotes(votes, by_sentence)


def pronoun_of(
    text: str, names: Iterable[str], others: Iterable[str], skip: tuple[int, int] = (0, 0)
) -> str | None:
    """The pronoun the prose gives a person: the first pronoun after their name in
    sentences that name them and no one else, leaving out the sentence at ``skip`` (so a
    mark's own pronoun never votes for itself). None unless one clearly leads."""
    return _pronoun_votes(text, names, others).leading(skip)


def _leading(votes: dict[str, int]) -> str | None:
    if not votes:
        return None
    best = max(votes, key=lambda key: votes[key])
    total = sum(votes.values())
    return best if votes[best] / total >= 0.75 else None


# ------------------------------------------------------------------ the check


def _short(words: str) -> str:
    said = " ".join(words.split())
    return said if len(said) <= 160 else said[:157].rstrip() + "…"


def identity_clashes(
    text: str,
    details: Sequence[AppearanceDetail],
    ages: Sequence[AgeKnown] = (),
    others: Iterable[str] = (),
) -> list[IdentityClash]:
    """Where the prose gives someone a height, a mark or an age the story bible says
    otherwise. ``others`` are the names of everyone else in the scene."""
    other_names = [name for name in others if name]
    names_of: dict[str, tuple[str, ...]] = {}
    for detail in details:
        names_of[detail.who] = tuple(dict.fromkeys([*names_of.get(detail.who, ()), *detail.names]))
    for age in ages:
        names_of[age.who] = tuple(dict.fromkeys([*names_of.get(age.who, ()), *age.names]))
    found: list[IdentityClash] = []
    seen: set[tuple[str, str]] = set()
    # Each person's pronoun votes, read from the whole text once, when a mark needs them;
    # and how many sentences with a mark in them have been read for each.
    votes: dict[str, _PronounVotes] = {}
    marked: dict[str, int] = {}

    def votes_for(who: str, names: Sequence[str], others: Sequence[str]) -> _PronounVotes:
        if who not in votes:
            votes[who] = _pronoun_votes(text, names, others)
        return votes[who]

    for sentence_match in _SENTENCE.finditer(text):
        sentence = sentence_match.group(0)
        if not sentence.strip():
            continue
        offset = sentence_match.start()
        for who, names in names_of.items():
            if not _named(sentence, names):
                continue
            everyone_else = [
                *[n for n in other_names if n not in names],
                *[n for other, more in names_of.items() if other != who for n in more],
            ]
            crowded = bool(_named(sentence, everyone_else))
            mine = [detail for detail in details if detail.who == who]
            clashes_here: list[IdentityClash] = []
            if (who, "height") not in seen:
                clashes_here.extend(_height_clashes(sentence, who, names, mine, crowded))
            if (who, "mark") not in seen and marked.get(who, 0) < _MAX_MARK_SENTENCES:
                if _MARK_WORD.search(sentence):
                    marked[who] = marked.get(who, 0) + 1
                    pronouns = partial(votes_for, who, names, everyone_else)
                    clashes_here.extend(
                        _mark_clashes(sentence, offset, who, names, mine, crowded, pronouns)
                    )
            if (who, "age") not in seen:
                clashes_here.extend(
                    _age_clashes(sentence, who, names, [a for a in ages if a.who == who], crowded)
                )
            for clash in clashes_here:
                if (who, clash.kind) in seen:
                    continue
                seen.add((who, clash.kind))
                found.append(
                    IdentityClash(
                        who=clash.who,
                        kind=clash.kind,
                        quote=clash.quote,
                        start=offset + clash.start,
                        end=offset + clash.end,
                        said=clash.said,
                        against=clash.against,
                        source=clash.source,
                        key=clash.key,
                    )
                )
    found.sort(key=lambda clash: clash.start)
    return found


def _height_clashes(
    sentence: str,
    who: str,
    names: Sequence[str],
    details: Sequence[AppearanceDetail],
    crowded: bool,
) -> list[IdentityClash]:
    if _HEDGE.search(sentence) or re.search(
        r"(?<![\w])(?:stood|stand|stands|standing|sat|felt|feel|feels|seemed|looked)\s+"
        r"(?:very\s+|so\s+)?" + _HEIGHT_WORD,
        sentence,
        re.IGNORECASE,
    ):
        return []
    accepted = [(detail, height) for detail in details for height in heights_in(detail.words)]
    if not accepted:
        return []
    said: list[tuple[str, float | None, int, int]] = []
    for index, pattern in enumerate(_height_patterns(names)):
        # In a sentence naming others, only "Wren, tall and pale," puts the word on her.
        if crowded and index != 1:
            continue
        for match in pattern.finditer(sentence):
            group = _height_group(match.group("word"))
            # "the tall Wren": the name follows, so only a statement is read on.
            if group is None or (
                index != 2 and not _about_the_body(sentence, match.end("word"), statement=True)
            ):
                continue
            said.append((group, None, match.start("word"), match.end("word")))
    if not crowded:
        for match in _height_number_patterns(names).finditer(sentence):
            inner = _HEIGHT_NUMBER.search(match.group("height"))
            value = _number_height(inner) if inner else None
            if value is not None:
                said.append(("number", value, match.start("height"), match.end("height")))
    for group, value, start, end in said:
        same_kind = [
            (detail, kept)
            for detail, kept in accepted
            if (kept[0] == "number") == (value is not None)
        ]
        if not same_kind:
            continue
        if value is None:
            agrees = any(kept[0] == group for _detail, kept in same_kind)
        else:
            agrees = any(
                kept[1] is not None and abs(kept[1] - value) <= 5 for _detail, kept in same_kind
            )
        if agrees:
            continue
        detail, kept = same_kind[0]
        return [
            IdentityClash(
                who,
                "height",
                sentence[start:end],
                start,
                end,
                sentence[start:end].lower(),
                _short(detail.words),
                detail.source,
                detail.key,
            )
        ]
    return []


def _mark_tied(
    sentence: str,
    offset: int,
    mark: Mark,
    names: Sequence[str],
    crowded: bool,
    pronouns: Callable[[], _PronounVotes],
) -> bool:
    owner = mark.owner.lower()
    if owner.endswith(("'s", "’s")):
        whose = " ".join(mark.owner[:-2].split())
        return any(whose.casefold() == name.casefold() for name in names) or (
            " " not in whose and _is_name(whose, names)
        )
    if owner == "the" or crowded:
        return False
    spans = [span for span in _named(sentence, names) if span[1] <= mark.start]
    if not spans:
        return False
    # The pronoun must be the one the prose gives them in its other sentences.
    pronoun = pronouns().leading((offset, offset + len(sentence)))
    if pronoun is None:
        # Or the first pronoun after their name in this sentence, before the mark's own.
        between = sentence[min(spans)[1] : mark.start]
        first = next(
            (
                _SUBJECT_PRONOUNS[word.lower()]
                for word in _WORD.findall(between)
                if word.lower() in _SUBJECT_PRONOUNS
            ),
            None,
        )
        pronoun = first
    return pronoun is not None and pronoun == _SUBJECT_PRONOUNS.get(owner)


def _mark_clashes(
    sentence: str,
    offset: int,
    who: str,
    names: Sequence[str],
    details: Sequence[AppearanceDetail],
    crowded: bool,
    pronouns: Callable[[], _PronounVotes],
) -> list[IdentityClash]:
    if _HEDGE.search(sentence) or re.search(
        r"(?<![\w])(?:faded|healed|removed|covered|hidden|hid|painted|drew|drawn|new|fresh)"
        r"(?![\w])",
        sentence,
        re.IGNORECASE,
    ):
        return []
    accepted = [(detail, mark) for detail in details for mark in marks_in(detail.words)]
    if not accepted:
        return []
    for mark in marks_in(sentence):
        if not _mark_tied(sentence, offset, mark, names, crowded, pronouns):
            continue
        same = [
            (detail, kept)
            for detail, kept in accepted
            if kept.kind == mark.kind and kept.part == mark.part
        ]
        if not same or any(kept.side == mark.side for _detail, kept in same):
            continue
        detail, _kept = same[0]
        quote = sentence[mark.start : mark.end]
        return [
            IdentityClash(
                who,
                "mark",
                quote,
                mark.start,
                mark.end,
                f"{mark.kind} {mark.side} {mark.part}",
                _short(detail.words),
                detail.source,
                detail.key,
            )
        ]
    return []


def _age_clashes(
    sentence: str, who: str, names: Sequence[str], ages: Sequence[AgeKnown], crowded: bool
) -> list[IdentityClash]:
    if not ages:
        return []
    for value, start, end in written_ages(sentence, names):
        if crowded and not re.match(
            rf"{_names_pattern(names)},", sentence[start:end], re.IGNORECASE
        ):
            # In a sentence naming others, only "Wren, sixteen," puts the age on her.
            continue
        known = [age for age in ages if not age.lo <= value <= age.hi]
        if len(known) < len(ages):
            continue
        age = known[0]
        return [
            IdentityClash(
                who,
                "age",
                sentence[start:end],
                start,
                end,
                str(value),
                _age_words(age.lo, age.hi),
                age.because,
                age.key,
            )
        ]
    return []


__all__ = [
    "FEATURES",
    "AgeKnown",
    "AppearanceClash",
    "AppearanceDetail",
    "NUMBER_PATTERN",
    "IdentityClash",
    "Mark",
    "Trait",
    "clashes",
    "detail_words",
    "differs",
    "heights_in",
    "identity_clashes",
    "marks_in",
    "number_of",
    "pronoun_of",
    "traits",
    "written_ages",
]
