"""
RAG（Retrieval-Augmented Generation）集成的知识库连接器。

提供统一接口，用于从以下来源搜索与检索知识：

- **Local documentation files** —— Markdown、纯文本与头文件将被递归索引，
  并通过 BM25 评分检索。
- **Code repositories** —— C++、HLSL（``.usf`` / ``.ush``）与 Python 源码
  与文档一起索引，以便搜索结果包含实现细节。
- **Unreal Engine module mapping** —— 专用启发式映射器
  (:func:`map_shader_to_ue_module`) 将 shader/binding 模式映射到可能的
  UE 渲染模块，辅助根因分析。

连接器在配置的目录上构建轻量倒排索引，并持久化到 SQLite。
查询采用 Okapi BM25 相关性评分。可选的 ``project_id`` 标签允许多个
project 共享索引，同时保持结果可过滤。
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
# Data classes（数据类）
# ---------------------------------------------------------------------------

@dataclass
class KBSearchResult:
    """知识库中的单条搜索结果。

    Attributes
    ----------
    doc_id:
        索引中的内部文档标识。
    path:
        源文件的文件系统路径。
    score:
        BM25 相关性得分（越高越好）。
    snippet:
        首次匹配位置周围的简短上下文。
    line_number:
        首次匹配的 1-based 行号。
    """

    doc_id: str
    path: str
    score: float
    snippet: str
    line_number: int


@dataclass
class UEModuleMapping:
    """从 shader/binding 模式到 Unreal Engine 模块的映射。

    Attributes
    ----------
    module_name:
        规范化的 UE module 或 pass 名称（如 ``"BasePassPixelShader"``）。
    confidence:
        启发式置信度 ``[0.0, 1.0]``。
    evidence:
        支持该映射的人类可读理由。
    usf_files:
        可能关联的 ``.usf`` / ``.ush`` 文件。
    material_nodes:
        可能关联的 material-graph node 名称。
    """

    module_name: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    usf_files: List[str] = field(default_factory=list)
    material_nodes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants（常量）
# ---------------------------------------------------------------------------

# KB connector 索引的文件扩展名。
_INDEXABLE_EXTENSIONS: Set[str] = {
    ".md", ".txt", ".h", ".cpp", ".usf", ".ush", ".py",
}

# BM25 调参参数（Okapi BM25 默认值）。
_BM25_K1 = 1.5
_BM25_B = 0.75

# Snippet 上下文：首个匹配位置左右的字符数。
_SNIPPET_CONTEXT = 120

# Tokeniser 模式：长度 >= 2 的类单词 token。
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def _new_id(prefix: str) -> str:
    """生成带 *prefix* 的短唯一标识符。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _tokenize(text: str) -> List[str]:
    """从 *text* 中提取小写单词 tokens。

    tokens 是以字母或下划线开头的 ``[A-Za-z0-9_]`` 序列，最小长度为 2。
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
    """具备 BM25 搜索与 UE 模块映射的知识库连接器。

    在本地文档与源码上构建轻量倒排索引，并持久化到 SQLite。
    查询使用 Okapi BM25 相关性评分。

    Parameters
    ----------
    index_dirs:
        在 :meth:`initialize` 时索引的目录。若稍后通过
        :meth:`index_directory` 添加目录，可为 ``None``。
    db_path:
        倒排索引使用的 SQLite 数据库路径。为 ``None`` 时使用内存库
        （便于测试）。
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
        """创建表并索引所有配置目录。

        可多次调用；修改时间未变化的文档会被跳过。
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
        """打开 SQLite 连接并创建表（同步）。"""
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
        """递归遍历 *dir_path* 并索引所有支持的文件。

        修改时间与已存值一致的文件会被跳过；新增或变更文件会被完全重建索引。

        Parameters
        ----------
        dir_path:
            要遍历的根目录。
        project_id:
            该目录下所有文档的 project 标识。
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
        """同步索引实现（在 executor 中运行）。"""
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

                # 检查该文件是否已索引且未变化。
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
        """使用 BM25 评分搜索已索引语料。

        Parameters
        ----------
        query:
            自然语言查询。
        filters:
            可选键值过滤器。支持的键：

            - ``file_type`` -- extension without dot (e.g. ``"cpp"``).
            - ``path_prefix`` -- only documents under this path prefix.
            - ``project_id`` -- exact match on the project tag.
        limit:
            返回结果的最大数量。

        Returns
        -------
        list[KBSearchResult]
            按 BM25 分数降序排序的结果。
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
        """BM25 搜索实现（同步，executor 中运行）。"""
        conn = self._ensure_conn()

        # 语料统计。
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
    """将 shader 与 binding 模式映射到 Unreal Engine 模块。

    使用基于资源命名约定、shader 源文件路径与 material 参数名的
    启发式规则，生成潜在 UE 模块映射的排序列表。

    Parameters
    ----------
    shader_info:
        描述 shader 的字典，常见键包括：

        - ``"source_path"`` —— shader 源文件的虚拟路径。
        - ``"resource_names"`` —— 绑定资源名称列表。
        - ``"stage"`` —— shader stage 字符串（如 ``"ps"``, ``"cs"``）。
        - ``"entry_point"`` —— shader entry-point 名称。
    bindings:
        资源绑定条目列表。每个元素应以字典键或对象属性形式
        暴露 ``resource_name`` 与 ``type``。
    artifact_store:
        可选的 artifact store，用于获取 shader 源文本以进行更深入分析。
        当前未使用，预留扩展。

    Returns
    -------
    list[UEModuleMapping]
        按置信度降序排序的可能模块映射。
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
    """UE 模块映射启发式的同步实现。"""
    # candidates[module_name] -> accumulated evidence dict
    candidates: Dict[str, Dict[str, Any]] = {}

    def _add_candidate(
        module: str,
        conf: float,
        reason: str,
        usf: Optional[List[str]] = None,
        nodes: Optional[List[str]] = None,
    ) -> None:
        """为候选模块累计证据。

        置信度采用独立证据组合公式：
        ``1 - (1 - a) * (1 - b)``，使多个弱信号逐步收敛而不超过 1.0。
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
