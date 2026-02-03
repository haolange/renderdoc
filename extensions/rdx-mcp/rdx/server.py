"""
RDX-MCP Server —— 用于 GPU debug automation 的主 MCP server。

注册所有 MCP tools，通过 lifespan 连接各类服务实例，并提供两个传输入口：

* ``main()``     —— stdio transport（默认，用于 ``rdx-mcp`` CLI）。
* ``main_sse()`` —— SSE transport（面向 web clients）。

每个 tool 函数均为 ``async``，遵循响应封装模式（``ToolResponse``），
并委派到对应的 service layer。
"""

from __future__ import annotations

import asyncio
import inspect
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
# Global service instances（在 lifespan 中初始化）
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
    max_local_workers = getattr(
        _config.worker,
        "max_local_workers",
        getattr(_config.worker, "max_workers_per_gpu", 1),
    )
    max_remote_workers = getattr(
        _config.worker,
        "max_remote_workers",
        getattr(_config.worker, "max_remote_controllers", 1),
    )
    _scheduler = WorkerScheduler(
        max_local_workers=max_local_workers,
        max_remote_workers=max_remote_workers,
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

def _create_mcp() -> FastMCP:
    description = (
        "RDX-MCP: RenderDoc GPU Debug Server. "
        "Provides 21 tools for automated GPU capture analysis, "
        "shader debugging, anomaly detection, bisect search, "
        "experiment management, and report generation."
    )

    kwargs: Dict[str, Any] = {}
    try:
        params = set(inspect.signature(FastMCP.__init__).parameters)
        if "description" in params:
            kwargs["description"] = description
        if "lifespan" in params:
            kwargs["lifespan"] = _lifespan
        # Older/newer FastMCP versions configure SSE host/port via init() not run().
        if "host" in params:
            kwargs["host"] = os.environ.get("RDX_SSE_HOST", "127.0.0.1")
        if "port" in params:
            kwargs["port"] = int(os.environ.get("RDX_SSE_PORT", "8765"))
    except (TypeError, ValueError):
        # Signature inspection can fail on some implementations; fall back to try/except.
        kwargs["description"] = description
        kwargs["lifespan"] = _lifespan

    try:
        return FastMCP("rdx-mcp", **kwargs)
    except TypeError:
        logger.warning("FastMCP init signature incompatible; retrying without description.")
        kwargs.pop("description", None)
        return FastMCP("rdx-mcp", **kwargs)


mcp = _create_mcp()


# ===================================================================
# Tool 1: rd.session.create
# ===================================================================

@mcp.tool(name="rd.session.create")
async def session_create(
    backend_type: str = "local",
    gpu_index: int = 0,
    force_api_validation: bool = False,
) -> str:
    """创建新的 RenderDoc replay session。

    Args:
        backend_type: "local" 或 "remote"。
        gpu_index: 使用的 GPU 设备索引。
        force_api_validation: 启用额外的 API validation layers。

    Returns:
        包含 session_id 与 capabilities 的 JSON ToolResponse。
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
    """关闭活跃 replay session 并释放资源。

    Args:
        session_id: 要关闭的 session 标识。

    Returns:
        确认关闭的 JSON ToolResponse。
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
    """在现有 session 中打开 RenderDoc capture（.rdc）文件。

    Args:
        session_id: 目标 session 标识。
        rdc_path: .rdc capture 文件路径。

    Returns:
        包含 capture 元数据（api、driver、event count）的 JSON ToolResponse。
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
    """构建并返回已打开 capture 的层级事件树。

    Args:
        session_id: 已打开 capture 的 session。
        include_passes: 为 true 时运行 pass 推断以注释事件。

    Returns:
        包含事件树与汇总统计的 JSON ToolResponse。
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
    """将 replay 设置到指定 event（draw call）以便检查。

    Args:
        session_id: 已打开 capture 的 session。
        event_id: 要导航到的 event ID。

    Returns:
        确认已激活该 event 的 JSON ToolResponse。
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
    """对 events 进行二分搜索以定位第一个 bad draw call。

    Args:
        session_id: 活跃 session 标识。
        capture_id: capture 标识。
        range_lo: event 范围起点（含）。
        range_hi: event 范围终点（含）。
        verifier_type: 使用的 verifier（naninf、image_diff 等）。
        verifier_params: verifier 参数的 JSON 字符串。
        strategy: bisect 策略（"binary" 或 "ddmin"）。
        max_iters: 最大 bisect 迭代次数。
        confidence_threshold: 接受结果的最小置信度。

    Returns:
        包含 bisect 结果的 JSON ToolResponse。
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
    """渲染指定 event 的输出并存储图像 artifact。

    Args:
        session_id: 活跃 session 标识。
        event_id: 要渲染的 event。
        output_format: 图像格式（"png", "exr", "hdr"）。
        source_config: source 配置的可选 JSON 字符串。
        view_config: view 配置的可选 JSON 字符串。

    Returns:
        包含 artifact 引用与渲染元数据的 JSON ToolResponse。
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
    """在指定 event 读取 texture 资源。

    Args:
        session_id: 活跃 session 标识。
        event_id: 读取的 event。
        texture_id: texture 的资源标识。
        subresource: subresource 选择的可选 JSON 字符串。
        region: ROI（region-of-interest）可选 JSON 字符串。

    Returns:
        包含 artifact 引用与 texture 元数据的 JSON ToolResponse。
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
    """对渲染输出运行 NaN/Inf verifier。

    检测 render target 像素中的 NaN 与 Inf。

    Args:
        session_id: 活跃 session 标识。
        capture_id: capture 标识。
        event_id: 要验证的 event。
        threshold: anomaly 判定的密度阈值。

    Returns:
        包含验证结果与 anomaly 信息的 JSON ToolResponse。
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
    """运行 image-diff verifier，将渲染输出与参考图像对比。

    Args:
        session_id: 活跃 session 标识。
        capture_id: capture 标识。
        event_id: 要验证的 event。
        reference_artifact_sha: 参考图像 artifact 的 SHA256。
        diff_threshold: 逐像素差异阈值。

    Returns:
        包含 diff 指标与可选 mask artifact 的 JSON ToolResponse。
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
    """在指定 event 捕获完整的 pipeline state。

    提取 shaders、render targets、blend states、depth/stencil、
    bindings、viewport 与 topology。

    Args:
        session_id: 活跃 session 标识。
        event_id: 要生成 snapshot 的 event。

    Returns:
        包含 PipelineSnapshot 数据的 JSON ToolResponse。
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
    """导出指定 stage 的 shader artifacts（disassembly、reflection、IR、source）。

    Args:
        session_id: 活跃 session 标识。
        event_id: 要导出 shader 的 event。
        stage: Shader stage（"vs", "ps", "cs" 等）。

    Returns:
        包含 ShaderExportBundle 数据与 artifact refs 的 JSON ToolResponse。
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
    """在坐标 (x, y) 调试 pixel shader invocation。

    逐步执行 shader，可选在第一个产生 NaN/Inf 的指令处停止。

    Args:
        session_id: 活跃 session 标识。
        event_id: 要调试的 draw call event。
        x: 像素 x 坐标。
        y: 像素 y 坐标。
        sample: Multisample sample index。
        mode: 调试模式（"run_to_naninf", "step_all", "run_to_end"）。
        max_steps: 在中止前允许的最大 shader step 数。

    Returns:
        包含 PixelDebugResult 数据与 trace artifact 的 JSON ToolResponse。
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
    """应用 shader hot-patch，在运行时修改 shader 行为。

    Args:
        session_id: 活跃 session 标识。
        event_id: 目标 draw call event。
        stage: 要打补丁的 shader stage（"vs", "ps", "cs" 等）。
        intent: patch 目的描述（如 "fix_naninf", "guard_div"）。
        ops: PatchOp 定义的 JSON 数组。

    Returns:
        包含 PatchResult 数据的 JSON ToolResponse。
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
    """回滚先前应用的 shader patch。

    Args:
        session_id: 活跃 session 标识。
        patch_id: 要回滚的 patch 标识。

    Returns:
        确认回滚成功的 JSON ToolResponse。
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
    """运行单个 experiment：可选应用 patch、验证并对比。

    experiment 会渲染 event、可选应用 shader patch、重新渲染，
    并运行 verifier 对比前后结果。

    Args:
        session_id: 活跃 session 标识。
        capture_id: capture 标识。
        event_id: experiment 目标 event。
        verifier_type: 使用的 verifier（"naninf", "image_diff" 等）。
        verifier_params: 额外 verifier 参数的 JSON 字符串。
        patch_id: 重新渲染前可选应用的 patch。
        description: 人类可读的 experiment 描述。

    Returns:
        包含 ExperimentResult 与 evidence 数据的 JSON ToolResponse。
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
    """在 event 范围内采样 GPU performance counters。

    Args:
        session_id: 活跃 session 标识。
        range_lo: 范围起始 event（含）。
        range_hi: 范围结束 event（含）。
        counter_ids: 可选的 counter ID 整数 JSON 数组。若省略，则采样全部可用 counters。

    Returns:
        包含 samples 与 summaries 的 PerfResult JSON ToolResponse。
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
    """生成自包含的 debug report bundle。

    输出 JSON、Markdown 与交互式 HTML 报告，并将引用的 artifacts
    拷贝到 assets 目录。

    Args:
        task_state_json: 需要生成报告的 TaskState JSON 字符串。
        output_dir: bundle 输出目录。默认写入 artifact store 根目录下的临时位置。

    Returns:
        包含生成报告文件路径的 JSON ToolResponse。
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
    """使用 BM25 文本检索搜索知识库。

    查询知识库连接器索引的本地文档、shader 源码与引擎代码。

    Args:
        query: 自然语言查询。
        file_type: 按不含点的扩展名过滤（如 "cpp", "usf"）。
        path_prefix: 仅包含该路径前缀下的结果。
        project_id: 按 project 标识过滤。
        limit: 最大返回结果数。

    Returns:
        包含排序结果的 JSON ToolResponse。
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
    """在知识库中匹配 fingerprint。

    查找与候选 fingerprint 相似的历史记录，并以 Jaccard 相似度评分。

    Args:
        fingerprint_type: fingerprint 类型（"pass" 或 "shader"）。
        fingerprint_json: fingerprint 数据的 JSON 字符串。
        threshold: 最小相似度阈值。

    Returns:
        包含排序匹配结果与相似度分数的 JSON ToolResponse。
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
    """端到端运行完整的 S0-S7 自动化 GPU debug pipeline。

    这是最高层工具：打开 capture、定位 anomalies、二分到首个 bad event、
    提取 pipeline state、生成并测试修复假设、映射到引擎源码，
    并生成最终报告。

    Args:
        rdc_path: .rdc capture 文件路径。
        description: 对视觉 bug 的自然语言描述。
        reference_image_path: 可选的已知正确参考图像路径。
        expected_image_path: 可选的期望正确输出图像路径。
        bug_type_hints: 可选的 bug type 提示 JSON 数组。
        backend_type: backend 类型（"local" 或 "remote"）。
        project_id: 用于 fingerprint tracking 的可选 project 标识。

    Returns:
        包含最终 TaskState 与报告路径的 JSON ToolResponse。
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
    """使用 stdio transport 运行 MCP server（默认 CLI 入口）。"""
    logging.basicConfig(
        level=os.environ.get("RDX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    mcp.run(transport="stdio")


def main_sse() -> None:
    """使用 SSE transport 运行 MCP server（面向 web clients）。"""
    logging.basicConfig(
        level=os.environ.get("RDX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    # Host/port are configured via FastMCP settings (FASTMCP_*/init args) in some versions.
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
