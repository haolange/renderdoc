"""
Content-addressable artifact storage for RDX-MCP.

Stores files (images, shader dumps, readback data, etc.) using their SHA256 hash
as the key.  The on-disk layout mirrors git object storage:

    <store_root>/<sha256[:2]>/<sha256[2:4]>/<sha256>

Artifacts are referenced through ``rdx://`` URIs and described by
:class:`rdx.models.ArtifactRef` instances.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import aiofiles
import aiofiles.os

from rdx.models import ArtifactRef

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 1 << 16  # 64 KiB read chunks


def _shard_path(root: Path, sha256: str) -> Path:
    """Return the two-level shard path for a given hash.

    Example::

        _shard_path(Path("/store"), "abcdef01...") -> Path("/store/ab/cd/abcdef01...")
    """
    return root / sha256[:2] / sha256[2:4] / sha256


def _build_uri(sha256: str) -> str:
    """Build the canonical ``rdx://`` URI for an artifact."""
    return f"rdx://artifacts/{sha256[:2]}/{sha256[2:4]}/{sha256}"


def _sha256_bytes(data: bytes) -> str:
    """Compute hex-encoded SHA256 of *data*."""
    return hashlib.sha256(data).hexdigest()


async def _sha256_file(path: Path) -> str:
    """Compute hex-encoded SHA256 of the file at *path* without reading
    the entire file into memory at once."""
    h = hashlib.sha256()
    async with aiofiles.open(path, "rb") as fh:
        while True:
            chunk = await fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# ArtifactStore
# ---------------------------------------------------------------------------


class ArtifactStore:
    """Directory-backed content-addressable store (CAS).

    Each blob is stored exactly once under a two-level shard directory keyed
    by its SHA256 digest.  Metadata is carried in-memory by the returned
    :class:`ArtifactRef`; the store itself is intentionally simple and
    stateless so that it can be safely used from multiple async tasks.

    Parameters
    ----------
    root:
        Root directory for the store.  Created on first write if it does not
        already exist.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    # -- properties ---------------------------------------------------------

    @property
    def root(self) -> Path:
        """Root directory of the store."""
        return self._root

    # -- public API ---------------------------------------------------------

    async def store(
        self,
        data: bytes,
        mime: str = "application/octet-stream",
        meta: Optional[Dict[str, Any]] = None,
    ) -> ArtifactRef:
        """Store raw bytes and return an :class:`ArtifactRef`.

        If an artifact with the same SHA256 already exists on disk the write
        is skipped (content-addressable deduplication).

        Parameters
        ----------
        data:
            The raw bytes to store.
        mime:
            MIME type for the artifact (e.g. ``image/png``).
        meta:
            Arbitrary metadata dict attached to the returned reference.

        Returns
        -------
        ArtifactRef
            A reference containing the ``rdx://`` URI, SHA256, MIME type,
            byte length, and metadata.
        """
        sha = _sha256_bytes(data)
        dest = _shard_path(self._root, sha)

        if not dest.exists():
            await aiofiles.os.makedirs(dest.parent, exist_ok=True)
            # Write to a temporary file first, then rename for atomicity.
            tmp = dest.with_suffix(".tmp")
            try:
                async with aiofiles.open(tmp, "wb") as fh:
                    await fh.write(data)
                await aiofiles.os.rename(tmp, dest)
            except BaseException:
                # Clean up partial write on any failure.
                try:
                    await aiofiles.os.remove(tmp)
                except OSError:
                    pass
                raise
            logger.debug("Stored artifact %s (%d bytes)", sha[:12], len(data))
        else:
            logger.debug("Artifact %s already present, skipping write", sha[:12])

        return ArtifactRef(
            uri=_build_uri(sha),
            sha256=sha,
            mime=mime,
            bytes=len(data),
            meta=meta or {},
        )

    async def store_file(
        self,
        path: Path,
        mime: str = "application/octet-stream",
        meta: Optional[Dict[str, Any]] = None,
    ) -> ArtifactRef:
        """Hash a file on disk and store it into the CAS.

        For large files this streams the hash computation so that the entire
        file does not need to reside in memory at once.  The actual file
        content is then copied into the shard directory.

        Parameters
        ----------
        path:
            Filesystem path to the source file.
        mime:
            MIME type for the artifact.
        meta:
            Arbitrary metadata dict attached to the returned reference.

        Returns
        -------
        ArtifactRef

        Raises
        ------
        FileNotFoundError
            If *path* does not exist.
        """
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Source file does not exist: {path}")

        sha = await _sha256_file(path)
        dest = _shard_path(self._root, sha)
        file_size = path.stat().st_size

        if not dest.exists():
            await aiofiles.os.makedirs(dest.parent, exist_ok=True)
            tmp = dest.with_suffix(".tmp")
            try:
                async with aiofiles.open(path, "rb") as src_fh, \
                           aiofiles.open(tmp, "wb") as dst_fh:
                    while True:
                        chunk = await src_fh.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        await dst_fh.write(chunk)
                await aiofiles.os.rename(tmp, dest)
            except BaseException:
                try:
                    await aiofiles.os.remove(tmp)
                except OSError:
                    pass
                raise
            logger.debug(
                "Stored artifact %s from %s (%d bytes)", sha[:12], path, file_size
            )
        else:
            logger.debug("Artifact %s already present, skipping copy", sha[:12])

        return ArtifactRef(
            uri=_build_uri(sha),
            sha256=sha,
            mime=mime,
            bytes=file_size,
            meta=meta or {},
        )

    async def retrieve(self, sha256: str) -> bytes:
        """Retrieve the raw bytes for an artifact by its SHA256 digest.

        Parameters
        ----------
        sha256:
            Hex-encoded SHA256 digest.

        Returns
        -------
        bytes

        Raises
        ------
        FileNotFoundError
            If no artifact with that hash is in the store.
        """
        dest = _shard_path(self._root, sha256)
        if not dest.is_file():
            raise FileNotFoundError(
                f"Artifact not found in store: {sha256}"
            )
        async with aiofiles.open(dest, "rb") as fh:
            return await fh.read()

    def get_path(self, sha256: str) -> Path:
        """Return the filesystem path where an artifact would be stored.

        This does **not** guarantee the file exists; use :meth:`exists` to
        check first if needed.

        Parameters
        ----------
        sha256:
            Hex-encoded SHA256 digest.

        Returns
        -------
        Path
        """
        return _shard_path(self._root, sha256)

    def exists(self, sha256: str) -> bool:
        """Check whether an artifact with the given hash is present.

        Parameters
        ----------
        sha256:
            Hex-encoded SHA256 digest.

        Returns
        -------
        bool
        """
        return _shard_path(self._root, sha256).is_file()
