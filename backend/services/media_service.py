"""Media identity and explicit evidence-link application service (2I).

Registration (asset/version metadata) is operator/administrator metadata and is not
a canon mutation. Link acceptance and retirement ARE canon mutations: they build
typed operations and commit through the atomic mutation path, so they are revisioned and
audited. Bytes remain in the scoped object store; Phase 2 only records identity and
explicit links (no processing/scanning).

Since batch 6 (AST-003, AST-006, AST-008, OPS-007; images only) every stored picture
version is prepared once by a ``media_process`` job: decoded in a separate, limited
process (``backend/adapters/image_processor.py``) into a 256px thumbnail and a 1024px
web copy with no metadata, plus its whole-picture segment. The job is keyed by the
version's checksum and the processor version, so running it again changes nothing.
Uploads are refused in plain words when the workspace's storage is full. Retention
removes stored files no record refers to (after a grace period) and the copies of
retired versions.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from backend.adapters.image_processor import (
    PROCESSOR_VERSION,
    VARIANT_CONTENT_TYPE,
    VARIANT_SIZES,
    ImageRejected,
)
from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.core.metrics import metrics
from backend.domain.branching import working_in
from backend.domain.canon.plans import PlanKind
from backend.domain.errors import ConflictError, DomainError, NotFoundError, ValidationError
from backend.domain.identity import AccessScope, AuthMode, utcnow
from backend.domain.image_metadata import ImageMetadataError, strip_metadata
from backend.domain.media import (
    PROCESSING_FAILURES as _FAILURES,
)
from backend.domain.media import (
    AssetLifecycle,
    AssetLink,
    LinkRole,
    MediaAsset,
    MediaAssetVersion,
    MediaLifecycleEvent,
    MediaProcessing,
    MediaSegment,
    MediaVariant,
    PictureUpload,
    ProcessingStatus,
    SegmentKind,
    StorageQuotaExceeded,
    UploadState,
    UploadTarget,
    VariantKind,
    is_link_role,
    processing_failure_words,
)
from backend.domain.operations import CanonOperation, LinkAssetOp, RetireAssetLinkOp
from backend.domain.tenancy import Permission
from backend.ports import (
    CanonRepositoryPort,
    MediaProcessingStorePort,
    MediaStorePort,
    ObjectStorePort,
)
from backend.services.mutation_service import CommitResult, MutationService

MAX_PICTURE_BYTES = 8 * 1024 * 1024
# How long one upload may take to arrive whole. A sender trickling bytes would otherwise
# hold a connection and a growing buffer for as long as it liked; behind a proxy that
# enforces its own read timeout this is a second line, not the only one.
UPLOAD_DEADLINE_SECONDS = 120.0
PICTURE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
# A picture shown on a scene links to the plan item that is the scene.
SCENE_TARGET = "plan_item"
MAX_LIBRARY = 200
MAX_LIBRARY_LINKS = 2000
MEDIA_PROCESS_KIND = "media_process"
# Marked parts (AST-007): at most this many on one picture version, and no side or
# corner of a part restored from an archive past this many pixels (the largest picture
# taken is 12,000 a side).
MAX_REGIONS_PER_VERSION = 50
MAX_REGION_PIXELS = 20_000
DEFAULT_QUOTA_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_ORPHAN_GRACE = timedelta(hours=24)
# Two-step uploads (AST-002): how long one may take from asking to finishing, how many one
# person (and a whole workspace) may have on their way at once, how long one being
# finished is left past its hour before it is cleared, and how often unfinished ones are.
UPLOAD_LIFETIME = timedelta(hours=1)
MAX_OPEN_UPLOADS = 20
MAX_OPEN_UPLOADS_PER_WORKSPACE = 100
FINISHING_GRACE = timedelta(minutes=15)
UPLOAD_SWEEP_SECONDS = 300.0
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
# The types of file this service itself stores: pictures and their smaller copies.
_OWNED_TYPES = PICTURE_TYPES | {VARIANT_CONTENT_TYPE}

# Called with (workspace_id, project_id, version_ids) when picture versions are retired,
# discarded or their picture is retired, so derived indexes (vectors) can forget them.
VersionsRemovedHook = Callable[[str, str, list[str]], Awaitable[None]]
# Called with (asset_id, version_id, link_id) the moment a finished upload's picture exists.
LandedHook = Callable[[str, str, str | None], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibraryPicture:
    """A picture the author can choose: its newest current version, and where it is shown."""

    asset: MediaAsset
    version: MediaAssetVersion
    # How many entries and scenes show it now; 0 when it is shown nowhere.
    shown: int
    # Its processing state, when processing is set up; None before it has been queued.
    processing: MediaProcessing | None = None
    has_thumbnail: bool = False


@dataclass(frozen=True)
class ProcessingView:
    """What the author is told about one picture version's preparation."""

    status: ProcessingStatus | None
    failure_code: str | None
    message: str | None
    retryable: bool
    attempts: int
    width: int | None
    height: int | None
    copies: tuple[str, ...]
    processor_version: str | None
    current: bool


@dataclass
class MaintenanceReport:
    """Counts from a reconciliation or retention run; never names or content."""

    queued: int = 0
    orphans_found: int = 0
    orphans_removed: int = 0
    orphan_bytes: int = 0
    retired_copies_removed: int = 0
    projects: int = 0
    skipped: dict[str, int] = field(default_factory=dict)


class PictureTooLarge(DomainError):
    """An upload larger than a picture may be, refused before it is read whole (AST-002)."""

    status_code = 413
    code = "too_large"
    title = "Too large"


class PictureTooSlow(DomainError):
    """An upload that did not arrive whole in time (AST-002)."""

    status_code = 408
    code = "too_slow"
    title = "Too slow"


@dataclass(frozen=True)
class ReceivedUpload:
    """The bytes as they arrived, with the SHA-256 worked out while they streamed in."""

    data: bytes
    sha256: str
    # The sender's digest: "matched" (a sha-256 one agreed), "none" (none was sent) or
    # "not_checked" (only methods this server does not check were sent).
    digest: str = "none"

    @property
    def size(self) -> int:
        return len(self.data)


# RFC 9530 Content-Digest / Repr-Digest: ``sha-256=:<base64>:`` among other members.
_DIGEST_MEMBER = re.compile(r"(?:^|,)\s*sha-256\s*=\s*:([A-Za-z0-9+/=]+):", re.IGNORECASE)
_TOO_LARGE = f"The picture is too large (the limit is {MAX_PICTURE_BYTES // (1024 * 1024)} MB)."
_TOO_SLOW = "The picture took too long to arrive. Check the connection and add it again."
_NOT_A_PICTURE = "Only PNG, JPEG, and WebP pictures can be added."
_UPLOAD_STATE_WORDS = {
    UploadState.RECEIVED: "This picture has already arrived. Finish it to add it.",
    UploadState.FINISHING: "This picture is being added now.",
    UploadState.FINISHED: "This picture has already been added.",
    UploadState.EXPIRED: "This upload was not finished within the hour and was cleared. "
    "Add the picture again.",
    UploadState.WAITING: "The picture has not arrived yet. Send it first.",
}
# Enough leading bytes to tell a PNG, a JPEG or a WebP file from anything else.
_SIGNATURE_BYTES = 12


def _declared_digest(header: str | None) -> bytes | None:
    if not header:
        return None
    found = _DIGEST_MEMBER.search(header)
    if found is None:
        return None
    try:
        return base64.b64decode(found.group(1), validate=True)
    except (ValueError, binascii.Error):
        raise ValidationError("The upload's digest could not be read.") from None


async def receive_picture(
    chunks: AsyncIterator[bytes],
    *,
    declared_length: str | None = None,
    content_digest: str | None = None,
    max_bytes: int = MAX_PICTURE_BYTES,
    deadline_seconds: float | None = UPLOAD_DEADLINE_SECONDS,
) -> ReceivedUpload:
    """Read an upload as it streams in (AST-002).

    Refused before anything is read when its declared length is over the limit, as soon
    as more than the limit has arrived, and once its first bytes show it is not a PNG,
    JPEG or WebP file, so a large or wrong upload is never held whole. The SHA-256 is
    worked out chunk by chunk; when the sender gave one (``Content-Digest`` or
    ``Repr-Digest``, RFC 9530) the two must agree. A digest by another method only
    (``sha-512``, say) is not checked, and the answer says so (``digest``). The whole
    upload must arrive within ``deadline_seconds``.
    """
    if declared_length is not None:
        if not declared_length.isdigit():
            raise ValidationError("The upload's length could not be read.")
        if int(declared_length) > max_bytes:
            metrics.increment(
                "media_uploads_refused_total",
                "Picture uploads refused, by reason.",
                reason="too_large",
            )
            raise PictureTooLarge(_TOO_LARGE)
    expected = _declared_digest(content_digest)
    hasher = hashlib.sha256()
    data = bytearray()
    checked = False
    try:
        async with asyncio.timeout(deadline_seconds):
            async for chunk in chunks:
                if not chunk:
                    continue
                data.extend(chunk)
                if len(data) > max_bytes:
                    metrics.increment(
                        "media_uploads_refused_total",
                        "Picture uploads refused, by reason.",
                        reason="too_large",
                    )
                    raise PictureTooLarge(_TOO_LARGE)
                hasher.update(chunk)
                if not checked and len(data) >= _SIGNATURE_BYTES:
                    checked = True
                    if sniff_image_type(bytes(data[:_SIGNATURE_BYTES])) is None:
                        raise ValidationError(_NOT_A_PICTURE)
    except TimeoutError:
        metrics.increment(
            "media_uploads_refused_total", "Picture uploads refused, by reason.", reason="too_slow"
        )
        raise PictureTooSlow(_TOO_SLOW) from None
    if not data:
        raise ValidationError("The picture is empty.")
    if expected is not None and not hmac.compare_digest(expected, hasher.digest()):
        raise ValidationError("The picture did not arrive whole. Add it again.")
    digest = "matched" if expected is not None else "not_checked" if content_digest else "none"
    return ReceivedUpload(data=bytes(data), sha256=hasher.hexdigest(), digest=digest)


@dataclass(frozen=True)
class FoundPicture:
    """A picture found for words: by its name or reviewed description, or by meaning."""

    picture: LibraryPicture
    # "words": the words are in its name or description; "meaning": search by meaning.
    reason: str
    # What matched: "name", "description", "picture", "caption" or "part" (a part of it
    # the author marked: its name, the entry it shows, or the part found by meaning).
    matched: str
    score: float | None = None
    # The marked part's name (or the entry it shows) when a part matched.
    part: str | None = None


@dataclass(frozen=True)
class PictureSearch:
    found: list[FoundPicture]
    # Whether search by meaning was used, and when not, why in plain words.
    by_meaning: bool
    note: str | None = None


@dataclass(frozen=True)
class DeletedPicture:
    """What deleting a picture's files removed; counts only, never names or content."""

    asset: MediaAsset
    versions: int
    files_removed: int
    copies_removed: int
    bytes_freed: int
    # What a model said about it (observations), removed with it (AST-008).
    notes_removed: int = 0


_WORD = re.compile(r"[\w’']+", re.UNICODE)
MAX_QUERY = 200


def query_words(query: str) -> list[str]:
    """The words of a search, lower-cased, in order, each once."""
    return list(dict.fromkeys(word.lower() for word in _WORD.findall(query) if len(word) > 1))


def sniff_image_type(data: bytes) -> str | None:
    """The image type shown by the file's own leading bytes, or ``None``."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class MediaService:
    def __init__(
        self,
        *,
        media: MediaStorePort,
        objects: ObjectStorePort,
        mutation: MutationService,
        reader: CanonRepositoryPort,
        journal: object | None = None,
        tenancy: object | None = None,
        local_mode: bool = False,
        processing: MediaProcessingStorePort | None = None,
        processor: Any = None,
        runner: Any = None,
        quota_bytes: int = DEFAULT_QUOTA_BYTES,
        orphan_grace: timedelta = DEFAULT_ORPHAN_GRACE,
    ) -> None:
        self._media = media
        self._objects = objects
        self._mutation = mutation
        self._reader = reader
        self._journal = journal
        self._tenancy = tenancy
        self._local_mode = local_mode
        self._processing = processing
        self._processor = processor
        self._runner = runner
        self._quota_bytes = max(0, int(quota_bytes))
        self._orphan_grace = orphan_grace
        self._removed_hooks: list[VersionsRemovedHook] = []
        # Told (workspace, project, who, picture) when a picture's marked parts change.
        self._region_hooks: list[Callable[..., Awaitable[Any]]] = []
        # Set by the container: reviewed descriptions to find pictures by (AST-007), and
        # search by meaning (``MediaSearchService``), when it is set up.
        self.observation_store: Any = None
        self.search: Any = None
        # One lock per workspace around "is there room?" and the write that takes it.
        self._quota_locks: dict[str, asyncio.Lock] = {}

    # -------------------------------------------------------------- hooks (AST-008)
    def on_versions_removed(self, callback: VersionsRemovedHook) -> None:
        """Be told when picture versions stop being usable: a version retired, its
        picture retired, or an upload discarded. For derived indexes (vectors) to forget
        them. A failing callback is logged and never undoes the removal."""
        self._removed_hooks.append(callback)

    async def _versions_removed(
        self, workspace_id: str, project_id: str, version_ids: list[str]
    ) -> None:
        if not version_ids:
            return
        for callback in self._removed_hooks:
            try:
                await callback(workspace_id, project_id, list(version_ids))
            except Exception as error:  # noqa: BLE001 - cleanup never undoes a retirement
                log_event(
                    logger,
                    "media.removed_hook_failed",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                    versions=len(version_ids),
                )

    @property
    def processing_enabled(self) -> bool:
        return self._processing is not None and self._processor is not None

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ assets
    async def register_asset(
        self, scope: AccessScope, workspace_id: str, project_id: str, *, name: str
    ) -> MediaAsset:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_ADMIN)
        if not name.strip():
            raise ValidationError("Asset name is required.")
        return await self._media.create_asset(
            workspace_id=workspace_id,
            project_id=project_id,
            name=name,
            actor_user_id=scope.actor_user_id,
        )

    async def list_assets(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[MediaAsset]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._media.list_assets(workspace_id=workspace_id, project_id=project_id)

    async def get_asset(
        self, scope: AccessScope, workspace_id: str, project_id: str, asset_id: str
    ) -> MediaAsset:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        asset = await self._media.get_asset(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )
        if asset is None:
            raise NotFoundError("Asset not found.")
        return asset

    # ---------------------------------------------------------------- versions
    async def register_version(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        *,
        object_key: str,
        source: str | None = None,
        licence: str | None = None,
        attribution: str | None = None,
        authority: str | None = None,
        visibility: str | None = None,
    ) -> MediaAssetVersion:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_ADMIN)
        asset = await self._media.get_asset(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )
        if asset is None:
            raise NotFoundError("Asset not found.")
        stat = self._objects.stat(object_key, workspace_id=workspace_id, project_id=project_id)
        if stat is None:
            raise ValidationError("Object not found in this project's object store.")
        # A registered version takes room just as an upload does.
        async with self._quota_guard(workspace_id):
            await self._check_quota(workspace_id, stat.byte_length)
            version = await self._media.create_version(
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=asset_id,
                object_key=object_key,
                content_type=stat.content_type,
                byte_length=stat.byte_length,
                checksum=stat.checksum,
                source=source,
                licence=licence,
                attribution=attribution,
                authority=authority,
                visibility=visibility,
                created_revision=await self._retired_revision(workspace_id, project_id) or 0,
                actor_user_id=scope.actor_user_id,
            )
        await self._queue_quietly(scope, workspace_id, project_id, version)
        return version

    async def list_lifecycle(
        self, scope: AccessScope, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaLifecycleEvent]:
        """Who registered, imported, or retired this asset and its versions, and when."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        await self.get_asset(scope, workspace_id, project_id, asset_id)
        return await self._media.list_lifecycle_events(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )

    async def list_versions(
        self, scope: AccessScope, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaAssetVersion]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._media.list_versions(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )

    async def get_version(
        self, scope: AccessScope, workspace_id: str, project_id: str, version_id: str
    ) -> MediaAssetVersion:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._media.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        if version is None:
            raise NotFoundError("Asset version not found.")
        return version

    async def _retired_revision(self, workspace_id: str, project_id: str) -> int | None:
        if self._journal is None:
            return None
        branch = await self._journal.working_branch(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        return await self._reader.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )

    async def retire_asset(
        self, scope: AccessScope, workspace_id: str, project_id: str, asset_id: str
    ) -> MediaAsset:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_ADMIN)
        revision = await self._retired_revision(workspace_id, project_id)
        retired = await self._media.retire_asset(
            workspace_id=workspace_id,
            project_id=project_id,
            asset_id=asset_id,
            retired_revision=revision,
            actor_user_id=scope.actor_user_id,
        )
        if retired is None:
            raise NotFoundError("Asset not found.")
        versions = await self._media.list_versions(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )
        await self._versions_removed(workspace_id, project_id, [v.id for v in versions])
        return retired

    async def retire_version(
        self, scope: AccessScope, workspace_id: str, project_id: str, version_id: str
    ) -> MediaAssetVersion:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_ADMIN)
        revision = await self._retired_revision(workspace_id, project_id)
        retired = await self._media.retire_version(
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=version_id,
            retired_revision=revision,
            actor_user_id=scope.actor_user_id,
        )
        if retired is None:
            raise NotFoundError("Asset version not found.")
        await self._versions_removed(workspace_id, project_id, [version_id])
        return retired

    # ------------------------------------------------- deleting a picture (AST-008)
    async def _shown_anywhere(self, workspace_id: str, project_id: str, asset_id: str) -> int:
        """How many entries and scenes show a picture now, in every line of the story.

        Counted as the library counts it: a picture on a scene taken out of the plan is
        not shown, so it can be deleted once that is its only place."""
        lines: list[str | None] = [None]
        if self._journal is not None and hasattr(self._journal, "list_branches"):
            lines += [
                branch.id
                for branch in await self._journal.list_branches(
                    workspace_id=workspace_id, project_id=project_id
                )
                if not branch.is_root
            ]
        shown = 0
        for line in lines:
            with working_in(line):
                links = await self._reader.list_links(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    asset_id=asset_id,
                    limit=MAX_LIBRARY_LINKS,
                )
                for link in links:
                    if link.asset_id != asset_id or not link.is_active:
                        continue
                    if link.target_record_type == SCENE_TARGET:
                        scene = await self._reader.get_plan_item(
                            workspace_id=workspace_id,
                            project_id=project_id,
                            plan_id=link.target_record_id,
                        )
                        if scene is not None and not scene.is_active:
                            continue
                    shown += 1
        return shown

    async def delete_picture(
        self, scope: AccessScope, workspace_id: str, project_id: str, asset_id: str
    ) -> DeletedPicture:
        """Delete a picture's files before its story is deleted (AST-008).

        For an owner or administrator, and only once the picture is shown on no entry or
        scene in any line of the story (taking it off one is an ordinary reviewed
        change). The picture is first marked deleted, so from then on it cannot be
        linked, opened or prepared; where it is shown is checked again, and only then
        are every version's original and smaller copies removed from storage. A stored
        file another picture still uses (an archive restore can share one) is kept.
        Derived indexes are told to forget the versions (``on_versions_removed``). What
        stays is the tombstone: each version's row (its checksum, size and type, with
        when its file was deleted) and an audit event naming who did it. History that
        mentions the picture keeps its name; the picture can no longer be opened.

        A deletion stopped part way (the server stopped, say) is finished by asking
        again; a picture whose every file is already gone is not found.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_ADMIN)
        asset = await self._media.get_asset(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )
        if asset is None:
            raise NotFoundError("That picture was not found.")
        versions = await self._media.list_versions(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )
        live = [version for version in versions if version.deleted_at is None]
        if asset.lifecycle is AssetLifecycle.DELETED and not live:
            raise NotFoundError("That picture was not found.")
        self._refuse_if_shown(await self._shown_anywhere(workspace_id, project_id, asset_id))
        # Marked first: nothing can link, open or prepare it while its files go.
        if not await self._media.hold_for_deletion(
            workspace_id=workspace_id,
            project_id=project_id,
            asset_id=asset_id,
            lifecycle=asset.lifecycle,
        ):
            raise ConflictError("This picture changed just now. Look again, then delete it.")
        shown = await self._shown_anywhere(workspace_id, project_id, asset_id)
        if shown:
            # Shown somewhere since the first look: put it back as it was.
            if asset.lifecycle is not AssetLifecycle.DELETED:
                await self._media.release_hold(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    asset_id=asset_id,
                    lifecycle=asset.lifecycle,
                )
            self._refuse_if_shown(shown)
        revision = await self._retired_revision(workspace_id, project_id)
        # Committed to deleting it: what a model said about it goes first, so a deletion
        # stopped part way never leaves notes about a picture that can no longer be seen.
        notes = await self._remove_notes(scope, workspace_id, project_id, asset_id, revision)
        copies_of: dict[str, list[MediaVariant]] = {}
        if self._processing is not None and live:
            copies_of = await self._processing.list_variants(
                workspace_id=workspace_id,
                project_id=project_id,
                version_ids=[version.id for version in live],
            )
        keys = {version.object_key for version in live} | {
            copy.object_key for found in copies_of.values() for copy in found
        }
        # A file another picture still uses stays: only this picture's records go.
        kept = await self._media.object_keys_in_use(
            workspace_id=workspace_id,
            project_id=project_id,
            object_keys=sorted(keys),
            except_asset_id=asset_id,
        )
        removed: set[str] = set()
        files = copies = freed = 0
        for version in live:
            if self._processing is not None:
                for key in await self._processing.remove_variants(
                    workspace_id=workspace_id, project_id=project_id, version_id=version.id
                ):
                    if key in kept or key in removed:
                        continue
                    removed.add(key)
                    if self._objects.delete(key, workspace_id=workspace_id, project_id=project_id):
                        copies += 1
            key = version.object_key
            if key not in kept and key not in removed:
                removed.add(key)
                if self._objects.delete(key, workspace_id=workspace_id, project_id=project_id):
                    files += 1
                    freed += version.byte_length
            await self._media.delete_original(
                workspace_id=workspace_id,
                project_id=project_id,
                version_id=version.id,
                retired_revision=revision,
                actor_user_id=scope.actor_user_id,
            )
        gone = await self._media.delete_asset(
            workspace_id=workspace_id,
            project_id=project_id,
            asset_id=asset_id,
            retired_revision=revision,
            actor_user_id=scope.actor_user_id,
        )
        await self._versions_removed(workspace_id, project_id, [v.id for v in versions])
        metrics.increment("media_pictures_deleted_total", "Pictures whose files were deleted.")
        log_event(
            logger,
            "media.picture_deleted",
            versions=len(versions),
            files=files,
            copies=copies,
            bytes=freed,
            kept=len(kept),
            notes=notes,
        )
        return DeletedPicture(
            asset=gone or asset,
            versions=len(versions),
            files_removed=files,
            copies_removed=copies,
            bytes_freed=freed,
            notes_removed=notes,
        )

    async def _remove_notes(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        revision: int | None,
    ) -> int:
        """Remove what a model said about a picture being deleted (AST-008): its
        observations (captions and appearance details, whatever the author decided), so
        neither the picture page, an archive, a check nor search can repeat them. Their
        search rows go with the versions (``on_versions_removed``). Recorded as a
        ``notes_removed`` event with how many, never the words. Proposals already made
        from them stay in review and history: they are the author's own records."""
        store = self.observation_store
        remove = getattr(store, "delete_for_asset", None)
        if remove is None:
            return 0
        count = int(
            await remove(workspace_id=workspace_id, project_id=project_id, asset_id=asset_id)
        )
        if count:
            await self._media.record_notes_removed(
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=asset_id,
                count=count,
                actor_user_id=scope.actor_user_id,
                canon_revision=revision,
            )
        return count

    @staticmethod
    def _refuse_if_shown(shown: int) -> None:
        if shown:
            places = "one entry or scene" if shown == 1 else f"{shown} entries or scenes"
            raise ConflictError(
                f"This picture is still shown on {places}. Take it off them first; "
                "then its file can be deleted."
            )

    # ---------------------------------------------- finding pictures (AST-007)
    async def search_pictures(
        self, scope: AccessScope, workspace_id: str, project_id: str, query: str, limit: int = 24
    ) -> PictureSearch:
        """Pictures of this line of the story for some words.

        First those whose name or reviewed description (an accepted or corrected
        caption or detail) holds every word, then those found by meaning when search by
        meaning is set up, without repeats. Only pictures the library offers are found,
        so a picture of another line, a retired one or a deleted one never is. The words
        are never logged.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        cleaned = " ".join(query.split())[:MAX_QUERY]
        words = query_words(cleaned)
        if not words:
            raise ValidationError("Type a word or two to look for.")
        library = await self.picture_library(scope, workspace_id, project_id)
        by_id = {picture.asset.id: picture for picture in library}
        described: dict[str, list[str]] = {}
        if self.observation_store is not None and by_id:
            for seen in await self.observation_store.for_assets(
                workspace_id=workspace_id, project_id=project_id, asset_ids=list(by_id)
            ):
                if seen.state.value not in ("accepted", "corrected"):
                    continue
                text_of = seen.corrected_text if seen.state.value == "corrected" else seen.text
                if text_of:
                    described.setdefault(seen.asset_id, []).append(text_of.lower())
        parts = await self._part_words(workspace_id, project_id, set(by_id))
        found: list[FoundPicture] = []
        for picture in library:
            name = picture.asset.name.lower()
            about = " ".join(described.get(picture.asset.id, []))
            marked = next(
                (
                    shown
                    for shown, said, _segment_id in parts.get(picture.asset.id, [])
                    if all(word in said for word in words)
                ),
                None,
            )
            if all(word in name for word in words):
                found.append(FoundPicture(picture, "words", "name"))
            elif marked is not None:
                found.append(FoundPicture(picture, "words", "part", part=marked))
            elif about and all(word in f"{name} {about}" for word in words):
                found.append(FoundPicture(picture, "words", "description"))
        note: str | None = "Search by meaning is not set up here."
        by_meaning = False
        part_names = {
            asset_id: [(segment_id, shown) for shown, _said, segment_id in items]
            for asset_id, items in parts.items()
        }
        if self.search is not None and len(found) < limit:
            result = await self.search.pictures_for(
                workspace_id, project_id, query=cleaned, limit=limit
            )
            by_meaning, note = bool(result.semantic), result.reason
            already = {item.picture.asset.id for item in found}
            for candidate in result.pictures:
                match = by_id.get(candidate.asset_id)
                if match is None or candidate.asset_id in already:
                    continue
                already.add(candidate.asset_id)
                shown = None
                if candidate.matched == "part":
                    shown = next(
                        (
                            label
                            for segment_id, label in part_names.get(candidate.asset_id, [])
                            if segment_id == candidate.segment_id
                        ),
                        None,
                    )
                found.append(
                    FoundPicture(
                        match,
                        "meaning",
                        candidate.matched or "picture",
                        candidate.score,
                        part=shown,
                    )
                )
        metrics.increment(
            "media_picture_searches_total",
            "Searches for pictures by words, by whether meaning was used.",
            by_meaning=str(by_meaning).lower(),
        )
        return PictureSearch(found=found[:limit], by_meaning=by_meaning, note=note)

    async def _part_words(
        self, workspace_id: str, project_id: str, asset_ids: set[str]
    ) -> dict[str, list[tuple[str, str, str]]]:
        """Each picture's current marked parts with a name or an entry: what to call the
        part, the words it is found by (lowercased name and entry) and its id."""
        if self._processing is None or not asset_ids:
            return {}
        regions = [
            region
            for region in await self._processing.list_regions(
                workspace_id=workspace_id, project_id=project_id
            )
            if region.asset_id in asset_ids
        ]
        names: dict[str, str | None] = {}
        found: dict[str, list[tuple[str, str, str]]] = {}
        for region in regions:
            entity_name = None
            if region.entity_id:
                if region.entity_id not in names:
                    entity = await self._reader.get_entity(
                        workspace_id=workspace_id, project_id=project_id, entity_id=region.entity_id
                    )
                    names[region.entity_id] = (
                        entity.name
                        if entity is not None and getattr(entity, "is_active", True)
                        else None
                    )
                entity_name = names[region.entity_id]
            shown = region.label or entity_name
            if not shown:
                continue
            said = " ".join(item for item in (region.label, entity_name) if item).lower()
            found.setdefault(region.asset_id, []).append((shown, said, region.id))
        return found

    # ------------------------------------------------- two-step uploads (AST-002)
    async def start_upload(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        target_type: UploadTarget,
        target_id: str,
        byte_length: int,
        sha256: str,
        name: str | None = None,
    ) -> PictureUpload:
        """Ask to send a picture: say what it is for, how large it is and its SHA-256.

        Refused before any byte is sent when it is too large, the workspace has no room
        for it, what it is for is not in the story, or too many are already on their way.
        The answer is where to send it; it must be sent, and then finished, within an
        hour. The one-step upload (``add_picture``) stays for small pictures."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        digest = sha256.strip().lower()
        if not _SHA256_HEX.match(digest):
            raise ValidationError("Give the picture's SHA-256 as 64 letters and digits.")
        if byte_length <= 0:
            raise ValidationError("The picture is empty.")
        if byte_length > MAX_PICTURE_BYTES:
            metrics.increment(
                "media_uploads_refused_total",
                "Picture uploads refused, by reason.",
                reason="too_large",
            )
            raise PictureTooLarge(_TOO_LARGE)
        label = " ".join((name or "").split())[:120] or None
        await self._upload_target(workspace_id, project_id, target_type, target_id)
        await self._check_quota(workspace_id, byte_length)
        now = utcnow()
        upload = PictureUpload(
            workspace_id=workspace_id,
            project_id=project_id,
            id=f"upl_{uuid7()}",
            branch_id=await self._line_id(workspace_id, project_id),
            actor_user_id=scope.actor_user_id,
            target_type=target_type,
            target_id=target_id,
            name=label,
            expected_bytes=byte_length,
            expected_sha256=digest,
            created_at=now,
            expires_at=now + UPLOAD_LIFETIME,
        )
        # Counted and added in one step, so two at once cannot both take the last place.
        created = await self._media.create_upload(
            upload,
            max_per_person=MAX_OPEN_UPLOADS,
            max_per_workspace=MAX_OPEN_UPLOADS_PER_WORKSPACE,
        )
        if created is None:
            mine = await self._media.open_uploads(
                workspace_id=workspace_id, actor_user_id=scope.actor_user_id or ""
            )
            whose = (
                f"{MAX_OPEN_UPLOADS} of your pictures are"
                if mine >= MAX_OPEN_UPLOADS
                else f"{MAX_OPEN_UPLOADS_PER_WORKSPACE} pictures in this workspace are"
            )
            raise ConflictError(
                f"{whose} already on their way. Finish those, or wait an hour for "
                "unfinished ones to be cleared, then add this one."
            )
        log_event(logger, "media.upload_started", bytes=byte_length, target=target_type.value)
        return created

    async def receive_upload(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        upload_id: str,
        chunks: AsyncIterator[bytes],
        *,
        declared_length: str | None = None,
        content_digest: str | None = None,
    ) -> tuple[PictureUpload, ReceivedUpload]:
        """Take the picture's bytes for an upload, as they stream in.

        Never more than was promised is read; what arrived must be exactly the size and
        SHA-256 promised, and a picture by its first bytes. Kept aside (not yet in the
        story or the library) until it is finished. When it does not match, nothing is
        kept and it can be sent again while the upload has not expired."""
        upload = await self._own_upload(scope, workspace_id, project_id, upload_id)
        if upload.state is not UploadState.WAITING:
            raise ConflictError(_UPLOAD_STATE_WORDS.get(upload.state, "This upload has moved on."))
        received = await receive_picture(
            chunks,
            declared_length=declared_length,
            content_digest=content_digest,
            max_bytes=upload.expected_bytes,
        )
        if received.size != upload.expected_bytes or not hmac.compare_digest(
            received.sha256, upload.expected_sha256
        ):
            metrics.increment(
                "media_uploads_refused_total",
                "Picture uploads refused, by reason.",
                reason="not_as_promised",
            )
            raise ValidationError(
                "What arrived is not the picture that was promised: its size or SHA-256 "
                "differs. Nothing was kept; send it again."
            )
        content_type = sniff_image_type(received.data)
        if content_type is None:
            raise ValidationError(_NOT_A_PICTURE)
        stat = self._objects.put(
            received.data,
            workspace_id=workspace_id,
            project_id=project_id,
            content_type=content_type,
        )
        moved = await self._media.move_upload(
            workspace_id=workspace_id,
            project_id=project_id,
            upload_id=upload_id,
            from_state=UploadState.WAITING,
            to_state=UploadState.RECEIVED,
            staged_key=stat.key,
            content_type=content_type,
        )
        if moved is None:
            # Sent twice at once, or it expired while arriving: keep nothing of this one.
            self._objects.delete(stat.key, workspace_id=workspace_id, project_id=project_id)
            raise ConflictError(
                "This upload has already arrived or has expired. Look again before sending it."
            )
        log_event(logger, "media.upload_received", bytes=received.size)
        return moved, received

    async def finish_upload(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        upload_id: str,
        *,
        expected_revision: int | None = None,
    ) -> tuple[PictureUpload, AssetLink | None, MediaAssetVersion]:
        """Make a received upload into a picture: the stored bytes are checked against
        what was promised once more, hidden details are taken out as for any picture,
        and it is shown on its entry or scene (a change to the story, so the story's
        revision is needed) or added as a new version of its picture. Asking again once
        it is finished answers with what it made."""
        upload = await self._own_upload(scope, workspace_id, project_id, upload_id)
        if upload.state is UploadState.FINISHED:
            return await self._finished(workspace_id, project_id, upload)
        if upload.state is not UploadState.RECEIVED:
            raise ConflictError(_UPLOAD_STATE_WORDS.get(upload.state, "This upload has moved on."))
        if upload.target_type is not UploadTarget.ASSET and expected_revision is None:
            raise ValidationError("Send the story's revision with the picture.")
        if upload.branch_id != await self._line_id(workspace_id, project_id):
            raise ConflictError(
                "This picture was started in another line of the story. Switch back to it to "
                "finish it, or add the picture again here."
            )
        claimed = await self._media.move_upload(
            workspace_id=workspace_id,
            project_id=project_id,
            upload_id=upload_id,
            from_state=UploadState.RECEIVED,
            to_state=UploadState.FINISHING,
        )
        if claimed is None:
            raise ConflictError("This upload is being finished now, or has expired.")
        staged = claimed.staged_key or ""
        made: PictureUpload | None = None

        async def landed(asset_id: str, version_id: str, link_id: str | None) -> None:
            # Recorded the moment the picture exists, before anything else can fail, so
            # asking again answers with this picture and never makes a second one.
            nonlocal made
            made = await self._media.move_upload(
                workspace_id=workspace_id,
                project_id=project_id,
                upload_id=upload_id,
                from_state=UploadState.FINISHING,
                to_state=UploadState.FINISHED,
                asset_id=asset_id,
                version_id=version_id,
                link_id=link_id,
                unexpired=False,
            ) or claimed.model_copy(
                update={
                    "state": UploadState.FINISHED,
                    "asset_id": asset_id,
                    "version_id": version_id,
                    "link_id": link_id,
                }
            )

        try:
            try:
                data = self._objects.open(staged, workspace_id=workspace_id, project_id=project_id)
            except Exception as error:  # noqa: BLE001 - said plainly below
                raise ValidationError(
                    "The picture that arrived can no longer be found. Add it again."
                ) from error
            if len(data) != claimed.expected_bytes or not hmac.compare_digest(
                hashlib.sha256(data).hexdigest(), claimed.expected_sha256
            ):
                raise ValidationError(
                    "The picture that arrived changed while it waited. Add it again."
                )
            data, content_type = await self._checked_picture(data)
            link: AssetLink | None = None
            if claimed.target_type is UploadTarget.ASSET:
                version = await self._store_version(
                    scope,
                    workspace_id,
                    project_id,
                    claimed.target_id,
                    data,
                    content_type,
                    landed=landed,
                )
            else:
                target = await self._upload_target(
                    workspace_id, project_id, claimed.target_type, claimed.target_id
                )
                _, link, version = await self._store_and_link(
                    scope,
                    workspace_id,
                    project_id,
                    data=data,
                    content_type=content_type,
                    label=claimed.name or target,
                    target_record_type=claimed.target_type.value,
                    target_id=claimed.target_id,
                    expected_revision=int(expected_revision or 0),
                    landed=landed,
                )
        except BaseException:
            if made is None:
                # Nothing was made: it can be finished again while it has not expired.
                await self._media.move_upload(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    upload_id=upload_id,
                    from_state=UploadState.FINISHING,
                    to_state=UploadState.RECEIVED,
                    unexpired=False,
                )
            else:
                self._drop_staged(staged, workspace_id, project_id)
            raise
        self._drop_staged(staged, workspace_id, project_id)
        log_event(logger, "media.upload_finished", target=claimed.target_type.value)
        return made or claimed, link, version

    def _drop_staged(self, staged: str, workspace_id: str, project_id: str) -> None:
        """Remove a finished upload's staged file; a stray left here is cleared later."""
        try:
            self._objects.delete(staged, workspace_id=workspace_id, project_id=project_id)
        except Exception:  # noqa: BLE001 - retention removes strays later
            log_event(logger, "media.upload_cleanup_failed", level=logging.WARNING)

    async def expire_uploads(self) -> int:
        """Clear uploads not finished within the hour: what arrived is removed. One being
        finished right now is left a while longer, so its file is not taken from under
        it. Bounded work, run by the background worker about every five minutes."""
        expired = await self._media.expire_uploads(
            limit=100, finishing_grace_seconds=int(FINISHING_GRACE.total_seconds())
        )
        for upload in expired:
            if upload.staged_key:
                try:
                    self._objects.delete(
                        upload.staged_key,
                        workspace_id=upload.workspace_id,
                        project_id=upload.project_id,
                    )
                except Exception:  # noqa: BLE001 - retention removes strays later
                    log_event(logger, "media.upload_cleanup_failed", level=logging.WARNING)
        if expired:
            log_event(logger, "media.uploads_expired", count=len(expired))
        return len(expired)

    async def _own_upload(
        self, scope: AccessScope, workspace_id: str, project_id: str, upload_id: str
    ) -> PictureUpload:
        """An upload of this project, started by the person asking."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        upload = await self._media.get_upload(
            workspace_id=workspace_id, project_id=project_id, upload_id=upload_id
        )
        if upload is None or (
            upload.actor_user_id is not None and upload.actor_user_id != scope.actor_user_id
        ):
            raise NotFoundError("That upload was not found.")
        if upload.state in (UploadState.WAITING, UploadState.RECEIVED) and (
            upload.expires_at <= utcnow()
        ):
            raise ConflictError(_UPLOAD_STATE_WORDS[UploadState.EXPIRED])
        return upload

    async def _finished(
        self, workspace_id: str, project_id: str, upload: PictureUpload
    ) -> tuple[PictureUpload, AssetLink | None, MediaAssetVersion]:
        version = await self._media.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=upload.version_id or ""
        )
        if version is None:
            raise NotFoundError("The picture this upload made was not found.")
        link = (
            await self._reader.get_link(
                workspace_id=workspace_id, project_id=project_id, link_id=upload.link_id
            )
            if upload.link_id
            else None
        )
        return upload, link, version

    async def _line_id(self, workspace_id: str, project_id: str) -> str | None:
        if self._journal is None:
            return None
        branch = await self._journal.working_branch(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        return None if getattr(branch, "is_root", True) else str(branch.id)

    async def _upload_target(
        self, workspace_id: str, project_id: str, target_type: UploadTarget, target_id: str
    ) -> str:
        """Check what an upload is for is in this line of the story; its default label."""
        if target_type is UploadTarget.ENTITY:
            entity = await self._reader.get_entity(
                workspace_id=workspace_id, project_id=project_id, entity_id=target_id
            )
            if entity is None or not entity.is_active:
                raise NotFoundError("Entry not found.")
            return f"Picture of {entity.name}"[:120]
        if target_type is UploadTarget.SCENE:
            scene = await self._reader.get_plan_item(
                workspace_id=workspace_id, project_id=project_id, plan_id=target_id
            )
            if scene is None or not scene.is_active or scene.kind != PlanKind.SCENE:
                raise NotFoundError("Scene not found.")
            return f"Picture for {scene.title}"[:120]
        asset = await self._media.get_asset(
            workspace_id=workspace_id, project_id=project_id, asset_id=target_id
        )
        versions = (
            await self._media.list_versions(
                workspace_id=workspace_id, project_id=project_id, asset_id=target_id
            )
            if asset is not None
            else []
        )
        if (
            asset is None
            or not asset.is_active
            or not any(version.content_type in PICTURE_TYPES for version in versions)
        ):
            raise NotFoundError("That picture was not found.")
        return asset.name

    async def _store_version(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        data: bytes,
        content_type: str,
        *,
        landed: LandedHook | None = None,
    ) -> MediaAssetVersion:
        """A new version of a picture already in the story (AST-001, AST-002). Where it
        is shown is unchanged: links that follow the newest version show it from now on;
        a link that shows one version keeps showing that one. ``landed`` is told the
        moment the version exists."""
        await self._upload_target(workspace_id, project_id, UploadTarget.ASSET, asset_id)
        async with self._quota_guard(workspace_id):
            await self._check_quota(workspace_id, len(data))
            stat = self._objects.put(
                data, workspace_id=workspace_id, project_id=project_id, content_type=content_type
            )
            version = await self._media.create_version(
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=asset_id,
                object_key=stat.key,
                content_type=stat.content_type,
                byte_length=stat.byte_length,
                checksum=stat.checksum,
                authority="author_reference",
                created_revision=await self._retired_revision(workspace_id, project_id) or 0,
                actor_user_id=scope.actor_user_id,
            )
        if landed is not None:
            await landed(asset_id, version.id, None)
        await self._queue_quietly(scope, workspace_id, project_id, version)
        return version

    # ---------------------------------------------------------------- pictures
    async def add_picture(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        entity_id: str,
        data: bytes,
        name: str | None,
        expected_revision: int,
    ) -> tuple[CommitResult, AssetLink, MediaAssetVersion]:
        """Store an image and link it to an entry as a picture of it.

        Any member who can write may do this. The type is decided from the bytes,
        never from what the upload claims, and only still images are accepted.
        Where and when it was taken, the device and any other hidden details are
        removed before it is stored, keeping only which way up it goes (AST-006).
        Nothing is decoded here or sent to a model: a ``media_process`` job prepares
        its smaller copies afterwards, in a separate, limited process.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        data, content_type = await self._checked_picture(data)
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        if entity is None or not entity.is_active:
            raise NotFoundError("Entry not found.")
        return await self._store_and_link(
            scope,
            workspace_id,
            project_id,
            data=data,
            content_type=content_type,
            label=" ".join((name or "").split())[:120] or f"Picture of {entity.name}",
            target_record_type="entity",
            target_id=entity.id,
            expected_revision=expected_revision,
        )

    async def add_scene_picture(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        scene_id: str,
        data: bytes,
        name: str | None,
        expected_revision: int,
    ) -> tuple[CommitResult, AssetLink, MediaAssetVersion]:
        """Store an image and show it on a scene of the plan (AST-EXP-018).

        The same checks as a picture of an entry. The link points at the plan item
        (``target_record_type="plan_item"``) with the role ``DEPICTS``.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        data, content_type = await self._checked_picture(data)
        scene = await self._reader.get_plan_item(
            workspace_id=workspace_id, project_id=project_id, plan_id=scene_id
        )
        if scene is None or not scene.is_active or scene.kind != PlanKind.SCENE:
            raise NotFoundError("Scene not found.")
        return await self._store_and_link(
            scope,
            workspace_id,
            project_id,
            data=data,
            content_type=content_type,
            label=" ".join((name or "").split())[:120] or f"Picture for {scene.title}"[:120],
            target_record_type=SCENE_TARGET,
            target_id=scene.id,
            expected_revision=expected_revision,
        )

    async def _checked_picture(self, data: bytes) -> tuple[bytes, str]:
        """The upload as it will be stored, and its type from its own bytes."""
        if not data:
            raise ValidationError("The picture is empty.")
        if len(data) > MAX_PICTURE_BYTES:
            raise ValidationError(
                f"The picture is too large (the limit is {MAX_PICTURE_BYTES // (1024 * 1024)} MB)."
            )
        content_type = sniff_image_type(data)
        if content_type is None:
            raise ValidationError("Only PNG, JPEG, and WebP pictures can be added.")
        try:
            # Walking a large file's structure takes a moment: off the event loop.
            data = await asyncio.to_thread(strip_metadata, data, content_type)
        except ImageMetadataError as error:
            raise ValidationError(
                "This picture could not be read. Save it again as a PNG, JPEG, or WebP "
                "file and add it once more."
            ) from error
        return data, content_type

    async def _store_and_link(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        data: bytes,
        content_type: str,
        label: str,
        target_record_type: str,
        target_id: str,
        expected_revision: int,
        landed: LandedHook | None = None,
    ) -> tuple[CommitResult, AssetLink, MediaAssetVersion]:
        """Store a picture and show it on its target. ``landed`` is told the moment the
        link is in the story, before it is read back or queued for preparation."""
        # Checked and counted together: the room is only taken up once the version row
        # exists, so two uploads at once must not both see the room as free.
        async with self._quota_guard(workspace_id):
            await self._check_quota(workspace_id, len(data))
            stat = self._objects.put(
                data, workspace_id=workspace_id, project_id=project_id, content_type=content_type
            )
            asset = await self._media.create_asset(
                workspace_id=workspace_id,
                project_id=project_id,
                name=label,
                actor_user_id=scope.actor_user_id,
            )
            version = await self._media.create_version(
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=asset.id,
                object_key=stat.key,
                content_type=stat.content_type,
                byte_length=stat.byte_length,
                checksum=stat.checksum,
                authority="author_reference",
                created_revision=await self._retired_revision(workspace_id, project_id) or 0,
                actor_user_id=scope.actor_user_id,
            )
        link_id = format_asset_link_id()
        try:
            result = await self._commit(
                scope,
                workspace_id,
                project_id,
                LinkAssetOp(
                    id=link_id,
                    asset_id=asset.id,
                    asset_version_id=version.id,
                    target_record_type=target_record_type,
                    target_id=target_id,
                    role=LinkRole.DEPICTS.value,
                ),
                expected_revision,
                "",
            )
        except Exception:
            # The picture never reached the story: leave nothing behind for the
            # library to offer.
            kept = await self._discard_upload(workspace_id, project_id, link_id, asset.id, stat.key)
            if kept and landed is not None:
                # The commit did land before it failed: the picture is in the story.
                await landed(asset.id, version.id, link_id)
            raise
        if landed is not None:
            await landed(asset.id, version.id, link_id)
        link = await self._reader.get_link(
            workspace_id=workspace_id, project_id=project_id, link_id=link_id
        )
        assert link is not None
        await self._queue_quietly(scope, workspace_id, project_id, version)
        return result, link, version

    async def _discard_upload(
        self, workspace_id: str, project_id: str, link_id: str, asset_id: str, object_key: str
    ) -> bool:
        """Remove an upload whose link was refused; a failure here never hides why. True
        when the link turns out to be in the story after all, so nothing was removed."""
        try:
            # Checked first, in case the commit did land before it failed.
            if (
                await self._reader.get_link(
                    workspace_id=workspace_id, project_id=project_id, link_id=link_id
                )
                is not None
            ):
                return True
            versions = await self._media.list_versions(
                workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
            )
            await self._media.discard_asset(
                workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
            )
            self._objects.delete(object_key, workspace_id=workspace_id, project_id=project_id)
            await self._versions_removed(workspace_id, project_id, [v.id for v in versions])
        except Exception:  # noqa: BLE001 - the refusal is what the author needs to see
            logger.warning("media.discard_failed", exc_info=True)
        return False

    async def picture_library(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[LibraryPicture]:
        """Every picture in this line of the story that can still be shown, newest first.

        Pictures no longer shown anywhere are included: removing a picture from an
        entry or a scene retires the link, never the picture, so it can be used again.
        Only pictures linked at some time in the line being worked in are offered, so
        a picture added in a what-if stays out of the main story. A picture on a scene
        taken out of the plan is not counted as shown. Each picture is its newest
        version that is still current.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        assets = await self._media.list_assets(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_LIBRARY
        )
        links = await self._reader.list_links(
            workspace_id=workspace_id,
            project_id=project_id,
            include_retired=True,
            limit=MAX_LIBRARY_LINKS,
        )
        linked = {link.asset_id for link in links}
        active = [link for link in links if link.is_active]
        retired_scenes: set[str] = set()
        if any(link.target_record_type == SCENE_TARGET for link in active):
            retired_scenes = {
                item.id
                for item in await self._reader.list_plan_items(
                    workspace_id=workspace_id, project_id=project_id, include_retired=True
                )
                if not item.is_active
            }
        shown: dict[str, int] = {}
        for link in active:
            if link.target_record_type == SCENE_TARGET and link.target_record_id in retired_scenes:
                continue
            shown[link.asset_id] = shown.get(link.asset_id, 0) + 1
        candidates = [asset for asset in assets if asset.is_active and asset.id in linked]
        versions = await self._media.list_versions_for_assets(
            workspace_id=workspace_id,
            project_id=project_id,
            asset_ids=[asset.id for asset in candidates],
        )
        newest_of: list[tuple[MediaAsset, MediaAssetVersion]] = []
        for asset in candidates:
            current = [
                version
                for version in versions.get(asset.id, [])
                if version.retired_revision is None and version.content_type in PICTURE_TYPES
            ]
            if not current:
                continue
            newest_of.append((asset, max(current, key=lambda version: version.version)))
        states: dict[str, MediaProcessing] = {}
        copies: dict[str, list[MediaVariant]] = {}
        if self._processing is not None and newest_of:
            ids = [version.id for _asset, version in newest_of]
            states = await self._processing.list_processing(
                workspace_id=workspace_id, project_id=project_id, version_ids=ids
            )
            copies = await self._processing.list_variants(
                workspace_id=workspace_id, project_id=project_id, version_ids=ids
            )
        return [
            LibraryPicture(
                asset=asset,
                version=version,
                shown=shown.get(asset.id, 0),
                processing=states.get(version.id),
                has_thumbnail=any(
                    copy.kind is VariantKind.THUMBNAIL for copy in copies.get(version.id, [])
                ),
            )
            for asset, version in newest_of
        ]

    async def open_content(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
    ) -> tuple[bytes, str]:
        """The stored bytes of one media version, for members of the project only."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._servable_version(workspace_id, project_id, asset_id, version_id)
        try:
            data = self._objects.open(
                version.object_key, workspace_id=workspace_id, project_id=project_id
            )
        except Exception as error:  # noqa: BLE001 - a missing object is a missing picture
            raise NotFoundError("Media not found.") from error
        return data, version.content_type

    async def open_picture(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
        *,
        copy: VariantKind | None,
    ) -> tuple[bytes, str, str]:
        """A picture to show: its smaller copy when asked for and made, else the
        original. Returns the bytes, their type, and which was served (the copy's kind
        or ``original``). Members of the project only; the same checks as the original."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._servable_version(workspace_id, project_id, asset_id, version_id)
        if copy is not None and self._processing is not None:
            variant = await self._processing.get_variant(
                workspace_id=workspace_id, project_id=project_id, version_id=version.id, kind=copy
            )
            if variant is not None and variant.source_checksum == version.checksum:
                try:
                    data = self._objects.open(
                        variant.object_key, workspace_id=workspace_id, project_id=project_id
                    )
                    return data, variant.content_type, variant.kind.value
                except Exception:  # noqa: BLE001 - a lost copy falls back to the original
                    log_event(logger, "media.copy_missing", level=logging.WARNING)
        try:
            data = self._objects.open(
                version.object_key, workspace_id=workspace_id, project_id=project_id
            )
        except Exception as error:  # noqa: BLE001 - a missing object is a missing picture
            raise NotFoundError("Media not found.") from error
        return data, version.content_type, "original"

    async def _servable_version(
        self, workspace_id: str, project_id: str, asset_id: str, version_id: str
    ) -> MediaAssetVersion:
        version = await self._media.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        if version is None or version.asset_id != asset_id or version.retired_revision is not None:
            raise NotFoundError("Media not found.")
        asset = await self._media.get_asset(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )
        if asset is None or asset.lifecycle is not AssetLifecycle.ACTIVE:
            raise NotFoundError("Media not found.")
        # Only types this application stored itself are served, and only as images.
        if version.content_type not in PICTURE_TYPES:
            raise NotFoundError("Media not found.")
        return version

    # ------------------------------------------------------- processing (AST-003)
    def register_jobs(self) -> None:
        """Teach the job runner to prepare pictures."""
        if self._runner is not None and self.processing_enabled:
            self._runner.register(MEDIA_PROCESS_KIND, self._run_process_job)
        if self._runner is not None and hasattr(self._runner, "every"):
            # Unfinished two-step uploads are cleared once their hour is up (AST-002).
            self._runner.every(UPLOAD_SWEEP_SECONDS, self.expire_uploads)

    async def storage_used(self, workspace_id: str) -> int:
        if self._processing is None:
            return 0
        return await self._processing.workspace_bytes(workspace_id=workspace_id)

    def _quota_guard(self, workspace_id: str) -> asyncio.Lock:
        """Held from the room check until the new version row is written, so uploads to
        one workspace at the same time are counted one after another. This covers one
        API process (how the application is run); several processes would need a
        database lock here as well. An archive restore does not take it and may go over
        the room: the author's own archive is restored whole, and the next upload is
        then refused in the usual plain words."""
        lock = self._quota_locks.get(workspace_id)
        if lock is None:
            lock = self._quota_locks[workspace_id] = asyncio.Lock()
        return lock

    async def _check_quota(self, workspace_id: str, incoming: int) -> None:
        """Refuse, in plain words, a picture that would take the workspace over its room."""
        if self._quota_bytes <= 0 or self._processing is None:
            return
        used = await self._processing.workspace_bytes(workspace_id=workspace_id)
        # Room is kept for the smaller copies too: they are a fraction of the original.
        if used + incoming + incoming // 4 > self._quota_bytes:
            metrics.increment(
                "media_uploads_refused_total",
                "Picture uploads refused, by reason.",
                reason="storage_full",
            )
            raise StorageQuotaExceeded(
                "There is no room left for this picture: this workspace is using "
                f"{_size(used)} of its {_size(self._quota_bytes)}. Remove pictures you no "
                "longer need, or ask whoever runs this site for more room."
            )

    async def _queue_quietly(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        version: MediaAssetVersion,
    ) -> None:
        """Queue a new picture for preparation; a failure here never undoes the upload
        (the backfill finds it later)."""
        if not self.processing_enabled or version.content_type not in PICTURE_TYPES:
            return
        try:
            await self._queue(scope, workspace_id, project_id, version, attempt=0)
        except Exception as error:  # noqa: BLE001 - the picture itself is stored and linked
            log_event(
                logger,
                "media.queue_failed",
                level=logging.WARNING,
                error_type=type(error).__name__,
            )

    async def _queue(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        version: MediaAssetVersion,
        *,
        attempt: int,
    ) -> MediaProcessing:
        assert self._processing is not None
        job_id: str | None = None
        if self._runner is not None:
            from backend.services.job_runner import JobSubmission

            # Keyed by the bytes and the processor: asking again is the same job.
            key = f"media:{version.id}:{version.checksum[:16]}:{PROCESSOR_VERSION}:{attempt}"
            job = await self._runner.submit(
                scope,
                workspace_id,
                project_id,
                JobSubmission(
                    kind=MEDIA_PROCESS_KIND,
                    payload={
                        "asset_id": version.asset_id,
                        "asset_version_id": version.id,
                        "source_checksum": version.checksum,
                        "processor_version": PROCESSOR_VERSION,
                    },
                    idempotency_key=key[:200],
                ),
            )
            job_id = job.id
        return await self._processing.queue(
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=version.id,
            asset_id=version.asset_id,
            checksum=version.checksum,
            processor_version=PROCESSOR_VERSION,
            job_id=job_id,
        )

    async def processing_status(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
    ) -> ProcessingView:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._version_of(workspace_id, project_id, asset_id, version_id)
        return await self._view(workspace_id, project_id, version)

    async def _view(
        self, workspace_id: str, project_id: str, version: MediaAssetVersion
    ) -> ProcessingView:
        state = None
        copies: list[MediaVariant] = []
        if self._processing is not None:
            state = await self._processing.get_processing(
                workspace_id=workspace_id, project_id=project_id, version_id=version.id
            )
            copies = (
                await self._processing.list_variants(
                    workspace_id=workspace_id, project_id=project_id, version_ids=[version.id]
                )
            ).get(version.id, [])
        if state is None:
            return ProcessingView(
                status=None,
                failure_code=None,
                message=None,
                retryable=self.processing_enabled and version.content_type in PICTURE_TYPES,
                attempts=0,
                width=None,
                height=None,
                copies=(),
                processor_version=None,
                current=False,
            )
        failed = state.status is ProcessingStatus.FAILED
        return ProcessingView(
            status=state.status,
            failure_code=state.failure_code if failed else None,
            message=processing_failure_words(state.failure_code) if failed else None,
            # Anything that failed can be tried again; some failures say it will not help.
            retryable=failed and self.processing_enabled,
            attempts=state.attempts,
            width=state.width,
            height=state.height,
            copies=tuple(sorted(copy.kind.value for copy in copies)),
            processor_version=state.processor_version,
            current=state.is_current(
                checksum=version.checksum, processor_version=PROCESSOR_VERSION
            ),
        )

    async def retry_processing(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
    ) -> ProcessingView:
        """Prepare a picture again after it failed (or was never prepared). A picture
        already prepared by this processor from these bytes is left as it is."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not self.processing_enabled:
            raise ValidationError("Pictures cannot be prepared on this installation.")
        version = await self._version_of(workspace_id, project_id, asset_id, version_id)
        if version.content_type not in PICTURE_TYPES:
            raise ValidationError("Only PNG, JPEG and WebP pictures are prepared.")
        assert self._processing is not None
        state = await self._processing.get_processing(
            workspace_id=workspace_id, project_id=project_id, version_id=version.id
        )
        busy = state is not None and state.status in (
            ProcessingStatus.QUEUED,
            ProcessingStatus.PROCESSING,
        )
        done = (
            state is not None
            and state.status is ProcessingStatus.READY
            and state.is_current(checksum=version.checksum, processor_version=PROCESSOR_VERSION)
        )
        if not busy and not done:
            attempt = (state.attempts if state is not None else 0) + 1
            await self._queue(scope, workspace_id, project_id, version, attempt=attempt)
        return await self._view(workspace_id, project_id, version)

    # ------------------------------------------------------ segments (AST-003)
    async def list_segments(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
    ) -> list[MediaSegment]:
        """The whole picture (once it is prepared) and the regions the author chose."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._version_of(workspace_id, project_id, asset_id, version_id)
        if self._processing is None:
            return []
        return list(
            await self._processing.list_segments(
                workspace_id=workspace_id, project_id=project_id, version_id=version.id
            )
        )

    async def _ready_size(
        self, workspace_id: str, project_id: str, version: MediaAssetVersion
    ) -> tuple[int, int]:
        """The prepared picture's size in pixels; refused before it is ready."""
        if self._processing is None:
            raise ValidationError("Pictures cannot be prepared on this installation.")
        state = await self._processing.get_processing(
            workspace_id=workspace_id, project_id=project_id, version_id=version.id
        )
        if (
            state is None
            or state.status is not ProcessingStatus.READY
            or state.width is None
            or state.height is None
        ):
            raise ValidationError("A part of a picture can be marked once the picture is ready.")
        return int(state.width), int(state.height)

    @staticmethod
    def _inside(x: int, y: int, width: int, height: int, size: tuple[int, int]) -> None:
        if width < 1 or height < 1 or x < 0 or y < 0:
            raise ValidationError("A marked part needs a place and a size inside the picture.")
        if x + width > size[0] or y + height > size[1]:
            raise ValidationError(
                f"That part goes past the picture's edge ({size[0]} by {size[1]})."
            )

    async def _region_entry(
        self, workspace_id: str, project_id: str, entity_id: str | None
    ) -> str | None:
        """The entry a marked part shows: one that is in the story now, or none."""
        wanted = (entity_id or "").strip()
        if not wanted:
            return None
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=wanted
        )
        if entity is None or not getattr(entity, "is_active", True):
            raise ValidationError("That entry is not in the story. Choose another, or none.")
        return wanted

    async def add_region(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        label: str | None = None,
        entity_id: str | None = None,
    ) -> MediaSegment:
        """Mark a part of a prepared picture (a face, a sign) so it can be pointed at on
        its own, with a name and the entry it shows. Pixels of the picture as shown,
        inside its edges. The author's own markup: nothing in the story changes."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        version = await self._servable_version(workspace_id, project_id, asset_id, version_id)
        size = await self._ready_size(workspace_id, project_id, version)
        self._inside(x, y, width, height, size)
        entry = await self._region_entry(workspace_id, project_id, entity_id)
        clean = " ".join((label or "").split())[:120] or None
        assert self._processing is not None
        if await self._region_count(workspace_id, project_id, version.id) >= (
            MAX_REGIONS_PER_VERSION
        ):
            raise ValidationError(
                f"A picture can have at most {MAX_REGIONS_PER_VERSION} marked parts. "
                "Take one off before marking another."
            )
        segment = await self._processing.add_segment(
            MediaSegment(
                workspace_id=workspace_id,
                project_id=project_id,
                id=f"seg_{uuid7()}",
                asset_id=version.asset_id,
                asset_version_id=version.id,
                kind=SegmentKind.REGION,
                x=x,
                y=y,
                width=width,
                height=height,
                label=clean,
                entity_id=entry,
                content_hash=_region_hash(version.checksum, x, y, width, height),
                created_by=scope.actor_user_id,
            )
        )
        log_event(logger, "media.region_marked", named=clean is not None, entry=entry is not None)
        await self._regions_changed(scope, workspace_id, project_id, version.asset_id)
        return segment  # type: ignore[no-any-return]

    async def update_region(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
        segment_id: str,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        label: str | None = None,
        entity_id: str | None = None,
    ) -> MediaSegment:
        """Move, resize, rename or relink a marked part. Notes already pointing at it keep
        the rectangle they were made with."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        version = await self._servable_version(workspace_id, project_id, asset_id, version_id)
        current = (
            await self._processing.get_segment(
                workspace_id=workspace_id, project_id=project_id, segment_id=segment_id
            )
            if self._processing is not None
            else None
        )
        if (
            current is None
            or current.asset_version_id != version.id
            or current.kind is not SegmentKind.REGION
            or current.retired_at is not None
        ):
            raise NotFoundError("That marked part was not found.")
        size = await self._ready_size(workspace_id, project_id, version)
        self._inside(x, y, width, height, size)
        entry = await self._region_entry(workspace_id, project_id, entity_id)
        clean = " ".join((label or "").split())[:120] or None
        assert self._processing is not None
        updated = await self._processing.update_segment(
            current.model_copy(
                update={
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "label": clean,
                    "entity_id": entry,
                    "content_hash": _region_hash(version.checksum, x, y, width, height),
                }
            )
        )
        if updated is None:
            raise NotFoundError("That marked part was not found.")
        log_event(logger, "media.region_changed", named=clean is not None, entry=entry is not None)
        await self._regions_changed(scope, workspace_id, project_id, version.asset_id)
        return updated  # type: ignore[no-any-return]

    async def _region_count(self, workspace_id: str, project_id: str, version_id: str) -> int:
        """How many marked parts one picture version has now (taken-off ones not counted)."""
        assert self._processing is not None
        return sum(
            1
            for segment in await self._processing.list_segments(
                workspace_id=workspace_id, project_id=project_id, version_id=version_id
            )
            if segment.kind is SegmentKind.REGION and segment.retired_at is None
        )

    def on_regions_changed(self, callback: Callable[..., Awaitable[Any]]) -> None:
        """Be told when a picture's marked parts change (for the search index, CTX-012),
        as ``callback(workspace_id, project_id, scope, asset_id)``. It is called inside
        the author's request, so it should only queue work, never do it."""
        self._region_hooks.append(callback)

    async def _regions_changed(
        self, scope: AccessScope | None, workspace_id: str, project_id: str, asset_id: str
    ) -> None:
        for callback in list(getattr(self, "_region_hooks", [])):
            try:
                await callback(workspace_id, project_id, scope, asset_id)
            except Exception as error:  # noqa: BLE001 - the index catches up later
                log_event(
                    logger,
                    "media.region_hook_failed",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                )

    async def retire_region(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
        segment_id: str,
    ) -> MediaSegment:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        await self._version_of(workspace_id, project_id, asset_id, version_id)
        retired = (
            await self._processing.retire_segment(
                workspace_id=workspace_id, project_id=project_id, segment_id=segment_id
            )
            if self._processing is not None
            else None
        )
        if retired is None or retired.asset_version_id != version_id:
            raise NotFoundError("That marked part was not found.")
        await self._regions_changed(scope, workspace_id, project_id, asset_id)
        return retired  # type: ignore[no-any-return]

    async def export_regions(self, workspace_id: str, project_id: str) -> list[dict[str, Any]]:
        """The parts of pictures the author marked, for an archive (they are the author's;
        the whole-picture segments are rebuilt by processing and are left out)."""
        if self._processing is None:
            return []
        return [
            {
                "id": region.id,
                "asset_id": region.asset_id,
                "asset_version_id": region.asset_version_id,
                "x": region.x,
                "y": region.y,
                "width": region.width,
                "height": region.height,
                "label": region.label,
                "entity_id": region.entity_id,
                "created_at": region.created_at.isoformat(),
            }
            for region in await self._processing.list_regions(
                workspace_id=workspace_id, project_id=project_id
            )
        ]

    async def restore_regions(self, workspace_id: str, project_id: str, regions: list[Any]) -> int:
        """Put back marked parts from an archive, for pictures that came back with it.
        A malformed entry, one naming a picture not there, one outside its picture (or
        past any picture's size) and any past a picture's limit of parts are skipped. A
        part showing an entry no longer in the story comes back without that link."""
        if self._processing is None or not isinstance(regions, list):
            return 0
        restored = 0
        counts: dict[str, int] = {}
        for raw in regions:
            if not isinstance(raw, dict):
                continue
            try:
                version = await self._media.get_version(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    version_id=str(raw["asset_version_id"]),
                )
                if version is None or version.asset_id != raw["asset_id"]:
                    continue
                x, y, width, height = (int(raw[key]) for key in ("x", "y", "width", "height"))
                if min(x, y) < 0 or min(width, height) < 1 or not str(raw["id"]).startswith("seg_"):
                    continue
                if x + width > MAX_REGION_PIXELS or y + height > MAX_REGION_PIXELS:
                    continue
                state = await self._processing.get_processing(
                    workspace_id=workspace_id, project_id=project_id, version_id=version.id
                )
                # Once its picture is prepared, a part must lie inside it; before, the
                # bound above is all that can be checked.
                if (
                    state is not None
                    and state.width is not None
                    and state.height is not None
                    and (x + width > int(state.width) or y + height > int(state.height))
                ):
                    continue
                if version.id not in counts:
                    counts[version.id] = await self._region_count(
                        workspace_id, project_id, version.id
                    )
                if counts[version.id] >= MAX_REGIONS_PER_VERSION:
                    continue
                label = " ".join(str(raw.get("label") or "").split())[:120] or None
                try:
                    entry = await self._region_entry(
                        workspace_id, project_id, str(raw.get("entity_id") or "")[:200]
                    )
                except ValidationError:
                    entry = None  # that entry is not in the story: the part comes back unlinked
                await self._processing.add_segment(
                    MediaSegment(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        id=str(raw["id"])[:80],
                        asset_id=version.asset_id,
                        asset_version_id=version.id,
                        kind=SegmentKind.REGION,
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                        label=label,
                        entity_id=entry,
                        content_hash=_region_hash(version.checksum, x, y, width, height),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
            counts[version.id] += 1
            restored += 1
        return restored

    async def storage(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> dict[str, int]:
        """How much room the workspace's pictures take, and its limit (0 is none)."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return {
            "used_bytes": await self.storage_used(workspace_id),
            "quota_bytes": self._quota_bytes,
        }

    async def _version_of(
        self, workspace_id: str, project_id: str, asset_id: str, version_id: str
    ) -> MediaAssetVersion:
        version = await self._media.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        if version is None or version.asset_id != asset_id:
            raise NotFoundError("Picture not found.")
        return version

    async def _run_process_job(self, ctx: Any) -> dict[str, Any]:
        job = ctx.job
        version_id = str(job.payload.get("asset_version_id") or "")
        if not version_id:
            raise ValidationError("This piece of work names no picture.")
        await ctx.step("checking", 5)
        return await self.process_version(job.workspace_id, job.project_id, version_id, ctx=ctx)

    async def process_version(
        self, workspace_id: str, project_id: str, version_id: str, *, ctx: Any = None
    ) -> dict[str, Any]:
        """Prepare one picture version: smaller copies, the whole-picture segment and its
        size. Idempotent: a version already prepared by this processor version from the
        same bytes is left untouched. Returns counts and fixed words only."""
        if self._processing is None or self._processor is None:
            return {"outcome": "unavailable"}
        version = await self._media.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        asset = (
            await self._media.get_asset(
                workspace_id=workspace_id, project_id=project_id, asset_id=version.asset_id
            )
            if version is not None
            else None
        )
        if version is None or asset is None or version.retired_revision is not None:
            return {"outcome": "gone"}
        if asset.lifecycle is not AssetLifecycle.ACTIVE:
            return {"outcome": "gone"}
        if version.content_type not in PICTURE_TYPES:
            return {"outcome": "not_a_picture"}
        state = await self._processing.get_processing(
            workspace_id=workspace_id, project_id=project_id, version_id=version.id
        )
        if (
            state is not None
            and state.status is ProcessingStatus.READY
            and state.is_current(checksum=version.checksum, processor_version=PROCESSOR_VERSION)
        ):
            copies = (
                await self._processing.list_variants(
                    workspace_id=workspace_id, project_id=project_id, version_ids=[version.id]
                )
            ).get(version.id, [])
            if {copy.kind.value for copy in copies} >= set(VARIANT_SIZES):
                self._count("already_done")
                return {"outcome": "already_done"}
        if state is None or not state.is_current(
            checksum=version.checksum, processor_version=PROCESSOR_VERSION
        ):
            await self._processing.queue(
                workspace_id=workspace_id,
                project_id=project_id,
                version_id=version.id,
                asset_id=version.asset_id,
                checksum=version.checksum,
                processor_version=PROCESSOR_VERSION,
                job_id=getattr(getattr(ctx, "job", None), "id", None),
            )
        await self._processing.start(
            workspace_id=workspace_id, project_id=project_id, version_id=version.id
        )
        started = utcnow()
        stored: list[str] = []
        try:
            try:
                data = self._objects.open(
                    version.object_key, workspace_id=workspace_id, project_id=project_id
                )
            except Exception:  # noqa: BLE001 - a missing original is a fixed failure
                return await self._failed(workspace_id, project_id, version.id, "missing_original")
            if hashlib.sha256(data).hexdigest() != version.checksum:
                return await self._failed(workspace_id, project_id, version.id, "damaged")
            if ctx is not None:
                await ctx.step("opening", 30)
            holding: AbstractAsyncContextManager[Any] = (
                ctx.holding() if ctx is not None else nullcontext()
            )
            try:
                async with holding:
                    processed = await self._processor.process(data)
            except ImageRejected as rejected:
                code = rejected.code if rejected.code in _FAILURES else "processor_failed"
                return await self._failed(workspace_id, project_id, version.id, code)
            if ctx is not None:
                await ctx.step("storing", 80)
            variants: list[MediaVariant] = []
            for rendition in processed.renditions:
                stat = self._objects.put(
                    rendition.data,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    content_type=rendition.content_type,
                )
                stored.append(stat.key)
                variants.append(
                    MediaVariant(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        asset_version_id=version.id,
                        kind=VariantKind(rendition.kind),
                        object_key=stat.key,
                        content_type=stat.content_type,
                        width=rendition.width,
                        height=rendition.height,
                        byte_length=stat.byte_length,
                        checksum=stat.checksum,
                        source_checksum=version.checksum,
                        processor_version=PROCESSOR_VERSION,
                    )
                )
            whole = MediaSegment(
                workspace_id=workspace_id,
                project_id=project_id,
                id=f"seg_{uuid7()}",
                asset_id=version.asset_id,
                asset_version_id=version.id,
                kind=SegmentKind.WHOLE,
                x=0,
                y=0,
                width=processed.width,
                height=processed.height,
                content_hash=version.checksum,
                processor_version=PROCESSOR_VERSION,
            )
            if await self._deleted_meanwhile(workspace_id, project_id, version.id):
                # Its picture was deleted while it was being prepared: keep nothing.
                await self._drop_copies(workspace_id, project_id, version.id, stored)
                return {"outcome": "gone"}
            replaced = await self._processing.complete(
                workspace_id=workspace_id,
                project_id=project_id,
                version_id=version.id,
                width=processed.width,
                height=processed.height,
                detected_format=processed.detected_format,
                variants=variants,
                whole=whole,
            )
        except BaseException:
            # Nothing half-made is left behind: copies stored before the failure go.
            for key in stored:
                self._objects.delete(key, workspace_id=workspace_id, project_id=project_id)
            if not stored:
                # Unknown failures still leave a retryable, plain state (not "processing").
                try:
                    current = await self._processing.get_processing(
                        workspace_id=workspace_id, project_id=project_id, version_id=version.id
                    )
                    if current is not None and current.status is ProcessingStatus.PROCESSING:
                        await self._processing.fail(
                            workspace_id=workspace_id,
                            project_id=project_id,
                            version_id=version.id,
                            failure_code="processor_failed",
                        )
                except Exception:  # noqa: BLE001 - the original error is the one raised
                    pass
            raise
        for key in replaced:
            self._objects.delete(key, workspace_id=workspace_id, project_id=project_id)
        if await self._deleted_meanwhile(workspace_id, project_id, version.id):
            # Deleted between the last look and the copies being recorded: remove them.
            await self._drop_copies(workspace_id, project_id, version.id, [])
            return {"outcome": "gone"}
        metrics.observe(
            "media_processing_seconds",
            "Time to prepare one picture, from start to stored copies.",
            (utcnow() - started).total_seconds(),
        )
        self._count("ready")
        return {
            "outcome": "ready",
            "width": processed.width,
            "height": processed.height,
            "copies": len(variants),
        }

    async def _deleted_meanwhile(self, workspace_id: str, project_id: str, version_id: str) -> bool:
        """Whether a version's file, or its picture, has been deleted (AST-008)."""
        version = await self._media.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        if version is None or version.deleted_at is not None:
            return True
        asset = await self._media.get_asset(
            workspace_id=workspace_id, project_id=project_id, asset_id=version.asset_id
        )
        return asset is None or asset.lifecycle is AssetLifecycle.DELETED

    async def _drop_copies(
        self, workspace_id: str, project_id: str, version_id: str, stored: list[str]
    ) -> None:
        """Remove copies just stored and any recorded for a deleted version."""
        assert self._processing is not None
        recorded = await self._processing.remove_variants(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        for key in {*stored, *recorded}:
            self._objects.delete(key, workspace_id=workspace_id, project_id=project_id)
        self._count("gone")

    async def _failed(
        self, workspace_id: str, project_id: str, version_id: str, code: str
    ) -> dict[str, Any]:
        assert self._processing is not None
        await self._processing.fail(
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=version_id,
            failure_code=code,
        )
        self._count("failed", code)
        # The code only: never the file's name, its bytes or a decoder's words.
        log_event(logger, "media.process.failed", level=logging.WARNING, failure_code=code)
        return {"outcome": "failed", "failure_code": code}

    @staticmethod
    def _count(outcome: str, code: str = "none") -> None:
        metrics.increment(
            "media_processing_total",
            "Pictures prepared, by outcome and failure code.",
            outcome=outcome,
            failure_code=code,
        )

    # ------------------------------------------------ operations (OPS-007, AST-008)
    async def processing_lag(self) -> dict[str, Any] | None:
        """How many pictures wait to be prepared and the oldest wait; counts only."""
        if self._processing is None:
            return None
        return await self._processing.lag()

    async def queue_backfill(
        self,
        *,
        workspace_id: str | None = None,
        project_id: str | None = None,
        limit: int = 500,
        include_failed: bool = False,
        local_actor: str | None = None,
    ) -> MaintenanceReport:
        """Queue every current picture not yet prepared by this processor version (an
        operator command). Each is queued as the person who added it; on a local
        install as the local user."""
        report = MaintenanceReport()
        if not self.processing_enabled:
            report.skipped["processing_off"] = 1
            return report
        assert self._processing is not None
        pending = await self._processing.pending_versions(
            processor_version=PROCESSOR_VERSION,
            content_types=sorted(PICTURE_TYPES),
            workspace_id=workspace_id,
            project_id=project_id,
            limit=limit,
            include_failed=include_failed,
        )
        for item in pending:
            actor = local_actor if self._local_mode and local_actor else item.actor_user_id
            if not actor:
                report.skipped["no_actor"] = report.skipped.get("no_actor", 0) + 1
                continue
            version = await self._media.get_version(
                workspace_id=item.workspace_id,
                project_id=item.project_id,
                version_id=item.asset_version_id,
            )
            if version is None:
                continue
            scope = AccessScope(
                actor_user_id=actor,
                auth_mode=AuthMode.LOCAL if self._local_mode else AuthMode.HOSTED,
                correlation_id=f"media-backfill-{uuid7()}",
                workspace_id=item.workspace_id,
                project_id=item.project_id,
            )
            state = await self._processing.get_processing(
                workspace_id=item.workspace_id,
                project_id=item.project_id,
                version_id=item.asset_version_id,
            )
            try:
                await self._queue(
                    scope,
                    item.workspace_id,
                    item.project_id,
                    version,
                    attempt=(state.attempts if state is not None else 0) + 1,
                )
            except Exception as error:  # noqa: BLE001 - one refusal never stops the rest
                reason = type(error).__name__.lower()[:40]
                report.skipped[reason] = report.skipped.get(reason, 0) + 1
                continue
            report.queued += 1
        return report

    async def queue_project(self, scope: AccessScope, workspace_id: str, project_id: str) -> int:
        """Queue every picture of one story not yet prepared (after a restore)."""
        if not self.processing_enabled:
            return 0
        assert self._processing is not None
        pending = await self._processing.pending_versions(
            processor_version=PROCESSOR_VERSION,
            content_types=sorted(PICTURE_TYPES),
            workspace_id=workspace_id,
            project_id=project_id,
        )
        queued = 0
        for item in pending:
            version = await self._media.get_version(
                workspace_id=workspace_id, project_id=project_id, version_id=item.asset_version_id
            )
            if version is None:
                continue
            await self._queue_quietly(scope, workspace_id, project_id, version)
            queued += 1
        return queued

    async def variant_object_keys(self, workspace_id: str, project_id: str) -> set[str]:
        """Object keys that hold smaller copies (rebuildable; left out of an archive)."""
        if self._processing is None:
            return set()
        return await self._processing.variant_object_keys(
            workspace_id=workspace_id, project_id=project_id
        )

    async def reconcile_orphans(
        self,
        *,
        workspace_id: str | None = None,
        project_id: str | None = None,
        dry_run: bool = True,
        grace: timedelta | None = None,
    ) -> MaintenanceReport:
        """Find stored pictures no picture version or copy refers to, older than the grace
        period, and (unless ``dry_run``) remove them. A file newer than the grace period
        may be an upload still on its way, so it is never touched. Only picture files are
        considered: other kinds in the project's store are not this service's to remove."""
        report = MaintenanceReport()
        if self._processing is None:
            report.skipped["processing_off"] = 1
            return report
        cutoff = utcnow() - (grace if grace is not None else self._orphan_grace)
        if workspace_id is not None and project_id is not None:
            projects = [(workspace_id, project_id)]
        else:
            projects = [
                pair
                for pair in await self._processing.projects_with_media()
                if workspace_id is None or pair[0] == workspace_id
            ]
        for ws, pid in projects:
            report.projects += 1
            known = await self._processing.known_object_keys(workspace_id=ws, project_id=pid)
            for stat in self._objects.list_objects(workspace_id=ws, project_id=pid):
                if stat.key in known or stat.created_at > cutoff:
                    continue
                # Only files of the kinds this service writes (originals and copies) are
                # its own to remove; anything else in the project's store (a seeded
                # drawing, an export, a non-picture file an archive brought) is left alone.
                if stat.content_type not in _OWNED_TYPES:
                    continue
                report.orphans_found += 1
                report.orphan_bytes += stat.byte_length
                if not dry_run and self._objects.delete(stat.key, workspace_id=ws, project_id=pid):
                    report.orphans_removed += 1
        log_event(
            logger,
            "media.reconcile",
            projects=report.projects,
            orphans_found=report.orphans_found,
            orphans_removed=report.orphans_removed,
            dry_run=dry_run,
        )
        return report

    async def remove_retired_copies(
        self, *, dry_run: bool = True, limit: int = 1000
    ) -> MaintenanceReport:
        """Remove the smaller copies of retired versions and retired pictures. The
        original stays (its history is the author's); a copy can always be made again."""
        report = MaintenanceReport()
        if self._processing is None:
            report.skipped["processing_off"] = 1
            return report
        for ws, pid, version_id in await self._processing.retired_with_variants(limit=limit):
            if dry_run:
                copies = (
                    await self._processing.list_variants(
                        workspace_id=ws, project_id=pid, version_ids=[version_id]
                    )
                ).get(version_id, [])
                report.retired_copies_removed += len(copies)
                continue
            for key in await self._processing.remove_variants(
                workspace_id=ws, project_id=pid, version_id=version_id
            ):
                self._objects.delete(key, workspace_id=ws, project_id=pid)
                report.retired_copies_removed += 1
        return report

    # ------------------------------------------------------------------- links
    async def list_links(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        asset_id: str | None = None,
        target_id: str | None = None,
        include_retired: bool = False,
    ) -> list[AssetLink]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._reader.list_links(
            workspace_id=workspace_id,
            project_id=project_id,
            asset_id=asset_id,
            target_record_id=target_id,
            include_retired=include_retired,
        )

    async def create_link(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        asset_id: str,
        target_id: str,
        role: str = LinkRole.EVIDENCE_FOR.value,
        asset_version_id: str | None = None,
        target_record_type: str = "entity",
        expected_revision: int,
        idempotency_key: str = "",
    ) -> tuple[CommitResult, AssetLink]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not is_link_role(role):
            raise ValidationError(f"Unknown link role {role!r}.")
        link_id = format_asset_link_id()
        operation = LinkAssetOp(
            id=link_id,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            target_record_type=target_record_type,
            target_id=target_id,
            role=role,
        )
        result = await self._commit(
            scope, workspace_id, project_id, operation, expected_revision, idempotency_key
        )
        link = await self._reader.get_link(
            workspace_id=workspace_id, project_id=project_id, link_id=link_id
        )
        assert link is not None
        return result, link

    async def retire_link(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        link_id: str,
        *,
        expected_revision: int,
    ) -> CommitResult:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        existing = await self._reader.get_link(
            workspace_id=workspace_id, project_id=project_id, link_id=link_id
        )
        if existing is None:
            raise NotFoundError("Asset link not found.")
        operation = RetireAssetLinkOp(id=link_id)
        return await self._commit(scope, workspace_id, project_id, operation, expected_revision, "")

    async def _commit(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        operation: CanonOperation,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommitResult:
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=[operation],
            expected_head=expected_revision,
            idempotency_key=idempotency_key,
        )
        return await self._mutation.commit(scope, mutation.id)


def _size(byte_count: int) -> str:
    """A size in plain words: "512 KB", "3.2 MB", "2 GB"."""
    if byte_count < 1024 * 1024:
        return f"{max(1, round(byte_count / 1024))} KB"
    if byte_count < 1024 * 1024 * 1024:
        return f"{byte_count / (1024 * 1024):.1f} MB".replace(".0 MB", " MB")
    return f"{byte_count / (1024 * 1024 * 1024):.1f} GB".replace(".0 GB", " GB")


def _region_hash(checksum: str, x: int, y: int, width: int, height: int) -> str:
    """What a marked part covers, so the search index embeds it again only when it moved."""
    return hashlib.sha256(f"{checksum}:{x}:{y}:{width}:{height}".encode()).hexdigest()


def format_asset_link_id() -> str:
    from backend.domain.canon.ids import format_canon_id

    return format_canon_id("asset_link", str(uuid7()))


__all__ = [
    "DeletedPicture",
    "FoundPicture",
    "PictureSearch",
    "PictureTooLarge",
    "PictureTooSlow",
    "ReceivedUpload",
    "query_words",
    "receive_picture",
    "MEDIA_PROCESS_KIND",
    "LibraryPicture",
    "MaintenanceReport",
    "MediaService",
    "ProcessingView",
    "VersionsRemovedHook",
]
