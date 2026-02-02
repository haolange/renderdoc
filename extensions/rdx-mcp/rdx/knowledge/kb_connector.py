"""
Knowledge base connector for RAG (Retrieval-Augmented Generation) integration.

Provides a simple interface to search and retrieve knowledge from:

- **Local documentation files** -- Markdown, plain-text, and header files are
  recursively indexed and searchable via BM25 scoring.
- **Code repositories** -- C++, HLSL (``.usf`` / ``.ush``), and Python sources
  are indexed alongside documentation so that implementation details surface
  in search results.
- **Unreal Engine module mapping** -- A specialised heuristic mapper
  (:func:`map_shader_to_ue_module`) translates shader/binding patterns into
  likely UE rendering modules, aiding root-cause analysis.

The connector builds a lightweight inverted index over configured directories,
persisted in SQLite.  Queries are scored using the Okapi BM25 relevance
function.  An optional ``project_id`` tag lets multiple projects share a
single index while keeping results filterable.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import time
import uuid
from asyncio import get_running_loop
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class KBSearchResult:
    """A single search result from the knowledge base.

    Attributes
    ----------
    doc_id:
        Internal document identifier in the index.
    path:
        Filesystem path to the source file.
    score:
        BM25 relevance score (higher is better).
    snippet:
        Short context string around the first match position.
    line_number:
        1-based line number of the first match.
    """

    doc_id: str
    path: str
    score: float
    snippet: str
    line_number: int


@dataclass
class UEModuleMapping:
    """Mapping from a shader/binding pattern to an Unreal Engine module.

    Attributes
    ----------
    module_name:
        Canonical UE module or pass name (e.g. ``"BasePassPixelShader"``).
    confidence:
        Heuristic confidence in ``[0.0, 1.0]``.
    evidence:
        Human-readable reasons supporting this mapping.
    usf_files:
        Potential ``.usf`` / ``.ush`` files associated with the module.
    material_nodes:
        Potential material-graph node names relevant to this module.
    """

    module_name: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    usf_files: List[str] = field(default_factory=list)
    material_nodes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# File extensions indexed by the KB connector.
_INDEXABLE_EXTENSIONS: Set[str] = {
    ".md", ".txt", ".h", ".cpp", ".usf", ".ush", ".py",
}

# BM25 tuning parameters (Okapi BM25 defaults).
_BM25_K1 = 1.5
_BM25_B = 0.75

# Snippet context: number of characters either side of the first match.
_SNIPPET_CONTEXT = 120

# Tokeniser pattern: word-like tokens of length >= 2.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def _new_id(prefix: str) -> str:
    """Generate a short unique identifier with the given *prefix*."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _tokenize(text: str) -> List[str]:
    """Extract lowercase word tokens from *text*.

    Tokens are sequences of ``[A-Za-z0-9_]`` starting with a letter or
    underscore, with a minimum length of 2.
    """
    return [m.group().lower() for m in _TOKEN_RE.finditer(text)]


# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

_CREATE_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    project_id   TEXT NOT NULL DEFAULT '',
    file_type    TEXT NOT NULL DEFAULT '',
    token_count  INTEGER NOT NULL DEFAULT 0,
    mtime        REAL NOT NULL DEFAULT 0,
    indexed_at   REAL NOT NULL DEFAULT 0
);
"""

_CREATE_POSTINGS = """
CREATE TABLE IF NOT EXISTS postings (
    term      TEXT NOT NULL,
    doc_id    TEXT NOT NULL,
    positions TEXT NOT NULL DEFAULT '[]',
    tf        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (term, doc_id)
);
"""

_CREATE_CORPUS_STATS = """
CREATE TABLE IF NOT EXISTS corpus_stats (
    key   TEXT PRIMARY KEY,
    value REAL NOT NULL DEFAULT 0
);
"""

_CREATE_POSTING_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_postings_term ON postings(term);"
)


# ---------------------------------------------------------------------------
# KBConnector
# ---------------------------------------------------------------------------

class KBConnector:
    """Knowledge base connector with BM25 search and UE module mapping.

    Builds a lightweight inverted index over local documentation and source
    files, persisted in SQLite.  Queries are scored using the Okapi BM25
    relevance function.

    Parameters
    ----------
    index_dirs:
        Directories to index on :meth:`initialize`.  May be ``None``
        if directories will be added later via :meth:`index_directory`.
    db_path:
        Path to the SQLite database used for the inverted index.  When
        ``None`` an in-memory database is used (useful for tests).
    """

    def __init__(
        self,
        index_dirs: Optional[List[Path]] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self._index_dirs: List[Path] = list(index_dirs or [])
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # -- lifecycle ----------------------------------------------------------

    async def initialize(self) -> None:
        """Create tables and index all configured directories.

        This is safe to call multiple times; existing documents whose
        modification time has not changed are skipped.
        """
        loop = get_running_loop()
        self._conn = await loop.run_in_executor(None, self._open_db)

        for d in self._index_dirs:
            await self.index_directory(d)

        logger.info(
            "KBConnector initialized (dirs=%d, db=%s)",
            len(self._index_dirs),
            self._db_path or ":memory:",
        )

    def _open_db(self) -> sqlite3.Connection:
        """Open the SQLite connection and create tables (sync)."""
        if self._db_path is not None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        else:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(_CREATE_DOCUMENTS)
        conn.execute(_CREATE_POSTINGS)
        conn.execute(_CREATE_CORPUS_STATS)
        conn.execute(_CREATE_POSTING_INDEX)
        conn.commit()
        return conn

    def _ensure_conn(self) -> sqlite3.Connection:
        """Return the active connection or raise if not initialised."""
        if self._conn is None:
            raise RuntimeError(
                "KBConnector has not been initialized. "
                "Call await connector.initialize() first."
            )
        return self._conn

    # -- indexing -----------------------------------------------------------

    async def index_directory(
        self,
        dir_path: Path,
        project_id: str = "",
    ) -> None:
        """Walk *dir_path* recursively and index all supported files.

        Files whose modification time matches the stored value are
        skipped.  New or changed files are fully re-indexed.

        Parameters
        ----------
        dir_path:
            Root directory to walk.
        project_id:
            Project identifier attached to every document from this
            directory.
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            logger.warning("Index directory does not exist: %s", dir_path)
            return

        loop = get_running_loop()
        await loop.run_in_executor(
            None,
            partial(self._index_directory_sync, dir_path, project_id),
        )

    def _index_directory_sync(
        self,
        dir_path: Path,
        project_id: str,
    ) -> None:
        """Synchronous indexing implementation (run in executor)."""
        conn = self._ensure_conn()
        indexed_count = 0

        for root, _dirs, files in os.walk(dir_path):
            for fname in files:
                fpath = Path(root) / fname
                if fpath.suffix.lower() not in _INDEXABLE_EXTENSIONS:
                    continue

                try:
                    mtime = fpath.stat().st_mtime
                except OSError:
                    continue

                # Check whether this file is already indexed and unchanged.
                existing = conn.execute(
                    "SELECT doc_id, mtime FROM documents WHERE path = ?",
                    (str(fpath),),
                ).fetchone()

                if existing is not None and existing["mtime"] == mtime:
                    continue

                # Read file content.
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    logger.debug("Could not read %s, skipping", fpath)
                    continue

                doc_id = (
                    existing["doc_id"]
                    if existing is not None
                    else _new_id("doc")
                )
                tokens = _tokenize(content)
                token_count = len(tokens)

                # Upsert document metadata.
                conn.execute(
                    """
                    INSERT INTO documents
                        (doc_id, path, project_id, file_type,
                         token_count, mtime, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        project_id  = excluded.project_id,
                        file_type   = excluded.file_type,
                        token_count = excluded.token_count,
                        mtime       = excluded.mtime,
                        indexed_at  = excluded.indexed_at
                    """,
                    (
                        doc_id,
                        str(fpath),
                        project_id,
                        fpath.suffix.lower().lstrip("."),
                        token_count,
                        mtime,
                        time.time(),
                    ),
                )

                # Remove stale postings for this document.
                conn.execute(
                    "DELETE FROM postings WHERE doc_id = ?",
                    (doc_id,),
                )

                # Build term -> positions mapping.
                term_positions: Dict[str, List[int]] = {}
                for pos, tok in enumerate(tokens):
                    term_positions.setdefault(tok, []).append(pos)

                # Insert postings in bulk.
                conn.executemany(
                    """
                    INSERT INTO postings (term, doc_id, positions, tf)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (term, doc_id, json.dumps(positions), len(positions))
                        for term, positions in term_positions.items()
                    ],
                )

                indexed_count += 1

        # Update corpus-level statistics.
        total_docs = conn.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0]
        avg_dl = conn.execute(
            "SELECT COALESCE(AVG(token_count), 0) FROM documents"
        ).fetchone()[0]

        conn.execute(
            """
            INSERT INTO corpus_stats (key, value)
            VALUES ('total_docs', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (float(total_docs),),
        )
        conn.execute(
            """
            INSERT INTO corpus_stats (key, value)
            VALUES ('avg_dl', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (float(avg_dl),),
        )

        conn.commit()

        if indexed_count > 0:
            logger.info(
                "Indexed %d files in %s (total docs: %d)",
                indexed_count, dir_path, total_docs,
            )

    async def refresh_index(self) -> None:
        """Re-index all previously configured directories.

        Changed and new files are picked up; unchanged files are skipped.
        """
        for d in self._index_dirs:
            await self.index_directory(d)

    # -- search -------------------------------------------------------------

    async def search(
        self,
        query: str,
        filters: Optional[Dict[str, str]] = None,
        limit: int = 10,
    ) -> List[KBSearchResult]:
        """Search the indexed corpus using BM25 scoring.

        Parameters
        ----------
        query:
            Free-text search query.
        filters:
            Optional key/value filters.  Recognised keys:

            - ``file_type`` -- extension without dot (e.g. ``"cpp"``).
            - ``path_prefix`` -- only documents under this path prefix.
            - ``project_id`` -- exact match on the project tag.
        limit:
            Maximum number of results to return.

        Returns
        -------
        list[KBSearchResult]
            Results sorted by BM25 score descending.
        """
        tokens = _tokenize(query)
        if not tokens:
            return []

        loop = get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(
                self._search_sync,
                tokens=tokens,
                filters=filters or {},
                limit=limit,
            ),
        )

    def _search_sync(
        self,
        tokens: List[str],
        filters: Dict[str, str],
        limit: int,
    ) -> List[KBSearchResult]:
        """BM25 search implementation (sync, run in executor)."""
        conn = self._ensure_conn()

        # Corpus statistics.
        total_docs_row = conn.execute(
            "SELECT value FROM corpus_stats WHERE key = 'total_docs'"
        ).fetchone()
        avg_dl_row = conn.execute(
            "SELECT value FROM corpus_stats WHERE key = 'avg_dl'"
        ).fetchone()

        total_docs = total_docs_row["value"] if total_docs_row else 0.0
        avg_dl = avg_dl_row["value"] if avg_dl_row else 1.0

        if total_docs == 0:
            return []

        # Accumulate BM25 scores across query terms.
        doc_scores: Dict[str, float] = {}
        doc_first_pos: Dict[str, int] = {}
        unique_tokens = set(tokens)

        # Pre-fetch document lengths to avoid repeated lookups.
        doc_lengths: Dict[str, float] = {}
        for row in conn.execute("SELECT doc_id, token_count FROM documents"):
            doc_lengths[row["doc_id"]] = float(row["token_count"])

        for term in unique_tokens:
            postings = conn.execute(
                "SELECT doc_id, tf, positions FROM postings WHERE term = ?",
                (term,),
            ).fetchall()

            df = len(postings)
            if df == 0:
                continue

            # IDF component (with smoothing to avoid negative values).
            idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1.0)

            for posting in postings:
                doc_id = posting["doc_id"]
                tf = posting["tf"]
                positions = json.loads(posting["positions"])

                dl = doc_lengths.get(doc_id, avg_dl)

                # BM25 term-frequency normalisation.
                tf_norm = (tf * (_BM25_K1 + 1)) / (
                    tf + _BM25_K1 * (
                        1.0 - _BM25_B + _BM25_B * dl / max(avg_dl, 1.0)
                    )
                )

                doc_scores[doc_id] = (
                    doc_scores.get(doc_id, 0.0) + idf * tf_norm
                )

                # Track the earliest token position per document (for snippets).
                if positions:
                    first = positions[0]
                    if (
                        doc_id not in doc_first_pos
                        or first < doc_first_pos[doc_id]
                    ):
                        doc_first_pos[doc_id] = first

        # Apply post-filters and build results.
        filter_type = filters.get("file_type")
        filter_prefix = filters.get("path_prefix")
        filter_project = filters.get("project_id")

        results: List[KBSearchResult] = []
        for doc_id, score in sorted(
            doc_scores.items(), key=lambda kv: kv[1], reverse=True,
        ):
            doc_row = conn.execute(
                "SELECT path, project_id, file_type "
                "FROM documents WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()

            if doc_row is None:
                continue

            doc_path = doc_row["path"]

            if filter_type and doc_row["file_type"] != filter_type:
                continue
            if filter_prefix and not doc_path.startswith(filter_prefix):
                continue
            if filter_project and doc_row["project_id"] != filter_project:
                continue

            # Build snippet around first match position.
            snippet, line_number = self._extract_snippet(
                doc_path, doc_first_pos.get(doc_id, 0), tokens,
            )

            results.append(KBSearchResult(
                doc_id=doc_id,
                path=doc_path,
                score=score,
                snippet=snippet,
                line_number=line_number,
            ))

            if len(results) >= limit:
                break

        return results

    @staticmethod
    def _extract_snippet(
        doc_path: str,
        first_token_pos: int,
        query_tokens: List[str],
    ) -> Tuple[str, int]:
        """Read the file and extract a context snippet around the first match.

        Parameters
        ----------
        doc_path:
            Filesystem path to the document.
        first_token_pos:
            Token-level offset of the first match (used as a fallback).
        query_tokens:
            Lowercased query tokens to locate in the text.

        Returns
        -------
        tuple[str, int]
            ``(snippet, line_number)`` where *line_number* is 1-based.
        """
        try:
            content = Path(doc_path).read_text(
                encoding="utf-8", errors="replace",
            )
        except OSError:
            return ("", 0)

        # Find the character position of the earliest query token occurrence.
        lower_content = content.lower()
        first_char_pos = -1
        for tok in query_tokens:
            pos = lower_content.find(tok)
            if pos >= 0 and (first_char_pos < 0 or pos < first_char_pos):
                first_char_pos = pos

        if first_char_pos < 0:
            first_char_pos = 0

        start = max(0, first_char_pos - _SNIPPET_CONTEXT)
        end = min(len(content), first_char_pos + _SNIPPET_CONTEXT)
        snippet = content[start:end].replace("\n", " ").strip()

        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."

        # Compute 1-based line number.
        line_number = content[:first_char_pos].count("\n") + 1

        return snippet, line_number

    # -- document retrieval -------------------------------------------------

    async def get_document(
        self,
        doc_id: str,
        span: Optional[Tuple[int, int]] = None,
    ) -> str:
        """Return document content by ``doc_id``.

        Parameters
        ----------
        doc_id:
            Identifier of the document in the index.
        span:
            Optional ``(start_line, end_line)`` tuple (1-based, inclusive)
            to return only a subset of lines.

        Returns
        -------
        str
            The full document content, or the requested span of lines.

        Raises
        ------
        FileNotFoundError
            If the document is not in the index or the underlying file
            is missing.
        """
        loop = get_running_loop()
        path = await loop.run_in_executor(
            None,
            partial(self._get_doc_path, doc_id),
        )
        if path is None:
            raise FileNotFoundError(
                f"Document not found in index: {doc_id}"
            )

        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise FileNotFoundError(
                f"Underlying file missing for document {doc_id}: {path}"
            ) from exc

        if span is not None:
            start_line, end_line = span
            lines = content.splitlines(keepends=True)
            # Convert to 0-based indices; clamp to valid range.
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)
            content = "".join(lines[start_idx:end_idx])

        return content

    def _get_doc_path(self, doc_id: str) -> Optional[str]:
        """Resolve a doc_id to its filesystem path (sync)."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT path FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        return row["path"] if row else None


# ---------------------------------------------------------------------------
# UE Module Mapper -- heuristic tables
# ---------------------------------------------------------------------------

# Resource name patterns -> UE module metadata.
_RESOURCE_TO_MODULE: Dict[str, Dict[str, Any]] = {
    "SceneColor": {
        "module": "PostProcessing",
        "usf": [
            "PostProcessCombineLUTs.usf",
            "PostProcessTonemap.usf",
        ],
        "nodes": ["SceneColor"],
    },
    "GBufferA": {
        "module": "BasePassPixelShader",
        "usf": [
            "BasePassPixelShader.usf",
            "DeferredShadingCommon.ush",
        ],
        "nodes": ["WorldNormal"],
    },
    "GBufferB": {
        "module": "BasePassPixelShader",
        "usf": [
            "BasePassPixelShader.usf",
            "DeferredShadingCommon.ush",
        ],
        "nodes": ["Metallic", "Specular", "Roughness"],
    },
    "GBufferC": {
        "module": "BasePassPixelShader",
        "usf": [
            "BasePassPixelShader.usf",
            "DeferredShadingCommon.ush",
        ],
        "nodes": ["BaseColor"],
    },
    "GBufferD": {
        "module": "BasePassPixelShader",
        "usf": [
            "BasePassPixelShader.usf",
            "DeferredShadingCommon.ush",
        ],
        "nodes": ["CustomData"],
    },
    "GBufferE": {
        "module": "BasePassPixelShader",
        "usf": [
            "BasePassPixelShader.usf",
            "DeferredShadingCommon.ush",
        ],
        "nodes": ["PrecomputedShadowFactors"],
    },
    "SceneDepth": {
        "module": "DepthRendering",
        "usf": [
            "DepthOnlyPixelShader.usf",
            "DepthOnlyVertexShader.usf",
        ],
        "nodes": ["SceneDepth"],
    },
    "CustomDepth": {
        "module": "CustomDepthRendering",
        "usf": ["DepthOnlyPixelShader.usf"],
        "nodes": ["CustomDepth"],
    },
    "Velocity": {
        "module": "VelocityRendering",
        "usf": ["VelocityShader.usf"],
        "nodes": ["Velocity"],
    },
    "ShadowDepth": {
        "module": "ShadowRendering",
        "usf": [
            "ShadowDepthPixelShader.usf",
            "ShadowDepthVertexShader.usf",
        ],
        "nodes": [],
    },
    "LightAttenuation": {
        "module": "LightRendering",
        "usf": ["DeferredLightPixelShaders.usf"],
        "nodes": [],
    },
    "Translucency": {
        "module": "TranslucencyLighting",
        "usf": ["TranslucencyLightingVolumeShaders.usf"],
        "nodes": ["Opacity", "TranslucencyLighting"],
    },
    "SSAO": {
        "module": "AmbientOcclusion",
        "usf": ["PostProcessAmbientOcclusion.usf"],
        "nodes": ["AmbientOcclusion"],
    },
    "SSR": {
        "module": "ScreenSpaceReflections",
        "usf": ["ScreenSpaceReflections.usf"],
        "nodes": ["ScreenSpaceReflections"],
    },
    "Fog": {
        "module": "FogRendering",
        "usf": [
            "HeightFogPixelShader.usf",
            "VolumetricFogShared.ush",
        ],
        "nodes": ["FogInput"],
    },
    "BloomSetup": {
        "module": "PostProcessing",
        "usf": ["PostProcessBloom.usf"],
        "nodes": ["BloomIntensity"],
    },
    "HZB": {
        "module": "HierarchicalZBuffer",
        "usf": ["PostProcessBuildHZB.usf"],
        "nodes": [],
    },
}

# Shader virtual-path prefixes -> UE module metadata.
_PATH_TO_MODULE: Dict[str, Dict[str, Any]] = {
    "/Engine/Shaders/Private/BasePassPixelShader": {
        "module": "BasePassPixelShader",
        "usf": ["BasePassPixelShader.usf"],
    },
    "/Engine/Shaders/Private/BasePassVertexShader": {
        "module": "BasePassVertexShader",
        "usf": ["BasePassVertexShader.usf"],
    },
    "/Engine/Shaders/Private/DeferredLightPixelShaders": {
        "module": "DeferredLighting",
        "usf": ["DeferredLightPixelShaders.usf"],
    },
    "/Engine/Shaders/Private/PostProcess": {
        "module": "PostProcessing",
        "usf": [],
    },
    "/Engine/Shaders/Private/ShadowDepth": {
        "module": "ShadowRendering",
        "usf": [
            "ShadowDepthPixelShader.usf",
            "ShadowDepthVertexShader.usf",
        ],
    },
    "/Engine/Shaders/Private/Lumen": {
        "module": "Lumen",
        "usf": [],
    },
    "/Engine/Shaders/Private/Nanite": {
        "module": "Nanite",
        "usf": [],
    },
    "/Engine/Shaders/Private/VirtualShadowMaps": {
        "module": "VirtualShadowMaps",
        "usf": [],
    },
    "/Engine/Shaders/Private/HairStrands": {
        "module": "HairStrands",
        "usf": [],
    },
    "/Engine/Shaders/Private/Substrate": {
        "module": "Substrate",
        "usf": [],
    },
}

# Material parameter names -> material-graph node names.
_MATERIAL_PARAM_TO_NODE: Dict[str, str] = {
    "BaseColor": "BaseColor",
    "Metallic": "Metallic",
    "Specular": "Specular",
    "Roughness": "Roughness",
    "EmissiveColor": "EmissiveColor",
    "Opacity": "Opacity",
    "OpacityMask": "OpacityMask",
    "Normal": "Normal",
    "WorldPositionOffset": "WorldPositionOffset",
    "SubsurfaceColor": "SubsurfaceColor",
    "AmbientOcclusion": "AmbientOcclusion",
    "Refraction": "Refraction",
    "PixelDepthOffset": "PixelDepthOffset",
}


# ---------------------------------------------------------------------------
# UE Module Mapper -- public API
# ---------------------------------------------------------------------------

async def map_shader_to_ue_module(
    shader_info: Dict[str, Any],
    bindings: List[Any],
    artifact_store: Any = None,
) -> List[UEModuleMapping]:
    """Map shader and binding patterns to Unreal Engine modules.

    Uses a set of heuristics based on resource naming conventions,
    shader source file paths, and material parameter names to produce
    a ranked list of potential UE module mappings.

    Parameters
    ----------
    shader_info:
        Dictionary describing the shader.  Expected keys include:

        - ``"source_path"`` -- virtual path to the shader source file.
        - ``"resource_names"`` -- list of bound resource names.
        - ``"stage"`` -- shader stage string (e.g. ``"ps"``, ``"cs"``).
        - ``"entry_point"`` -- shader entry-point name.
    bindings:
        List of resource binding entries.  Each element should expose
        ``resource_name`` and ``type`` as dictionary keys or object
        attributes.
    artifact_store:
        Optional artifact store for fetching shader source text if
        needed for deeper analysis.  Currently unused but reserved
        for future expansion.

    Returns
    -------
    list[UEModuleMapping]
        Possible module mappings sorted by confidence descending.
    """
    loop = get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(_map_shader_to_ue_module_sync, shader_info, bindings),
    )


def _map_shader_to_ue_module_sync(
    shader_info: Dict[str, Any],
    bindings: List[Any],
) -> List[UEModuleMapping]:
    """Synchronous implementation of UE module mapping heuristics."""
    # candidates[module_name] -> accumulated evidence dict
    candidates: Dict[str, Dict[str, Any]] = {}

    def _add_candidate(
        module: str,
        conf: float,
        reason: str,
        usf: Optional[List[str]] = None,
        nodes: Optional[List[str]] = None,
    ) -> None:
        """Accumulate evidence for a candidate module.

        Confidences are combined using the independent-evidence formula:
        ``1 - (1 - a) * (1 - b)`` so that multiple weak signals
        converge towards certainty without exceeding 1.0.
        """
        if module not in candidates:
            candidates[module] = {
                "confidence": 0.0,
                "evidence": [],
                "usf_files": set(),
                "material_nodes": set(),
            }
        entry = candidates[module]
        entry["confidence"] = 1.0 - (
            (1.0 - entry["confidence"]) * (1.0 - conf)
        )
        entry["evidence"].append(reason)
        if usf:
            entry["usf_files"].update(usf)
        if nodes:
            entry["material_nodes"].update(nodes)

    # -- Heuristic 1: resource naming conventions --------------------------

    resource_names: List[str] = []
    for binding in bindings:
        name = (
            binding.get("resource_name", "")
            if isinstance(binding, dict)
            else getattr(binding, "resource_name", "")
        )
        if name:
            resource_names.append(name)

    # Also incorporate resource_names from shader_info itself.
    resource_names.extend(shader_info.get("resource_names", []))

    for rname in resource_names:
        for pattern, mapping in _RESOURCE_TO_MODULE.items():
            if pattern.lower() in rname.lower():
                _add_candidate(
                    module=mapping["module"],
                    conf=0.6,
                    reason=(
                        f"Resource '{rname}' matches pattern '{pattern}'"
                    ),
                    usf=mapping.get("usf"),
                    nodes=mapping.get("nodes"),
                )

    # -- Heuristic 2: shader source file path patterns ---------------------

    source_path = shader_info.get("source_path", "")
    if source_path:
        for path_prefix, mapping in _PATH_TO_MODULE.items():
            if path_prefix.lower() in source_path.lower():
                _add_candidate(
                    module=mapping["module"],
                    conf=0.75,
                    reason=(
                        f"Source path '{source_path}' "
                        f"matches '{path_prefix}'"
                    ),
                    usf=mapping.get("usf"),
                )

    # -- Heuristic 3: material parameter names -----------------------------

    for rname in resource_names:
        for param_name, node_name in _MATERIAL_PARAM_TO_NODE.items():
            if param_name.lower() in rname.lower():
                _add_candidate(
                    module="MaterialGraph",
                    conf=0.4,
                    reason=(
                        f"Resource '{rname}' suggests "
                        f"material parameter '{param_name}'"
                    ),
                    nodes=[node_name],
                )

    # -- Heuristic 4: shader stage hints -----------------------------------

    stage = shader_info.get("stage", "")
    entry_point = shader_info.get("entry_point", "")
    stage_lower = str(stage).lower()

    if "compute" in stage_lower or stage_lower == "cs":
        # Only add a generic "ComputeShader" candidate when no more
        # specific module was already identified via path matching.
        path_modules = {
            m["module"]
            for m in _PATH_TO_MODULE.values()
            if source_path
            and m["module"] in candidates
        }
        if not path_modules:
            _add_candidate(
                module="ComputeShader",
                conf=0.2,
                reason=f"Compute shader stage ({stage})",
            )

    if entry_point and entry_point != "main":
        _add_candidate(
            module="CustomShader",
            conf=0.15,
            reason=f"Non-standard entry point '{entry_point}'",
        )

    # -- Build sorted results ----------------------------------------------

    results: List[UEModuleMapping] = []
    for module_name, data in candidates.items():
        results.append(UEModuleMapping(
            module_name=module_name,
            confidence=round(data["confidence"], 4),
            evidence=data["evidence"],
            usf_files=sorted(data["usf_files"]),
            material_nodes=sorted(data["material_nodes"]),
        ))

    results.sort(key=lambda m: m.confidence, reverse=True)
    return results
