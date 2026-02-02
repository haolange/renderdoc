"""
RDX-MCP Server -- main MCP server for GPU debug automation.

Registers all MCP tools, wires up service instances via lifespan,
and exposes two transport entry points:

* ``main()``     -- stdio transport (default, used by ``rdx-mcp`` CLI).
* ``main_sse()`` -- SSE transport for web-based clients.

Every tool function is ``async``, follows the response envelope pattern
(``ToolResponse``), and delegates to the appropriate service layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

from rdx.config import RdxConfig
from rdx.models import (
    ArtifactRef,
    BisectResult,
    BisectStrategy,
    CaptureInfo,
    ErrorDetail,
    EventNode,
    ExperimentDef,
    ExperimentResult,
    PassFingerprint,
    PatchOp,
    PatchResult,
    PatchSpec,
    PerfResult,
    PipelineSnapshot,
    PixelDebugResult,
    SessionInfo,
    ShaderExportBundle,
    ShaderFingerprint,
    ShaderStage,
    TaskInput,
    TaskState,
    ToolResponse,
    VerifierConfig,
    VerifierType,
    _new_id,
)
from rdx.core.session_manager import SessionError, SessionManager
from rdx.core.event_graph import EventGraphService
from rdx.core.render_service import RenderService
from rdx.core.pipeline_service import PipelineService
from rdx.core.verifiers import VerifierEngine, VerifyContext, VerifyResult
from rdx.core.patch_engine import PatchEngine
from rdx.core.experiment_runner import ExperimentRunner
from rdx.core.debug_service import DebugService
from rdx.core.perf_service import PerfService
from rdx.core.report_builder import ReportBuilder
from rdx.knowledge.fingerprint_store import FingerprintStore
from rdx.knowledge.kb_connector import KBConnector
from rdx.skills.workflows import run_full_debug_pipeline
from rdx.utils.artifact_store import ArtifactStore
from rdx.utils.scheduler import WorkerScheduler

logger = logging.getLogger("rdx.server")

# ---------------------------------------------------------------------------
# Global service instances -- populated during lifespan
# ---------------------------------------------------------------------------

_config: Optional[RdxConfig] = None
_session_manager: Optional[SessionManager] = None
_event_graph_service: Optional[EventGraphService] = None
_render_service: Optional[RenderService] = None
_pipeline_service: Optional[PipelineService] = None
_verifier_engine: Optional[VerifierEngine] = None
_patch_engine: Optional[PatchEngine] = None
_experiment_runner: Optional[ExperimentRunner] = None
_debug_service: Optional[DebugService] = None
_perf_service: Optional[PerfService] = None
_report_builder: Optional[ReportBuilder] = None
_fingerprint_store: Optional[FingerprintStore] = None
_kb_connector: Optional[KBConnector] = None
_artifact_store: Optional[ArtifactStore] = None
_scheduler: Optional[WorkerScheduler] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_response(
    *,
    trace_id: Optional[str] = None,
    artifact: Optional[ArtifactRef] = None,
    **extra: Any,
) -> str:
    """Build and serialise a successful ToolResponse."""
    resp = ToolResponse(
        ok=True,
        trace_id=trace_id or _new_id("trc"),
        artifact=artifact,
    )
    data = resp.model_dump(mode="json", exclude_none=True)
    if extra:
        data.update(extra)
    return json.dumps(data)


def _err_response(
    code: str,
    message: str,
    *,
    trace_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> str:
    """Build and serialise an error ToolResponse."""
    resp = ToolResponse(
        ok=False,
        trace_id=trace_id or _new_id("trc"),
        error=ErrorDetail(code=code, message=message, details=details),
    )
    return json.dumps(resp.model_dump(mode="json", exclude_none=True))


def _session_err_response(exc: SessionError) -> str:
    """Build a ToolResponse from a SessionError."""
    return _err_response(
        code=exc.detail.code,
        message=exc.detail.message,
        details=exc.detail.details,
    )


# ---------------------------------------------------------------------------
# Lifespan: initialise and tear down services
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(server: FastMCP):
    """Initialise all services on startup and clean up on shutdown."""
    global _config, _session_manager, _event_graph_service, _render_service
    global _pipeline_service, _verifier_engine, _patch_engine
    global _experiment_runner, _debug_service, _perf_service
    global _report_builder, _fingerprint_store, _kb_connector
    global _artifact_store, _scheduler

    logger.info("RDX-MCP server starting up...")

    # -- Configuration -------------------------------------------------------
    _config = RdxConfig.from_env()

    # -- Artifact store ------------------------------------------------------
    artifact_root = Path(
        os.environ.get("RDX_ARTIFACT_DIR", "/tmp/rdx-artifacts")
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    _artifact_store = ArtifactStore(root=artifact_root)

    # -- Core services -------------------------------------------------------
    _session_manager = SessionManager()
    _event_graph_service = EventGraphService()
    _render_service = RenderService()
    _pipeline_service = PipelineService()
    _verifier_engine = VerifierEngine()
    _patch_engine = PatchEngine()
    _debug_service = DebugService()
    _perf_service = PerfService()
    _report_builder = ReportBuilder()

    # -- Experiment runner (depends on core services) ------------------------
    _experiment_runner = ExperimentRunner(
        session_manager=_session_manager,
        render_service=_render_service,
        verifier_engine=_verifier_engine,
        patch_engine=_patch_engine,
        artifact_store=_artifact_store,
    )

    # -- Worker scheduler ----------------------------------------------------
    _scheduler = WorkerScheduler(
        max_local_workers=_config.worker.max_local_workers,
        max_remote_workers=_config.worker.max_remote_workers,
    )

    # -- Knowledge services --------------------------------------------------
    db_dir = Path(
        os.environ.get("RDX_DB_DIR", "/tmp/rdx-db")
    )
    db_dir.mkdir(parents=True, exist_ok=True)

    _fingerprint_store = FingerprintStore(db_path=db_dir / "fingerprints.db")
    await _fingerprint_store.initialize()

    index_dirs = []
    index_env = os.environ.get("RDX_KB_INDEX_DIRS", "")
    if index_env:
        index_dirs = [Path(d.strip()) for d in index_env.split(":") if d.strip()]

    _kb_connector = KBConnector(
        index_dirs=index_dirs,
        db_path=db_dir / "kb_index.db",
    )
    await _kb_connector.initialize()

    logger.info("RDX-MCP server ready.")

    try:
        yield
    finally:
        # Shutdown: close any open sessions.
        logger.info("RDX-MCP server shutting down...")
        if _session_manager is not None:
            for sid in list(_session_manager._sessions.keys()):
                try:
                    await _session_manager.close_session(sid)
                except Exception:
                    logger.warning("Error closing session %s during shutdown", sid)
        logger.info("RDX-MCP server stopped.")


# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "rdx-mcp",
    description=(
        "RDX-MCP: RenderDoc GPU Debug Server. "
        "Provides 21 tools for automated GPU capture analysis, "
        "shader debugging, anomaly detection, bisect search, "
        "experiment management, and report generation."
    ),
    lifespan=_lifespan,
)


# ===================================================================
# Tool 1: rd.session.create
# ===================================================================

@mcp.tool(name="rd.session.create")
async def session_create(
    backend_type: str = "local",
    gpu_index: int = 0,
    force_api_validation: bool = False,
) -> str:
    """Create a new RenderDoc replay session.

    Args:
        backend_type: "local" or "remote".
        gpu_index: GPU device index to use.
        force_api_validation: Enable extra API validation layers.

    Returns:
        JSON ToolResponse with session_id and capabilities.
    """
    trace_id = _new_id("trc")
    try:
        backend_config = {
            "type": backend_type,
            "gpu_index": gpu_index,
        }
        replay_config = {
            "force_api_validation": force_api_validation,
        }
        session_info: SessionInfo = await _session_manager.create_session(
            backend_config=backend_config,
            replay_config=replay_config,
        )
        return _ok_response(
            trace_id=trace_id,
            session=session_info.model_dump(mode="json"),
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.session.create failed")
        return _err_response("SESSION_CREATE_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 2: rd.session.close
# ===================================================================

@mcp.tool(name="rd.session.close")
async def session_close(session_id: str) -> str:
    """Close an active replay session and release its resources.

    Args:
        session_id: Identifier of the session to close.

    Returns:
        JSON ToolResponse confirming closure.
    """
    trace_id = _new_id("trc")
    try:
        await _session_manager.close_session(session_id)
        return _ok_response(
            trace_id=trace_id,
            closed_session_id=session_id,
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.session.close failed")
        return _err_response("SESSION_CLOSE_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 3: rd.capture.open
# ===================================================================

@mcp.tool(name="rd.capture.open")
async def capture_open(session_id: str, rdc_path: str) -> str:
    """Open a RenderDoc capture (.rdc) file within an existing session.

    Args:
        session_id: Target session identifier.
        rdc_path: Filesystem path to the .rdc capture file.

    Returns:
        JSON ToolResponse with capture metadata (api, driver, event count).
    """
    trace_id = _new_id("trc")
    try:
        capture_info: CaptureInfo = await _session_manager.open_capture(
            session_id, rdc_path,
        )
        return _ok_response(
            trace_id=trace_id,
            capture=capture_info.model_dump(mode="json"),
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.capture.open failed")
        return _err_response("CAPTURE_OPEN_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 4: rd.capture.get_event_tree
# ===================================================================

@mcp.tool(name="rd.capture.get_event_tree")
async def capture_get_event_tree(
    session_id: str,
    include_passes: bool = False,
) -> str:
    """Build and return the hierarchical event tree for an open capture.

    Args:
        session_id: Session with an open capture.
        include_passes: If true, run pass inference to annotate events.

    Returns:
        JSON ToolResponse with the event tree and summary statistics.
    """
    trace_id = _new_id("trc")
    try:
        loop = asyncio.get_running_loop()
        event_tree: List[EventNode] = await loop.run_in_executor(
            None,
            _event_graph_service.build_event_tree,
            session_id,
            _session_manager,
        )

        if include_passes:
            passes: List[EventNode] = await loop.run_in_executor(
                None,
                _event_graph_service.infer_passes,
                event_tree,
                session_id,
                _session_manager,
            )
        else:
            passes = []

        draw_events = _event_graph_service.get_draw_events(event_tree)
        event_range = _event_graph_service.get_event_range(event_tree)

        tree_data = [node.model_dump(mode="json") for node in event_tree]
        pass_data = [node.model_dump(mode="json") for node in passes]

        return _ok_response(
            trace_id=trace_id,
            event_tree=tree_data,
            passes=pass_data,
            total_events=len(draw_events),
            event_range=list(event_range) if event_range else None,
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.capture.get_event_tree failed")
        return _err_response("EVENT_TREE_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 5: rd.event.set
# ===================================================================

@mcp.tool(name="rd.event.set")
async def event_set(session_id: str, event_id: int) -> str:
    """Set the replay to a specific event (draw call) for inspection.

    Args:
        session_id: Session with an open capture.
        event_id: The event ID to navigate to.

    Returns:
        JSON ToolResponse confirming the event is now active.
    """
    trace_id = _new_id("trc")
    try:
        controller = _session_manager.get_controller(session_id)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, controller.SetFrameEvent, event_id, False)
        return _ok_response(
            trace_id=trace_id,
            event_id=event_id,
            status="set",
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.event.set failed")
        return _err_response("EVENT_SET_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 6: rd.event.bisect_first_bad
# ===================================================================

@mcp.tool(name="rd.event.bisect_first_bad")
async def event_bisect_first_bad(
    session_id: str,
    capture_id: str,
    range_lo: int,
    range_hi: int,
    verifier_type: str = "naninf",
    verifier_params: Optional[str] = None,
    strategy: str = "binary",
    max_iters: int = 60,
    confidence_threshold: float = 0.85,
) -> str:
    """Binary-search events to find the first bad draw call.

    Args:
        session_id: Active session identifier.
        capture_id: Capture identifier.
        range_lo: Start of the event range (inclusive).
        range_hi: End of the event range (inclusive).
        verifier_type: Verifier to use (naninf, image_diff, etc.).
        verifier_params: JSON string of verifier parameters.
        strategy: Bisect strategy ("binary" or "ddmin").
        max_iters: Maximum number of bisect iterations.
        confidence_threshold: Minimum confidence to accept result.

    Returns:
        JSON ToolResponse with the bisect result.
    """
    trace_id = _new_id("trc")
    try:
        params = json.loads(verifier_params) if verifier_params else {}
        verifier_config = VerifierConfig(
            type=VerifierType(verifier_type),
            params=params,
        )

        bisect_result: BisectResult = await _experiment_runner.run_bisect(
            session_id=session_id,
            capture_id=capture_id,
            range_lo=range_lo,
            range_hi=range_hi,
            verifier_config=verifier_config,
            strategy=strategy,
            max_iters=max_iters,
            confidence_threshold=confidence_threshold,
        )

        return _ok_response(
            trace_id=trace_id,
            bisect=bisect_result.model_dump(mode="json"),
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.event.bisect_first_bad failed")
        return _err_response("BISECT_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 7: rd.output.render
# ===================================================================

@mcp.tool(name="rd.output.render")
async def output_render(
    session_id: str,
    event_id: int,
    output_format: str = "png",
    source_config: Optional[str] = None,
    view_config: Optional[str] = None,
) -> str:
    """Render the output of a specific event and store the image artifact.

    Args:
        session_id: Active session identifier.
        event_id: Event to render.
        output_format: Image format ("png", "exr", "hdr").
        source_config: Optional JSON string for source configuration.
        view_config: Optional JSON string for view configuration.

    Returns:
        JSON ToolResponse with artifact reference and render metadata.
    """
    trace_id = _new_id("trc")
    try:
        src_cfg = json.loads(source_config) if source_config else None
        view_cfg = json.loads(view_config) if view_config else None

        artifact_ref, metadata = await _render_service.render_event(
            session_id=session_id,
            event_id=event_id,
            session_manager=_session_manager,
            artifact_store=_artifact_store,
            source_config=src_cfg,
            view_config=view_cfg,
            output_format=output_format,
        )

        return _ok_response(
            trace_id=trace_id,
            artifact=artifact_ref,
            render_meta=metadata,
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.output.render failed")
        return _err_response("RENDER_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 8: rd.output.readback
# ===================================================================

@mcp.tool(name="rd.output.readback")
async def output_readback(
    session_id: str,
    event_id: int,
    texture_id: str,
    subresource: Optional[str] = None,
    region: Optional[str] = None,
) -> str:
    """Read back a texture resource at a specific event.

    Args:
        session_id: Active session identifier.
        event_id: Event to read back from.
        texture_id: Resource identifier of the texture.
        subresource: Optional JSON string for subresource selection.
        region: Optional JSON string for region-of-interest.

    Returns:
        JSON ToolResponse with artifact reference and texture metadata.
    """
    trace_id = _new_id("trc")
    try:
        sub = json.loads(subresource) if subresource else None
        rgn = json.loads(region) if region else None

        artifact_ref, metadata = await _render_service.readback_texture(
            session_id=session_id,
            event_id=event_id,
            texture_id=texture_id,
            session_manager=_session_manager,
            artifact_store=_artifact_store,
            subresource=sub,
            region=rgn,
        )

        return _ok_response(
            trace_id=trace_id,
            artifact=artifact_ref,
            texture_meta=metadata,
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.output.readback failed")
        return _err_response("READBACK_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 9: rd.verify.naninf
# ===================================================================

@mcp.tool(name="rd.verify.naninf")
async def verify_naninf(
    session_id: str,
    capture_id: str,
    event_id: int,
    threshold: float = 0.0,
) -> str:
    """Run the NaN/Inf verifier on a rendered event output.

    Detects NaN and Inf values in render target pixels.

    Args:
        session_id: Active session identifier.
        capture_id: Capture identifier.
        event_id: Event to verify.
        threshold: Density threshold for anomaly classification.

    Returns:
        JSON ToolResponse with verification result and any anomaly info.
    """
    trace_id = _new_id("trc")
    try:
        context = VerifyContext(
            session_id=session_id,
            capture_id=capture_id,
            event_id=event_id,
            session_manager=_session_manager,
            artifact_store=_artifact_store,
            render_service=_render_service,
            params={"threshold": threshold},
        )

        result: VerifyResult = await _verifier_engine.run_verifier(
            "naninf", context,
        )

        result_data: Dict[str, Any] = {
            "passed": result.passed,
            "metrics": result.metrics,
            "notes": result.notes,
        }
        if result.anomaly is not None:
            result_data["anomaly"] = result.anomaly.model_dump(mode="json")
        if result.artifacts:
            result_data["artifacts"] = [
                a.model_dump(mode="json") for a in result.artifacts
            ]

        return _ok_response(
            trace_id=trace_id,
            verify_result=result_data,
        )
    except Exception as exc:
        logger.exception("rd.verify.naninf failed")
        return _err_response("VERIFY_NANINF_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 10: rd.verify.image_diff
# ===================================================================

@mcp.tool(name="rd.verify.image_diff")
async def verify_image_diff(
    session_id: str,
    capture_id: str,
    event_id: int,
    reference_artifact_sha: Optional[str] = None,
    diff_threshold: float = 0.01,
) -> str:
    """Run the image-diff verifier comparing rendered output to a reference.

    Args:
        session_id: Active session identifier.
        capture_id: Capture identifier.
        event_id: Event to verify.
        reference_artifact_sha: SHA256 of the reference image artifact.
        diff_threshold: Per-pixel difference threshold.

    Returns:
        JSON ToolResponse with diff metrics and optional mask artifact.
    """
    trace_id = _new_id("trc")
    try:
        params: Dict[str, Any] = {
            "diff_threshold": diff_threshold,
        }
        if reference_artifact_sha:
            params["reference_sha"] = reference_artifact_sha

        context = VerifyContext(
            session_id=session_id,
            capture_id=capture_id,
            event_id=event_id,
            session_manager=_session_manager,
            artifact_store=_artifact_store,
            render_service=_render_service,
            params=params,
        )

        result: VerifyResult = await _verifier_engine.run_verifier(
            "image_diff", context,
        )

        result_data: Dict[str, Any] = {
            "passed": result.passed,
            "metrics": result.metrics,
            "notes": result.notes,
        }
        if result.anomaly is not None:
            result_data["anomaly"] = result.anomaly.model_dump(mode="json")
        if result.artifacts:
            result_data["artifacts"] = [
                a.model_dump(mode="json") for a in result.artifacts
            ]

        return _ok_response(
            trace_id=trace_id,
            verify_result=result_data,
        )
    except Exception as exc:
        logger.exception("rd.verify.image_diff failed")
        return _err_response("VERIFY_IMAGE_DIFF_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 11: rd.pipeline.snapshot
# ===================================================================

@mcp.tool(name="rd.pipeline.snapshot")
async def pipeline_snapshot(session_id: str, event_id: int) -> str:
    """Capture the full pipeline state at a specific event.

    Extracts shaders, render targets, blend states, depth/stencil,
    bindings, viewport, and topology.

    Args:
        session_id: Active session identifier.
        event_id: Event to snapshot.

    Returns:
        JSON ToolResponse with PipelineSnapshot data.
    """
    trace_id = _new_id("trc")
    try:
        snapshot: PipelineSnapshot = await _pipeline_service.snapshot_pipeline(
            session_id=session_id,
            event_id=event_id,
            session_manager=_session_manager,
        )

        return _ok_response(
            trace_id=trace_id,
            pipeline=snapshot.model_dump(mode="json"),
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.pipeline.snapshot failed")
        return _err_response("PIPELINE_SNAPSHOT_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 12: rd.shader.export_artifacts
# ===================================================================

@mcp.tool(name="rd.shader.export_artifacts")
async def shader_export_artifacts(
    session_id: str,
    event_id: int,
    stage: str = "ps",
) -> str:
    """Export shader artifacts (disassembly, reflection, IR, source) for a stage.

    Args:
        session_id: Active session identifier.
        event_id: Event whose shader to export.
        stage: Shader stage ("vs", "ps", "cs", etc.).

    Returns:
        JSON ToolResponse with ShaderExportBundle data and artifact refs.
    """
    trace_id = _new_id("trc")
    try:
        shader_stage = ShaderStage(stage)
        bundle: ShaderExportBundle = await _pipeline_service.export_shader(
            session_id=session_id,
            event_id=event_id,
            stage=shader_stage,
            session_manager=_session_manager,
            artifact_store=_artifact_store,
        )

        return _ok_response(
            trace_id=trace_id,
            shader_export=bundle.model_dump(mode="json"),
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.shader.export_artifacts failed")
        return _err_response("SHADER_EXPORT_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 13: rd.debug.pixel
# ===================================================================

@mcp.tool(name="rd.debug.pixel")
async def debug_pixel(
    session_id: str,
    event_id: int,
    x: int,
    y: int,
    sample: int = 0,
    mode: str = "run_to_naninf",
    max_steps: int = 20000,
) -> str:
    """Debug a pixel shader invocation at coordinates (x, y).

    Steps through shader execution and optionally stops at the first
    NaN/Inf producing instruction.

    Args:
        session_id: Active session identifier.
        event_id: Draw call event to debug.
        x: Pixel x coordinate.
        y: Pixel y coordinate.
        sample: Multisample sample index.
        mode: Debug mode ("run_to_naninf", "step_all", "run_to_end").
        max_steps: Maximum shader steps before aborting.

    Returns:
        JSON ToolResponse with PixelDebugResult data and trace artifact.
    """
    trace_id = _new_id("trc")
    try:
        result: PixelDebugResult = await _debug_service.debug_pixel(
            session_id=session_id,
            event_id=event_id,
            x=x,
            y=y,
            session_manager=_session_manager,
            artifact_store=_artifact_store,
            sample=sample,
            mode=mode,
            max_steps=max_steps,
        )

        result_data = result.model_dump(mode="json")

        artifact = result.trace_artifact

        return _ok_response(
            trace_id=trace_id,
            artifact=artifact,
            debug_result=result_data,
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.debug.pixel failed")
        return _err_response("DEBUG_PIXEL_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 14: rd.patch.apply
# ===================================================================

@mcp.tool(name="rd.patch.apply")
async def patch_apply(
    session_id: str,
    event_id: int,
    stage: str = "ps",
    intent: str = "fix_naninf",
    ops: Optional[str] = None,
) -> str:
    """Apply a shader hot-patch to modify shader behavior at runtime.

    Args:
        session_id: Active session identifier.
        event_id: Target draw call event.
        stage: Shader stage to patch ("vs", "ps", "cs", etc.).
        intent: Patch intent description (e.g. "fix_naninf", "guard_div").
        ops: JSON array of PatchOp definitions.

    Returns:
        JSON ToolResponse with PatchResult data.
    """
    trace_id = _new_id("trc")
    try:
        patch_ops = []
        if ops:
            raw_ops = json.loads(ops)
            for op_data in raw_ops:
                patch_ops.append(PatchOp(**op_data))

        shader_stage = ShaderStage(stage)
        patch_spec = PatchSpec(
            target_event_id=event_id,
            target_stage=shader_stage,
            intent=intent,
            ops=patch_ops,
        )

        result: PatchResult = await _patch_engine.apply_patch(
            session_id=session_id,
            event_id=event_id,
            stage=shader_stage,
            session_manager=_session_manager,
            patch_spec=patch_spec,
        )

        return _ok_response(
            trace_id=trace_id,
            patch=result.model_dump(mode="json"),
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.patch.apply failed")
        return _err_response("PATCH_APPLY_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 15: rd.patch.revert
# ===================================================================

@mcp.tool(name="rd.patch.revert")
async def patch_revert(session_id: str, patch_id: str) -> str:
    """Revert a previously applied shader patch.

    Args:
        session_id: Active session identifier.
        patch_id: Identifier of the patch to revert.

    Returns:
        JSON ToolResponse confirming revert success.
    """
    trace_id = _new_id("trc")
    try:
        success: bool = await _patch_engine.revert_patch(
            session_id=session_id,
            patch_id=patch_id,
            session_manager=_session_manager,
        )

        if success:
            return _ok_response(
                trace_id=trace_id,
                reverted_patch_id=patch_id,
            )
        else:
            return _err_response(
                "PATCH_NOT_FOUND",
                f"Patch {patch_id} not found or already reverted",
                trace_id=trace_id,
            )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.patch.revert failed")
        return _err_response("PATCH_REVERT_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 16: rd.experiment.run
# ===================================================================

@mcp.tool(name="rd.experiment.run")
async def experiment_run(
    session_id: str,
    capture_id: str,
    event_id: int,
    verifier_type: str = "naninf",
    verifier_params: Optional[str] = None,
    patch_id: Optional[str] = None,
    description: str = "",
) -> str:
    """Run a single experiment: apply optional patch, verify, compare.

    An experiment renders an event, optionally applies a shader patch,
    re-renders, and runs a verifier to compare before/after results.

    Args:
        session_id: Active session identifier.
        capture_id: Capture identifier.
        event_id: Target event for the experiment.
        verifier_type: Verifier to use ("naninf", "image_diff", etc.).
        verifier_params: JSON string of additional verifier parameters.
        patch_id: Optional patch to apply before re-rendering.
        description: Human-readable experiment description.

    Returns:
        JSON ToolResponse with ExperimentResult and evidence data.
    """
    trace_id = _new_id("trc")
    try:
        params = json.loads(verifier_params) if verifier_params else {}
        verifier_config = VerifierConfig(
            type=VerifierType(verifier_type),
            params=params,
        )

        experiment_def = ExperimentDef(
            session_id=session_id,
            capture_id=capture_id,
            event_id=event_id,
            verifier=verifier_config,
            patch_id=patch_id,
            description=description,
        )

        result: ExperimentResult = await _experiment_runner.run_experiment(
            experiment_def,
        )

        return _ok_response(
            trace_id=trace_id,
            experiment=result.model_dump(mode="json"),
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.experiment.run failed")
        return _err_response("EXPERIMENT_RUN_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 17: rd.perf.sample_counters
# ===================================================================

@mcp.tool(name="rd.perf.sample_counters")
async def perf_sample_counters(
    session_id: str,
    range_lo: int,
    range_hi: int,
    counter_ids: Optional[str] = None,
) -> str:
    """Sample GPU performance counters across a range of events.

    Args:
        session_id: Active session identifier.
        range_lo: Start event of the range (inclusive).
        range_hi: End event of the range (inclusive).
        counter_ids: Optional JSON array of integer counter IDs. If omitted,
            all available counters are sampled.

    Returns:
        JSON ToolResponse with PerfResult including samples and summaries.
    """
    trace_id = _new_id("trc")
    try:
        ids: List[int] = []
        if counter_ids:
            ids = json.loads(counter_ids)

        if not ids:
            # Enumerate all available counters first.
            all_counters = await _perf_service.enumerate_counters(
                session_id=session_id,
                session_manager=_session_manager,
            )
            ids = [c["counter_id"] for c in all_counters]

        perf_result: PerfResult = await _perf_service.sample_counters(
            session_id=session_id,
            event_range=(range_lo, range_hi),
            counter_ids=ids,
            session_manager=_session_manager,
        )

        return _ok_response(
            trace_id=trace_id,
            perf=perf_result.model_dump(mode="json"),
        )
    except SessionError as exc:
        return _session_err_response(exc)
    except Exception as exc:
        logger.exception("rd.perf.sample_counters failed")
        return _err_response("PERF_SAMPLE_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 18: rd.report.build_bundle
# ===================================================================

@mcp.tool(name="rd.report.build_bundle")
async def report_build_bundle(
    task_state_json: str,
    output_dir: Optional[str] = None,
) -> str:
    """Generate a self-contained debug report bundle.

    Produces JSON, Markdown, and interactive HTML reports with all
    referenced artifacts copied into an assets directory.

    Args:
        task_state_json: JSON string of the TaskState to report on.
        output_dir: Directory where the bundle will be written. Defaults
            to a temporary location under the artifact store root.

    Returns:
        JSON ToolResponse with paths to the generated report files.
    """
    trace_id = _new_id("trc")
    try:
        task_data = json.loads(task_state_json)
        task_state = TaskState.model_validate(task_data)

        if output_dir:
            out_path = Path(output_dir)
        else:
            out_path = Path(
                os.environ.get("RDX_ARTIFACT_DIR", "/tmp/rdx-artifacts")
            ) / "reports" / task_state.task_id
        out_path.mkdir(parents=True, exist_ok=True)

        bundle_paths: Dict[str, Any] = await _report_builder.build_bundle(
            task_state=task_state,
            artifact_store=_artifact_store,
            output_dir=out_path,
        )

        return _ok_response(
            trace_id=trace_id,
            report=bundle_paths,
        )
    except Exception as exc:
        logger.exception("rd.report.build_bundle failed")
        return _err_response("REPORT_BUILD_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 19: rd.kb.search
# ===================================================================

@mcp.tool(name="rd.kb.search")
async def kb_search(
    query: str,
    file_type: Optional[str] = None,
    path_prefix: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Search the knowledge base using BM25 text retrieval.

    Queries local documentation, shader sources, and engine code indexed
    by the knowledge base connector.

    Args:
        query: Free-text search query.
        file_type: Filter by file extension without dot (e.g. "cpp", "usf").
        path_prefix: Only include results under this path prefix.
        project_id: Filter by project identifier.
        limit: Maximum number of results.

    Returns:
        JSON ToolResponse with ranked search results.
    """
    trace_id = _new_id("trc")
    try:
        filters: Dict[str, str] = {}
        if file_type:
            filters["file_type"] = file_type
        if path_prefix:
            filters["path_prefix"] = path_prefix
        if project_id:
            filters["project_id"] = project_id

        results = await _kb_connector.search(
            query=query,
            filters=filters if filters else None,
            limit=limit,
        )

        results_data = []
        for r in results:
            results_data.append({
                "doc_id": r.doc_id,
                "path": r.path,
                "score": round(r.score, 4),
                "snippet": r.snippet,
                "line_number": r.line_number,
            })

        return _ok_response(
            trace_id=trace_id,
            results=results_data,
            total=len(results_data),
        )
    except Exception as exc:
        logger.exception("rd.kb.search failed")
        return _err_response("KB_SEARCH_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 20: rd.fingerprint.match
# ===================================================================

@mcp.tool(name="rd.fingerprint.match")
async def fingerprint_match(
    fingerprint_type: str = "pass",
    fingerprint_json: str = "{}",
    threshold: float = 0.5,
) -> str:
    """Match a fingerprint against the knowledge store.

    Finds previously recorded fingerprints that are similar to the
    provided candidate, scored by Jaccard similarity.

    Args:
        fingerprint_type: Type of fingerprint ("pass" or "shader").
        fingerprint_json: JSON string of the fingerprint data.
        threshold: Minimum similarity score to include in results.

    Returns:
        JSON ToolResponse with ranked matches and similarity scores.
    """
    trace_id = _new_id("trc")
    try:
        fp_data = json.loads(fingerprint_json)

        if fingerprint_type == "pass":
            pass_fp = PassFingerprint.model_validate(fp_data)
            matches = await _fingerprint_store.match_pass_fingerprint(
                pass_fp, threshold=threshold,
            )
        elif fingerprint_type == "shader":
            shader_fp = ShaderFingerprint.model_validate(fp_data)
            matches = await _fingerprint_store.match_shader_fingerprint(
                shader_fp, threshold=threshold,
            )
        else:
            return _err_response(
                "INVALID_FINGERPRINT_TYPE",
                f"Unknown fingerprint type: {fingerprint_type}. "
                f"Expected 'pass' or 'shader'.",
                trace_id=trace_id,
            )

        matches_data = []
        for record, score in matches:
            matches_data.append({
                "record": record.model_dump(mode="json"),
                "score": round(score, 4),
            })

        return _ok_response(
            trace_id=trace_id,
            matches=matches_data,
            total=len(matches_data),
        )
    except Exception as exc:
        logger.exception("rd.fingerprint.match failed")
        return _err_response("FINGERPRINT_MATCH_ERROR", str(exc), trace_id=trace_id)


# ===================================================================
# Tool 21: rd.pipeline.run_full_debug
# ===================================================================

@mcp.tool(name="rd.pipeline.run_full_debug")
async def pipeline_run_full_debug(
    rdc_path: str,
    description: str,
    reference_image_path: Optional[str] = None,
    expected_image_path: Optional[str] = None,
    bug_type_hints: Optional[str] = None,
    backend_type: str = "local",
    project_id: str = "",
) -> str:
    """Run the complete S0-S7 automated GPU debug pipeline end-to-end.

    This is the highest-level tool: it opens a capture, localizes
    anomalies, bisects to the first bad event, extracts pipeline state,
    generates and tests fix hypotheses, maps to engine source, and
    builds a final report.

    Args:
        rdc_path: Path to the .rdc capture file.
        description: Natural-language description of the visual bug.
        reference_image_path: Optional path to a known-good reference image.
        expected_image_path: Optional path to the expected correct output.
        bug_type_hints: Optional JSON array of bug type hint strings.
        backend_type: Backend type ("local" or "remote").
        project_id: Optional project identifier for fingerprint tracking.

    Returns:
        JSON ToolResponse with the final TaskState and report paths.
    """
    trace_id = _new_id("trc")
    try:
        hints = []
        if bug_type_hints:
            raw_hints = json.loads(bug_type_hints)
            from rdx.models import BugType
            hints = [BugType(h) for h in raw_hints]

        task_input = TaskInput(
            rdc_path=rdc_path,
            description=description,
            reference_image_path=reference_image_path,
            expected_image_path=expected_image_path,
            bug_type_hints=hints,
            backend_type=backend_type,
            project_id=project_id,
        )

        services = {
            "session_manager": _session_manager,
            "event_graph_service": _event_graph_service,
            "render_service": _render_service,
            "pipeline_service": _pipeline_service,
            "verifier_engine": _verifier_engine,
            "patch_engine": _patch_engine,
            "experiment_runner": _experiment_runner,
            "debug_service": _debug_service,
            "perf_service": _perf_service,
            "report_builder": _report_builder,
            "fingerprint_store": _fingerprint_store,
            "kb_connector": _kb_connector,
            "artifact_store": _artifact_store,
        }

        task_state: TaskState = await run_full_debug_pipeline(
            task_input=task_input,
            services=services,
        )

        # Build a summary response
        summary: Dict[str, Any] = {
            "task_id": task_state.task_id,
            "status": task_state.status,
            "anomaly_count": len(task_state.anomalies),
            "hypothesis_count": len(task_state.hypotheses),
            "experiment_count": len(task_state.experiments),
        }

        if task_state.bisect_result is not None:
            summary["first_bad_event_id"] = task_state.bisect_result.first_bad_event_id
            summary["bisect_confidence"] = task_state.bisect_result.confidence

        if task_state.pipeline is not None:
            summary["pipeline_event_id"] = task_state.pipeline.event_id
            summary["shader_count"] = len(task_state.pipeline.shaders)

        # Include fix candidate if present
        for hyp in task_state.hypotheses:
            if hyp.result is not None and hyp.result.value in ("fixed", "improved"):
                summary["fix_candidate"] = {
                    "hypothesis_id": hyp.hypothesis_id,
                    "title": hyp.title,
                    "result": hyp.result.value,
                }
                break

        return _ok_response(
            trace_id=trace_id,
            task_state=task_state.model_dump(mode="json"),
            summary=summary,
        )
    except Exception as exc:
        logger.exception("rd.pipeline.run_full_debug failed")
        return _err_response(
            "FULL_DEBUG_PIPELINE_ERROR", str(exc), trace_id=trace_id,
        )


# ---------------------------------------------------------------------------
# Transport entry points
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server using stdio transport (default CLI entry point)."""
    logging.basicConfig(
        level=os.environ.get("RDX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    mcp.run(transport="stdio")


def main_sse() -> None:
    """Run the MCP server using SSE transport for web-based clients."""
    logging.basicConfig(
        level=os.environ.get("RDX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    host = os.environ.get("RDX_SSE_HOST", "0.0.0.0")
    port = int(os.environ.get("RDX_SSE_PORT", "8765"))
    mcp.run(transport="sse", host=host, port=port)


if __name__ == "__main__":
    main()
