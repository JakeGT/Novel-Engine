"""How a scene sounds, measured without a model: the author's voice in numbers (CON-018).

Every measure here is worked out from the words alone, the same way every time, and
quickly enough to read a whole book on each request:

* **Sentence length**: the average, the middle, the long end and how much it varies,
  and how many sentences fall in each band (as writing tools graph it).
* **Dialogue share**: how much of the scene is inside quotation marks.
* **Words ending in -ly**: a rough count of adverbs in the narration, per 1,000 words,
  with common words that only look like adverbs ("only", "family") left out.
* **Repeated words**: the same word used three or more times within 50 words (an
  "echo", as editing tools call it), and words a scene leans on far more than the rest
  of the book does.
* **Who tells it**: "I" or "he" and "she" in the narration (dialogue is left out).
* **Tense**: past or present, from a pronoun and the verb after it in the narration.
* **Everyday words**: how often about sixty small words ("the", "and", "of") are used.
  Their mix is the most studied sign of who wrote a text (Burrows's Delta).

A scene is compared with the author's **usual**: the same measures across their other
scenes. A measure is said to drift only when it is far outside the usual spread (a
modified z-score above 3.5, with the median and the median absolute deviation, which
one odd scene cannot pull) **and** the difference is large enough to read. Drift is a
note for the author, never an error: a scene may sound different on purpose.

Sample sizes gate everything: a scene under 300 words is shown but not compared, the
usual needs three scenes and 2,000 words, and the everyday-words mix needs 1,500 words
in the scene and five scenes in the usual (stylometry finds a few thousand words the
least for a reliable reading, so this is a rough sign, and says so). Measures that
depend on English words (adverbs, who tells it, tense, everyday words) are only worked
out when the text reads as English; the others work for any language written with
spaces and full stops.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Literal

# Raised when any measure or threshold changes, so a reading can say which it used.
VOICE_VERSION = "voice/2"

MIN_SCENE_WORDS = 300
MIN_BASELINE_SCENES = 3
MIN_BASELINE_WORDS = 2_000
MIN_DELTA_WORDS = 1_500
MIN_DELTA_SCENES = 5
# A modified z-score above this is far outside the usual (Iglewicz and Hoaglin; NIST).
OUTLIER_SCORE = 3.5
ECHO_WINDOW = 50
ECHO_TIMES = 3
MAX_ECHOES = 5
MAX_LEANS = 3
MAX_SLIPS = 3
MAX_TEXT = 400_000
# Sentence-length bands, as a writing tool's length graph shows them.
BANDS: tuple[tuple[int, int | None], ...] = (
    (1, 5),
    (6, 10),
    (11, 20),
    (21, 30),
    (31, 40),
    (41, None),
)

Person = Literal["first", "second", "third", "unclear"]
Tense = Literal["past", "present", "mixed", "unclear"]

_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
# Linear on any input (CON-018 measures every request): a run of end marks is only
# tried from its first mark, and neither the marks nor the closing quotes give anything
# back, so a long run of dots is read once rather than once from every dot.
_SENTENCE_END = re.compile(r"(?<![.!?…])[.!?…]++[\"'”’»)\]]*+(?=\s|$)|\n\s*\n")
_MARK = re.compile(
    r"@\"(?P<quoted>[^\"\n]{1,120})\""
    r"|\[\[(?P<bracket>[^\[\]\n]{1,120})\]\]"
    r"|(?<![\w@])@(?P<word>[^\W\d_][\w'’\-]{0,60})",
    re.UNICODE,
)
_DOUBLE = re.compile(r"“[^”]*”?|\"[^\"\n]*\"?|«[^»]*»?|„[^“”]*[“”]?")
# Curly single quotes are dialogue only at a word's edge, so "Mara’s" stays a word. A
# quote ends within its paragraph, before the next opening mark and within
# ``MAX_SINGLE`` characters, and what it reads is never given back: each try stops where
# the next one starts, so the whole text is read in one pass.
MAX_SINGLE = 2_000
_SINGLE = re.compile(
    rf"(?<![^\W_])‘(?:[^’‘\n]|’(?=[^\W_])){{0,{MAX_SINGLE}}}+’(?![^\W_])", re.UNICODE
)

# The commonest English words, to tell whether a text reads as English.
_ENGLISH = frozenset(
    """the and to of a in that is was he she it for on with as his her had at by
    not but be are they you i this from have or one all were we there their an so
    what when him which if would out up been my me no do into them then could your
    about who said did its more than over now some only like back down just""".split()
)
# Small words whose mix marks a writer (the commonest English function words).
FUNCTION_WORDS: tuple[str, ...] = tuple(
    """the and to of a in that is was he she it for on with as his her had at by not
    but be are they you i this from have or one all were we there their an so what
    when him which if would out up been my me no do into them then could about who
    said did more than over now some only just like back down""".split()
)
_STOP = _ENGLISH | frozenset(
    """also very much many most such each other these those here where while upon
    after before again ever even still though through until because should might must
    shall will can cannot can't don't didn't wasn't weren't won't wouldn't couldn't
    isn't aren't hasn't haven't hadn't it's i'm i'd i'll you're he's she's they're
    we're that's there's what's let's yes yeah okay well too off onto toward towards
    away once every both few being having doing does done made make get got going went
    come came know knew think thought look looked see saw way thing things something
    nothing anything everything someone anyone everyone time times""".split()
)
# Words that end in -ly but are not adverbs, or are adverbs no one would count.
_NOT_ADVERBS = frozenset(
    """only family early reply rely apply supply ally belly holy ugly lonely friendly
    lovely silly likely unlikely daily weekly monthly yearly hourly july lily emily
    italy fly sly jolly bully rally sully jelly hilly chilly smelly curly burly surly
    elderly costly deadly lively lowly orderly scholarly ghastly homely comely
    cowardly kindly manly womanly worldly heavenly earthly leisurely melancholy
    assembly anomaly monopoly butterfly dragonfly firefly gully folly molly holly
    dolly wally polly sally billy willy kelly shelly beverly oily wily""".split()
)
_FIRST_SINGULAR = frozenset({"i", "me", "my", "mine", "myself", "i'm", "i'd", "i'll", "i've"})
_FIRST_PLURAL = frozenset({"we", "us", "our", "ours", "ourselves", "we're", "we'd", "we'll"})
_SECOND = frozenset({"you", "your", "yours", "yourself", "you're", "you'd", "you'll"})
_THIRD = frozenset(
    {"he", "him", "his", "himself", "she", "her", "hers", "herself", "they", "them", "their"}
)
_SUBJECTS = frozenset({"i", "you", "he", "she", "it", "we", "they"})
_SKIP_BETWEEN = frozenset(
    """never always just still only all both then also even almost nearly already
    finally suddenly simply quietly slowly quickly soon once often""".split()
)
_PAST = frozenset(
    """was were had did said went came saw took made knew thought felt found gave told
    left kept stood sat ran began brought held heard met paid put read set let lost
    meant sent spent built bought caught fought taught sought slept swept wept could
    would might became grew drew threw flew knelt dealt dreamt leant leapt rose spoke
    broke chose froze woke wore tore swore bore drove rode wrote struck stuck hung
    swung clung shook took forgot got hid bit lit fell slid spun won sang rang sank
    drank shrank swam began""".split()
)
_PRESENT = frozenset(
    """am is are has have do does says goes comes sees takes makes knows thinks feels
    finds gives tells leaves keeps stands sits runs begins brings holds hears meets
    can will shall may""".split()
)
_ED_NOT_PAST = frozenset(
    {
        "need",
        "feed",
        "seed",
        "bleed",
        "breed",
        "speed",
        "proceed",
        "succeed",
        "exceed",
        "heed",
        "weed",
        "shed",
        "wed",
        "bed",
        "red",
        "embed",
        "led",
    }
)


# --------------------------------------------------------------------------- reading


@dataclass(frozen=True)
class Echo:
    word: str
    times: int
    # Where the run of repeats starts and ends in the text.
    start: int
    end: int


@dataclass(frozen=True)
class Slip:
    """A sentence of narration in the other tense from the rest of the scene."""

    start: int
    end: int


@dataclass(frozen=True)
class VoiceStats:
    """One text's measures. Counts, never words, except the echoes' and leaned-on words."""

    words: int
    sentences: int
    english: bool
    sentence_mean: float
    sentence_median: float
    sentence_long: float
    sentence_spread: float
    bands: tuple[int, ...]
    dialogue_share: float
    narration_words: int
    adverb_rate: float | None
    adverbs: tuple[tuple[str, int], ...]
    echoes: tuple[Echo, ...]
    echo_rate: float
    person: Person
    first_rate: float
    third_rate: float
    tense: Tense
    past_share: float | None
    tense_pairs: int
    slips: tuple[Slip, ...]
    # Uses per 1,000 words of each of ``FUNCTION_WORDS``; empty when not English.
    function_rates: tuple[float, ...]
    # The scene's own content words and counts (the commonest 200), for "leans on".
    content: tuple[tuple[str, int], ...]
    # Words that are always capitalised here (names), so they are never "leaned on".
    names: frozenset[str] = frozenset()


def _unmark(text: str) -> str:
    """The author's marks (@"Name", [[Name]], @Name) as plain words, offsets kept."""

    def plain(match: re.Match[str]) -> str:
        whole = match.group(0)
        phrase = match.group("quoted") or match.group("bracket") or match.group("word") or ""
        lead = whole.index(phrase)
        return " " * lead + phrase + " " * (len(whole) - lead - len(phrase))

    return _MARK.sub(plain, text)


def dialogue_mask(text: str) -> list[bool]:
    """For each character, whether it is inside quotation marks."""
    mask = [False] * len(text)
    spans = [m.span() for m in _DOUBLE.finditer(text)]
    if not spans:
        spans = [m.span() for m in _SINGLE.finditer(text)]
    for start, end in spans:
        for index in range(start, end):
            mask[index] = True
    return mask


def _percentile(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * share
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _band(length: int) -> int:
    for index, (low, high) in enumerate(BANDS):
        if length >= low and (high is None or length <= high):
            return index
    return 0


def _verb_tense(word: str) -> Literal["past", "present"] | None:
    if word in _PAST:
        return "past"
    if word in _PRESENT:
        return "present"
    if (
        word.endswith("ed")
        and len(word) > 3
        and word not in _ED_NOT_PAST
        and not word.endswith("eed")
    ):
        return "past"
    return None


def measure(text: str) -> VoiceStats:
    """Every measure of one text. Pure and deterministic."""
    text = _unmark(text[:MAX_TEXT])
    mask = dialogue_mask(text)
    tokens = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text)]
    lowered = [token.lower().replace("’", "'") for token, _, _ in tokens]
    total = len(tokens)
    english = total > 0 and sum(1 for w in lowered if w in _ENGLISH) / total >= 0.25
    in_dialogue = [mask[start] for _, start, _ in tokens]
    narration = [i for i in range(total) if not in_dialogue[i]]
    dialogue_words = total - len(narration)

    # Sentences: split at end marks and blank lines; each keeps its word count.
    lengths: list[int] = []
    sentence_spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_END.finditer(text):
        sentence_spans.append((cursor, match.end()))
        cursor = match.end()
    if cursor < len(text) and text[cursor:].strip():
        sentence_spans.append((cursor, len(text)))
    token_index = 0
    sentence_tokens: list[list[int]] = []
    for start, end in sentence_spans:
        members: list[int] = []
        while token_index < total and tokens[token_index][1] < end:
            if tokens[token_index][1] >= start:
                members.append(token_index)
            token_index += 1
        if members:
            sentence_tokens.append(members)
            lengths.append(len(members))
    bands = [0] * len(BANDS)
    for length in lengths:
        bands[_band(length)] += 1
    mean = sum(lengths) / len(lengths) if lengths else 0.0
    spread = (
        math.sqrt(sum((length - mean) ** 2 for length in lengths) / len(lengths))
        if lengths
        else 0.0
    )

    # Adverbs in the narration.
    adverb_counts: Counter[str] = Counter()
    if english:
        for i in narration:
            word = lowered[i]
            if word.endswith("ly") and len(word) > 4 and word not in _NOT_ADVERBS:
                adverb_counts[word] += 1
    adverb_rate = (
        1000 * sum(adverb_counts.values()) / len(narration) if english and narration else None
    )

    # Names: words written with a capital wherever they appear, never at a sentence's start
    # only. They repeat by nature and are never counted as echoes or leaned-on words.
    capital: dict[str, bool] = {}
    for (token, _, _), word in zip(tokens, lowered, strict=True):
        capital[word] = capital.get(word, True) and token[:1].isupper()
    names = frozenset(word for word, always in capital.items() if always and word not in _STOP)

    # Echoes: a content word three or more times within a window of words.
    positions: dict[str, list[int]] = {}
    content: Counter[str] = Counter()
    for i, word in enumerate(lowered):
        if len(word) < 4 or word in names or (english and word in _STOP):
            continue
        stem = word[:-1] if word.endswith("s") and not word.endswith("ss") else word
        if english and stem in _STOP:
            continue
        positions.setdefault(stem, []).append(i)
        content[stem] += 1
    echoes: list[Echo] = []
    for stem, places in positions.items():
        if len(places) < ECHO_TIMES:
            continue
        best = (0, 0, 0)
        left = 0
        for right in range(len(places)):
            while places[right] - places[left] >= ECHO_WINDOW:
                left += 1
            count = right - left + 1
            if count > best[0]:
                best = (count, places[left], places[right])
        if best[0] >= ECHO_TIMES:
            echoes.append(Echo(stem, best[0], tokens[best[1]][1], tokens[best[2]][2]))
    echoes.sort(key=lambda item: (-item.times, item.start))
    echo_rate = 1000 * len(echoes) / total if total else 0.0

    # Who tells it, from the narration's pronouns.
    first = sum(
        1 for i in narration if lowered[i] in _FIRST_SINGULAR or lowered[i] in _FIRST_PLURAL
    )
    second = sum(1 for i in narration if lowered[i] in _SECOND)
    third = sum(1 for i in narration if lowered[i] in _THIRD)
    per = 100 / len(narration) if narration else 0.0
    first_rate, third_rate, second_rate = first * per, third * per, second * per
    person: Person = "unclear"
    if english and len(narration) >= 50:
        if first_rate >= 1.0:
            person = "first"
        elif second_rate >= 2.0 and first_rate < 0.5:
            person = "second"
        elif third_rate >= 1.5 and first_rate < 0.3:
            person = "third"

    # Tense: a subject pronoun in the narration and the verb after it.
    past = present = 0
    sentence_tense: list[tuple[int, int]] = []
    if english:
        for members in sentence_tokens:
            s_past = s_present = 0
            for position, i in enumerate(members):
                if in_dialogue[i] or lowered[i] not in _SUBJECTS:
                    continue
                for j in members[position + 1 : position + 3]:
                    if in_dialogue[j]:
                        break
                    word = lowered[j]
                    if word in _SKIP_BETWEEN or (word.endswith("ly") and word not in _NOT_ADVERBS):
                        continue
                    found = _verb_tense(word)
                    if found == "past":
                        s_past += 1
                    elif found == "present" or (
                        lowered[i] in {"he", "she", "it"}
                        and word.endswith("s")
                        and not word.endswith("ss")
                        and len(word) > 3
                        and word not in _STOP
                    ):
                        s_present += 1
                    break
            past += s_past
            present += s_present
            sentence_tense.append((s_past, s_present))
    pairs = past + present
    past_share = past / pairs if pairs else None
    tense: Tense = "unclear"
    if past_share is not None and pairs >= 10:
        tense = "past" if past_share >= 0.7 else "present" if past_share <= 0.3 else "mixed"
    slips: list[Slip] = []
    if tense == "past" and past_share is not None and past_share >= 0.85:
        for members, (s_past, s_present) in zip(sentence_tokens, sentence_tense, strict=False):
            if s_present and not s_past and len(slips) < MAX_SLIPS:
                slips.append(Slip(tokens[members[0]][1], tokens[members[-1]][2]))

    function_rates: tuple[float, ...] = ()
    if english and total:
        counts = Counter(lowered)
        function_rates = tuple(1000 * counts[word] / total for word in FUNCTION_WORDS)

    return VoiceStats(
        words=total,
        sentences=len(lengths),
        english=english,
        sentence_mean=round(mean, 2),
        sentence_median=float(median(lengths)) if lengths else 0.0,
        sentence_long=round(_percentile(lengths, 0.9), 2),
        sentence_spread=round(spread, 2),
        bands=tuple(bands),
        dialogue_share=round(dialogue_words / total, 4) if total else 0.0,
        narration_words=len(narration),
        adverb_rate=round(adverb_rate, 2) if adverb_rate is not None else None,
        adverbs=tuple(adverb_counts.most_common(5)),
        echoes=tuple(echoes[:MAX_ECHOES]),
        echo_rate=round(echo_rate, 2),
        person=person,
        first_rate=round(first_rate, 2),
        third_rate=round(third_rate, 2),
        tense=tense,
        past_share=round(past_share, 3) if past_share is not None else None,
        tense_pairs=pairs,
        slips=tuple(slips),
        function_rates=function_rates,
        content=tuple(content.most_common(200)),
        names=names,
    )


# --------------------------------------------------------------------------- the usual


@dataclass(frozen=True)
class Spread:
    """A measure across the usual: its middle and its median absolute deviation."""

    middle: float
    deviation: float

    def score(self, value: float, floor: float) -> float:
        """The modified z-score of ``value``; ``floor`` stands in for a deviation of 0."""
        deviation = max(self.deviation, floor)
        return 0.6745 * (value - self.middle) / deviation


def _spread(values: Sequence[float]) -> Spread | None:
    if not values:
        return None
    middle = float(median(values))
    return Spread(middle, float(median([abs(v - middle) for v in values])))


@dataclass(frozen=True)
class Baseline:
    """The author's usual: the same measures across the scenes chosen to stand for it."""

    scenes: int
    words: int
    sentence_mean: Spread | None
    sentence_spread: Spread | None
    dialogue_share: Spread | None
    adverb_rate: Spread | None
    echo_rate: Spread | None
    # The share of measured scenes told this way, and in this tense.
    person: Person
    person_share: float
    tense: Tense
    tense_share: float
    # Everyday words: their mean and standard deviation per word, and each scene's Delta.
    function_means: tuple[float, ...] = ()
    function_sds: tuple[float, ...] = ()
    deltas: Spread | None = None
    delta_scenes: int = 0
    # Uses of each content word per 1,000 words across the usual.
    content_rates: dict[str, float] = field(default_factory=dict)

    @property
    def enough(self) -> bool:
        return self.scenes >= MIN_BASELINE_SCENES and self.words >= MIN_BASELINE_WORDS


def _majority(values: Iterable[str]) -> tuple[str, float]:
    known = [value for value in values if value not in ("unclear", "mixed")]
    if not known:
        return "unclear", 0.0
    value, count = Counter(known).most_common(1)[0]
    return value, count / len(known)


def delta(rates: Sequence[float], means: Sequence[float], sds: Sequence[float]) -> float:
    """Burrows's Delta of one text from the usual's centre: the mean absolute z-score of
    its everyday words (the centre's own z-scores are all 0)."""
    used = [(r - m) / s for r, m, s in zip(rates, means, sds, strict=True) if s > 0]
    return sum(abs(z) for z in used) / len(used) if used else 0.0


def baseline(stats: Sequence[VoiceStats]) -> Baseline:
    """The usual from these scenes; scenes too short to measure are left out."""
    measured = [item for item in stats if item.words >= MIN_SCENE_WORDS]
    words = sum(item.words for item in measured)
    adverbs = [item.adverb_rate for item in measured if item.adverb_rate is not None]
    person, person_share = _majority(item.person for item in measured)
    tense, tense_share = _majority(item.tense for item in measured)
    profiles = [
        item.function_rates
        for item in measured
        if item.function_rates and item.words >= MIN_DELTA_WORDS
    ]
    means: tuple[float, ...] = ()
    sds: tuple[float, ...] = ()
    deltas: Spread | None = None
    if len(profiles) >= MIN_DELTA_SCENES:
        columns = list(zip(*profiles, strict=True))
        means = tuple(sum(column) / len(column) for column in columns)
        sds = tuple(
            math.sqrt(sum((v - m) ** 2 for v in column) / (len(column) - 1))
            for column, m in zip(columns, means, strict=True)
        )
        # Each scene's Delta against the others (leave one out), so a scene is never
        # measured against a centre it helped to set. Worked out from running sums, so a
        # long book stays quick.
        n = len(profiles)
        sums = [sum(column) for column in columns]
        squares = [sum(v * v for v in column) for column in columns]
        found: list[float] = []
        for profile in profiles:
            rest_means = [(s - x) / (n - 1) for s, x in zip(sums, profile, strict=True)]
            rest_sds = [
                math.sqrt(max((q - x * x - (n - 1) * m * m) / (n - 2), 0.0))
                for q, x, m in zip(squares, profile, rest_means, strict=True)
            ]
            found.append(delta(profile, rest_means, rest_sds))
        deltas = _spread(found)
    totals: Counter[str] = Counter()
    for item in measured:
        totals.update(dict(item.content))
    rates = {word: 1000 * count / words for word, count in totals.items()} if words else {}
    return Baseline(
        scenes=len(measured),
        words=words,
        sentence_mean=_spread([item.sentence_mean for item in measured]),
        sentence_spread=_spread([item.sentence_spread for item in measured]),
        dialogue_share=_spread([item.dialogue_share for item in measured]),
        adverb_rate=_spread(adverbs),
        echo_rate=_spread([item.echo_rate for item in measured]),
        person=person,  # type: ignore[arg-type]
        person_share=round(person_share, 3),
        tense=tense,  # type: ignore[arg-type]
        tense_share=round(tense_share, 3),
        function_means=means,
        function_sds=sds,
        deltas=deltas,
        delta_scenes=len(profiles),
        content_rates=rates,
    )


# ---------------------------------------------------------------------------- drift


@dataclass(frozen=True)
class VoiceNote:
    """A way this scene departs from the author's usual, in plain words. Advisory."""

    code: str
    message: str
    start: int | None = None
    end: int | None = None
    # What the note is about, short: which way it points ("longer", "present"), or whose
    # speech. A dismissal of the note holds while this stays the same.
    subject: str = ""


# measure -> (floor for a deviation of 0, the least difference worth saying)
_GATES: dict[str, tuple[float, float]] = {
    "sentence_mean": (1.5, 4.0),
    "sentence_spread": (1.5, 4.0),
    "dialogue_share": (0.05, 0.15),
    "adverb_rate": (2.0, 6.0),
    "echo_rate": (1.0, 3.0),
}


def _far(spread: Spread | None, value: float | None, gate: str) -> bool:
    if spread is None or value is None:
        return False
    floor, least = _GATES[gate]
    return abs(spread.score(value, floor)) > OUTLIER_SCORE and abs(value - spread.middle) >= least


def _people(person: str) -> str:
    return {
        "first": "with “I”",
        "second": "with “you”",
        "third": "with “he”, “she” or “they”",
    }.get(person, "")


def _percent(value: float) -> str:
    return f"{round(100 * value)}%"


def drift(stats: VoiceStats, usual: Baseline) -> list[VoiceNote]:
    """How far one scene departs from the usual. Empty when either is too small to say."""
    if stats.words < MIN_SCENE_WORDS or not usual.enough:
        return []
    notes: list[VoiceNote] = []
    if _far(usual.sentence_mean, stats.sentence_mean, "sentence_mean"):
        middle = usual.sentence_mean.middle if usual.sentence_mean else 0
        notes.append(
            VoiceNote(
                "voice_sentence_length",
                f"Sentences here run about {round(stats.sentence_mean)} words on average; "
                f"yours usually run about {round(middle)}.",
                subject="longer" if stats.sentence_mean > middle else "shorter",
            )
        )
    if _far(usual.sentence_spread, stats.sentence_spread, "sentence_spread"):
        middle = usual.sentence_spread.middle if usual.sentence_spread else 0
        more = stats.sentence_spread > middle
        notes.append(
            VoiceNote(
                "voice_sentence_variety",
                "Sentence lengths vary much more here than in your other scenes."
                if more
                else "Sentences here are much more alike in length than in your other scenes.",
                subject="more" if more else "less",
            )
        )
    if _far(usual.dialogue_share, stats.dialogue_share, "dialogue_share"):
        middle = usual.dialogue_share.middle if usual.dialogue_share else 0
        notes.append(
            VoiceNote(
                "voice_dialogue",
                f"About {_percent(stats.dialogue_share)} of this scene is dialogue; your scenes "
                f"are usually about {_percent(middle)}.",
                subject="more" if stats.dialogue_share > middle else "less",
            )
        )
    if _far(usual.adverb_rate, stats.adverb_rate, "adverb_rate"):
        middle = usual.adverb_rate.middle if usual.adverb_rate else 0
        shown = ", ".join(f"“{word}”" for word, _ in stats.adverbs[:3])
        notes.append(
            VoiceNote(
                "voice_adverbs",
                f"Words ending in -ly come about {round(stats.adverb_rate or 0)} times in every "
                f"1,000 words of narration here ({shown}); usually about {round(middle)}.",
                subject="more" if (stats.adverb_rate or 0) > middle else "less",
            )
        )
    if _far(usual.echo_rate, stats.echo_rate, "echo_rate") and stats.echoes:
        first = stats.echoes[0]
        notes.append(
            VoiceNote(
                "voice_echoes",
                f"More words repeated close together than usual, such as “{first.word}” "
                f"{first.times} times within {ECHO_WINDOW} words.",
                first.start,
                first.end,
            )
        )
    # Who tells it, only when the usual is clear (four in five scenes one way).
    if (
        usual.person in ("first", "second", "third")
        and usual.person_share >= 0.8
        and stats.person not in ("unclear", usual.person)
    ):
        notes.append(
            VoiceNote(
                "voice_person",
                f"This scene is told {_people(stats.person)} in the narration; your other "
                f"scenes are told {_people(usual.person)}.",
                subject=stats.person,
            )
        )
    elif usual.person == "third" and usual.person_share >= 0.8 and stats.person == "third":
        if stats.first_rate >= 0.3:
            notes.append(
                VoiceNote(
                    "voice_person_mixed",
                    "“I” turns up in the narration, outside quotation marks, in a scene told "
                    "with “he” or “she”. Thoughts written without quotation marks do this too.",
                )
            )
    if usual.tense in ("past", "present") and usual.tense_share >= 0.8:
        if stats.tense in ("past", "present") and stats.tense != usual.tense:
            notes.append(
                VoiceNote(
                    "voice_tense",
                    f"This scene is told in the {stats.tense} tense; your other scenes are in "
                    f"the {usual.tense}.",
                    subject=stats.tense,
                )
            )
        elif stats.tense == "mixed" and stats.tense_pairs >= 10:
            notes.append(
                VoiceNote(
                    "voice_tense_mixed",
                    f"The narration moves between past and present more than your other scenes "
                    f"do: about {_percent(stats.past_share or 0)} of it is in the past.",
                )
            )
    for slip in stats.slips if usual.tense == "past" else ():
        notes.append(
            VoiceNote(
                "voice_tense_slip",
                "This sentence of narration is in the present tense; the rest of the scene is "
                "in the past. Something always true can be, so check it reads as you meant.",
                slip.start,
                slip.end,
            )
        )
    if (
        usual.deltas is not None
        and stats.function_rates
        and stats.words >= MIN_DELTA_WORDS
        and usual.function_means
    ):
        value = delta(stats.function_rates, usual.function_means, usual.function_sds)
        if usual.deltas.score(value, 0.05) > OUTLIER_SCORE and value - usual.deltas.middle >= 0.25:
            notes.append(
                VoiceNote(
                    "voice_everyday_words",
                    "The small everyday words (the, and, of, but) come in a different mix from "
                    "your other scenes. That is often the first sign a scene sounds like someone "
                    "else, such as a kept draft. A rough sign below a few thousand words.",
                )
            )
    notes.extend(leans(stats, usual))
    return notes


def leans(stats: VoiceStats, usual: Baseline) -> list[VoiceNote]:
    """Words this scene uses far more than the usual does (at least four times, and five
    times as often, counting every word as used at least once elsewhere)."""
    if not stats.words or not usual.words:
        return []
    found: list[tuple[float, str, int]] = []
    floor = 1000 / usual.words
    for word, count in stats.content:
        if count < 4 or word in stats.names:
            continue
        rate = 1000 * count / stats.words
        elsewhere = max(usual.content_rates.get(word, 0.0), floor)
        if rate >= 5 * elsewhere and rate - elsewhere >= 2:
            found.append((rate / elsewhere, word, count))
    found.sort(key=lambda item: (-item[0], item[1]))
    if not found:
        return []
    # One note for all of them: "count" and "counting" are one habit, not two problems.
    listed = []
    for _, word, count in found[:MAX_LEANS]:
        elsewhere = usual.content_rates.get(word, 0.0)
        usual_words = (
            "not elsewhere"
            if elsewhere == 0
            else f"about once in every {round(1000 / elsewhere):,} words elsewhere"
        )
        listed.append(f"“{word}” {count} times ({usual_words})")
    lead = (
        "This scene leans on a word you seldom use elsewhere: "
        if len(listed) == 1
        else "This scene leans on words you seldom use elsewhere: "
    )
    return [
        VoiceNote(
            "voice_leans_on",
            lead + "; ".join(listed) + ".",
            subject=" ".join(sorted(word for _, word, _ in found[:MAX_LEANS])),
        )
    ]


# ---------------------------------------------------------------------------- trends


@dataclass(frozen=True)
class Trend:
    code: str
    message: str


def trends(stats: Sequence[tuple[str, VoiceStats]]) -> list[Trend]:
    """Gradual drift across scenes in reading order: the last third against the first,
    for scenes long enough to measure (at least six of them)."""
    measured = [item for _, item in stats if item.words >= MIN_SCENE_WORDS]
    if len(measured) < 6:
        return []
    third = len(measured) // 3
    first, last = measured[:third], measured[-third:]
    found: list[Trend] = []
    for gate in ("sentence_mean", "dialogue_share", "adverb_rate"):
        before = [getattr(item, gate) for item in first if getattr(item, gate) is not None]
        after = [getattr(item, gate) for item in last if getattr(item, gate) is not None]
        if not before or not after:
            continue
        a, b = float(median(before)), float(median(after))
        if abs(b - a) < _GATES[gate][1]:
            continue
        if gate == "sentence_mean":
            verb = "grow longer" if b > a else "grow shorter"
            words = (
                f"Across these scenes, sentences {verb}: from about {round(a)} words early on "
                f"to about {round(b)} by the end."
            )
        elif gate == "dialogue_share":
            words = (
                f"Across these scenes, dialogue moves from about {_percent(a)} early on to "
                f"about {_percent(b)} by the end."
            )
        else:
            words = (
                f"Across these scenes, words ending in -ly move from about {round(a)} in every "
                f"1,000 words early on to about {round(b)} by the end."
            )
        found.append(Trend(f"voice_trend_{gate}", words))
    return found


# ------------------------------------------------------------------ who says what

# Words that tie a line of dialogue to its speaker in a tag: "said Tobin", "Mara asked".
SPEECH_VERBS = frozenset(
    """said says asked asks replied replies answered answers whispered whispers shouted
    shouts called calls muttered mutters murmured murmurs cried cries snapped snaps added
    adds told tells began begins continued continues insisted insists admitted admits
    demanded demands protested protests yelled yells hissed hisses breathed breathes
    agreed agrees repeated repeats explained explains warned warns""".split()
)
# A character's speech is compared once there is this much of it, here and elsewhere.
MIN_SPEECH_WORDS = 60
MIN_SPEECH_LINES = 3
MIN_SPEECH_SCENES = 3
MIN_SPEECH_USUAL_WORDS = 300
# The share of a scene's dialogue that must be tied to someone for notes about who says
# what; below it, attribution is too uncertain to say anything.
MIN_ATTRIBUTED_SHARE = 0.5
_CONTRACTION = re.compile(r"[^\W\d_]+['’](?:t|s|re|ve|ll|d|m)(?![^\W\d_])", re.IGNORECASE)
_LINE_BREAK = re.compile(r"\n+")


@dataclass(frozen=True)
class Line:
    """One quoted line of dialogue, and who says it when that can be told for sure."""

    start: int
    end: int
    speaker: str | None
    # How it was told: "tag" (a speech verb and a name next to it) or "turns" (an
    # exchange between two people taking turns, each tagged elsewhere in it).
    how: str = ""


@dataclass(frozen=True)
class Speech:
    """One person's dialogue in one text, measured."""

    who: str
    lines: int
    words: int
    sentences: int
    contractions: int
    questions: int

    @property
    def sentence_mean(self) -> float:
        return self.words / self.sentences if self.sentences else 0.0

    @property
    def contraction_rate(self) -> float:
        """Short forms (don't, I'm, she'll) per 100 words of their speech."""
        return 100 * self.contractions / self.words if self.words else 0.0


def _quotes(text: str) -> list[tuple[int, int]]:
    spans = [m.span() for m in _DOUBLE.finditer(text)]
    return spans or [m.span() for m in _SINGLE.finditer(text)]


def _name_pattern(names: Iterable[str]) -> str:
    unique = sorted(
        {" ".join(name.split()) for name in names if name.strip()}, key=len, reverse=True
    )
    return "|".join(re.escape(name) for name in unique)


_VERBS = "|".join(sorted(SPEECH_VERBS))
_SPEECH_VERB = re.compile(rf"(?<![\w])(?:{_VERBS})(?![\w])", re.IGNORECASE)


@dataclass(frozen=True)
class _Names:
    """Everyone's names in one pattern each way, read once for a whole text."""

    who: dict[str, frozenset[str]]
    after: re.Pattern[str] | None
    before: re.Pattern[str] | None
    any: re.Pattern[str] | None

    @classmethod
    def of(cls, people: dict[str, tuple[str, ...]]) -> _Names:
        who: dict[str, set[str]] = {}
        for person, names in people.items():
            for name in names:
                if name.strip():
                    who.setdefault(" ".join(name.split()).casefold(), set()).add(person)
        everyone = _name_pattern(name for names in people.values() for name in names)
        if not everyone:
            return cls({}, None, None, None)
        return cls(
            {name: frozenset(persons) for name, persons in who.items()},
            re.compile(
                rf"\s*,?\s*(?:(?:{_VERBS})\s+(?P<name>{everyone})|(?P<first>{everyone})\s+"
                rf"(?:{_VERBS}))(?![\w])",
                re.IGNORECASE,
            ),
            re.compile(
                rf"(?<![\w])(?P<name>{everyone})\s+(?:{_VERBS})(?:\s+[\w-]+){{0,2}}\s*[,:]\s*$",
                re.IGNORECASE,
            ),
            re.compile(rf"(?<![\w])(?:{everyone})(?![\w])", re.IGNORECASE),
        )

    def people(self, name: str) -> frozenset[str]:
        return self.who.get(" ".join(name.split()).casefold(), frozenset())


def _tagged(
    text: str, start: int, end: int, names: _Names, paragraph: tuple[int, int]
) -> str | None:
    """The person a speech tag right next to a quote names: '"…," said Tobin', '"…" Tobin
    asked', 'Tobin said, "…"'. A pronoun ("she said") is never read."""
    if names.after is None or names.before is None:
        return None
    found: set[str] = set()
    after = names.after.match(text, end, min(paragraph[1], end + 60))
    if after is not None:
        found |= names.people(after.group("name") or after.group("first"))
    before = names.before.search(text[max(paragraph[0], start - 60) : start])
    if before is not None:
        found |= names.people(before.group("name"))
    return next(iter(found)) if len(found) == 1 else None


def attribute(text: str, people: dict[str, tuple[str, ...]]) -> list[Line]:
    """Every quoted line, with its speaker when a tag says so, or when it sits in an
    exchange of two people taking turns (one line a paragraph, each of the two tagged at
    least once, and every tag in the exchange where the turns put it). Anything else is
    left with no speaker: a line is never guessed from a pronoun or from who is nearby."""
    text = text[:MAX_TEXT]
    paragraphs: list[tuple[int, int]] = []
    cursor = 0
    for match in _LINE_BREAK.finditer(text):
        if text[cursor : match.start()].strip():
            paragraphs.append((cursor, match.start()))
        cursor = match.end()
    if text[cursor:].strip():
        paragraphs.append((cursor, len(text)))
    quotes = _quotes(text)
    starts = [start for start, _end in quotes]
    names = _Names.of(people)
    lines: list[Line] = []
    # Each paragraph's lines, the one person its tags name (None: no tag, or two), whether
    # it is all dialogue (and its tag), and the people its narration names.
    by_paragraph: list[tuple[list[int], str | None, bool, set[str]]] = []
    for paragraph in paragraphs:
        mine = list(range(bisect_left(starts, paragraph[0]), bisect_left(starts, paragraph[1])))
        tags = {
            tagged
            for index in mine
            if (tagged := _tagged(text, *quotes[index], names, paragraph)) is not None
        }
        pieces, at = [], paragraph[0]
        for index in mine:
            start, end = quotes[index]
            pieces.append(text[at:start])
            at = max(at, end)
        pieces.append(text[at : paragraph[1]] if at < paragraph[1] else "")
        narration = "".join(pieces)
        named = (
            {who for match in names.any.finditer(narration) for who in names.people(match.group(0))}
            if names.any is not None
            else set()
        )
        # A tag with a pronoun ("she said") is someone's turn, but not one to name.
        spoken_only = (
            bool(mine)
            and len(_WORD.findall(narration)) <= 4
            and not (_SPEECH_VERB.search(narration) and not tags)
        )
        speaker = next(iter(tags)) if len(tags) == 1 else None
        by_paragraph.append((mine, speaker, spoken_only, named))
    # Exchanges: runs of paragraphs with dialogue in them, one turn each.
    turns: dict[int, str] = {}
    run: list[int] = []

    def settle(run: list[int]) -> None:
        tagged = [(i, by_paragraph[i][1]) for i in run if by_paragraph[i][1] is not None]
        speakers = {who for _, who in tagged if who is not None}
        if len(speakers) != 2 or len(run) < 3:
            return
        # Anyone else named in the exchange may be the one speaking: no turns are given.
        if any(by_paragraph[i][3] - speakers for i in run):
            return
        first_index, first_who = tagged[0]
        other = next(who for who in speakers if who != first_who)
        for i, who in tagged:
            expected = first_who if (i - first_index) % 2 == 0 else other
            if who != expected:
                return
        for i in run:
            if by_paragraph[i][1] is None and by_paragraph[i][2]:
                turns[i] = str(first_who if (i - first_index) % 2 == 0 else other)

    for i, (mine, _speaker, _only, _named) in enumerate(by_paragraph):
        if mine:
            run.append(i)
        else:
            settle(run)
            run = []
    settle(run)
    for i, (mine, speaker, _only, _named) in enumerate(by_paragraph):
        for index in mine:
            start, end = quotes[index]
            if speaker is not None:
                lines.append(Line(start, end, speaker, "tag"))
            elif i in turns:
                lines.append(Line(start, end, turns[i], "turns"))
            else:
                lines.append(Line(start, end, None))
    return lines


def speeches(text: str, people: dict[str, tuple[str, ...]]) -> tuple[dict[str, Speech], float]:
    """Each person's dialogue in a text, measured, and the share of the text's dialogue
    (in words) that could be tied to someone."""
    text = text[:MAX_TEXT]
    lines = attribute(text, people)
    gathered: dict[str, list[str]] = {}
    total = 0
    tied = 0
    for line in lines:
        words = text[line.start : line.end]
        count = len(_WORD.findall(words))
        total += count
        if line.speaker is not None:
            tied += count
            gathered.setdefault(line.speaker, []).append(words)
    found: dict[str, Speech] = {}
    for who, said in gathered.items():
        spoken = sum(len(_WORD.findall(item)) for item in said)
        sentences = sum(max(1, len(re.findall(r"[.!?…]+", item.strip('"“”‘’ ')))) for item in said)
        found[who] = Speech(
            who=who,
            lines=len(said),
            words=spoken,
            sentences=sentences,
            contractions=sum(len(_CONTRACTION.findall(item)) for item in said),
            questions=sum(item.count("?") for item in said),
        )
    return found, (tied / total if total else 0.0)


def speech_drift_ready(here: Speech, elsewhere: Sequence[Speech]) -> bool:
    """Whether there is enough of someone's speech, here and elsewhere, to compare."""
    if here.words < MIN_SPEECH_WORDS or here.lines < MIN_SPEECH_LINES:
        return False
    usable = [item for item in elsewhere if item.words >= MIN_SPEECH_WORDS // 2]
    return len(usable) >= MIN_SPEECH_SCENES and sum(i.words for i in usable) >= (
        MIN_SPEECH_USUAL_WORDS
    )


def speech_drift(here: Speech, elsewhere: Sequence[Speech], share: float) -> list[VoiceNote]:
    """How one person's dialogue in this scene departs from their dialogue elsewhere: how
    long their sentences run, and how often they use short forms (don't, I'm). Said only
    when enough of the scene's dialogue is tied to its speakers, they say enough here, and
    there is enough of them elsewhere."""
    if share < MIN_ATTRIBUTED_SHARE or not speech_drift_ready(here, elsewhere):
        return []
    usable = [item for item in elsewhere if item.words >= MIN_SPEECH_WORDS // 2]
    notes: list[VoiceNote] = []
    lengths = _spread([item.sentence_mean for item in usable])
    if lengths is not None:
        value = here.sentence_mean
        if abs(lengths.score(value, 1.5)) > OUTLIER_SCORE and abs(value - lengths.middle) >= 4:
            notes.append(
                VoiceNote(
                    "voice_speaker_length",
                    f"{here.who}'s sentences run about {round(value)} words here when they "
                    f"speak; in your other scenes about {round(lengths.middle)}.",
                    subject=f"{here.who}\x1e{'longer' if value > lengths.middle else 'shorter'}",
                )
            )
    short = _spread([item.contraction_rate for item in usable])
    if short is not None:
        value = here.contraction_rate
        if abs(short.score(value, 1.0)) > OUTLIER_SCORE and abs(value - short.middle) >= 3:
            more = value > short.middle
            notes.append(
                VoiceNote(
                    "voice_speaker_short_forms",
                    f"{here.who} uses short forms such as “don't” and “I'm” "
                    + ("far more" if more else "far less")
                    + f" here: about {round(value)} in every 100 words they say, against "
                    f"about {round(short.middle)} in your other scenes.",
                    subject=f"{here.who}\x1e{'more' if more else 'less'}",
                )
            )
    return notes


__all__ = [
    "BANDS",
    "FUNCTION_WORDS",
    "MIN_BASELINE_SCENES",
    "MIN_BASELINE_WORDS",
    "MIN_DELTA_WORDS",
    "MIN_SCENE_WORDS",
    "VOICE_VERSION",
    "Baseline",
    "Echo",
    "Slip",
    "Spread",
    "Trend",
    "VoiceNote",
    "VoiceStats",
    "Line",
    "Speech",
    "attribute",
    "speech_drift",
    "speech_drift_ready",
    "speeches",
    "baseline",
    "delta",
    "dialogue_mask",
    "drift",
    "leans",
    "measure",
    "trends",
]
