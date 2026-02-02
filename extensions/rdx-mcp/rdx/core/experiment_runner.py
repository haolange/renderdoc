"""
Atomic experiment runner for RDX-MCP.

Orchestrates the full lifecycle of a GPU-debug experiment:

1. Navigate the replay to a specific draw-call event.
2. Run a verifier on the *baseline* state (before any patch).
3. Optionally apply a shader patch and re-verify.
4. Compare before / after metrics to produce a verdict.
5. Revert the patch so subsequent experiments start from a clean state.

The module also provides a *bisect* facility that binary-searches a range
of events to locate the first draw call where a verifier starts failing,
and a *batch* runner for executing multiple experiment definitions
sequentially.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from rdx.models import (
    ArtifactRef,
    BisectResult,
    BisectRange,
    ExperimentDef,
    ExperimentEvidence,
    ExperimentResult,
    ExperimentStatus,
    PatchSpec,
    VerdictResult,
    VerifierConfig,
    VerifierType,
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


# Mapping from verifier type to the metric key that best represents the
# "badness" of the result.  Lower values are always *better* for these
# primary metrics.
_PRIMARY_METRIC_KEY: Dict[str, str] = {
    VerifierType.NANINF:          "nan_inf_count",
    VerifierType.IMAGE_DIFF:      "diff_score",
    VerifierType.PIXEL_STATS:     "anomaly_score",
    VerifierType.BINDING_DIFF:    "mismatch_count",
    VerifierType.COUNTER_ANOMALY: "anomaly_score",
    VerifierType.CUSTOM:          "score",
}

# Minimum relative improvement (fraction) to qualify as ``IMPROVED``.
_IMPROVEMENT_THRESHOLD = 0.10


# ---------------------------------------------------------------------------
# ExperimentRunner
# ---------------------------------------------------------------------------

class ExperimentRunner:
    """Runs self-contained, auditable GPU-debug experiments.

    Each experiment captures baseline metrics, optionally applies a patch,
    re-measures, and produces a verdict with full evidence.  Experiments
    are *atomic*: every patch is reverted before the method returns,
    regardless of success or failure.

    Parameters
    ----------
    session_manager:
        Provides ``get_controller(session_id)`` to obtain the replay
        controller for a given session.
    render_service:
        Provides ``async capture_render(session_id, event_id)`` returning
        an :class:`ArtifactRef` for the rendered output at that event.
    verifier_engine:
        Provides ``async verify(session_id, event_id, config)`` returning
        a ``dict`` of metric results including a ``"passed"`` boolean key.
    patch_engine:
        A :class:`~rdx.core.patch_engine.PatchEngine` instance.
    artifact_store:
        Provides ``async store(data, metadata)`` returning an
        :class:`ArtifactRef`.
    """

    def __init__(
        self,
        session_manager: Any,
        render_service: Any,
        verifier_engine: Any,
        patch_engine: Any,
        artifact_store: Any,
    ) -> None:
        self._session_manager = session_manager
        self._render_service = render_service
        self._verifier_engine = verifier_engine
        self._patch_engine = patch_engine
        self._artifact_store = artifact_store

        # Registry of PatchSpec objects keyed by patch_id.  Callers must
        # register specs before referencing them in an ExperimentDef.
        self._patch_specs: Dict[str, PatchSpec] = {}

    # ------------------------------------------------------------------
    # Patch-spec registry
    # ------------------------------------------------------------------

    def register_patch_spec(self, spec: PatchSpec) -> None:
        """Register a :class:`PatchSpec` so experiments can reference it."""
        self._patch_specs[spec.patch_id] = spec

    def get_patch_spec(self, patch_id: str) -> Optional[PatchSpec]:
        """Look up a previously registered :class:`PatchSpec`."""
        return self._patch_specs.get(patch_id)

    # ------------------------------------------------------------------
    # Single experiment
    # ------------------------------------------------------------------

    async def run_experiment(
        self,
        experiment_def: ExperimentDef,
    ) -> ExperimentResult:
        """Execute a single experiment as defined by *experiment_def*.

        Workflow
        -------
        1. Navigate the replay to the target event.
        2. Run the configured verifier to collect **baseline** metrics and
           optionally capture a rendered artifact.
        3. If a ``patch_id`` is set on the definition:
           a. Resolve the :class:`PatchSpec` from the registry.
           b. Apply the patch through the patch engine.
           c. Force a re-render at the same event.
           d. Run the verifier again to collect **after** metrics.
        4. Build :class:`ExperimentEvidence` with both metric sets and
           rendered artifacts.
        5. Determine the verdict by comparing before / after metrics.
        6. Revert the patch (if one was applied) so the session returns
           to its original state.
        7. Return the :class:`ExperimentResult`.
        """
        t0 = _ts()
        session_id = experiment_def.session_id
        event_id = experiment_def.event_id
        patch_applied = False
        patch_id_used: Optional[str] = None

        try:
            controller = self._session_manager.get_controller(session_id)

            # 1 -- navigate to the event
            controller.SetFrameEvent(event_id, True)

            # 2 -- baseline verification
            before_artifact = await self._safe_capture(session_id, event_id)
            before_metrics = await self._safe_verify(
                session_id, event_id, experiment_def.verifier,
            )

            after_artifact: Optional[ArtifactRef] = None
            after_metrics: Dict[str, Any] = {}

            # 3 -- optional patch application
            if experiment_def.patch_id is not None:
                spec = self._resolve_patch_spec(experiment_def.patch_id)
                if spec is None:
                    return self._make_error_result(
                        experiment_def,
                        t0,
                        f"PatchSpec '{experiment_def.patch_id}' not found "
                        f"in registry or active patches",
                    )

                patch_result = await self._patch_engine.apply_patch(
                    session_id,
                    spec.target_event_id or event_id,
                    spec.target_stage,
                    self._session_manager,
                    spec,
                )
                if not patch_result.success:
                    return self._make_error_result(
                        experiment_def,
                        t0,
                        f"Patch application failed: {patch_result.error_message}",
                    )
                patch_applied = True
                patch_id_used = spec.patch_id

                # 3c -- force re-render with the patched shader
                controller.SetFrameEvent(event_id, True)

                # 3d -- post-patch verification
                after_artifact = await self._safe_capture(
                    session_id, event_id,
                )
                after_metrics = await self._safe_verify(
                    session_id, event_id, experiment_def.verifier,
                )

            # 4 -- build evidence
            verdict = self._determine_verdict(
                before_metrics,
                after_metrics,
                experiment_def.verifier.type,
            )
            evidence = ExperimentEvidence(
                experiment_id=experiment_def.experiment_id,
                before_artifact=before_artifact,
                after_artifact=after_artifact,
                before_metrics=before_metrics,
                after_metrics=after_metrics,
                verifier_passed=after_metrics.get("passed", False)
                    if after_metrics else before_metrics.get("passed", False),
                verdict=verdict,
                notes=self._build_evidence_notes(
                    before_metrics, after_metrics, verdict,
                ),
            )

            # 5 -- result
            status = ExperimentStatus.COMPLETED

        except Exception as exc:
            logger.exception(
                "Experiment %s failed", experiment_def.experiment_id,
            )
            evidence = ExperimentEvidence(
                experiment_id=experiment_def.experiment_id,
                verdict=VerdictResult.ERROR,
                notes=str(exc),
            )
            status = ExperimentStatus.FAILED

        finally:
            # 6 -- always revert the patch
            if patch_applied and patch_id_used is not None:
                try:
                    await self._patch_engine.revert_patch(
                        session_id, patch_id_used, self._session_manager,
                    )
                except Exception:
                    logger.exception(
                        "Failed to revert patch %s after experiment %s",
                        patch_id_used, experiment_def.experiment_id,
                    )

        return ExperimentResult(
            experiment_id=experiment_def.experiment_id,
            status=status,
            evidence=evidence,
            duration_seconds=_ts() - t0,
        )

    # ------------------------------------------------------------------
    # Bisect
    # ------------------------------------------------------------------

    async def run_bisect(
        self,
        session_id: str,
        capture_id: str,
        range_lo: int,
        range_hi: int,
        verifier_config: VerifierConfig,
        strategy: str = "binary",
        max_iters: int = 60,
        confidence_threshold: float = 0.85,
    ) -> BisectResult:
        """Binary-search a range of events to find the first "bad" one.

        Parameters
        ----------
        session_id:
            Active replay session identifier.
        capture_id:
            Capture that is being replayed (used for logging only).
        range_lo, range_hi:
            Inclusive event-ID boundaries to search.  The assumption is
            that ``range_lo`` is *good* (verifier passes) and
            ``range_hi`` is *bad* (verifier fails).
        verifier_config:
            Configuration passed to the verifier at each probe point.
        strategy:
            ``"binary"`` for classic binary search.
            ``"ddmin"`` for binary search followed by extra boundary
            verification probes to increase confidence.
        max_iters:
            Hard upper bound on verification calls.
        confidence_threshold:
            Target confidence level; the search may stop early once this
            threshold is reached and the boundary is adjacent.

        Returns
        -------
        BisectResult
            Identifies the first bad event, the last known good event, the
            evidence chain (experiment IDs for each probe), the calculated
            confidence, and the number of iterations consumed.
        """
        if range_hi <= range_lo:
            raise ValueError(
                f"Invalid bisect range: lo={range_lo} hi={range_hi}; "
                f"hi must be greater than lo"
            )

        total_range = range_hi - range_lo
        lo = range_lo
        hi = range_hi
        iterations = 0
        evidence_chain: List[str] = []
        boundary_consistent_count = 0
        last_good = lo
        last_bad = hi

        # -- Phase 1: binary search ----------------------------------------
        while lo + 1 < hi and iterations < max_iters:
            mid = (lo + hi) // 2

            exp_id = _new_id("bexp")
            metrics = await self._safe_verify(
                session_id, mid, verifier_config,
            )
            evidence_chain.append(exp_id)
            iterations += 1

            is_bad = not metrics.get("passed", True)

            if is_bad:
                last_bad = mid
                hi = mid
            else:
                last_good = mid
                lo = mid

            # Track adjacent boundary consistency.
            if abs(last_bad - last_good) <= 1:
                boundary_consistent_count += 1

            # Early exit when confidence is high enough.
            confidence = self._calculate_confidence(
                last_good, last_bad, total_range,
                boundary_consistent_count,
            )
            if confidence >= confidence_threshold and hi - lo <= 1:
                break

        # -- Phase 2 (ddmin): boundary reinforcement -----------------------
        if strategy == "ddmin":
            # Re-verify the boundary events and probe their immediate
            # neighbours to increase confidence.
            verification_points: List[tuple] = [
                (last_good, True),   # expect good
                (last_bad,  False),  # expect bad
            ]
            if last_good - 1 >= range_lo:
                verification_points.append((last_good - 1, True))
            if last_bad + 1 <= range_hi:
                verification_points.append((last_bad + 1, False))

            for point, expect_good in verification_points:
                if iterations >= max_iters:
                    break
                exp_id = _new_id("bexp")
                metrics = await self._safe_verify(
                    session_id, point, verifier_config,
                )
                evidence_chain.append(exp_id)
                iterations += 1

                is_good = metrics.get("passed", True)
                if is_good == expect_good:
                    boundary_consistent_count += 1
                else:
                    logger.warning(
                        "Bisect boundary probe at event %d returned "
                        "unexpected result (expected good=%s, got good=%s)",
                        point, expect_good, is_good,
                    )

        # -- Final confidence calculation ----------------------------------
        confidence = self._calculate_confidence(
            last_good, last_bad, total_range, boundary_consistent_count,
        )

        logger.info(
            "Bisect complete for capture %s: first_bad=%d, last_good=%d, "
            "confidence=%.3f, iterations=%d, strategy=%s",
            capture_id, last_bad, last_good, confidence, iterations, strategy,
        )

        return BisectResult(
            first_bad_event_id=last_bad,
            first_good_event_id=last_good,
            evidence_chain=evidence_chain,
            confidence=confidence,
            iterations=iterations,
        )

    # ------------------------------------------------------------------
    # Batch execution
    # ------------------------------------------------------------------

    async def batch_experiments(
        self,
        experiments: List[ExperimentDef],
    ) -> List[ExperimentResult]:
        """Run multiple experiments sequentially with clean state between each.

        After every experiment, all patches for the experiment's session
        are reverted as a safety measure to guarantee a pristine baseline
        for the next experiment.
        """
        results: List[ExperimentResult] = []

        for exp_def in experiments:
            result = await self.run_experiment(exp_def)
            results.append(result)

            # Belt-and-suspenders: revert any lingering patches so the
            # next experiment starts from unmodified state.
            try:
                await self._patch_engine.revert_all(
                    exp_def.session_id, self._session_manager,
                )
            except Exception:
                logger.exception(
                    "Failed to revert patches between batch experiments "
                    "(after experiment %s)",
                    exp_def.experiment_id,
                )

        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_patch_spec(self, patch_id: str) -> Optional[PatchSpec]:
        """Look up a :class:`PatchSpec` by *patch_id*.

        Checks the runner's own registry first, then falls back to any
        spec that the patch engine is already tracking (for patches
        applied outside of an experiment).
        """
        spec = self._patch_specs.get(patch_id)
        if spec is not None:
            return spec

        # Fall back: the patch engine stores specs of already-applied
        # patches.  Useful when the caller applied a patch directly.
        for active_spec in self._patch_engine.list_patches():
            if active_spec.patch_id == patch_id:
                return active_spec

        return None

    async def _safe_verify(
        self,
        session_id: str,
        event_id: int,
        config: VerifierConfig,
    ) -> Dict[str, Any]:
        """Run the verifier, returning an empty-ish dict on failure."""
        try:
            controller = self._session_manager.get_controller(session_id)
            controller.SetFrameEvent(event_id, True)
            return await self._verifier_engine.verify(
                session_id, event_id, config,
            )
        except Exception:
            logger.exception(
                "Verifier failed at event %d (session %s)",
                event_id, session_id,
            )
            return {"passed": False, "error": True}

    async def _safe_capture(
        self,
        session_id: str,
        event_id: int,
    ) -> Optional[ArtifactRef]:
        """Capture a rendered frame, returning ``None`` on failure."""
        try:
            return await self._render_service.capture_render(
                session_id, event_id,
            )
        except Exception:
            logger.exception(
                "Render capture failed at event %d (session %s)",
                event_id, session_id,
            )
            return None

    @staticmethod
    def _make_error_result(
        exp_def: ExperimentDef,
        t0: float,
        message: str,
    ) -> ExperimentResult:
        """Construct an :class:`ExperimentResult` for an error case."""
        return ExperimentResult(
            experiment_id=exp_def.experiment_id,
            status=ExperimentStatus.FAILED,
            evidence=ExperimentEvidence(
                experiment_id=exp_def.experiment_id,
                verdict=VerdictResult.ERROR,
                notes=message,
            ),
            duration_seconds=_ts() - t0,
        )

    @staticmethod
    def _build_evidence_notes(
        before: Dict[str, Any],
        after: Dict[str, Any],
        verdict: VerdictResult,
    ) -> str:
        """Build a human-readable summary of the metric comparison."""
        parts: List[str] = [f"Verdict: {verdict.value}"]

        before_passed = before.get("passed", False)
        parts.append(f"Baseline passed: {before_passed}")

        if after:
            after_passed = after.get("passed", False)
            parts.append(f"Post-patch passed: {after_passed}")

            # Summarise any numeric metrics that changed.
            for key in sorted(set(before) | set(after)):
                if key in ("passed", "error"):
                    continue
                bv = before.get(key)
                av = after.get(key)
                if isinstance(bv, (int, float)) and isinstance(av, (int, float)):
                    if bv != av:
                        parts.append(f"  {key}: {bv} -> {av}")

        return "; ".join(parts)

    # ------------------------------------------------------------------
    # Confidence calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_confidence(
        good_id: int,
        bad_id: int,
        total_range: int,
        boundary_consistent_count: int,
    ) -> float:
        """Compute a ``[0, 1]`` confidence score for a bisect boundary.

        Higher confidence when:

        * The good / bad boundary is *sharp* (adjacent event IDs).
        * Multiple consistent verifications have confirmed the boundary.
        * The total search range was small (fewer opportunities for
          anomalies).
        """
        if total_range <= 0:
            return 0.0

        boundary_gap = abs(bad_id - good_id)

        # Sharpness: 1.0 when the boundary is a single step, decaying
        # hyperbolically as the gap grows.
        sharpness = 1.0 / (1.0 + max(boundary_gap - 1, 0))

        # Consistency: saturates at 1.0 after 3 concordant probes.
        consistency = min(boundary_consistent_count / 3.0, 1.0)

        # Range factor: small ranges are inherently more trustworthy.
        range_factor = min(50.0 / max(total_range, 1), 1.0)

        confidence = (
            0.50 * sharpness
            + 0.35 * consistency
            + 0.15 * range_factor
        )
        return max(0.0, min(confidence, 1.0))

    # ------------------------------------------------------------------
    # Verdict determination
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_verdict(
        before_metrics: Dict[str, Any],
        after_metrics: Dict[str, Any],
        verifier_type: VerifierType,
    ) -> VerdictResult:
        """Compare before / after metrics and assign a verdict.

        Decision matrix
        ----------------
        +-----------------+--------------+-----------------------------------+
        | Baseline        | Post-patch   | Verdict                           |
        +=================+==============+===================================+
        | passing         | passing      | INCONCLUSIVE (patch unnecessary)  |
        | passing         | failing      | REJECTED (patch broke things)     |
        | failing         | passing      | FIXED                             |
        | failing         | failing      | IMPROVED / REJECTED / INCONCLUSIVE|
        +-----------------+--------------+-----------------------------------+

        When both baseline and post-patch are failing, the primary metric
        for the given *verifier_type* is compared.  An improvement of at
        least ``_IMPROVEMENT_THRESHOLD`` (10 %) qualifies as ``IMPROVED``;
        a regression of the same magnitude yields ``REJECTED``; anything
        in between is ``INCONCLUSIVE``.
        """
        if not after_metrics:
            # No patch was applied -- baseline-only run.
            if before_metrics.get("passed", False):
                return VerdictResult.INCONCLUSIVE
            return VerdictResult.INCONCLUSIVE

        before_passed = before_metrics.get("passed", False)
        after_passed = after_metrics.get("passed", False)

        # Simple boolean transitions.
        if before_passed and after_passed:
            return VerdictResult.INCONCLUSIVE
        if before_passed and not after_passed:
            return VerdictResult.REJECTED
        if not before_passed and after_passed:
            return VerdictResult.FIXED

        # Both failing -- compare the primary metric.
        metric_key = _PRIMARY_METRIC_KEY.get(verifier_type, "score")

        before_score = _extract_numeric(before_metrics, metric_key)
        after_score = _extract_numeric(after_metrics, metric_key)

        if before_score is not None and after_score is not None:
            if before_score == 0:
                # Avoid division by zero; no baseline signal to compare.
                return VerdictResult.INCONCLUSIVE

            # For all primary metrics, *lower* is better.
            relative_change = (before_score - after_score) / abs(before_score)

            if relative_change >= _IMPROVEMENT_THRESHOLD:
                return VerdictResult.IMPROVED
            if relative_change <= -_IMPROVEMENT_THRESHOLD:
                return VerdictResult.REJECTED

        return VerdictResult.INCONCLUSIVE


# ---------------------------------------------------------------------------
# Module-level utilities
# ---------------------------------------------------------------------------

def _extract_numeric(
    metrics: Dict[str, Any],
    key: str,
) -> Optional[float]:
    """Safely extract a numeric value from a metrics dictionary.

    Returns ``None`` if *key* is absent or its value is not numeric.
    Supports combined keys like ``"nan_inf_count"`` by also checking
    component keys (``"nan_count"`` + ``"inf_count"``).
    """
    value = metrics.get(key)
    if isinstance(value, (int, float)):
        return float(value)

    # Special-case: ``nan_inf_count`` may be stored as separate fields.
    if key == "nan_inf_count":
        nan_val = metrics.get("nan_count")
        inf_val = metrics.get("inf_count")
        if isinstance(nan_val, (int, float)) and isinstance(inf_val, (int, float)):
            return float(nan_val) + float(inf_val)

    return None
