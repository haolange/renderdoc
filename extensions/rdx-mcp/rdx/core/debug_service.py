"""
Shader debugging service for RDX-MCP.

Wraps RenderDoc's pixel, vertex, and compute-thread shader debugging APIs
behind an async interface.  The service lazily imports the ``renderdoc``
module so that the rest of the package can be loaded without it.

Key capabilities:
    * Step through a pixel shader looking for the first NaN/Inf output.
    * Collect a full shader trace up to a configurable step limit.
    * Debug a specific vertex invocation.
    * Persist traces as JSON artifacts for later analysis.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from rdx.models import (
    ArtifactRef,
    DebugStep,
    GraphicsAPI,
    PixelDebugResult,
)

if TYPE_CHECKING:
    pass  # avoid heavyweight imports at module scope

logger = logging.getLogger("rdx.core.debug_service")

# APIs known to support shader debugging in RenderDoc.
_DEBUG_SUPPORTED_APIS = frozenset({
    GraphicsAPI.D3D11,
    GraphicsAPI.D3D12,
    GraphicsAPI.VULKAN,
})


class DebugService:
    """High-level async wrapper around RenderDoc shader-debug primitives.

    All public methods accept loose service references (session_manager,
    artifact_store) so that the service is stateless and easy to test.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def debug_pixel(
        self,
        session_id: str,
        event_id: int,
        x: int,
        y: int,
        session_manager: Any,
        artifact_store: Any,
        *,
        sample: int = 0,
        mode: str = "run_to_naninf",
        max_steps: int = 20_000,
    ) -> PixelDebugResult:
        """Debug a pixel shader invocation at *(x, y)*.

        Parameters
        ----------
        session_id:
            Active session identifier.
        event_id:
            Draw-call event to debug.
        x, y:
            Pixel coordinates in the render target.
        session_manager:
            Reference to the session manager (provides replay controller).
        artifact_store:
            Reference to the artifact store (for trace persistence).
        sample:
            MSAA sample index (default 0).
        mode:
            ``"run_to_naninf"`` -- stop at first NaN/Inf variable.
            ``"full_trace"``    -- collect every step up to *max_steps*.
        max_steps:
            Hard cap on shader steps to prevent runaway traces.

        Returns
        -------
        PixelDebugResult
            Contains the trace artifact, total steps, and the first
            NaN/Inf step (if any).
        """
        # -- Guard: is shader debugging supported? --------------------------
        if not await self._check_debug_support(session_id, session_manager):
            return PixelDebugResult(
                ok=False,
                notes=(
                    "Shader debugging is not supported for the current "
                    "graphics API or driver."
                ),
            )

        # Lazy import of the renderdoc module.
        rd = _lazy_import_renderdoc()
        if rd is None:
            return PixelDebugResult(
                ok=False,
                notes="renderdoc Python module is not available.",
            )

        # -- Obtain replay controller ---------------------------------------
        controller = await self._get_controller(
            session_id, session_manager,
        )
        if controller is None:
            return PixelDebugResult(
                ok=False,
                notes="Could not obtain replay controller.",
            )

        trace = None
        try:
            # -- Navigate to the target event -------------------------------
            controller.SetFrameEvent(event_id, True)

            # -- Start pixel debug session ----------------------------------
            inputs = rd.DebugPixelInputs()
            inputs.sample = sample

            trace = controller.DebugPixel(x, y, inputs)

            if trace is None or not trace.valid:
                return PixelDebugResult(
                    ok=False,
                    notes=(
                        f"DebugPixel({x}, {y}) at event {event_id} "
                        f"returned an invalid trace. Shader debugging "
                        f"may not be available for this draw call."
                    ),
                )

            # -- Iterate debug states ---------------------------------------
            debugger = trace.debugger
            steps: List[DebugStep] = []
            naninf_step: Optional[DebugStep] = None
            step_index = 0

            while step_index < max_steps:
                try:
                    states = controller.ContinueDebug(debugger)
                except Exception as exc:
                    logger.warning(
                        "ContinueDebug raised at step %d: %s",
                        step_index,
                        exc,
                    )
                    break

                if not states:
                    # No more states -- shader execution finished.
                    break

                for state in states:
                    variables = self._extract_variables(state)
                    has_nan_inf = any(
                        self._has_naninf(v)
                        for v in variables.values()
                        if isinstance(v, (int, float))
                    )

                    step = DebugStep(
                        step_index=getattr(state, "stepIndex", step_index),
                        registers=variables,
                        is_naninf=has_nan_inf,
                    )
                    steps.append(step)
                    step_index += 1

                    if has_nan_inf and naninf_step is None:
                        naninf_step = step

                    if mode == "run_to_naninf" and naninf_step is not None:
                        break

                    if step_index >= max_steps:
                        break

                # Early exit if we found what we were looking for.
                if mode == "run_to_naninf" and naninf_step is not None:
                    break

        except Exception as exc:
            logger.exception(
                "Pixel debug failed for event %d at (%d, %d)",
                event_id,
                x,
                y,
            )
            return PixelDebugResult(
                ok=False,
                notes=f"Pixel debug failed: {exc}",
            )
        finally:
            # Always free the trace to release driver resources.
            if trace is not None:
                try:
                    controller.FreeTrace(trace)
                except Exception:
                    logger.debug("FreeTrace raised (non-critical)", exc_info=True)

        # -- Persist trace as JSON artifact ---------------------------------
        trace_artifact: Optional[ArtifactRef] = None
        if artifact_store is not None and steps:
            try:
                trace_payload = {
                    "session_id": session_id,
                    "event_id": event_id,
                    "pixel": {"x": x, "y": y},
                    "sample": sample,
                    "mode": mode,
                    "total_steps": len(steps),
                    "naninf_step_index": (
                        naninf_step.step_index if naninf_step else None
                    ),
                    "steps": [
                        {
                            "step_index": s.step_index,
                            "instruction": s.instruction,
                            "registers": _sanitize_for_json(s.registers),
                            "is_naninf": s.is_naninf,
                        }
                        for s in steps
                    ],
                }
                trace_artifact = await artifact_store.store_json(
                    data=trace_payload,
                    name=(
                        f"pixel_trace_evt{event_id}_"
                        f"x{x}_y{y}"
                    ),
                    session_id=session_id,
                    meta={
                        "type": "pixel_debug_trace",
                        "event_id": event_id,
                        "x": x,
                        "y": y,
                        "mode": mode,
                    },
                )
            except Exception as exc:
                logger.warning("Failed to persist trace artifact: %s", exc)

        return PixelDebugResult(
            ok=True,
            trace_artifact=trace_artifact,
            total_steps=len(steps),
            naninf_step=naninf_step,
            notes=(
                f"Traced {len(steps)} steps. "
                + (
                    f"First NaN/Inf at step {naninf_step.step_index}."
                    if naninf_step
                    else "No NaN/Inf detected."
                )
            ),
        )

    # ------------------------------------------------------------------

    async def debug_vertex(
        self,
        session_id: str,
        event_id: int,
        vertex_id: int,
        instance_id: int,
        session_manager: Any,
        artifact_store: Any,
        *,
        max_steps: int = 20_000,
    ) -> Dict[str, Any]:
        """Debug a vertex shader invocation.

        Parameters
        ----------
        session_id:
            Active session identifier.
        event_id:
            Draw-call event containing the vertex.
        vertex_id:
            Index of the vertex to debug.
        instance_id:
            Instance index (for instanced draws; typically 0).
        session_manager:
            Reference to the session manager.
        artifact_store:
            Reference to the artifact store.
        max_steps:
            Hard cap on shader steps.

        Returns
        -------
        dict
            Summary including ``ok``, ``total_steps``, ``trace_artifact``,
            and ``notes``.
        """
        if not await self._check_debug_support(session_id, session_manager):
            return {
                "ok": False,
                "notes": (
                    "Shader debugging is not supported for the current "
                    "graphics API or driver."
                ),
            }

        rd = _lazy_import_renderdoc()
        if rd is None:
            return {
                "ok": False,
                "notes": "renderdoc Python module is not available.",
            }

        controller = await self._get_controller(session_id, session_manager)
        if controller is None:
            return {"ok": False, "notes": "Could not obtain replay controller."}

        trace = None
        try:
            controller.SetFrameEvent(event_id, True)

            # DebugVertex(vertexId, instanceId, idx, view)
            # idx  = 0 (provoking vertex index)
            # view = 0 (multiview index)
            trace = controller.DebugVertex(vertex_id, instance_id, 0, 0)

            if trace is None or not trace.valid:
                return {
                    "ok": False,
                    "notes": (
                        f"DebugVertex({vertex_id}, inst={instance_id}) "
                        f"at event {event_id} returned an invalid trace."
                    ),
                }

            debugger = trace.debugger
            steps: List[Dict[str, Any]] = []
            step_index = 0

            while step_index < max_steps:
                try:
                    states = controller.ContinueDebug(debugger)
                except Exception as exc:
                    logger.warning(
                        "ContinueDebug raised at step %d: %s",
                        step_index,
                        exc,
                    )
                    break

                if not states:
                    break

                for state in states:
                    variables = self._extract_variables(state)
                    steps.append({
                        "step_index": getattr(state, "stepIndex", step_index),
                        "registers": _sanitize_for_json(variables),
                    })
                    step_index += 1
                    if step_index >= max_steps:
                        break

        except Exception as exc:
            logger.exception(
                "Vertex debug failed for event %d, vtx %d",
                event_id,
                vertex_id,
            )
            return {"ok": False, "notes": f"Vertex debug failed: {exc}"}
        finally:
            if trace is not None:
                try:
                    controller.FreeTrace(trace)
                except Exception:
                    logger.debug("FreeTrace raised (non-critical)", exc_info=True)

        # Persist artifact
        trace_artifact: Optional[ArtifactRef] = None
        if artifact_store is not None and steps:
            try:
                payload = {
                    "session_id": session_id,
                    "event_id": event_id,
                    "vertex_id": vertex_id,
                    "instance_id": instance_id,
                    "total_steps": len(steps),
                    "steps": steps,
                }
                trace_artifact = await artifact_store.store_json(
                    data=payload,
                    name=(
                        f"vertex_trace_evt{event_id}_"
                        f"vtx{vertex_id}_inst{instance_id}"
                    ),
                    session_id=session_id,
                    meta={
                        "type": "vertex_debug_trace",
                        "event_id": event_id,
                        "vertex_id": vertex_id,
                        "instance_id": instance_id,
                    },
                )
            except Exception as exc:
                logger.warning("Failed to persist vertex trace artifact: %s", exc)

        return {
            "ok": True,
            "total_steps": len(steps),
            "trace_artifact": trace_artifact,
            "notes": f"Traced {len(steps)} vertex shader steps.",
        }

    # ------------------------------------------------------------------
    # Support-check
    # ------------------------------------------------------------------

    async def _check_debug_support(
        self,
        session_id: str,
        session_manager: Any,
    ) -> bool:
        """Return ``True`` if the current API/driver supports shader debugging.

        D3D11, D3D12, and Vulkan are supported.
        OpenGL and OpenGL ES are not.
        """
        try:
            session_info = await session_manager.get_session(session_id)
            if session_info is None:
                logger.warning("Session %s not found", session_id)
                return False

            capabilities = getattr(session_info, "capabilities", None)
            if capabilities is not None:
                # If the session already exposes a flag, honour it.
                if hasattr(capabilities, "shader_debug_supported"):
                    return bool(capabilities.shader_debug_supported)

            # Fall back to API-based heuristic.
            api = GraphicsAPI.UNKNOWN
            if capabilities is not None and hasattr(capabilities, "api"):
                api = capabilities.api
            elif hasattr(session_info, "api"):
                api = session_info.api

            return api in _DEBUG_SUPPORTED_APIS

        except Exception as exc:
            logger.warning(
                "Could not determine debug support for session %s: %s",
                session_id,
                exc,
            )
            # Optimistic: try anyway and let DebugPixel/DebugVertex fail
            # gracefully if it truly is not supported.
            return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _get_controller(
        session_id: str,
        session_manager: Any,
    ) -> Any:
        """Obtain the replay controller for *session_id*.

        Returns ``None`` on failure.
        """
        try:
            controller = await session_manager.get_controller(session_id)
            return controller
        except Exception as exc:
            logger.error(
                "Failed to get controller for session %s: %s",
                session_id,
                exc,
            )
            return None

    @staticmethod
    def _extract_variables(state: Any) -> Dict[str, Any]:
        """Pull variable names and values out of a ``ShaderDebugState``.

        Each ``change`` in the state carries a ``SourceVariableMapping`` with
        ``name`` and ``value`` attributes.  We flatten these into a dict of
        ``{name: value}``.
        """
        result: Dict[str, Any] = {}

        changes = getattr(state, "changes", None)
        if changes is None:
            return result

        for change in changes:
            name = getattr(change, "name", None)
            if name is None:
                continue

            # RenderDoc exposes the value through different accessors
            # depending on the type.  Try the most common float path first.
            value: Any = None

            # Try .value first (SourceVariableMapping stores the new value).
            raw_value = getattr(change, "value", None)
            if raw_value is not None:
                # ShaderVariable-style: floatValue, uintValue, sintValue
                float_val = getattr(raw_value, "floatValue", None)
                if float_val is not None:
                    # floatValue is typically a 4-element array (xyzw).
                    try:
                        value = [float(float_val[i]) for i in range(4)]
                    except (IndexError, TypeError):
                        value = float(float_val)
                else:
                    uint_val = getattr(raw_value, "uintValue", None)
                    if uint_val is not None:
                        try:
                            value = [int(uint_val[i]) for i in range(4)]
                        except (IndexError, TypeError):
                            value = int(uint_val)
                    else:
                        sint_val = getattr(raw_value, "sintValue", None)
                        if sint_val is not None:
                            try:
                                value = [int(sint_val[i]) for i in range(4)]
                            except (IndexError, TypeError):
                                value = int(sint_val)
                        else:
                            # Last resort: use repr.
                            value = repr(raw_value)

            result[str(name)] = value

        return result

    @staticmethod
    def _has_naninf(value: Any) -> bool:
        """Return ``True`` if *value* is NaN or Inf.

        Handles single floats and lists/tuples of floats.
        """
        if isinstance(value, (list, tuple)):
            return any(
                DebugService._has_naninf(v)
                for v in value
            )
        if isinstance(value, float):
            return math.isnan(value) or math.isinf(value)
        if isinstance(value, int):
            return False
        return False


# ---------------------------------------------------------------------------
# Module-level helpers
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
            "renderdoc Python module not found. Shader debugging will be "
            "unavailable.  Ensure the module is on sys.path or set "
            "RDX_RENDERDOC_PATH."
        )
        _rd_module = None

    return _rd_module


def _sanitize_for_json(data: Any) -> Any:
    """Make *data* JSON-serializable.

    Replaces ``float('nan')`` and ``float('inf')`` with string sentinels so
    that ``json.dumps`` does not raise.
    """
    if isinstance(data, dict):
        return {k: _sanitize_for_json(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_sanitize_for_json(v) for v in data]
    if isinstance(data, float):
        if math.isnan(data):
            return "__NaN__"
        if math.isinf(data):
            return "__Inf__" if data > 0 else "__-Inf__"
    return data
