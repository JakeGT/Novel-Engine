"""In-memory stand-ins for the generation-versioned vector store (CTX-012).

``InMemoryEmbeddingIndexStore`` follows ``PgEmbeddingIndexStore``'s contract closely
enough for service tests: one active and one building generation at most, rows refused
into a retired generation or of the wrong size or modality, queries that name one
generation, and gates applied before ranking. The media side (which pictures are
current, secret, linked or ready) is described with ``MediaWorld``.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from backend.domain.embedding import (
    CaptionSource,
    EmbeddingError,
    EmbeddingSpace,
    Generation,
    GenerationState,
    MixedSpaceError,
    PictureSource,
    RegionSource,
    SearchGate,
    StoredHit,
    VectorRow,
    cosine,
    metadata_matches,
)


@dataclass
class Picture:
    asset_id: str
    version_id: str
    data: bytes
    content_type: str = "image/webp"
    active: bool = True
    current: bool = True
    rejected: bool = False
    secret: bool = False
    ready: bool = True
    segment_retired: bool = False
    # Secret links: (branch id, the characters who know what it shows).
    secret_links: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def segment_id(self) -> str:
        return f"seg_{self.version_id}"

    @property
    def object_key(self) -> str:
        return f"obj_{self.version_id}"


@dataclass
class MediaWorld:
    """What media looks like, per project, for the gates and the build sources."""

    pictures: dict[tuple[str, str], list[Picture]] = field(default_factory=dict)
    captions: dict[tuple[str, str], list[CaptionSource]] = field(default_factory=dict)
    regions: dict[tuple[str, str], list[RegionSource]] = field(default_factory=dict)
    # Marked parts taken off since they were indexed (the search gate refuses them).
    retired_regions: set[str] = field(default_factory=set)
    root_branch: str = "br_main"

    def add_picture(self, workspace_id: str, project_id: str, picture: Picture) -> Picture:
        self.pictures.setdefault((workspace_id, project_id), []).append(picture)
        return picture

    def add_caption(self, workspace_id: str, project_id: str, caption: CaptionSource) -> None:
        self.captions.setdefault((workspace_id, project_id), []).append(caption)

    def add_region(self, workspace_id: str, project_id: str, region: RegionSource) -> None:
        self.regions.setdefault((workspace_id, project_id), []).append(region)

    def picture(self, workspace_id: str, project_id: str, asset_id: str) -> Picture | None:
        found = [
            p
            for p in self.pictures.get((workspace_id, project_id), [])
            if p.asset_id == asset_id and p.current
        ]
        return found[-1] if found else None

    def eligible(
        self,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str | None,
        gate: SearchGate,
    ) -> bool:
        picture = self.picture(workspace_id, project_id, asset_id)
        if picture is None or not picture.active or picture.rejected:
            return False
        if version_id is not None and picture.version_id != version_id:
            return False
        if gate.point_of_view:
            if picture.secret:
                return False
            branch = gate.branch_id or self.root_branch
            for link_branch, known_by in picture.secret_links:
                if link_branch == branch and (
                    gate.point_of_view_id is None or gate.point_of_view_id not in known_by
                ):
                    return False
        return True


class FakeObjects:
    def __init__(self, world: MediaWorld) -> None:
        self._world = world
        self.missing: set[str] = set()

    def open(self, key: str, *, workspace_id: str, project_id: str) -> bytes:
        if key in self.missing:
            raise FileNotFoundError(key)
        for picture in self._world.pictures.get((workspace_id, project_id), []):
            if picture.object_key == key:
                return picture.data
        raise FileNotFoundError(key)


class InMemoryEmbeddingIndexStore:
    def __init__(self, world: MediaWorld | None = None) -> None:
        self.world = world or MediaWorld()
        self._generations: dict[str, Generation] = {}
        # (generation, workspace, project, id) -> row
        self._rows: dict[tuple[str, str, str, str], VectorRow] = {}
        self._ids = itertools.count(1)
        self.reachable = True
        # Named locks held, as PostgreSQL's advisory locks would hold them across servers.
        self.locks: set[str] = set()

    # --- generations ---------------------------------------------------------------

    def _state(self, state: GenerationState) -> Generation | None:
        return next((g for g in self._generations.values() if g.state is state), None)

    def active(self) -> Generation | None:
        return self._state(GenerationState.ACTIVE)

    def building(self) -> Generation | None:
        return self._state(GenerationState.BUILDING)

    def get(self, generation_id: str) -> Generation | None:
        return self._generations.get(generation_id)

    def list_generations(self) -> list[Generation]:
        return list(self._generations.values())

    def create_building(self, space: EmbeddingSpace, *, note: str | None = None) -> Generation:
        if self.building() is not None:
            raise EmbeddingError("a new search index is already being built")
        active = self.active()
        generation = Generation(
            id=f"gen_t{next(self._ids)}",
            model=space.model,
            revision=space.revision,
            dimension=space.dimension,
            native_dimension=space.native_dimension,
            modalities=space.modalities,
            tasks=dict(space.tasks),
            task_set=space.task_set,
            normalization=space.normalization,
            space_key=space.key,
            state=GenerationState.BUILDING,
            previous_generation_id=active.id if active else None,
            note=note,
        )
        self._generations[generation.id] = generation
        return generation

    def adopt(self, generation: Generation) -> Generation:
        """Put a ready-made generation in (a legacy one, say)."""
        self._generations[generation.id] = generation
        return generation

    def request_cancel(self, generation_id: str) -> bool:
        generation = self._generations.get(generation_id)
        if generation is None or generation.state is not GenerationState.BUILDING:
            return False
        self._generations[generation_id] = replace(generation, cancel_requested=True)
        return True

    def activate(self, generation_id: str) -> Generation:
        target = self._generations.get(generation_id)
        if target is None:
            raise EmbeddingError("that search index does not exist")
        if target.state is GenerationState.ACTIVE:
            return target
        current = self.active()
        if current is not None:
            self._generations[current.id] = replace(current, state=GenerationState.RETIRED)
        activated = replace(
            target,
            state=GenerationState.ACTIVE,
            cancel_requested=False,
            previous_generation_id=current.id if current else target.previous_generation_id,
        )
        self._generations[generation_id] = activated
        return activated

    def rollback(self) -> Generation:
        active = self.active()
        if active is None or not active.previous_generation_id:
            raise EmbeddingError("there is no earlier search index to go back to")
        if active.previous_generation_id not in self._generations:
            raise EmbeddingError("the earlier search index has been removed")
        return self.activate(active.previous_generation_id)

    def discard(self, generation_id: str) -> int:
        generation = self._generations.get(generation_id)
        if generation is None:
            return 0
        if generation.state is GenerationState.ACTIVE:
            raise EmbeddingError("the search index in use cannot be removed")
        keys = [key for key in self._rows if key[0] == generation_id]
        for key in keys:
            del self._rows[key]
        del self._generations[generation_id]
        return len(keys)

    # --- rows ----------------------------------------------------------------------

    def upsert(
        self, generation_id: str, rows: Sequence[VectorRow], *, workspace_id: str, project_id: str
    ) -> int:
        generation = self._generations.get(generation_id)
        if generation is None:
            raise EmbeddingError("that search index does not exist")
        if generation.state is GenerationState.RETIRED:
            raise EmbeddingError("a retired search index takes no new rows")
        for row in rows:
            if len(row.values) != generation.dimension:
                raise MixedSpaceError("wrong size for this generation")
            if row.modality not in generation.modalities:
                raise MixedSpaceError("this generation does not hold that modality")
        for row in rows:
            self._rows[(generation_id, workspace_id, project_id, row.id)] = row
        return len(rows)

    def _project(self, generation_id: str, workspace_id: str, project_id: str) -> list[VectorRow]:
        return [
            row
            for (g, w, p, _), row in self._rows.items()
            if g == generation_id and w == workspace_id and p == project_id
        ]

    def hashes(self, generation_id: str, *, workspace_id: str, project_id: str) -> dict[str, str]:
        return {
            row.id: row.content_hash
            for row in self._project(generation_id, workspace_id, project_id)
        }

    def text_rows(
        self, generation_id: str, *, workspace_id: str, project_id: str
    ) -> list[VectorRow]:
        return sorted(
            (
                replace(row, values=())
                for row in self._project(generation_id, workspace_id, project_id)
                if row.document is not None and row.modality.value == "text"
            ),
            key=lambda row: row.id,
        )

    def _eligible_row(
        self, row: VectorRow, workspace_id: str, project_id: str, gate: SearchGate
    ) -> bool:
        if gate.kinds and row.kind not in gate.kinds:
            return False
        if gate.assets is True and row.asset_id is None:
            return False
        if gate.assets is False and row.asset_id is not None:
            return False
        if gate.metadata and not metadata_matches(row.metadata, gate):
            return False
        if row.asset_id is None:
            return True
        if not self.world.eligible(
            workspace_id, project_id, row.asset_id, row.asset_version_id, gate
        ):
            return False
        if row.segment_id is not None:
            picture = self.world.picture(workspace_id, project_id, row.asset_id)
            if picture is not None and picture.segment_retired:
                return False
            if row.segment_id in self.world.retired_regions:
                return False
        return True

    def search(
        self,
        generation_id: str,
        values: Sequence[float],
        *,
        workspace_id: str,
        project_id: str,
        gate: SearchGate,
        limit: int,
    ) -> list[StoredHit]:
        generation = self._generations.get(generation_id)
        if generation is None:
            raise EmbeddingError("that search index does not exist")
        if len(values) != generation.dimension:
            raise MixedSpaceError("a query of the wrong size")
        # Gates first, then ranking: an ineligible row is never scored.
        eligible = [
            row
            for row in self._project(generation_id, workspace_id, project_id)
            if self._eligible_row(row, workspace_id, project_id, gate)
        ]
        scored = sorted(
            ((cosine(row.values, values), row) for row in eligible),
            key=lambda pair: (-pair[0], pair[1].id),
        )
        return [
            StoredHit(
                id=row.id,
                kind=row.kind,
                modality=row.modality,
                score=score,
                generation_id=generation_id,
                document=row.document,
                metadata=row.metadata,
                asset_id=row.asset_id,
                asset_version_id=row.asset_version_id,
                segment_id=row.segment_id,
            )
            for score, row in scored[:limit]
        ]

    def eligible_assets(
        self, asset_ids: Sequence[str], *, workspace_id: str, project_id: str, gate: SearchGate
    ) -> set[str]:
        return {
            asset_id
            for asset_id in asset_ids
            if self.world.eligible(workspace_id, project_id, asset_id, None, gate)
        }

    def _delete(self, predicate) -> int:  # type: ignore[no-untyped-def]
        keys = [key for key, row in self._rows.items() if predicate(key, row)]
        for key in keys:
            del self._rows[key]
        return len(keys)

    def delete_ids(self, ids: Sequence[str], *, workspace_id: str, project_id: str) -> int:
        wanted = set(ids)
        return self._delete(lambda k, r: k[1:3] == (workspace_id, project_id) and k[3] in wanted)

    def delete_ids_in(
        self, generation_id: str, ids: Sequence[str], *, workspace_id: str, project_id: str
    ) -> int:
        wanted = set(ids)
        return self._delete(
            lambda k, r: k[:3] == (generation_id, workspace_id, project_id) and k[3] in wanted
        )

    def delete_kinds_except(
        self,
        generation_id: str,
        kinds: Sequence[str],
        keep_ids: Sequence[str],
        *,
        workspace_id: str,
        project_id: str,
    ) -> int:
        keep = set(keep_ids)
        return self._delete(
            lambda k, r: (
                k[:3] == (generation_id, workspace_id, project_id)
                and r.kind in kinds
                and k[3] not in keep
            )
        )

    def delete_assets(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_ids: Sequence[str] = (),
        asset_version_ids: Sequence[str] = (),
    ) -> int:
        assets, versions = set(asset_ids), set(asset_version_ids)
        return self._delete(
            lambda k, r: (
                k[1:3] == (workspace_id, project_id)
                and (r.asset_id in assets or r.asset_version_id in versions)
            )
        )

    def delete_project(self, *, workspace_id: str, project_id: str) -> int:
        return self._delete(lambda k, r: k[1:3] == (workspace_id, project_id))

    def delete_workspace(self, *, workspace_id: str) -> int:
        return self._delete(lambda k, r: k[1] == workspace_id)

    # --- sources -------------------------------------------------------------------

    def pictures_ready(self, *, workspace_id: str, project_id: str) -> list[PictureSource]:
        return [
            PictureSource(
                asset_id=p.asset_id,
                asset_version_id=p.version_id,
                segment_id=None if p.segment_retired else p.segment_id,
                object_key=p.object_key,
                content_type=p.content_type,
                checksum=p.checksum,
            )
            for p in self.world.pictures.get((workspace_id, project_id), [])
            if p.ready and p.active and p.current
        ]

    def accepted_captions(self, *, workspace_id: str, project_id: str) -> list[CaptionSource]:
        return list(self.world.captions.get((workspace_id, project_id), []))

    def regions_ready(self, *, workspace_id: str, project_id: str) -> list[RegionSource]:
        return [
            region
            for region in self.world.regions.get((workspace_id, project_id), [])
            if region.segment_id not in self.world.retired_regions
            and self.world.picture(workspace_id, project_id, region.asset_id) is not None
        ]

    def projects(self) -> list[tuple[str, str]]:
        scopes = {(k[1], k[2]) for k in self._rows}
        scopes |= set(self.world.pictures) | set(self.world.captions) | set(self.world.regions)
        return sorted(scopes)

    def count(self, generation_id: str, *, workspace_id: str | None = None) -> int:
        return sum(
            1
            for k in self._rows
            if k[0] == generation_id and (workspace_id is None or k[1] == workspace_id)
        )

    def hold_lock(self, name: str) -> str | None:
        if name in self.locks:
            return None
        self.locks.add(name)
        return name

    def release_lock(self, held: str) -> None:
        self.locks.discard(held)

    def ping(self) -> bool:
        return self.reachable

    def rows(self, generation_id: str) -> list[VectorRow]:
        return [row for key, row in self._rows.items() if key[0] == generation_id]


__all__ = ["FakeObjects", "InMemoryEmbeddingIndexStore", "MediaWorld", "Picture"]
