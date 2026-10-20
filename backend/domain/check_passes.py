"""Checks with the assistant, one kind at a time (CON-008).

A check of a scene is made of passes, each asking about one thing only: what the
viewpoint character could know and the secrets kept from them, continuity with the
story bible, what the scene's plan asks for, the author's voice, and clarity. Each pass
is one model call with its own instructions and only the material it needs, so one
kind of judgment does not colour another (a judge asked about several criteria at once
tends to lean the same way on all of them: Multi-Crit, arXiv 2511.21662). The author
chooses which passes run; every note records the pass that found it.

What each pass reads is chosen here from the scene's context, by section. The
viewpoint pass is given what the viewpoint character knows and, apart and labelled as
the author's only, the secrets they do not: a check has to know a secret to see it slip
onto the page, and nothing it is given is ever written into the story.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from backend.domain.context import (
    ContextItem,
    ContextManifest,
    ContextSection,
    LeftOut,
    render,
)
from backend.domain.context_parts import ContextChoice, MediaPart


class CheckPass(StrEnum):
    VIEWPOINT = "viewpoint"
    CONTINUITY = "continuity"
    PLAN = "plan"
    VOICE = "voice"
    CLARITY = "clarity"
    # The scene against its pictures (CON-014): only when the scene has pictures the
    # author chose to send or that have an accepted description.
    PICTURES = "pictures"


# The order passes run and are listed in.
PASS_ORDER: tuple[CheckPass, ...] = (
    CheckPass.VIEWPOINT,
    CheckPass.CONTINUITY,
    CheckPass.PLAN,
    CheckPass.VOICE,
    CheckPass.CLARITY,
    CheckPass.PICTURES,
)

# What each pass is called where the author chooses, in plain words.
PASS_LABELS: dict[CheckPass, str] = {
    CheckPass.VIEWPOINT: "Viewpoint and secrets",
    CheckPass.CONTINUITY: "Continuity with the story bible",
    CheckPass.PLAN: "What the plan asks for",
    CheckPass.VOICE: "Your voice",
    CheckPass.CLARITY: "Clarity",
    CheckPass.PICTURES: "What the pictures show",
}

PASS_DESCRIPTIONS: dict[CheckPass, str] = {
    CheckPass.VIEWPOINT: (
        "Whether the viewpoint character knows or notices what they could not, and whether "
        "a secret slips onto the page."
    ),
    CheckPass.CONTINUITY: (
        "Anything that goes against the story bible, your story rules or what has happened."
    ),
    CheckPass.PLAN: (
        "Whether the scene does what its plan says: what the reader learns, what must be "
        "true before and after, what the viewpoint character wants."
    ),
    CheckPass.VOICE: "Passages that do not sound like your own writing or your notes on it.",
    CheckPass.CLARITY: "Passages a reader could find hard to follow.",
    CheckPass.PICTURES: (
        "Anything the scene says about how someone or somewhere looks that its pictures "
        "show differently. Only pictures you chose to send, or that have a description."
    ),
}

# The sections of the scene's context each pass reads (the scene's own card is in SCENE).
PASS_SECTIONS: dict[CheckPass, frozenset[ContextSection]] = {
    CheckPass.VIEWPOINT: frozenset(
        {
            ContextSection.CAST,
            ContextSection.KNOWS,
            ContextSection.WRONG,
            ContextSection.READER,
            ContextSection.SCENE,
        }
    ),
    CheckPass.CONTINUITY: frozenset(
        {
            ContextSection.RULES,
            ContextSection.CAST,
            ContextSection.TRUE,
            ContextSection.KNOWS,
            ContextSection.WRONG,
            ContextSection.HAPPENED,
            ContextSection.PICTURES,
            ContextSection.WRITING,
            ContextSection.SCENE,
            # Only when the author chose to give secrets with the scene's context.
            ContextSection.SECRETS,
        }
    ),
    CheckPass.PLAN: frozenset(
        {
            ContextSection.CAST,
            ContextSection.PLAN,
            ContextSection.READER,
            ContextSection.SCENE,
        }
    ),
    CheckPass.VOICE: frozenset({ContextSection.CAST, ContextSection.WRITING}),
    CheckPass.CLARITY: frozenset({ContextSection.CAST, ContextSection.SCENE}),
    CheckPass.PICTURES: frozenset(
        {ContextSection.CAST, ContextSection.PICTURES, ContextSection.SCENE}
    ),
}

# At most this many notes from one pass; none when the scene is sound in that respect.
MAX_PASS_NOTES = 8
# Roughly what one pass's answer takes.
PASS_ANSWER_TOKENS = 600
# Roughly the words of one pass's instructions.
PASS_INSTRUCTION_WORDS = 260
# Secrets given to the viewpoint pass, at most.
MAX_SECRETS = 60
# English prose runs at roughly four tokens to every three words.
TOKENS_PER_WORD = 4 / 3


def choose_passes(values: Iterable[str] | None) -> tuple[CheckPass, ...]:
    """The passes asked for, in their usual order, each once. None means every pass.
    An unknown name raises ``ValueError``."""
    if values is None:
        return PASS_ORDER
    asked = {CheckPass(str(value)) for value in values}
    return tuple(item for item in PASS_ORDER if item in asked)


@dataclass(frozen=True)
class PassMaterial:
    """What one pass is given besides the scene's words."""

    check: CheckPass
    # The story, for this pass, as context sections.
    story: str
    # Only for the viewpoint pass: the secrets kept from the viewpoint character.
    secrets: str = ""
    # Only for the voice pass: the author's notes on how they write.
    voice: str = ""
    # Only for the pictures pass: the scene's pictures, each sent as itself or described,
    # as the route allows when the pass runs (CTX-013).
    pictures: tuple[MediaPart, ...] = ()

    @property
    def words(self) -> int:
        return sum(len(part.split()) for part in (self.story, self.secrets, self.voice))

    @property
    def has_material(self) -> bool:
        """Whether there is anything for the pass to look at. Only the pictures pass can
        have nothing: a scene with no picture it may use is not checked against pictures."""
        if self.check is not CheckPass.PICTURES:
            return True
        return any(
            part.choice is ContextChoice.SEND or part.fallback_text.strip()
            for part in self.pictures
        )

    @property
    def picture_tokens(self) -> int:
        """The most the pictures could add: each one to send counted as sent."""
        return sum(part.tokens for part in self.pictures if part.choice is ContextChoice.SEND)


def _secret_items(manifest: ContextManifest) -> list[ContextItem]:
    """Every secret the scene's context knows of, given or left out, highest first.

    A secret left out of drafting (or cut for space) is still given, so the pass can
    spot it leaking; one the author removed outright is not."""
    found = {item.key: item for item in manifest.items if item.section is ContextSection.SECRETS}
    for omission in manifest.left_out:
        if omission.item.section is ContextSection.SECRETS and omission.reason in (
            LeftOut.SECRET,
            LeftOut.BUDGET,
        ):
            found.setdefault(omission.item.key, omission.item)
    ranked = sorted(found.values(), key=lambda item: (-item.score, item.key))
    return ranked[:MAX_SECRETS]


def pass_material(check: CheckPass, manifest: ContextManifest, voice: str = "") -> PassMaterial:
    """The material one pass reads, chosen from the scene's context by section."""
    sections = PASS_SECTIONS[check]
    if check is CheckPass.VIEWPOINT:
        story = render(item for item in manifest.items if item.section in sections)
        secrets = render(_secret_items(manifest))
        return PassMaterial(check, story=story, secrets=secrets)
    story = render(item for item in manifest.items if item.section in sections)
    if check is CheckPass.VOICE:
        return PassMaterial(check, story=story, voice=voice.strip())
    if check is CheckPass.PICTURES:
        pictures = tuple(
            item.media
            for item in manifest.items
            if item.section is ContextSection.PICTURES
            and item.media is not None
            and item.media.choice is not ContextChoice.LEAVE_OUT
        )
        return PassMaterial(check, story=story, pictures=pictures)
    return PassMaterial(check, story=story)


@dataclass(frozen=True)
class PassEstimate:
    check: CheckPass
    prompt_tokens: int
    completion_tokens: int

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def estimate_pass(material: PassMaterial, content: str) -> PassEstimate:
    """Roughly what one pass reads and writes, before anything is sent."""
    if not material.has_material:
        return PassEstimate(check=material.check, prompt_tokens=0, completion_tokens=0)
    reads = material.words + len(content.split()) + PASS_INSTRUCTION_WORDS
    return PassEstimate(
        check=material.check,
        prompt_tokens=math.ceil(reads * TOKENS_PER_WORD) + material.picture_tokens,
        completion_tokens=PASS_ANSWER_TOKENS,
    )


def estimate_passes(
    checks: Sequence[CheckPass], manifest: ContextManifest, content: str, voice: str = ""
) -> list[PassEstimate]:
    return [estimate_pass(pass_material(check, manifest, voice), content) for check in checks]


__all__ = [
    "MAX_PASS_NOTES",
    "PASS_ANSWER_TOKENS",
    "PASS_DESCRIPTIONS",
    "PASS_INSTRUCTION_WORDS",
    "PASS_LABELS",
    "PASS_ORDER",
    "PASS_SECTIONS",
    "CheckPass",
    "PassEstimate",
    "PassMaterial",
    "choose_passes",
    "estimate_pass",
    "estimate_passes",
    "pass_material",
]
