"""
Pluggable verifier engine for RDX-MCP.

Each verifier takes experiment inputs (context) and returns a structured
verdict with metrics, anomaly information, and artifact references.

Verifiers registered by default:
    naninf       -- detect NaN / Inf pixels in render output
    image_diff   -- compare render output against a reference image
    pixel_stats  -- check pixel statistics in a region
    binding_diff -- compare resource bindings between two events
"""

from __future__ import annotations

import abc
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from rdx.models import (
    AnomalyInfo,
    ArtifactRef,
    BBox,
    VerifierType,
)

if TYPE_CHECKING:
    pass  # forward references only; no heavy imports at module level

logger = logging.getLogger("rdx.core.verifiers")


# ---------------------------------------------------------------------------
# Data-transfer objects
# ---------------------------------------------------------------------------

@dataclass
class VerifyContext:
    """All inputs a verifier needs to do its job."""

    session_id: str
    capture_id: str
    event_id: int
    session_manager: Any          # rdx.core.session.SessionManager
    artifact_store: Any           # rdx.core.artifacts.ArtifactStore
    render_service: Any           # rdx.core.render.RenderService
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VerifyResult:
    """Structured verdict returned by every verifier."""

    passed: bool
    metrics: Dict[str, Any] = field(default_factory=dict)
    anomaly: Optional[AnomalyInfo] = None
    artifacts: List[ArtifactRef] = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseVerifier(abc.ABC):
    """Interface that every verifier must implement."""

    @abc.abstractmethod
    def name(self) -> str:
        """Machine-readable identifier (e.g. ``"naninf"``)."""
        ...

    @abc.abstractmethod
    async def verify(self, context: VerifyContext) -> VerifyResult:
        """Execute the verification and return a result."""
        ...


# ---------------------------------------------------------------------------
# NaN / Inf verifier
# ---------------------------------------------------------------------------

class NaNInfVerifier(BaseVerifier):
    """Detect NaN and Inf values in the rendered output at a given event.

    Workflow
    --------
    1.  Navigate replay to *event_id* and render the output.
    2.  Read back the pixel buffer (float32 RGBA).
    3.  Compute a NaN/Inf mask via ``image_utils.compute_naninf_mask()``.
    4.  Build metrics and (when anomalous) an ``AnomalyInfo`` with bounding
        box and mask artifact.
    """

    def name(self) -> str:
        return "naninf"

    async def verify(self, context: VerifyContext) -> VerifyResult:
        # Late imports -- renderdoc / numpy may not be on sys.path at
        # module-load time.
        try:
            from rdx.utils import image_utils
        except ImportError:
            logger.error("image_utils not available; cannot run NaNInfVerifier")
            return VerifyResult(
                passed=False,
                notes="image_utils module unavailable",
            )

        render_svc = context.render_service
        artifact_store = context.artifact_store

        # 1. Render the output --------------------------------------------------
        try:
            render_result = await render_svc.render_event(
                session_id=context.session_id,
                capture_id=context.capture_id,
                event_id=context.event_id,
            )
            if render_result is None:
                return VerifyResult(
                    passed=False,
                    notes="render_event returned None",
                )
        except Exception as exc:
            logger.exception("Failed to render event %d", context.event_id)
            return VerifyResult(
                passed=False,
                notes=f"Render failed: {exc}",
            )

        # 2. Readback raw pixel data -------------------------------------------
        try:
            pixel_data = await render_svc.readback_texture(
                session_id=context.session_id,
                capture_id=context.capture_id,
                event_id=context.event_id,
            )
            if pixel_data is None:
                return VerifyResult(
                    passed=False,
                    notes="Texture readback returned None",
                )
            width = pixel_data.get("width", 0)
            height = pixel_data.get("height", 0)
            pixels = pixel_data.get("data")  # expected numpy float32 array
            if pixels is None or width == 0 or height == 0:
                return VerifyResult(
                    passed=False,
                    notes="Invalid pixel data from readback",
                )
        except Exception as exc:
            logger.exception("Texture readback failed for event %d",
                             context.event_id)
            return VerifyResult(
                passed=False,
                notes=f"Readback failed: {exc}",
            )

        # 3. Compute NaN/Inf mask -----------------------------------------------
        try:
            mask_result = image_utils.compute_naninf_mask(
                pixels, width, height,
            )
        except Exception as exc:
            logger.exception("compute_naninf_mask failed")
            return VerifyResult(
                passed=False,
                notes=f"NaN/Inf mask computation failed: {exc}",
            )

        nan_count: int = mask_result.get("nan_count", 0)
        inf_count: int = mask_result.get("inf_count", 0)
        total_pixels: int = width * height
        density = (nan_count + inf_count) / max(total_pixels, 1)
        mask_image = mask_result.get("mask")       # uint8 image or None
        bbox_raw = mask_result.get("bbox")         # (x0, y0, x1, y1) or None

        passed = nan_count == 0 and inf_count == 0

        metrics: Dict[str, Any] = {
            "nan_count": nan_count,
            "inf_count": inf_count,
            "total_pixels": total_pixels,
            "density": density,
        }

        # 4. Build anomaly & artifacts if something was detected ----------------
        anomaly: Optional[AnomalyInfo] = None
        artifacts: List[ArtifactRef] = []

        if not passed:
            bbox: Optional[BBox] = None
            if bbox_raw is not None:
                try:
                    bbox = BBox(
                        x0=int(bbox_raw[0]),
                        y0=int(bbox_raw[1]),
                        x1=int(bbox_raw[2]),
                        y1=int(bbox_raw[3]),
                    )
                except (IndexError, TypeError, ValueError):
                    logger.warning("Could not parse NaN/Inf bounding box")

            # Store mask image as artifact
            mask_artifact_ref: Optional[ArtifactRef] = None
            if mask_image is not None and artifact_store is not None:
                try:
                    mask_artifact_ref = await artifact_store.store_image(
                        image=mask_image,
                        name=f"naninf_mask_evt{context.event_id}",
                        session_id=context.session_id,
                        meta={
                            "verifier": "naninf",
                            "event_id": context.event_id,
                            "nan_count": nan_count,
                            "inf_count": inf_count,
                        },
                    )
                    artifacts.append(mask_artifact_ref)
                except Exception as exc:
                    logger.warning("Failed to store NaN/Inf mask artifact: %s",
                                   exc)

            anomaly = AnomalyInfo(
                type="naninf",
                bbox=bbox,
                nan_count=nan_count,
                inf_count=inf_count,
                total_pixels=total_pixels,
                density=density,
                mask_artifact=mask_artifact_ref,
            )

        return VerifyResult(
            passed=passed,
            metrics=metrics,
            anomaly=anomaly,
            artifacts=artifacts,
            notes="" if passed else (
                f"Detected {nan_count} NaN and {inf_count} Inf pixels "
                f"({density:.4%} of {total_pixels} total)"
            ),
        )


# ---------------------------------------------------------------------------
# Image-diff verifier
# ---------------------------------------------------------------------------

class ImageDiffVerifier(BaseVerifier):
    """Compare the render output against a reference image.

    Required *params* keys:
        reference_image_path (str): Path to the reference image file.
    Optional:
        threshold (float): Mean-diff threshold below which the check passes.
            Default ``0.01``.
    """

    DEFAULT_THRESHOLD: float = 0.01

    def name(self) -> str:
        return "image_diff"

    async def verify(self, context: VerifyContext) -> VerifyResult:
        try:
            from rdx.utils import image_utils
        except ImportError:
            return VerifyResult(
                passed=False,
                notes="image_utils module unavailable",
            )

        reference_path: Optional[str] = context.params.get(
            "reference_image_path",
        )
        if not reference_path:
            return VerifyResult(
                passed=False,
                notes="reference_image_path not provided in params",
            )

        threshold: float = float(
            context.params.get("threshold", self.DEFAULT_THRESHOLD),
        )

        render_svc = context.render_service
        artifact_store = context.artifact_store

        # 1. Render current output ----------------------------------------------
        try:
            pixel_data = await render_svc.readback_texture(
                session_id=context.session_id,
                capture_id=context.capture_id,
                event_id=context.event_id,
            )
            if pixel_data is None:
                return VerifyResult(
                    passed=False,
                    notes="Texture readback returned None",
                )
            current_image = pixel_data.get("data")
            width = pixel_data.get("width", 0)
            height = pixel_data.get("height", 0)
            if current_image is None or width == 0 or height == 0:
                return VerifyResult(
                    passed=False,
                    notes="Invalid pixel data from readback",
                )
        except Exception as exc:
            logger.exception("Render/readback failed for event %d",
                             context.event_id)
            return VerifyResult(
                passed=False,
                notes=f"Render/readback failed: {exc}",
            )

        # 2. Load reference image -----------------------------------------------
        try:
            ref_image = image_utils.load_image(reference_path)
            if ref_image is None:
                return VerifyResult(
                    passed=False,
                    notes=f"Could not load reference image: {reference_path}",
                )
        except Exception as exc:
            logger.exception("Failed to load reference image %s",
                             reference_path)
            return VerifyResult(
                passed=False,
                notes=f"Failed to load reference image: {exc}",
            )

        # 3. Compute diff -------------------------------------------------------
        try:
            diff_result = image_utils.compute_diff_map(
                current_image, ref_image, width, height,
            )
        except Exception as exc:
            logger.exception("compute_diff_map failed")
            return VerifyResult(
                passed=False,
                notes=f"Diff computation failed: {exc}",
            )

        mean_diff: float = diff_result.get("mean_diff", 1.0)
        max_diff: float = diff_result.get("max_diff", 1.0)
        diff_pixel_count: int = diff_result.get("diff_pixel_count", 0)
        total_pixels = width * height
        diff_ratio = diff_pixel_count / max(total_pixels, 1)
        heatmap = diff_result.get("heatmap")  # uint8 RGB image

        passed = mean_diff < threshold

        metrics: Dict[str, Any] = {
            "mean_diff": mean_diff,
            "max_diff": max_diff,
            "diff_pixel_count": diff_pixel_count,
            "diff_ratio": diff_ratio,
            "threshold": threshold,
        }

        artifacts: List[ArtifactRef] = []

        # 4. Store diff heatmap artifact ----------------------------------------
        if heatmap is not None and artifact_store is not None:
            try:
                heatmap_ref = await artifact_store.store_image(
                    image=heatmap,
                    name=f"diff_heatmap_evt{context.event_id}",
                    session_id=context.session_id,
                    meta={
                        "verifier": "image_diff",
                        "event_id": context.event_id,
                        "mean_diff": mean_diff,
                        "max_diff": max_diff,
                        "threshold": threshold,
                    },
                )
                artifacts.append(heatmap_ref)
            except Exception as exc:
                logger.warning("Failed to store diff heatmap artifact: %s", exc)

        anomaly: Optional[AnomalyInfo] = None
        if not passed:
            anomaly = AnomalyInfo(
                type="image_diff",
                stats={
                    "mean_diff": mean_diff,
                    "max_diff": max_diff,
                    "diff_pixel_count": diff_pixel_count,
                    "diff_ratio": diff_ratio,
                },
            )

        return VerifyResult(
            passed=passed,
            metrics=metrics,
            anomaly=anomaly,
            artifacts=artifacts,
            notes="" if passed else (
                f"Image diff exceeds threshold: mean={mean_diff:.6f} "
                f"(threshold={threshold:.6f}), {diff_pixel_count} differing "
                f"pixels ({diff_ratio:.4%})"
            ),
        )


# ---------------------------------------------------------------------------
# Pixel-statistics verifier
# ---------------------------------------------------------------------------

class PixelStatsVerifier(BaseVerifier):
    """Verify pixel statistics within a region of the render output.

    Checks for anomalous values (NaN, Inf, extreme magnitudes) and verifies
    that pixel values fall within an expected range.

    Optional *params* keys:
        region (dict): ``{x0, y0, x1, y1}`` bounding box.  Full image if
            omitted.
        expected_range (list[float]): ``[min_val, max_val]`` for valid pixel
            values.  Default ``[0.0, 1.0]``.
        channels (list[int]): Channel indices to check (0=R, 1=G, 2=B, 3=A).
            Default ``[0, 1, 2]``.
    """

    def name(self) -> str:
        return "pixel_stats"

    async def verify(self, context: VerifyContext) -> VerifyResult:
        import math

        render_svc = context.render_service

        # Parse params -----------------------------------------------------------
        region = context.params.get("region")  # {x0, y0, x1, y1} or None
        expected_range = context.params.get("expected_range", [0.0, 1.0])
        channels: List[int] = context.params.get("channels", [0, 1, 2])

        range_min = float(expected_range[0])
        range_max = float(expected_range[1])

        # 1. Read back pixels ----------------------------------------------------
        try:
            pixel_data = await render_svc.readback_texture(
                session_id=context.session_id,
                capture_id=context.capture_id,
                event_id=context.event_id,
            )
            if pixel_data is None:
                return VerifyResult(
                    passed=False,
                    notes="Texture readback returned None",
                )
            pixels = pixel_data.get("data")
            width = pixel_data.get("width", 0)
            height = pixel_data.get("height", 0)
            if pixels is None or width == 0 or height == 0:
                return VerifyResult(
                    passed=False,
                    notes="Invalid pixel data from readback",
                )
        except Exception as exc:
            logger.exception("Readback failed for event %d", context.event_id)
            return VerifyResult(
                passed=False,
                notes=f"Readback failed: {exc}",
            )

        # 2. Determine region bounds ---------------------------------------------
        x0 = 0
        y0 = 0
        x1 = width
        y1 = height
        if region is not None:
            x0 = max(0, int(region.get("x0", 0)))
            y0 = max(0, int(region.get("y0", 0)))
            x1 = min(width, int(region.get("x1", width)))
            y1 = min(height, int(region.get("y1", height)))

        # 3. Iterate pixels in region and collect stats --------------------------
        nan_count = 0
        inf_count = 0
        out_of_range_count = 0
        total_checked = 0
        channel_sums: Dict[int, float] = {ch: 0.0 for ch in channels}
        channel_mins: Dict[int, float] = {ch: float("inf") for ch in channels}
        channel_maxs: Dict[int, float] = {ch: float("-inf") for ch in channels}

        try:
            # pixels is expected to be a flat array or 2D array with RGBA per
            # pixel.  We try to handle both numpy arrays and plain lists.
            for row in range(y0, y1):
                for col in range(x0, x1):
                    base_idx = (row * width + col) * 4
                    for ch in channels:
                        try:
                            val = float(pixels[base_idx + ch])
                        except (IndexError, TypeError):
                            continue

                        total_checked += 1

                        if math.isnan(val):
                            nan_count += 1
                            continue
                        if math.isinf(val):
                            inf_count += 1
                            continue

                        channel_sums[ch] += val
                        if val < channel_mins[ch]:
                            channel_mins[ch] = val
                        if val > channel_maxs[ch]:
                            channel_maxs[ch] = val

                        if val < range_min or val > range_max:
                            out_of_range_count += 1
        except Exception as exc:
            logger.exception("Error iterating pixel data")
            return VerifyResult(
                passed=False,
                notes=f"Pixel iteration error: {exc}",
            )

        total_region_pixels = (x1 - x0) * (y1 - y0)
        anomalous = nan_count + inf_count + out_of_range_count
        passed = anomalous == 0

        # Build per-channel means ------------------------------------------------
        channel_means: Dict[str, float] = {}
        valid_count = total_checked - nan_count - inf_count
        for ch in channels:
            if valid_count > 0:
                channel_means[f"ch{ch}_mean"] = channel_sums[ch] / valid_count
            else:
                channel_means[f"ch{ch}_mean"] = 0.0

        metrics: Dict[str, Any] = {
            "nan_count": nan_count,
            "inf_count": inf_count,
            "out_of_range_count": out_of_range_count,
            "total_checked": total_checked,
            "total_region_pixels": total_region_pixels,
            "anomalous_total": anomalous,
            **channel_means,
        }

        # Include min/max per channel (only for finite values)
        for ch in channels:
            min_v = channel_mins[ch]
            max_v = channel_maxs[ch]
            metrics[f"ch{ch}_min"] = min_v if math.isfinite(min_v) else None
            metrics[f"ch{ch}_max"] = max_v if math.isfinite(max_v) else None

        anomaly: Optional[AnomalyInfo] = None
        if not passed:
            anomaly = AnomalyInfo(
                type="pixel_stats",
                bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1) if region else None,
                nan_count=nan_count,
                inf_count=inf_count,
                total_pixels=total_region_pixels,
                density=anomalous / max(total_region_pixels, 1),
                stats={
                    "out_of_range_count": out_of_range_count,
                    "expected_range": [range_min, range_max],
                },
            )

        return VerifyResult(
            passed=passed,
            metrics=metrics,
            anomaly=anomaly,
            artifacts=[],
            notes="" if passed else (
                f"Region ({x0},{y0})-({x1},{y1}): "
                f"{nan_count} NaN, {inf_count} Inf, "
                f"{out_of_range_count} out-of-range "
                f"[{range_min}, {range_max}]"
            ),
        )


# ---------------------------------------------------------------------------
# Binding-diff verifier
# ---------------------------------------------------------------------------

class BindingDiffVerifier(BaseVerifier):
    """Compare resource bindings between two events (good vs. bad).

    Required *params* keys:
        good_event_id (int): Event that renders correctly.
        bad_event_id  (int): Event that renders incorrectly.

    Optional:
        pipeline_service: Explicit pipeline-service reference.  If omitted
            the verifier will attempt to obtain one from *context.params*.
    """

    def name(self) -> str:
        return "binding_diff"

    async def verify(self, context: VerifyContext) -> VerifyResult:
        good_event_id: Optional[int] = context.params.get("good_event_id")
        bad_event_id: Optional[int] = context.params.get("bad_event_id")

        if good_event_id is None or bad_event_id is None:
            return VerifyResult(
                passed=False,
                notes="Both good_event_id and bad_event_id must be provided",
            )

        pipeline_service = context.params.get("pipeline_service")

        if pipeline_service is None:
            return VerifyResult(
                passed=False,
                notes="pipeline_service is required for binding_diff verifier",
            )

        # 1. Snapshot both events ------------------------------------------------
        try:
            good_snap = await pipeline_service.snapshot(
                session_id=context.session_id,
                capture_id=context.capture_id,
                event_id=int(good_event_id),
            )
            bad_snap = await pipeline_service.snapshot(
                session_id=context.session_id,
                capture_id=context.capture_id,
                event_id=int(bad_event_id),
            )
        except Exception as exc:
            logger.exception("Pipeline snapshot failed")
            return VerifyResult(
                passed=False,
                notes=f"Pipeline snapshot failed: {exc}",
            )

        if good_snap is None or bad_snap is None:
            return VerifyResult(
                passed=False,
                notes="One or both pipeline snapshots returned None",
            )

        # 2. Compare bindings ----------------------------------------------------
        good_bindings = {
            (b.set_or_space, b.binding, b.type): b
            for b in getattr(good_snap, "bindings", [])
        }
        bad_bindings = {
            (b.set_or_space, b.binding, b.type): b
            for b in getattr(bad_snap, "bindings", [])
        }

        all_keys = set(good_bindings.keys()) | set(bad_bindings.keys())

        added: List[Dict[str, Any]] = []
        removed: List[Dict[str, Any]] = []
        changed: List[Dict[str, Any]] = []

        for key in sorted(all_keys):
            good_b = good_bindings.get(key)
            bad_b = bad_bindings.get(key)

            if good_b is None and bad_b is not None:
                added.append({
                    "set_or_space": key[0],
                    "binding": key[1],
                    "type": key[2],
                    "resource_id": bad_b.resource_id,
                    "resource_name": bad_b.resource_name,
                })
            elif good_b is not None and bad_b is None:
                removed.append({
                    "set_or_space": key[0],
                    "binding": key[1],
                    "type": key[2],
                    "resource_id": good_b.resource_id,
                    "resource_name": good_b.resource_name,
                })
            elif good_b is not None and bad_b is not None:
                if (good_b.resource_id != bad_b.resource_id
                        or good_b.format != bad_b.format):
                    changed.append({
                        "set_or_space": key[0],
                        "binding": key[1],
                        "type": key[2],
                        "good_resource_id": good_b.resource_id,
                        "bad_resource_id": bad_b.resource_id,
                        "good_format": good_b.format,
                        "bad_format": bad_b.format,
                    })

        total_diffs = len(added) + len(removed) + len(changed)
        passed = total_diffs == 0

        metrics: Dict[str, Any] = {
            "good_event_id": good_event_id,
            "bad_event_id": bad_event_id,
            "bindings_added": len(added),
            "bindings_removed": len(removed),
            "bindings_changed": len(changed),
            "total_diffs": total_diffs,
        }

        # Store the detailed diff as an artifact --------------------------------
        artifacts: List[ArtifactRef] = []
        artifact_store = context.artifact_store
        if total_diffs > 0 and artifact_store is not None:
            diff_payload = {
                "good_event_id": good_event_id,
                "bad_event_id": bad_event_id,
                "added": added,
                "removed": removed,
                "changed": changed,
            }
            try:
                diff_ref = await artifact_store.store_json(
                    data=diff_payload,
                    name=(
                        f"binding_diff_evt{good_event_id}_"
                        f"vs_evt{bad_event_id}"
                    ),
                    session_id=context.session_id,
                    meta={"verifier": "binding_diff"},
                )
                artifacts.append(diff_ref)
            except Exception as exc:
                logger.warning("Failed to store binding diff artifact: %s", exc)

        anomaly: Optional[AnomalyInfo] = None
        if not passed:
            anomaly = AnomalyInfo(
                type="binding_diff",
                stats={
                    "added": added,
                    "removed": removed,
                    "changed": changed,
                },
            )

        return VerifyResult(
            passed=passed,
            metrics=metrics,
            anomaly=anomaly,
            artifacts=artifacts,
            notes="" if passed else (
                f"Binding differences between event {good_event_id} "
                f"(good) and {bad_event_id} (bad): "
                f"{len(added)} added, {len(removed)} removed, "
                f"{len(changed)} changed"
            ),
        )


# ---------------------------------------------------------------------------
# Verifier engine (registry + dispatcher)
# ---------------------------------------------------------------------------

class VerifierEngine:
    """Registry of verifiers with convenience dispatch.

    Pre-registers the four built-in verifiers on construction.

    Usage::

        engine = VerifierEngine()
        result = await engine.run_verifier("naninf", context)
    """

    DEFAULT_VERIFIER = "naninf"

    def __init__(self) -> None:
        self._registry: Dict[str, BaseVerifier] = {}
        self._register_builtins()

    # -- public API ----------------------------------------------------------

    def register(self, name: str, verifier: BaseVerifier) -> None:
        """Register a verifier under *name* (overwrites existing entry)."""
        if not isinstance(verifier, BaseVerifier):
            raise TypeError(
                f"Expected BaseVerifier instance, got {type(verifier).__name__}"
            )
        self._registry[name] = verifier
        logger.debug("Registered verifier %r", name)

    def get(self, name: str) -> BaseVerifier:
        """Retrieve a registered verifier by name.

        Raises ``KeyError`` if not found.
        """
        try:
            return self._registry[name]
        except KeyError:
            available = ", ".join(sorted(self._registry)) or "(none)"
            raise KeyError(
                f"No verifier registered under {name!r}.  "
                f"Available: {available}"
            ) from None

    async def run_verifier(
        self,
        name: str,
        context: VerifyContext,
    ) -> VerifyResult:
        """Look up verifier *name* and execute it with *context*.

        Catches unexpected exceptions so callers always get a
        ``VerifyResult`` (with ``passed=False`` on failure).
        """
        try:
            verifier = self.get(name)
        except KeyError as exc:
            return VerifyResult(
                passed=False,
                notes=str(exc),
            )

        t0 = time.monotonic()
        try:
            result = await verifier.verify(context)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.exception(
                "Verifier %r raised after %.2fs", name, elapsed,
            )
            return VerifyResult(
                passed=False,
                notes=f"Verifier {name!r} raised: {exc}",
            )

        elapsed = time.monotonic() - t0
        logger.info(
            "Verifier %r completed in %.2fs -- passed=%s",
            name,
            elapsed,
            result.passed,
        )
        return result

    # -- internals -----------------------------------------------------------

    def _register_builtins(self) -> None:
        self.register("naninf", NaNInfVerifier())
        self.register("image_diff", ImageDiffVerifier())
        self.register("pixel_stats", PixelStatsVerifier())
        self.register("binding_diff", BindingDiffVerifier())
