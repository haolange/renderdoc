"""
Fingerprint storage and matching for GPU debug knowledge accumulation.

Uses SQLite for persistence.  The database stores fingerprints from
successful debug sessions that can be matched against new captures to
guide diagnosis.

Tables
------
``fingerprints``
    One row per recorded fingerprint, linking a pass and/or shader
    fingerprint to the bug type, verdict, and project metadata from the
    original debug session.

``regression_entries``
    Anchor rows for regression monitoring.  Each entry records a
    capture + event pair where a known bug was found, together with the
    verifier configuration and expected metric baselines so the system
    can re-check automatically after engine updates.

Governance rule
~~~~~~~~~~~~~~~
Only fingerprints from sessions with a ``FIXED`` or ``IMPROVED`` verdict
are stored as *positive* examples.  All other verdicts are stored with
``is_negative=True`` so the knowledge base can learn what strategies to
avoid without presenting them as recommended solutions.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from asyncio import get_running_loop
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rdx.models import (
    BugType,
    FingerprintRecord,
    PassFingerprint,
    RegressionEntry,
    ShaderFingerprint,
    VerdictResult,
    VerifierConfig,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _new_id(prefix: str) -> str:
    """Generate a short unique identifier with the given *prefix*."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _ts() -> float:
    """Current wall-clock time as a POSIX timestamp."""
    return time.time()


# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

_CREATE_FINGERPRINTS = """
CREATE TABLE IF NOT EXISTS fingerprints (
    record_id           TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL DEFAULT '',
    bug_type            TEXT NOT NULL DEFAULT 'unknown',
    verdict             TEXT NOT NULL DEFAULT 'inconclusive',
    project_id          TEXT NOT NULL DEFAULT '',
    engine_branch       TEXT NOT NULL DEFAULT '',
    fingerprint_version INTEGER NOT NULL DEFAULT 1,
    is_negative         INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    pass_fp_json        TEXT,
    shader_fp_json      TEXT
);
"""

_CREATE_REGRESSION_ENTRIES = """
CREATE TABLE IF NOT EXISTS regression_entries (
    entry_id              TEXT PRIMARY KEY,
    capture_hash          TEXT NOT NULL DEFAULT '',
    first_bad_event_id    INTEGER NOT NULL DEFAULT 0,
    verifier_config_json  TEXT,
    patch_id              TEXT,
    expected_metrics_json TEXT,
    project_id            TEXT NOT NULL DEFAULT '',
    created_at            REAL NOT NULL
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_fp_bug_type ON fingerprints(bug_type);",
    "CREATE INDEX IF NOT EXISTS idx_fp_project  ON fingerprints(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_fp_verdict  ON fingerprints(verdict);",
    "CREATE INDEX IF NOT EXISTS idx_reg_project ON regression_entries(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_reg_capture ON regression_entries(capture_hash);",
]


# ---------------------------------------------------------------------------
# Similarity helpers
# ---------------------------------------------------------------------------

def _jaccard(set_a: set, set_b: set) -> float:
    """Jaccard similarity coefficient between two sets.

    Returns 0.0 when both sets are empty (by convention, the intersection
    of two empty sets is vacuously empty).

    Parameters
    ----------
    set_a, set_b:
        Arbitrary sets of hashable elements.

    Returns
    -------
    float
        Value in ``[0.0, 1.0]``.
    """
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def _weighted_score(
    similarities: Dict[str, float],
    weights: Dict[str, float],
) -> float:
    """Compute a weighted average of similarity scores.

    Only dimensions present in both *similarities* and *weights*
    contribute.  Returns 0.0 when the total weight is zero.

    Parameters
    ----------
    similarities:
        Mapping from dimension name to its ``[0, 1]`` similarity value.
    weights:
        Mapping from dimension name to its non-negative weight.

    Returns
    -------
    float
        Weighted average in ``[0.0, 1.0]`` (assuming inputs are valid).
    """
    total_weight = 0.0
    weighted_sum = 0.0
    for key, weight in weights.items():
        if key in similarities:
            weighted_sum += similarities[key] * weight
            total_weight += weight
    if total_weight == 0.0:
        return 0.0
    return weighted_sum / total_weight


# ---------------------------------------------------------------------------
# FingerprintStore
# ---------------------------------------------------------------------------

class FingerprintStore:
    """SQLite-backed store for GPU-debug fingerprints and regression entries.

    Fingerprints capture the structural signature of a render-pass or shader
    at the time a bug was successfully diagnosed.  When a new capture comes
    in, fingerprint matching helps the system skip straight to the most
    promising diagnosis strategy.

    Governance rule
    ~~~~~~~~~~~~~~~
    Only fingerprints from sessions with a ``FIXED`` or ``IMPROVED`` verdict
    are stored as positive examples.  Failed sessions may be stored with
    ``is_negative=True`` so the system can learn what *not* to try.

    Parameters
    ----------
    db_path:
        Filesystem path for the SQLite database file.  Parent directories
        are created automatically on :meth:`initialize`.
    """

    # Weights for pass-fingerprint matching dimensions.
    _PASS_WEIGHTS: Dict[str, float] = {
        "rt_formats": 0.40,
        "blend_modes": 0.30,
        "binding_pattern": 0.30,
    }

    # Weights for shader-fingerprint matching dimensions.
    _SHADER_WEIGHTS: Dict[str, float] = {
        "resource_names": 0.25,
        "slot_pattern": 0.25,
        "ir_kgram_hashes": 0.50,
    }

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    # -- lifecycle ----------------------------------------------------------

    async def initialize(self) -> None:
        """Create the database file and tables if they do not already exist.

        Must be called (and awaited) before any other method.  Safe to
        call multiple times; subsequent calls are no-ops if the tables
        already exist.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        loop = get_running_loop()
        self._conn = await loop.run_in_executor(None, self._open_db)
        logger.info("FingerprintStore initialized at %s", self._db_path)

    def _open_db(self) -> sqlite3.Connection:
        """Open the SQLite connection and create tables (sync, run in executor)."""
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute(_CREATE_FINGERPRINTS)
        conn.execute(_CREATE_REGRESSION_ENTRIES)
        for idx_sql in _CREATE_INDEXES:
            conn.execute(idx_sql)
        conn.commit()
        return conn

    def _ensure_conn(self) -> sqlite3.Connection:
        """Return the active connection, raising if :meth:`initialize` was not called."""
        if self._conn is None:
            raise RuntimeError(
                "FingerprintStore has not been initialized. "
                "Call await store.initialize() first."
            )
        return self._conn

    # -- store fingerprint --------------------------------------------------

    async def store_fingerprint(self, record: FingerprintRecord) -> str:
        """Persist a fingerprint record and return its ``record_id``.

        Governance rule
        ~~~~~~~~~~~~~~~
        Only records whose verdict is ``FIXED`` or ``IMPROVED`` are stored as
        positive examples.  All other verdicts are stored with
        ``is_negative=True`` so the knowledge base can learn from failures
        without presenting them as recommended solutions.

        Parameters
        ----------
        record:
            The :class:`~rdx.models.FingerprintRecord` to store.

        Returns
        -------
        str
            The ``record_id`` of the stored (or updated) record.
        """
        is_negative = record.verdict not in (
            VerdictResult.FIXED,
            VerdictResult.IMPROVED,
        )

        pass_fp_json = (
            record.pass_fp.model_dump_json() if record.pass_fp else None
        )
        shader_fp_json = (
            record.shader_fp.model_dump_json() if record.shader_fp else None
        )

        record_id = record.record_id or _new_id("fpr")

        loop = get_running_loop()
        await loop.run_in_executor(
            None,
            partial(
                self._insert_fingerprint,
                record_id=record_id,
                task_id=record.task_id,
                bug_type=record.bug_type.value,
                verdict=record.verdict.value,
                project_id=record.project_id,
                engine_branch=record.engine_branch,
                fingerprint_version=record.fingerprint_version,
                is_negative=int(is_negative),
                created_at=record.created_at or _ts(),
                pass_fp_json=pass_fp_json,
                shader_fp_json=shader_fp_json,
            ),
        )

        logger.debug(
            "Stored fingerprint %s (bug_type=%s, verdict=%s, negative=%s)",
            record_id,
            record.bug_type.value,
            record.verdict.value,
            is_negative,
        )
        return record_id

    def _insert_fingerprint(self, **kwargs: Any) -> None:
        """INSERT OR REPLACE a fingerprint row (sync, run in executor)."""
        conn = self._ensure_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO fingerprints
                (record_id, task_id, bug_type, verdict, project_id,
                 engine_branch, fingerprint_version, is_negative,
                 created_at, pass_fp_json, shader_fp_json)
            VALUES
                (:record_id, :task_id, :bug_type, :verdict, :project_id,
                 :engine_branch, :fingerprint_version, :is_negative,
                 :created_at, :pass_fp_json, :shader_fp_json)
            """,
            kwargs,
        )
        conn.commit()

    # -- query fingerprints -------------------------------------------------

    async def query_fingerprints(
        self,
        bug_type: Optional[str] = None,
        shader_hash: Optional[str] = None,
        rt_formats: Optional[List[str]] = None,
        project_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[FingerprintRecord]:
        """Query stored fingerprints by various criteria.

        Parameters
        ----------
        bug_type:
            Filter by bug type string (exact match).
        shader_hash:
            Filter by shader hash.  Supports prefix matching: if the
            value is shorter than a full hash the store returns records
            whose shader hash starts with the given prefix.
        rt_formats:
            Filter to records whose pass fingerprint contains at least
            one of the specified render-target formats (subset match).
        project_id:
            Filter by project identifier (exact match).
        limit:
            Maximum number of records to return.

        Returns
        -------
        list[FingerprintRecord]
        """
        loop = get_running_loop()
        rows = await loop.run_in_executor(
            None,
            partial(
                self._query_fingerprints_sync,
                bug_type=bug_type,
                shader_hash=shader_hash,
                rt_formats=rt_formats,
                project_id=project_id,
                limit=limit,
            ),
        )
        return [self._row_to_record(r) for r in rows]

    def _query_fingerprints_sync(
        self,
        bug_type: Optional[str],
        shader_hash: Optional[str],
        rt_formats: Optional[List[str]],
        project_id: Optional[str],
        limit: int,
    ) -> List[sqlite3.Row]:
        """Execute the fingerprint query in SQL + Python post-filtering."""
        conn = self._ensure_conn()

        conditions: List[str] = []
        params: Dict[str, Any] = {}

        if bug_type is not None:
            conditions.append("bug_type = :bug_type")
            params["bug_type"] = bug_type

        if project_id is not None:
            conditions.append("project_id = :project_id")
            params["project_id"] = project_id

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        # Fetch a generous number of rows so that Python-side post-filters
        # (shader_hash prefix, rt_format subset) can still fill the limit.
        fetch_limit = limit * 5
        sql = f"""
            SELECT * FROM fingerprints
            {where}
            ORDER BY created_at DESC
            LIMIT :fetch_limit
        """
        params["fetch_limit"] = fetch_limit
        rows = conn.execute(sql, params).fetchall()

        # Post-filter for criteria that require JSON parsing.
        results: List[sqlite3.Row] = []
        for row in rows:
            # -- shader_hash prefix match --
            if shader_hash is not None:
                sfp_json = row["shader_fp_json"]
                if sfp_json is None:
                    continue
                sfp_data = json.loads(sfp_json)
                stored_hash = sfp_data.get("shader_hash", "")
                if not stored_hash.startswith(shader_hash):
                    continue

            # -- rt_format subset match --
            if rt_formats is not None:
                pfp_json = row["pass_fp_json"]
                if pfp_json is None:
                    continue
                pfp_data = json.loads(pfp_json)
                stored_formats = set(pfp_data.get("rt_formats", []))
                if not stored_formats & set(rt_formats):
                    continue

            results.append(row)
            if len(results) >= limit:
                break

        return results

    # -- match pass fingerprint ---------------------------------------------

    async def match_pass_fingerprint(
        self,
        pass_fp: PassFingerprint,
        threshold: float = 0.5,
    ) -> List[Tuple[FingerprintRecord, float]]:
        """Score stored fingerprints against a candidate pass fingerprint.

        The similarity is a weighted Jaccard coefficient across the
        ``rt_formats``, ``blend_modes``, and ``binding_pattern``
        dimensions.

        Parameters
        ----------
        pass_fp:
            The candidate pass fingerprint to match against.
        threshold:
            Minimum similarity score (inclusive) to include in results.

        Returns
        -------
        list[tuple[FingerprintRecord, float]]
            Records paired with their similarity score, sorted by score
            descending.
        """
        loop = get_running_loop()
        rows = await loop.run_in_executor(
            None,
            self._fetch_all_with_pass_fp,
        )

        query_rt = set(pass_fp.rt_formats)
        query_blend = set(pass_fp.blend_modes)
        query_binding = set(pass_fp.binding_pattern)

        scored: List[Tuple[FingerprintRecord, float]] = []
        for row in rows:
            pfp_json = row["pass_fp_json"]
            if pfp_json is None:
                continue

            pfp_data = json.loads(pfp_json)
            stored_rt = set(pfp_data.get("rt_formats", []))
            stored_blend = set(pfp_data.get("blend_modes", []))
            stored_binding = set(pfp_data.get("binding_pattern", []))

            sims = {
                "rt_formats": _jaccard(query_rt, stored_rt),
                "blend_modes": _jaccard(query_blend, stored_blend),
                "binding_pattern": _jaccard(query_binding, stored_binding),
            }
            score = _weighted_score(sims, self._PASS_WEIGHTS)

            if score >= threshold:
                scored.append((self._row_to_record(row), score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _fetch_all_with_pass_fp(self) -> List[sqlite3.Row]:
        """Return every fingerprint row that has a pass fingerprint."""
        conn = self._ensure_conn()
        return conn.execute(
            "SELECT * FROM fingerprints WHERE pass_fp_json IS NOT NULL"
        ).fetchall()

    # -- match shader fingerprint -------------------------------------------

    async def match_shader_fingerprint(
        self,
        shader_fp: ShaderFingerprint,
        threshold: float = 0.5,
    ) -> List[Tuple[FingerprintRecord, float]]:
        """Score stored fingerprints against a candidate shader fingerprint.

        An exact ``shader_hash`` match yields a score of 1.0 immediately.
        Otherwise the similarity is a weighted combination of
        ``resource_names``, ``slot_pattern``, and ``ir_kgram_hashes``
        Jaccard coefficients.

        Parameters
        ----------
        shader_fp:
            The candidate shader fingerprint to match against.
        threshold:
            Minimum similarity score (inclusive) to include in results.

        Returns
        -------
        list[tuple[FingerprintRecord, float]]
            Records paired with their similarity score, sorted by score
            descending.
        """
        loop = get_running_loop()
        rows = await loop.run_in_executor(
            None,
            self._fetch_all_with_shader_fp,
        )

        query_hash = shader_fp.shader_hash
        query_resources = set(shader_fp.resource_names)
        query_slots = set(shader_fp.slot_pattern)
        query_kgrams = set(shader_fp.ir_kgram_hashes)

        scored: List[Tuple[FingerprintRecord, float]] = []
        for row in rows:
            sfp_json = row["shader_fp_json"]
            if sfp_json is None:
                continue

            sfp_data = json.loads(sfp_json)
            stored_hash = sfp_data.get("shader_hash", "")

            # Exact hash match is a perfect score.
            if query_hash and stored_hash and query_hash == stored_hash:
                scored.append((self._row_to_record(row), 1.0))
                continue

            stored_resources = set(sfp_data.get("resource_names", []))
            stored_slots = set(sfp_data.get("slot_pattern", []))
            stored_kgrams = set(sfp_data.get("ir_kgram_hashes", []))

            sims = {
                "resource_names": _jaccard(query_resources, stored_resources),
                "slot_pattern": _jaccard(query_slots, stored_slots),
                "ir_kgram_hashes": _jaccard(query_kgrams, stored_kgrams),
            }
            score = _weighted_score(sims, self._SHADER_WEIGHTS)

            if score >= threshold:
                scored.append((self._row_to_record(row), score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _fetch_all_with_shader_fp(self) -> List[sqlite3.Row]:
        """Return every fingerprint row that has a shader fingerprint."""
        conn = self._ensure_conn()
        return conn.execute(
            "SELECT * FROM fingerprints WHERE shader_fp_json IS NOT NULL"
        ).fetchall()

    # -- regression entries -------------------------------------------------

    async def store_regression_entry(self, entry: RegressionEntry) -> str:
        """Persist a regression entry and return its ``entry_id``.

        Regression entries anchor a known-bad capture + event so the
        system can monitor for regressions after engine updates.

        Parameters
        ----------
        entry:
            The :class:`~rdx.models.RegressionEntry` to store.

        Returns
        -------
        str
            The ``entry_id`` of the stored entry.
        """
        entry_id = entry.entry_id or _new_id("reg")
        verifier_json = entry.verifier_config.model_dump_json()
        metrics_json = json.dumps(entry.expected_metrics)

        loop = get_running_loop()
        await loop.run_in_executor(
            None,
            partial(
                self._insert_regression_entry,
                entry_id=entry_id,
                capture_hash=entry.capture_hash,
                first_bad_event_id=entry.first_bad_event_id,
                verifier_config_json=verifier_json,
                patch_id=entry.patch_id,
                expected_metrics_json=metrics_json,
                project_id=entry.project_id,
                created_at=entry.created_at or _ts(),
            ),
        )

        logger.debug("Stored regression entry %s", entry_id)
        return entry_id

    def _insert_regression_entry(self, **kwargs: Any) -> None:
        """INSERT OR REPLACE a regression_entries row (sync, run in executor)."""
        conn = self._ensure_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO regression_entries
                (entry_id, capture_hash, first_bad_event_id,
                 verifier_config_json, patch_id, expected_metrics_json,
                 project_id, created_at)
            VALUES
                (:entry_id, :capture_hash, :first_bad_event_id,
                 :verifier_config_json, :patch_id, :expected_metrics_json,
                 :project_id, :created_at)
            """,
            kwargs,
        )
        conn.commit()

    async def get_regression_set(
        self,
        project_id: Optional[str] = None,
    ) -> List[RegressionEntry]:
        """Return all regression entries, optionally filtered by project.

        Parameters
        ----------
        project_id:
            If provided, only entries for this project are returned.

        Returns
        -------
        list[RegressionEntry]
        """
        loop = get_running_loop()
        rows = await loop.run_in_executor(
            None,
            partial(self._fetch_regression_entries, project_id=project_id),
        )
        return [self._row_to_regression(r) for r in rows]

    def _fetch_regression_entries(
        self,
        project_id: Optional[str],
    ) -> List[sqlite3.Row]:
        """Fetch regression rows, optionally filtered (sync, run in executor)."""
        conn = self._ensure_conn()
        if project_id is not None:
            return conn.execute(
                "SELECT * FROM regression_entries "
                "WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM regression_entries ORDER BY created_at DESC"
        ).fetchall()

    # -- deletion -----------------------------------------------------------

    async def delete_fingerprint(self, record_id: str) -> bool:
        """Remove a fingerprint record by its ``record_id``.

        Parameters
        ----------
        record_id:
            Identifier of the record to delete.

        Returns
        -------
        bool
            ``True`` if a record was deleted, ``False`` if not found.
        """
        loop = get_running_loop()
        deleted = await loop.run_in_executor(
            None,
            partial(self._delete_fingerprint_sync, record_id=record_id),
        )
        if deleted:
            logger.debug("Deleted fingerprint %s", record_id)
        return deleted

    def _delete_fingerprint_sync(self, record_id: str) -> bool:
        """DELETE one fingerprint row (sync, run in executor)."""
        conn = self._ensure_conn()
        cursor = conn.execute(
            "DELETE FROM fingerprints WHERE record_id = ?",
            (record_id,),
        )
        conn.commit()
        return cursor.rowcount > 0

    # -- stats --------------------------------------------------------------

    async def get_stats(self) -> Dict[str, Any]:
        """Return summary statistics about the fingerprint store.

        Returns
        -------
        dict
            Keys:

            - ``total_fingerprints`` -- total number of fingerprint records.
            - ``by_bug_type`` -- ``{bug_type_str: count}`` mapping.
            - ``by_verdict`` -- ``{verdict_str: count}`` mapping.
            - ``total_regression_entries`` -- total regression entries.
        """
        loop = get_running_loop()
        return await loop.run_in_executor(None, self._get_stats_sync)

    def _get_stats_sync(self) -> Dict[str, Any]:
        """Compute aggregate stats (sync, run in executor)."""
        conn = self._ensure_conn()

        total_fp = conn.execute(
            "SELECT COUNT(*) FROM fingerprints"
        ).fetchone()[0]

        by_bug_type: Dict[str, int] = {}
        for row in conn.execute(
            "SELECT bug_type, COUNT(*) AS cnt "
            "FROM fingerprints GROUP BY bug_type"
        ):
            by_bug_type[row["bug_type"]] = row["cnt"]

        by_verdict: Dict[str, int] = {}
        for row in conn.execute(
            "SELECT verdict, COUNT(*) AS cnt "
            "FROM fingerprints GROUP BY verdict"
        ):
            by_verdict[row["verdict"]] = row["cnt"]

        total_reg = conn.execute(
            "SELECT COUNT(*) FROM regression_entries"
        ).fetchone()[0]

        return {
            "total_fingerprints": total_fp,
            "by_bug_type": by_bug_type,
            "by_verdict": by_verdict,
            "total_regression_entries": total_reg,
        }

    # -- row-to-model conversion helpers ------------------------------------

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> FingerprintRecord:
        """Deserialise a ``fingerprints`` row into a :class:`FingerprintRecord`."""
        pass_fp = None
        if row["pass_fp_json"]:
            pass_fp = PassFingerprint.model_validate_json(row["pass_fp_json"])

        shader_fp = None
        if row["shader_fp_json"]:
            shader_fp = ShaderFingerprint.model_validate_json(
                row["shader_fp_json"]
            )

        return FingerprintRecord(
            record_id=row["record_id"],
            task_id=row["task_id"],
            pass_fp=pass_fp,
            shader_fp=shader_fp,
            bug_type=BugType(row["bug_type"]),
            verdict=VerdictResult(row["verdict"]),
            project_id=row["project_id"],
            engine_branch=row["engine_branch"],
            fingerprint_version=row["fingerprint_version"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_regression(row: sqlite3.Row) -> RegressionEntry:
        """Deserialise a ``regression_entries`` row into a :class:`RegressionEntry`."""
        verifier_config = VerifierConfig()
        if row["verifier_config_json"]:
            verifier_config = VerifierConfig.model_validate_json(
                row["verifier_config_json"]
            )

        expected_metrics: Dict[str, Any] = {}
        if row["expected_metrics_json"]:
            expected_metrics = json.loads(row["expected_metrics_json"])

        return RegressionEntry(
            entry_id=row["entry_id"],
            capture_hash=row["capture_hash"],
            first_bad_event_id=row["first_bad_event_id"],
            verifier_config=verifier_config,
            patch_id=row["patch_id"],
            expected_metrics=expected_metrics,
            project_id=row["project_id"],
            created_at=row["created_at"],
        )
