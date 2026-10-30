"""Parts of pictures the author marked, in the search index (CTX-012): each named part is
its own row pointing at its segment: its words in every generation, the part itself only
in one whose model takes images. In-memory store; the deterministic stand-in embedder;
a stand-in cutter in place of the limited picture worker."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, replace

import pytest

from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.domain.embedding import Modality, RegionSource
from backend.domain.media_observations import (
    MediaObservation,
    ObservationKind,
    ObservationState,
    Region,
    description_of,
    part_of,
    quoted,
    region_caption,
)
from backend.services.embedding_index import (
    EmbeddingIndexService,
    region_caption_row_id,
    region_row_id,
)
from backend.services.media_observations import PartAnchor, _bare_name
from backend.services.scene_checks import PictureNote, picture_anchor
from tests.application.test_embedding_index import P, Rig, W, _built

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@dataclass(frozen=True)
class _Cut:
    data: bytes
    content_type: str = "image/webp"


class FakeCropper:
    """Cuts nothing: answers fixed bytes per picture and box, so the stand-in embedder
    can be told what each part shows."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, float, float, float]] = []

    @staticmethod
    def bytes_for(data: bytes, box: tuple[float, float, float, float]) -> bytes:
        return hashlib.sha256(data + repr(box).encode()).digest()

    async def crop(self, data: bytes, box: tuple[float, float, float, float]) -> _Cut:
        self.calls.append(box)
        return _Cut(self.bytes_for(data, box))


def _rig(embedder: DeterministicEmbedding | None = None) -> tuple[Rig, FakeCropper]:
    rig = Rig(embedder)
    cropper = FakeCropper()
    rig.service = EmbeddingIndexService(
        index=rig.index, embedder=rig.embedder, objects=rig.objects, cropper=cropper
    )
    return rig, cropper


def _part(
    rig: Rig,
    segment_id: str,
    *,
    asset: str = "ast_inn",
    box: tuple[float, float, float, float] = (0.1, 0.1, 0.3, 0.2),
    label: str | None = "The inn sign",
    entity: str | None = None,
    notes: tuple[str, ...] = (),
    shows: str = "",
) -> RegionSource:
    picture = rig.world.picture(W, P, asset)
    assert picture is not None
    source = RegionSource(
        asset_id=asset,
        asset_version_id=picture.version_id,
        segment_id=segment_id,
        box=box,
        object_key=picture.object_key,
        content_type="image/webp",
        checksum=picture.checksum,
        region_hash=hashlib.sha256(repr(box).encode()).hexdigest(),
        label=label,
        entity_name=entity,
        notes=notes,
    )
    rig.world.add_region(W, P, source)
    if shows:
        rig.embedder.describe(FakeCropper.bytes_for(picture.data, box), shows)
    return source


async def test_a_named_part_is_its_own_row_and_a_text_only_index_holds_its_words() -> None:
    rig, cropper = _rig(DeterministicEmbedding(dimension=256, modalities=(Modality.TEXT,)))
    rig.picture("ast_inn", "a street at night")
    _part(rig, "seg_sign", entity="The Black Swan", notes=("A painted swan on a board.",))
    # Neither named nor linked: not indexed.
    _part(rig, "seg_blank", label=None, box=(0.5, 0.5, 0.1, 0.1))
    generation = await _built(rig)
    rows = {row.id: row for row in rig.index.rows(generation)}
    words = rows[region_caption_row_id("seg_sign")]
    assert words.kind == "region_caption" and words.segment_id == "seg_sign"
    assert words.document == "The inn sign. Shows The Black Swan. A painted swan on a board."
    assert region_row_id("seg_sign") not in rows  # never pretends to have seen the part
    assert region_caption_row_id("seg_blank") not in rows
    assert cropper.calls == []

    found = await rig.search.pictures_for(W, P, query="black swan inn sign", limit=3)
    (hit,) = found.pictures
    assert hit.asset_id == "ast_inn" and hit.matched == "part" and hit.segment_id == "seg_sign"


async def test_an_index_that_takes_images_holds_the_part_itself_cut_in_the_worker() -> None:
    rig, cropper = _rig()
    rig.picture("ast_inn", "a street at night")
    _part(rig, "seg_sign", shows="a painted swan")
    generation = await _built(rig)
    rows = {row.id: row for row in rig.index.rows(generation)}
    part = rows[region_row_id("seg_sign")]
    assert part.modality is Modality.IMAGE and part.segment_id == "seg_sign"
    assert cropper.calls == [(0.1, 0.1, 0.3, 0.2)]
    found = await rig.search.pictures_for(W, P, query="painted swan", limit=3)
    assert [(p.asset_id, p.matched) for p in found.pictures] == [("ast_inn", "part")]

    # Built again with nothing changed: nothing is cut or embedded again.
    report = await rig.service.build_project(generation, W, P)
    assert report.regions == 0 and cropper.calls == [(0.1, 0.1, 0.3, 0.2)]

    # Moved: the part is cut again under the same row.
    rig.world.regions[(W, P)] = [
        replace(
            rig.world.regions[(W, P)][0],
            box=(0.2, 0.2, 0.3, 0.2),
            region_hash="moved",
        )
    ]
    assert (await rig.service.regions_changed(W, P)) == 1
    assert cropper.calls[-1] == (0.2, 0.2, 0.3, 0.2)


async def test_a_part_taken_off_is_refused_at_once_and_its_rows_leave_every_index() -> None:
    rig, _cropper = _rig()
    rig.picture("ast_inn", "a street at night")
    _part(rig, "seg_sign", label="swan sign", shows="a painted swan")
    first = await _built(rig)
    rig.world.retired_regions.add("seg_sign")
    # Refused by the search gate before anything is ranked, before the rows go.
    found = await rig.search.search(W, P, query=_text("a painted swan"), limit=5)
    assert all(hit.segment_id != "seg_sign" for hit in found)
    await rig.service.regions_changed(W, P)
    assert not any(row.segment_id == "seg_sign" for row in rig.index.rows(first))


async def test_lag_counts_parts_and_a_part_that_cannot_be_cut_is_counted_not_fatal() -> None:
    rig = Rig()  # no cutter
    rig.picture("ast_inn", "a street at night")
    _part(rig, "seg_sign")
    generation = await rig.service.start_build()
    lag = await rig.service.project_lag(generation, W, P)
    assert lag.regions == 2 and lag.as_dict()["regions"] == 2
    report = await rig.service.build_project(generation.id, W, P)
    assert report.regions == 1 and report.regions_unreadable == 1
    assert (await rig.service.project_lag(generation, W, P)).regions == 1


def _text(words: str):  # type: ignore[no-untyped-def]
    from backend.domain.embedding import TextInput

    return TextInput(text=words)


# --- the words around parts ------------------------------------------------------------


def _seen(kind: ObservationKind, words: str, **extra) -> MediaObservation:  # type: ignore[no-untyped-def]
    return MediaObservation(
        workspace_id=W,
        project_id=P,
        id=f"obs_{abs(hash((kind, words)))}",
        asset_id="ast",
        asset_version_id="ver",
        kind=kind,
        text=words,
        state=ObservationState.ACCEPTED,
        **extra,
    )


async def test_the_description_keeps_the_whole_caption_first_and_quotes_writing() -> None:
    said = description_of(
        [
            _seen(ObservationKind.CAPTION, "A close view of a scar.", segment_id="seg_1"),
            _seen(ObservationKind.CAPTION, "A woman under an inn sign."),
            _seen(ObservationKind.TEXT_IN_PICTURE, "“THE BLACK SWAN”"),
        ],
        "ver",
    )
    assert said == (
        "A woman under an inn sign. A close view of a scar. "
        "Written in the picture: “THE BLACK SWAN”"
    )
    assert region_caption(None, None, ["anything"]) == ""
    assert region_caption(" A  sign ", None, []) == "A sign."


async def test_a_rectangle_belongs_to_the_smallest_part_it_lies_mostly_inside() -> None:
    big = Region(x=0, y=0, width=1, height=1)
    small = Region(x=0.4, y=0.4, width=0.2, height=0.2)
    inside = Region(x=0.45, y=0.45, width=0.1, height=0.1)
    across = Region(x=0.55, y=0.55, width=0.2, height=0.2)
    assert part_of(inside, [("big", big), ("small", small)]) == "small"
    assert part_of(across, [("small", small)]) is None
    assert part_of(None, [("small", small)]) is None


async def test_a_note_points_only_at_what_was_given() -> None:
    class Given:
        pictures = {
            "picture 1": PartAnchor("ast", "ver", "“Mara”, version 1"),
            "mara": PartAnchor("ast", "ver", "“Mara”, version 1"),
        }
        parts = {
            "part 1": PartAnchor(
                "ast",
                "ver",
                "Mara",
                segment_id="seg",
                region=Region(x=0, y=0, width=0.5, height=0.5),
            )
        }

    named = picture_anchor(PictureNote(quote="q", problem="p", part=" Part  1 "), Given())
    assert named is not None and named.segment_id == "seg"
    by_name = picture_anchor(
        PictureNote(
            quote="q", problem="p", picture="Mara", region={"x": 0, "y": 0, "width": 1, "height": 1}
        ),  # type: ignore[arg-type]
        Given(),
    )
    # Only a picture sent ("picture 1") can be pointed into.
    assert by_name is not None and by_name.region is None
    off = picture_anchor(
        PictureNote(
            quote="q",
            problem="p",
            picture="picture 1",
            region={"x": 0.9, "y": 0, "width": 0.5, "height": 1},
        ),  # type: ignore[arg-type]
        Given(),
    )
    assert off is not None and off.region is None
    assert picture_anchor(PictureNote(quote="q", problem="p", picture="picture 4"), Given()) is None


async def test_the_words_around_parts_take_linear_time_on_long_input() -> None:
    """Everything here that reads text is linear: 500,000 characters each, well under a
    second together (the batch 8-9 rule)."""
    long = ("word, version 1 " * 40_000)[:500_000]
    spaced = ", " + " " * 499_990 + "x"
    started = time.perf_counter()
    region_caption(long, long, [long] * 3)
    quoted('"' * 250_000 + "a" + '"' * 249_999)
    _bare_name(long)
    _bare_name(spaced)
    description_of(
        [
            _seen(ObservationKind.CAPTION, long),
            _seen(ObservationKind.TEXT_IN_PICTURE, long),
        ],
        "ver",
    )
    picture_anchor(
        PictureNote(quote="q", problem="p", picture=long, part=long),
        type("Given", (), {"pictures": {}, "parts": {}})(),
    )
    assert time.perf_counter() - started < 1.0


async def test_quotes_inside_writing_read_in_a_picture_never_close_its_quote() -> None:
    assert quoted("THE “BLACK” SWAN") == "“THE ‘BLACK’ SWAN”"
    assert quoted("“Rooms” to let”") == "“Rooms’ to let”"
    said = quoted("a ” b " * 100_000)
    # Only the last character closes it, and that is linear on 600,000 characters.
    assert said.count("”") == 1 and said.endswith("”")
