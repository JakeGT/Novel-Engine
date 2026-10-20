"""A small synthetic, publishable corpus for picture and words search (CTX-014).

Everything here is made by this file: pictures are coloured shapes drawn with Pillow,
and the words are short descriptions and lines of prose written for them. Nothing is
taken from a real book or a real picture, so the corpus can be published with the code.

Pictures are chosen so that every one has *hard negatives*: pictures that share all but
one property (same shape, other colour; same colour, other shape). A second drawing of
some pictures (moved and resized) is the *same subject drawn again*, for picture to
picture search. Some pictures carry a gate: secret, rejected, retired, linked secretly
to a character the point of view does not know, or in another story. None of those may
ever be returned where its gate applies.

With the deterministic stand-in embedder, a picture is ``described`` with its words, so
retrieval quality measures the harness, the gates and the ranking, not a model. A real
model (EmbeddingGemma 2) sees only the drawn pixels.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

SET_VERSION = "shapes-multimodal/1"

COLOURS: dict[str, tuple[int, int, int]] = {
    "red": (210, 30, 30),
    "blue": (30, 60, 210),
    "green": (30, 160, 60),
    "yellow": (235, 200, 20),
}
BACKGROUNDS: dict[str, tuple[int, int, int]] = {"white": (250, 250, 250), "black": (15, 15, 15)}
SHAPES = ("circle", "square", "triangle")

WORKSPACE = "ws_eval"
PROJECT = "prj_eval"
OTHER_PROJECT = "prj_eval_other"


@dataclass(frozen=True)
class EvalPicture:
    id: str
    shape: str
    colour: str
    background: str
    # A second drawing of the same subject: moved and resized.
    variant: int = 0
    project: str = PROJECT
    secret: bool = False
    rejected: bool = False
    retired: bool = False
    # A secret link to this character (the point of view does not know them).
    secret_link: str | None = None

    @property
    def words(self) -> str:
        return f"a {self.colour} {self.shape} on a {self.background} background"

    @property
    def subject(self) -> tuple[str, str, str]:
        return (self.shape, self.colour, self.background)

    @property
    def gated(self) -> bool:
        return (
            self.secret
            or self.rejected
            or self.retired
            or self.secret_link is not None
            or self.project != PROJECT
        )


def draw(picture: EvalPicture, size: int = 224) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (size, size), BACKGROUNDS[picture.background])
    pen = ImageDraw.Draw(image)
    colour = COLOURS[picture.colour]
    margin = 40 + 25 * picture.variant
    offset = 18 * picture.variant
    box = (margin + offset, margin, size - margin + offset // 2, size - margin)
    if picture.shape == "circle":
        pen.ellipse(box, fill=colour)
    elif picture.shape == "square":
        pen.rectangle(box, fill=colour)
    else:
        left, top, right, bottom = box
        pen.polygon([((left + right) // 2, top), (right, bottom), (left, bottom)], fill=colour)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _pictures() -> list[EvalPicture]:
    out: list[EvalPicture] = []
    for shape in SHAPES:
        for colour in COLOURS:
            out.append(
                EvalPicture(
                    id=f"pic_{colour}_{shape}", shape=shape, colour=colour, background="white"
                )
            )
    # The same subjects drawn again, for picture-to-picture search.
    for colour, shape in (("red", "circle"), ("blue", "square"), ("green", "triangle")):
        out.append(
            EvalPicture(
                id=f"pic_{colour}_{shape}_again",
                shape=shape,
                colour=colour,
                background="white",
                variant=1,
            )
        )
    # Near-duplicates behind gates: each would rank highly if a gate failed.
    out += [
        EvalPicture(
            id="gate_secret", shape="circle", colour="red", background="black", secret=True
        ),
        EvalPicture(
            id="gate_rejected", shape="circle", colour="red", background="black", rejected=True
        ),
        EvalPicture(
            id="gate_retired", shape="square", colour="blue", background="black", retired=True
        ),
        EvalPicture(
            id="gate_linked_secret",
            shape="triangle",
            colour="yellow",
            background="black",
            secret_link="ent_smuggler",
        ),
        EvalPicture(
            id="gate_other_story",
            shape="square",
            colour="green",
            background="black",
            project=OTHER_PROJECT,
        ),
    ]
    return out


PICTURES: tuple[EvalPicture, ...] = tuple(_pictures())


@dataclass(frozen=True)
class Passage:
    id: str
    words: str
    # The picture subject it is about: (shape, colour).
    about: tuple[str, str]


def _passages() -> list[Passage]:
    lines = {
        "circle": "a round {colour} disc, a perfect circle",
        "square": "a {colour} square with four straight sides",
        "triangle": "a {colour} triangle pointing upward",
    }
    out = []
    for shape in SHAPES:
        for colour in COLOURS:
            out.append(
                Passage(
                    id=f"passage:{colour}_{shape}",
                    words=f"Over the gate hung a banner: {lines[shape].format(colour=colour)}.",
                    about=(shape, colour),
                )
            )
    return out


PASSAGES: tuple[Passage, ...] = tuple(_passages())


@dataclass(frozen=True)
class TextQuery:
    id: str
    words: str
    shape: str
    colour: str
    tags: frozenset[str] = field(default_factory=frozenset)


TEXT_QUERIES: tuple[TextQuery, ...] = tuple(
    TextQuery(id=f"q_{colour}_{shape}", words=f"{colour} {shape}", shape=shape, colour=colour)
    for shape in SHAPES
    for colour in COLOURS
)


def relevant_pictures(shape: str, colour: str) -> set[str]:
    """Visible pictures of this subject (both drawings)."""
    return {p.id for p in PICTURES if not p.gated and p.shape == shape and p.colour == colour}


def hard_negatives(shape: str, colour: str) -> set[str]:
    """Visible pictures sharing all but one property with the subject."""
    return {p.id for p in PICTURES if not p.gated and ((p.shape == shape) != (p.colour == colour))}


__all__ = [
    "OTHER_PROJECT",
    "PASSAGES",
    "PICTURES",
    "PROJECT",
    "SET_VERSION",
    "TEXT_QUERIES",
    "WORKSPACE",
    "EvalPicture",
    "Passage",
    "TextQuery",
    "draw",
    "hard_negatives",
    "relevant_pictures",
]
