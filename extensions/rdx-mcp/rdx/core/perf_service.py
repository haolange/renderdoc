"""Performance counter sampling service for RDX-MCP.

Wraps RenderDoc's GPU performance counter APIs -- enumerate, describe, fetch,
and analyse -- behind an async interface suitable for MCP tool handlers.

The ``renderdoc`` module is imported lazily so that the rest of the package
can be loaded and tested without it.

Key capabilities:
    * Enumerate available GPU performance counters with metadata.
    * Sample a set of counters over a specified event range, producing
      per-event samples, per-counter summaries, and statistical anomalies.
    * Detect performance hotspot events by GPU duration.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from rdx.models import PerfResult, CounterSample, CounterSummary

logger = logging.getLogger("rdx.core.perf_service")

# ---------------------------------------------------------------------------
# Lazy renderdoc import
# ---------------------------------------------------------------------------

_rd_module: Any = None
_rd_import_attempted: bool = False


def _lazy_import_renderdoc() -> Any:
    """Import ``renderdoc`` on first use.

    Returns the module object or ``None`` if it cannot be loaded.
    """
    global _rd_module, _rd_import_attempted

    if _rd_import_attempted:
        return _rd_module

    _rd_import_attempted = True
    try:
        import renderdoc as rd  # type: ignore[import-not-found]
        _rd_module = rd
        logger.debug("renderdoc module loaded successfully")
    except ImportError:
        logger.warning(
            "renderdoc Python module not found.  Performance counter "
            "sampling will be unavailable.  Ensure the module is on "
            "sys.path or set RDX_RENDERDOC_PATH."
        )
        _rd_module = None

    return _rd_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Mapping from ``CounterDescription.resultType`` enum names to the
# attribute on ``CounterValue`` that should be read.
_RESULT_TYPE_ATTR: Dict[str, str] = {
    "Float":  "f",
    "UInt32": "u32",
    "UInt64": "u64",
    "Double": "d",
}

# Well-known GPU-duration counter names (checked in priority order).
_GPU_DURATION_NAMES: Tuple[str, ...] = (
    "EventGPUDuration",
    "GPUDuration",
    "GPU Duration",
)

# Standard anomaly z-score threshold (mean + N * std).
_ANOMALY_Z_THRESHOLD: float = 3.0


def _extract_counter_value(result: Any, desc: Any) -> float:
    """Extract a scalar numeric value from a ``CounterValue`` union.

    Parameters
    ----------
    result:
        A ``CounterResult`` instance whose ``.value`` attribute is a
        ``CounterValue`` union.
    desc:
        The ``CounterDescription`` for this counter, used to determine
        which union member to read via ``resultType``.

    Returns
    -------
    float
        The extracted value cast to a Python float.
    """
    value_obj = result.value
    result_type_name = str(desc.resultType)

    # Try the direct enum name first (e.g. "Float", "UInt32").
    for type_key, attr_name in _RESULT_TYPE_ATTR.items():
        if type_key in result_type_name:
            raw = getattr(value_obj, attr_name, None)
            if raw is not None:
                return float(raw)

    # Fallback: walk through all known accessors.
    for attr_name in ("d", "f", "u64", "u32"):
        raw = getattr(value_obj, attr_name, None)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue

    logger.warning(
        "Could not extract value for counter %s (resultType=%s); "
        "returning 0.0",
        getattr(desc, "name", "?"),
        result_type_name,
    )
    return 0.0


def _compute_p95(values: List[float]) -> float:
    """Compute the 95th percentile of *values* without NumPy.

    Uses the linear-interpolation method consistent with
    ``numpy.percentile(values, 95, interpolation='linear')``.

    Returns ``0.0`` for an empty list.
    """
    if not values:
        return 0.0

    n = len(values)
    if n == 1:
        return values[0]

    sorted_vals = sorted(values)

    # Rank for the 95th percentile using the C = 1 convention.
    rank = 0.95 * (n - 1)
    lo_idx = int(math.floor(rank))
    hi_idx = min(lo_idx + 1, n - 1)
    frac = rank - lo_idx

    return sorted_vals[lo_idx] + frac * (sorted_vals[hi_idx] - sorted_vals[lo_idx])


def _compute_std(values: List[float], mean: float) -> float:
    """Compute population standard deviation."""
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


# ---------------------------------------------------------------------------
# PerfService
# ---------------------------------------------------------------------------


class PerfService:
    """High-level async service for GPU performance counter operations.

    All public methods accept loose service references (session_manager)
    so that the service is stateless and straightforward to test with
    fakes or mocks.
    """

    # ------------------------------------------------------------------
    # Executor helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _offload(fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a synchronous callable in the default thread-pool executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(fn, *args, **kwargs),
        )

    # ------------------------------------------------------------------
    # enumerate_counters
    # ------------------------------------------------------------------

    async def enumerate_counters(
        self,
        session_id: str,
        session_manager: Any,
    ) -> List[Dict[str, Any]]:
        """Return available GPU performance counters with descriptions.

        Each entry in the returned list is a dict with keys:

        * ``counter_id`` (``int``)  -- numeric value of the ``GPUCounter`` enum.
        * ``name`` (``str``)        -- human-readable counter name.
        * ``description`` (``str``) -- longer description from the driver.
        * ``unit`` (``str``)        -- measurement unit (e.g. ``"seconds"``,
          ``"percentage"``).
        * ``result_type`` (``str``) -- value type (``Float``, ``UInt32``,
          ``UInt64``, ``Double``).

        Parameters
        ----------
        session_id:
            Active session identifier.
        session_manager:
            Provides ``get_controller(session_id)`` to obtain the replay
            controller.

        Returns
        -------
        list[dict]
            A list of counter description dicts, or an empty list if the
            renderdoc module is unavailable or enumeration fails.
        """
        rd = _lazy_import_renderdoc()
        if rd is None:
            logger.warning("renderdoc unavailable; returning empty counter list")
            return []

        try:
            controller = session_manager.get_controller(session_id)
        except Exception as exc:
            logger.error(
                "Failed to get controller for session %s: %s",
                session_id, exc,
            )
            return []

        try:
            counter_enums = await self._offload(controller.EnumerateCounters)
        except Exception as exc:
            logger.error("EnumerateCounters failed: %s", exc)
            return []

        results: List[Dict[str, Any]] = []
        for counter in counter_enums:
            try:
                desc = await self._offload(
                    controller.DescribeCounter, counter,
                )
                results.append({
                    "counter_id": int(counter),
                    "name": str(getattr(desc, "name", "")),
                    "description": str(getattr(desc, "description", "")),
                    "unit": str(getattr(desc, "unit", "")),
                    "result_type": str(getattr(desc, "resultType", "")),
                })
            except Exception as exc:
                logger.warning(
                    "DescribeCounter failed for counter %s: %s",
                    counter, exc,
                )
                continue

        logger.debug(
            "Enumerated %d counters for session %s",
            len(results), session_id,
        )
        return results

    # ------------------------------------------------------------------
    # sample_counters
    # ------------------------------------------------------------------

    async def sample_counters(
        self,
        session_id: str,
        event_range: Tuple[int, int],
        counter_ids: List[int],
        session_manager: Any,
    ) -> PerfResult:
        """Fetch and analyse performance counters for a range of events.

        Parameters
        ----------
        session_id:
            Active session identifier.
        event_range:
            ``(lo, hi)`` inclusive event-ID boundaries.  Only counter
            results whose ``eventId`` falls within this range are included.
        counter_ids:
            List of ``GPUCounter`` integer values to sample.  Pass the
            ``counter_id`` values obtained from :meth:`enumerate_counters`.
        session_manager:
            Provides the replay controller.

        Returns
        -------
        PerfResult
            Contains ``samples`` (per-event, per-counter values),
            ``summaries`` (per-counter statistics including min, max,
            mean, p95, and hotspot event), and ``anomaly_events``
            (event IDs where any counter exceeded mean + 3*std).
        """
        rd = _lazy_import_renderdoc()
        if rd is None:
            logger.warning("renderdoc unavailable; returning empty PerfResult")
            return PerfResult()

        try:
            controller = session_manager.get_controller(session_id)
        except Exception as exc:
            logger.error(
                "Failed to get controller for session %s: %s",
                session_id, exc,
            )
            return PerfResult()

        lo, hi = event_range

        # -- Resolve GPUCounter enums from integer IDs ---------------------
        counter_list: List[Any] = []
        try:
            all_counters = await self._offload(controller.EnumerateCounters)
            available_map: Dict[int, Any] = {
                int(c): c for c in all_counters
            }
        except Exception as exc:
            logger.error("EnumerateCounters failed: %s", exc)
            return PerfResult()

        for cid in counter_ids:
            if cid in available_map:
                counter_list.append(available_map[cid])
            else:
                logger.warning(
                    "Requested counter_id %d not available on this GPU; "
                    "skipping",
                    cid,
                )

        if not counter_list:
            logger.warning("No valid counters to sample")
            return PerfResult()

        # -- Build counter description lookup ------------------------------
        desc_map: Dict[int, Any] = {}
        name_map: Dict[int, str] = {}
        for counter in counter_list:
            try:
                desc = await self._offload(
                    controller.DescribeCounter, counter,
                )
                desc_map[int(counter)] = desc
                name_map[int(counter)] = str(getattr(desc, "name", ""))
            except Exception as exc:
                logger.warning(
                    "DescribeCounter failed for %s: %s", counter, exc,
                )

        # -- Fetch counters ------------------------------------------------
        try:
            raw_results = await self._offload(
                controller.FetchCounters, counter_list,
            )
        except Exception as exc:
            logger.error("FetchCounters failed: %s", exc)
            return PerfResult()

        # -- Filter to event range and build samples -----------------------
        samples: List[CounterSample] = []
        # Accumulator: counter_id -> list of (event_id, value) pairs.
        per_counter: Dict[int, List[Tuple[int, float]]] = {}

        for r in raw_results:
            eid = int(r.eventId)
            if eid < lo or eid > hi:
                continue

            cid = int(r.counter)
            desc = desc_map.get(cid)
            if desc is None:
                continue

            value = _extract_counter_value(r, desc)
            cname = name_map.get(cid, "")

            samples.append(CounterSample(
                event_id=eid,
                counter_id=cid,
                counter_name=cname,
                value=value,
            ))

            per_counter.setdefault(cid, []).append((eid, value))

        # -- Compute per-counter summaries ---------------------------------
        summaries: List[CounterSummary] = []
        for cid, pairs in per_counter.items():
            values = [v for _, v in pairs]
            if not values:
                continue

            min_val = min(values)
            max_val = max(values)
            mean_val = sum(values) / len(values)
            p95_val = _compute_p95(values)

            # Hotspot: event with the maximum value for this counter.
            hotspot_eid = max(pairs, key=lambda p: p[1])[0]

            summaries.append(CounterSummary(
                counter_name=name_map.get(cid, f"counter_{cid}"),
                min_val=min_val,
                max_val=max_val,
                mean_val=mean_val,
                p95_val=p95_val,
                hotspot_event_id=hotspot_eid,
            ))

        # -- Detect anomaly events (z-score > 3 on any counter) -----------
        anomaly_event_set: set = set()
        for cid, pairs in per_counter.items():
            values = [v for _, v in pairs]
            if len(values) < 2:
                continue

            mean_val = sum(values) / len(values)
            std_val = _compute_std(values, mean_val)

            if std_val <= 0.0:
                continue

            threshold = mean_val + _ANOMALY_Z_THRESHOLD * std_val
            for eid, v in pairs:
                if v > threshold:
                    anomaly_event_set.add(eid)

        anomaly_events = sorted(anomaly_event_set)

        logger.info(
            "Sampled %d counters across events [%d, %d]: "
            "%d samples, %d summaries, %d anomaly events",
            len(counter_list), lo, hi,
            len(samples), len(summaries), len(anomaly_events),
        )

        return PerfResult(
            samples=samples,
            summaries=summaries,
            anomaly_events=anomaly_events,
        )

    # ------------------------------------------------------------------
    # detect_hotspots
    # ------------------------------------------------------------------

    async def detect_hotspots(
        self,
        session_id: str,
        session_manager: Any,
        *,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """Identify the top-K most expensive events by GPU duration.

        Samples the ``EventGPUDuration`` counter (or its platform-specific
        equivalent) across all events in the capture, then returns the
        *top_k* slowest events sorted by descending duration.

        Parameters
        ----------
        session_id:
            Active session identifier.
        session_manager:
            Provides the replay controller.
        top_k:
            Maximum number of hotspot entries to return (default 10).

        Returns
        -------
        list[dict]
            Each entry has:

            * ``event_id`` (``int``) -- the draw-call event ID.
            * ``duration_us`` (``float``) -- GPU duration in microseconds.
            * ``rank`` (``int``) -- 1-based rank (1 = slowest).

            An empty list is returned if the GPU duration counter is not
            available or the renderdoc module cannot be loaded.
        """
        rd = _lazy_import_renderdoc()
        if rd is None:
            logger.warning("renderdoc unavailable; cannot detect hotspots")
            return []

        try:
            controller = session_manager.get_controller(session_id)
        except Exception as exc:
            logger.error(
                "Failed to get controller for session %s: %s",
                session_id, exc,
            )
            return []

        # -- Find the GPU-duration counter ---------------------------------
        try:
            all_counters = await self._offload(controller.EnumerateCounters)
        except Exception as exc:
            logger.error("EnumerateCounters failed: %s", exc)
            return []

        duration_counter: Any = None
        duration_desc: Any = None

        for counter in all_counters:
            try:
                desc = await self._offload(
                    controller.DescribeCounter, counter,
                )
                cname = str(getattr(desc, "name", ""))
                # Check the counter name against known GPU-duration names,
                # as well as the GPUCounter enum member name itself.
                enum_name = str(counter)
                if any(
                    dn.lower() in cname.lower() or dn.lower() in enum_name.lower()
                    for dn in _GPU_DURATION_NAMES
                ):
                    duration_counter = counter
                    duration_desc = desc
                    break
            except Exception:
                continue

        if duration_counter is None:
            logger.warning(
                "No GPU duration counter found among %d available counters",
                len(all_counters),
            )
            return []

        # -- Fetch the duration counter for all events ---------------------
        try:
            raw_results = await self._offload(
                controller.FetchCounters, [duration_counter],
            )
        except Exception as exc:
            logger.error("FetchCounters for GPU duration failed: %s", exc)
            return []

        # -- Extract (event_id, duration) pairs ----------------------------
        event_durations: List[Tuple[int, float]] = []
        for r in raw_results:
            eid = int(r.eventId)
            value = _extract_counter_value(r, duration_desc)
            event_durations.append((eid, value))

        if not event_durations:
            logger.info("No duration samples returned; capture may be empty")
            return []

        # -- Sort descending and take top-K --------------------------------
        event_durations.sort(key=lambda p: p[1], reverse=True)
        top = event_durations[:max(1, top_k)]

        hotspots: List[Dict[str, Any]] = []
        for rank, (eid, dur) in enumerate(top, start=1):
            hotspots.append({
                "event_id": eid,
                "duration_us": dur,
                "rank": rank,
            })

        logger.info(
            "Detected %d hotspot events (top_k=%d) in session %s; "
            "slowest event=%d (%.2f us)",
            len(hotspots), top_k, session_id,
            hotspots[0]["event_id"], hotspots[0]["duration_us"],
        )

        return hotspots
