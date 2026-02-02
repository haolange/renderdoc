"""
RDX-MCP 自动化 GPU debugging 的技能工作流。

定义了 8 个可组合技能（S0--S7），作为自动化 debug pipeline 的构建块。
每个技能是一个 ``async`` 函数，接收 :class:`SkillContext`
（包含服务引用与当前 :class:`~rdx.models.TaskState`）并返回更新后的
``TaskState``。

Skills **不是** MCP tools；它们是更高层的 workflow 组合，
由编排 agent 按顺序或选择性调用，以完成端到端 GPU debug session。

Skill index
-----------
S0  :func:`intake_and_normalize`      -- open capture, parse description,
                                         build event tree
S1  :func:`localize_anomaly`          -- render final output, run verifiers,
                                         find anomalous pixels
S2  :func:`search_first_bad`          -- binary-search events for the first
                                         bad draw call
S3  :func:`attribute_pass_draw`       -- narrow the bug to a specific pass
                                         and draw call
S4  :func:`extract_pipeline_shader`   -- snapshot pipeline state and export
                                         shader artifacts
S5  :func:`hypothesis_and_patch_loop` -- generate, test, and rank fix
                                         hypotheses
S6  :func:`map_to_engine`             -- map pipeline artifacts to engine
                                         source (e.g. Unreal Engine)
S7  :func:`build_report`              -- assemble the final report bundle
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from rdx.models import (
    TaskState,
    TaskInput,
    BugType,
    VerifierType,
    VerifierConfig,
    Hypothesis,
    VerdictResult,
    PatchSpec,
    PatchOp,
    PatchType,
    ShaderStage,
    ExperimentDef,
    BisectRange,
    AnomalyInfo,
    EventNode,
    _new_id,
    CaptureInfo,
    ReportBundle,
    FingerprintRecord,
    PassFingerprint,
    ShaderFingerprint,
    ExperimentResult,
    ExperimentStatus,
    PipelineSnapshot,
    ShaderExportBundle,
)
from rdx.core.verifiers import VerifyContext, VerifyResult

logger = logging.getLogger("rdx.skills.workflows")


# ---------------------------------------------------------------------------
# Timestamp helper（时间戳辅助）
# ---------------------------------------------------------------------------

def _ts() -> float:
    """当前墙钟时间（POSIX timestamp）。"""
    return time.time()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BUG_TYPE_KEYWORDS: Dict[BugType, List[str]] = {
    BugType.NANINF: ["nan", "inf", "infinity", "not a number"],
    BugType.PRECISION: ["precision", "imprecise", "accuracy", "rounding"],
    BugType.BINDING_ERROR: [
        "binding", "descriptor", "missing texture", "unbound",
    ],
    BugType.TRANSPARENCY: [
        "transparent", "transparency", "translucent", "alpha blending",
    ],
    BugType.VERTEX_DEFORM: [
        "vertex", "deform", "mesh", "geometry distortion",
    ],
    BugType.COLORSPACE: [
        "color", "colour", "gamma", "srgb", "linear", "colorspace",
    ],
    BugType.PERFORMANCE: [
        "performance", "slow", "fps", "stall", "bottleneck", "latency",
    ],
}

_HOTSPOT_PIXEL_COUNT = 5

_DEFAULT_MAX_HYPOTHESES = 5


# ---------------------------------------------------------------------------
# SkillContext
# ---------------------------------------------------------------------------

@dataclass
class SkillContext:
    """保存所有服务引用与当前 task state。

    该对象会传递给每个 skill 函数。随着 pipeline 推进，``task`` 字段会
    原地更新。无法放入严格 ``TaskState`` Pydantic model 的临时数据
    会保存在 context 的辅助字段中。
    """

    # Core services（核心服务）
    session_manager: Any
    event_graph_service: Any
    render_service: Any
    pipeline_service: Any
    verifier_engine: Any
    patch_engine: Any
    experiment_runner: Any
    debug_service: Any
    perf_service: Any

    # Reporting and knowledge services（报告与知识服务）
    report_builder: Any
    fingerprint_store: Any
    kb_connector: Any
    artifact_store: Any

    # Current task state（由 skills 修改）
    task: TaskState

    # Transient working data（不持久化到 TaskState）
    event_tree: List[EventNode] = field(default_factory=list)
    capture_info: Optional[CaptureInfo] = None
    shader_exports: Dict[str, ShaderExportBundle] = field(default_factory=dict)
    attribution: Dict[str, Any] = field(default_factory=dict)
    engine_mapping: Dict[str, Any] = field(default_factory=dict)
    candidate_pixels: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helper: bug-type inference from description text
# ---------------------------------------------------------------------------

def _infer_bug_types(description: str) -> List[BugType]:
    """Parse a user description and return plausible :class:`BugType` values.

    Uses simple keyword matching against the description text.  Returns
    ``[BugType.UNKNOWN]`` when no keywords match.
    """
    if not description:
        return [BugType.UNKNOWN]

    desc_lower = description.lower()
    found: List[BugType] = []

    for bug_type, keywords in _BUG_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in desc_lower:
                found.append(bug_type)
                break  # one match per bug type is sufficient

    return found if found else [BugType.UNKNOWN]


# ---------------------------------------------------------------------------
# Helper: verifier selection
# ---------------------------------------------------------------------------

def _select_verifier(bug_types: List[BugType]) -> VerifierConfig:
    """Pick the most appropriate :class:`VerifierConfig` for the given bug types.

    Priority order (first match wins):
        1. NANINF or PRECISION  -> ``naninf`` verifier
        2. COLORSPACE           -> ``image_diff`` verifier
        3. BINDING_ERROR        -> ``binding_diff`` verifier
        4. PERFORMANCE          -> ``counter_anomaly`` verifier
        5. Fallback             -> ``naninf`` verifier
    """
    type_set = set(bug_types)

    if type_set & {BugType.NANINF, BugType.PRECISION}:
        return VerifierConfig(type=VerifierType.NANINF)
    if BugType.COLORSPACE in type_set:
        return VerifierConfig(
            type=VerifierType.IMAGE_DIFF,
            params={"threshold": 0.01},
        )
    if BugType.BINDING_ERROR in type_set:
        return VerifierConfig(type=VerifierType.BINDING_DIFF)
    if BugType.PERFORMANCE in type_set:
        return VerifierConfig(type=VerifierType.COUNTER_ANOMALY)

    # Default: NaN/Inf is the most generally useful verifier.
    return VerifierConfig(type=VerifierType.NANINF)


# ---------------------------------------------------------------------------
# Helper: hypothesis generation
# ---------------------------------------------------------------------------

def _generate_hypotheses(
    task_state: TaskState,
    pipeline: Optional[PipelineSnapshot],
    shader_info: Dict[str, Any],
) -> List[Hypothesis]:
    """Generate patch hypotheses based on bug type, pipeline, and shader analysis.

    Returns hypotheses sorted by priority score (highest first).
    """
    bug_types = task_state.input.bug_type_hints or [BugType.UNKNOWN]
    type_set = set(bug_types)
    hypotheses: List[Hypothesis] = []

    # --- NaN/Inf and Precision hypotheses ---
    if type_set & {BugType.NANINF, BugType.PRECISION, BugType.UNKNOWN}:
        hypotheses.append(Hypothesis(
            title="Force full precision in pixel shader",
            description=(
                "Promote all reduced-precision types to full precision "
                "and strip RelaxedPrecision decorations to eliminate "
                "precision-related NaN/Inf generation."
            ),
            bug_types=[BugType.NANINF, BugType.PRECISION],
            priority_score=0.9,
        ))
        hypotheses.append(Hypothesis(
            title="Guard NaN/Inf at shader output",
            description=(
                "Insert isnan/isinf guards before the final output "
                "write to clamp invalid values to zero."
            ),
            bug_types=[BugType.NANINF],
            priority_score=0.75,
        ))
        hypotheses.append(Hypothesis(
            title="Guard division operations",
            description=(
                "Wrap division expressions with a zero-denominator "
                "guard to prevent NaN from 0/0 or Inf from x/0."
            ),
            bug_types=[BugType.NANINF, BugType.PRECISION],
            priority_score=0.6,
        ))

    # --- Colorspace hypotheses ---
    if BugType.COLORSPACE in type_set:
        hypotheses.append(Hypothesis(
            title="Fix sRGB/linear conversion",
            description=(
                "Ensure proper sRGB-to-linear conversion on texture "
                "reads and linear-to-sRGB on output writes."
            ),
            bug_types=[BugType.COLORSPACE],
            priority_score=0.85,
        ))
        if pipeline is not None:
            has_srgb_rt = any(
                rt.is_srgb for rt in getattr(pipeline, "render_targets", [])
            )
            if has_srgb_rt:
                hypotheses.append(Hypothesis(
                    title="Disable sRGB on render target",
                    description=(
                        "The render target has sRGB format.  Try rendering "
                        "as linear and applying gamma correction manually."
                    ),
                    bug_types=[BugType.COLORSPACE],
                    priority_score=0.7,
                ))

    # --- Transparency hypotheses ---
    if BugType.TRANSPARENCY in type_set:
        hypotheses.append(Hypothesis(
            title="Fix premultiplied alpha",
            description=(
                "Convert between premultiplied and straight alpha "
                "at the shader output stage."
            ),
            bug_types=[BugType.TRANSPARENCY],
            priority_score=0.8,
        ))

    # --- Binding error hypotheses ---
    if BugType.BINDING_ERROR in type_set:
        hypotheses.append(Hypothesis(
            title="Guard unbound resource reads",
            description=(
                "Insert null-resource guards in the shader to return "
                "default values when resources are unbound."
            ),
            bug_types=[BugType.BINDING_ERROR],
            priority_score=0.7,
        ))

    # --- Pipeline-state-driven hypotheses ---
    if pipeline is not None:
        for bs in getattr(pipeline, "blend_states", []):
            if bs.enabled and "One" in bs.src_alpha and "One" in bs.dst_alpha:
                hypotheses.append(Hypothesis(
                    title="Fix additive alpha blending overflow",
                    description=(
                        "Blend state uses additive alpha which can "
                        "accumulate to values > 1.0, causing downstream "
                        "precision issues."
                    ),
                    bug_types=[BugType.PRECISION, BugType.TRANSPARENCY],
                    priority_score=0.55,
                ))
                break

    # --- Shader-info-driven hypotheses ---
    disasm_text = shader_info.get("disasm_text", "")
    if disasm_text and "RelaxedPrecision" in disasm_text:
        for h in hypotheses:
            if "full precision" in h.title.lower():
                h.priority_score = min(h.priority_score + 0.1, 1.0)

    hypotheses.sort(key=lambda h: h.priority_score, reverse=True)
    return hypotheses


# ---------------------------------------------------------------------------
# Helper: create patch spec from hypothesis
# ---------------------------------------------------------------------------

def _create_patch_for_hypothesis(
    hypothesis: Hypothesis,
    pipeline: Optional[PipelineSnapshot],
    shader_info: Dict[str, Any],
    event_id: int,
) -> PatchSpec:
    """Create a :class:`PatchSpec` that implements a given hypothesis.

    The patch targets the pixel shader (or compute shader if no PS is bound)
    at the given *event_id*.
    """
    target_stage = ShaderStage.PS
    target_shader_id = ""

    if pipeline is not None:
        for si in getattr(pipeline, "shaders", []):
            if si.stage == ShaderStage.PS:
                target_shader_id = si.resource_id
                target_stage = ShaderStage.PS
                break
            if si.stage == ShaderStage.CS:
                target_stage = ShaderStage.CS
                target_shader_id = si.resource_id

    ops: List[PatchOp] = []
    intent = "generic_fix"
    title_lower = hypothesis.title.lower()

    if "full precision" in title_lower:
        intent = "fix_precision"
        ops.append(PatchOp(op="force_full_precision", variables=[]))

    elif "guard" in title_lower and "output" in title_lower:
        intent = "fix_naninf"
        ops.append(PatchOp(
            op="insert_guard",
            guard_expr="output.color",
            guard_replacement="float4(0, 0, 0, 1)",
        ))

    elif "division" in title_lower:
        intent = "fix_naninf"
        ops.append(PatchOp(
            op="insert_guard",
            guard_expr="1.0 / ",
            guard_replacement="1.0 / max(abs(",
        ))

    elif "srgb" in title_lower and "linear" in title_lower:
        intent = "fix_colorspace"
        ops.append(PatchOp(
            op="replace_expr",
            expr_from="pow(color.rgb, 2.2)",
            expr_to="pow(color.rgb, 1.0 / 2.2)",
        ))

    elif "disable srgb" in title_lower:
        intent = "fix_colorspace"
        ops.append(PatchOp(
            op="replace_expr",
            expr_from="sRGB",
            expr_to="UNORM",
        ))

    elif "premultiplied alpha" in title_lower:
        intent = "fix_transparency"
        ops.append(PatchOp(
            op="replace_expr",
            expr_from="output.a * output.rgb",
            expr_to="output.rgb",
        ))

    elif "unbound resource" in title_lower:
        intent = "fix_binding"
        ops.append(PatchOp(
            op="insert_guard",
            guard_expr="texture.Sample",
            guard_replacement="float4(0, 0, 0, 0)",
        ))

    elif "alpha blending" in title_lower:
        intent = "fix_precision"
        ops.append(PatchOp(
            op="replace_expr",
            expr_from="output.a",
            expr_to="saturate(output.a)",
        ))

    else:
        intent = "generic_guard"
        ops.append(PatchOp(
            op="insert_guard",
            guard_expr="output",
            guard_replacement="float4(0, 0, 0, 1)",
        ))

    spec = PatchSpec(
        target_event_id=event_id,
        target_stage=target_stage,
        target_shader_id=target_shader_id,
        intent=intent,
        ops=ops,
    )

    hypothesis.proposed_patches.append(spec.patch_id)
    return spec


# ---------------------------------------------------------------------------
# Helper: event tree utilities
# ---------------------------------------------------------------------------

def _flatten_draw_events(event_tree: List[EventNode]) -> List[EventNode]:
    """Collect all draw/dispatch events from the tree in document order."""
    result: List[EventNode] = []

    def _walk(nodes: List[EventNode]) -> None:
        for node in nodes:
            if node.flags.is_draw or node.flags.is_dispatch:
                result.append(node)
            _walk(node.children)

    _walk(event_tree)
    return result


def _find_last_event(event_tree: List[EventNode]) -> Optional[EventNode]:
    """Return the event node with the highest event_id in the tree."""
    best: Optional[EventNode] = None
    best_id = -1

    def _walk(nodes: List[EventNode]) -> None:
        nonlocal best, best_id
        for node in nodes:
            if node.event_id > best_id:
                best_id = node.event_id
                best = node
            _walk(node.children)

    _walk(event_tree)
    return best


def _has_markers(event_tree: List[EventNode]) -> bool:
    """Return ``True`` if the event tree contains any debug marker events."""
    def _check(nodes: List[EventNode]) -> bool:
        for node in nodes:
            if node.flags.is_marker:
                return True
            if _check(node.children):
                return True
        return False

    return _check(event_tree)


# =========================================================================
# S0: Intake and Normalize
# =========================================================================

async def intake_and_normalize(ctx: SkillContext) -> TaskState:
    """**S0** -- Parse input, open capture, build event tree.

    1. Infer bug types from the user description (if no hints provided).
    2. Create a RenderDoc replay session (local backend, headless).
    3. Open the ``.rdc`` capture file.
    4. Build the event tree from the capture.
    5. Infer render-pass boundaries if no debug markers are present.
    6. Populate ``TaskState`` with session_id, capture_id, and basic info.
    """
    task = ctx.task
    task.current_skill = "S0_intake_and_normalize"
    task.status = "running"
    task.updated_at = _ts()

    logger.info(
        "S0: Starting intake for task %s, capture: %s",
        task.task_id, task.input.rdc_path,
    )

    # 1. Infer bug types if none provided
    if not task.input.bug_type_hints:
        inferred = _infer_bug_types(task.input.description)
        task.input.bug_type_hints = inferred
        logger.info("S0: Inferred bug types: %s", [b.value for b in inferred])

    # 2. Create session
    try:
        session_info = await ctx.session_manager.create_session(
            backend_config={"type": task.input.backend_type.value},
            replay_config={"width": 1920, "height": 1080},
        )
        task.session_id = session_info.session_id
        logger.info("S0: Created session %s", session_info.session_id)
    except Exception as exc:
        logger.error("S0: Failed to create session: %s", exc, exc_info=True)
        task.status = "error"
        task.updated_at = _ts()
        return task

    # 3. Open capture
    try:
        capture_info = await ctx.session_manager.open_capture(
            session_id=task.session_id,
            rdc_path=task.input.rdc_path,
        )
        task.capture_id = capture_info.capture_id
        ctx.capture_info = capture_info
        logger.info(
            "S0: Opened capture %s (%s, %d events)",
            capture_info.capture_id,
            capture_info.api.value,
            capture_info.total_events,
        )
    except Exception as exc:
        logger.error("S0: Failed to open capture: %s", exc, exc_info=True)
        task.status = "error"
        task.updated_at = _ts()
        return task

    # 4. Build event tree
    try:
        tree = await asyncio.to_thread(
            ctx.event_graph_service.build_event_tree,
            task.session_id,
            ctx.session_manager,
        )
        ctx.event_tree = tree
        logger.info("S0: Built event tree with %d top-level nodes", len(tree))
    except Exception as exc:
        logger.error("S0: Failed to build event tree: %s", exc, exc_info=True)
        task.status = "error"
        task.updated_at = _ts()
        return task

    # 5. Infer passes if no markers present
    try:
        if not _has_markers(ctx.event_tree):
            ctx.event_tree = await asyncio.to_thread(
                ctx.event_graph_service.infer_passes,
                ctx.event_tree,
                task.session_id,
                ctx.session_manager,
            )
            logger.info("S0: Inferred render passes (no debug markers found)")
        else:
            logger.info("S0: Debug markers present; skipping pass inference")
    except Exception as exc:
        logger.warning("S0: Pass inference failed (non-fatal): %s", exc)

    task.updated_at = _ts()
    logger.info("S0: Intake complete for task %s", task.task_id)
    return task


# =========================================================================
# S1: Localize Anomaly
# =========================================================================

async def localize_anomaly(ctx: SkillContext) -> TaskState:
    """**S1** -- Render final output, detect anomalies, find hotspot pixels.

    1. Identify the last event (final output).
    2. Render the final output image and store as an artifact.
    3. Run the NaN/Inf verifier on the final output.
    4. If a reference image is provided, also run the ``image_diff`` verifier.
    5. Collect anomaly information (bounding box, mask, statistics).
    6. Sample candidate hotspot pixels from anomalous regions.
    7. Update ``TaskState.anomalies``.
    """
    task = ctx.task
    task.current_skill = "S1_localize_anomaly"
    task.updated_at = _ts()

    if not task.session_id or not task.capture_id:
        logger.error("S1: No session/capture available; skipping")
        return task

    logger.info("S1: Localizing anomaly for task %s", task.task_id)

    # 1. Find the last event (final output)
    last_event = _find_last_event(ctx.event_tree)
    if last_event is None:
        logger.error("S1: No events found in tree")
        return task

    final_eid = last_event.event_id
    logger.info("S1: Final output event: %d", final_eid)

    # 2. Render final output image
    try:
        artifact_ref, view_meta = await ctx.render_service.render_event(
            session_id=task.session_id,
            event_id=final_eid,
            session_manager=ctx.session_manager,
            artifact_store=ctx.artifact_store,
            source_config={"source": "final_output"},
            output_format="png",
        )
        logger.info("S1: Rendered final output -> %s", artifact_ref.uri)
    except Exception as exc:
        logger.error("S1: Failed to render final output: %s", exc, exc_info=True)
        return task

    # 3. Run NaN/Inf verifier
    anomalies_found: List[AnomalyInfo] = []

    try:
        verify_ctx = VerifyContext(
            session_id=task.session_id,
            capture_id=task.capture_id,
            event_id=final_eid,
            session_manager=ctx.session_manager,
            artifact_store=ctx.artifact_store,
            render_service=ctx.render_service,
        )
        naninf_result = await ctx.verifier_engine.run_verifier(
            "naninf", verify_ctx,
        )
        if not naninf_result.passed and naninf_result.anomaly is not None:
            anomalies_found.append(naninf_result.anomaly)
            logger.info(
                "S1: NaN/Inf detected: nan=%d inf=%d density=%.4f",
                naninf_result.anomaly.nan_count,
                naninf_result.anomaly.inf_count,
                naninf_result.anomaly.density,
            )
        elif naninf_result.passed:
            logger.info("S1: NaN/Inf verifier passed (no anomalies)")
    except Exception as exc:
        logger.warning("S1: NaN/Inf verifier failed: %s", exc)

    # 4. If reference image provided, run image_diff verifier
    if task.input.reference_image_path:
        try:
            diff_ctx = VerifyContext(
                session_id=task.session_id,
                capture_id=task.capture_id,
                event_id=final_eid,
                session_manager=ctx.session_manager,
                artifact_store=ctx.artifact_store,
                render_service=ctx.render_service,
                params={
                    "reference_image_path": task.input.reference_image_path,
                    "threshold": 0.01,
                },
            )
            diff_result = await ctx.verifier_engine.run_verifier(
                "image_diff", diff_ctx,
            )
            if not diff_result.passed and diff_result.anomaly is not None:
                anomalies_found.append(diff_result.anomaly)
                logger.info("S1: Image diff anomaly: %s", diff_result.notes)
        except Exception as exc:
            logger.warning("S1: Image diff verifier failed: %s", exc)

    # 5. & 6. Collect anomaly info and sample candidate hotspot pixels
    pipeline_snapshot: Optional[PipelineSnapshot] = None
    tex_id: Optional[str] = None

    try:
        pipeline_snapshot = await ctx.pipeline_service.snapshot_pipeline(
            session_id=task.session_id,
            event_id=final_eid,
            session_manager=ctx.session_manager,
        )
        if pipeline_snapshot and pipeline_snapshot.render_targets:
            tex_id = pipeline_snapshot.render_targets[0].resource_id
    except Exception as exc:
        logger.debug("S1: Pipeline snapshot for pixel picking failed: %s", exc)

    seen_coords: set = set()
    for anomaly in anomalies_found:
        if anomaly.bbox is None or tex_id is None:
            continue
        bbox = anomaly.bbox
        cx = (bbox.x0 + bbox.x1) // 2
        cy = (bbox.y0 + bbox.y1) // 2
        sample_points = [
            (cx, cy),
            (bbox.x0, bbox.y0),
            (bbox.x1, bbox.y0),
            (bbox.x0, bbox.y1),
            (bbox.x1, bbox.y1),
        ]
        for px, py in sample_points:
            if (px, py) in seen_coords:
                continue
            if len(ctx.candidate_pixels) >= _HOTSPOT_PIXEL_COUNT:
                break
            seen_coords.add((px, py))
            try:
                pixel_info = await ctx.render_service.pick_pixel(
                    session_id=task.session_id,
                    event_id=final_eid,
                    texture_id=tex_id,
                    x=px,
                    y=py,
                    session_manager=ctx.session_manager,
                )
                ctx.candidate_pixels.append(pixel_info)
            except Exception as exc:
                logger.debug(
                    "S1: Pixel pick at (%d,%d) failed: %s", px, py, exc,
                )

    # 7. Update TaskState
    task.anomalies = anomalies_found
    task.updated_at = _ts()

    logger.info(
        "S1: Localization complete: %d anomalies, %d candidate pixels",
        len(anomalies_found), len(ctx.candidate_pixels),
    )
    return task


# =========================================================================
# S2: Search First Bad
# =========================================================================

async def search_first_bad(ctx: SkillContext) -> TaskState:
    """**S2** -- Binary-search events to find the first bad draw call.

    1. Determine which verifier to use based on bug type.
    2. Get the range of draw events from the event tree.
    3. Run bisect via ``experiment_runner.run_bisect()``.
    4. Store ``BisectResult`` in ``TaskState``.
    """
    task = ctx.task
    task.current_skill = "S2_search_first_bad"
    task.updated_at = _ts()

    if not task.session_id or not task.capture_id:
        logger.error("S2: No session/capture available; skipping")
        return task

    logger.info("S2: Searching for first bad event in task %s", task.task_id)

    # 1. Select verifier
    verifier_config = _select_verifier(task.input.bug_type_hints)
    logger.info("S2: Using verifier: %s", verifier_config.type.value)

    # If the verifier is image_diff and we have a reference, add the path.
    if (verifier_config.type == VerifierType.IMAGE_DIFF
            and task.input.reference_image_path):
        verifier_config.params["reference_image_path"] = (
            task.input.reference_image_path
        )

    # 2. Get event range from draw events
    draw_events = _flatten_draw_events(ctx.event_tree)
    if len(draw_events) < 2:
        logger.warning(
            "S2: Not enough draw events (%d) for bisect; skipping",
            len(draw_events),
        )
        return task

    range_lo = draw_events[0].event_id
    range_hi = draw_events[-1].event_id
    logger.info("S2: Bisect range: [%d, %d] (%d draws)", range_lo, range_hi,
                len(draw_events))

    # 3. Run bisect
    try:
        bisect_result = await ctx.experiment_runner.run_bisect(
            session_id=task.session_id,
            capture_id=task.capture_id,
            range_lo=range_lo,
            range_hi=range_hi,
            verifier_config=verifier_config,
            strategy="binary",
            max_iters=60,
            confidence_threshold=0.85,
        )
        # 4. Store result
        task.bisect_result = bisect_result
        logger.info(
            "S2: Bisect complete: first_bad=%d, last_good=%d, "
            "confidence=%.3f, iterations=%d",
            bisect_result.first_bad_event_id,
            bisect_result.first_good_event_id,
            bisect_result.confidence,
            bisect_result.iterations,
        )
    except Exception as exc:
        logger.error("S2: Bisect failed: %s", exc, exc_info=True)

    task.updated_at = _ts()
    return task


# =========================================================================
# S3: Attribute Pass / Draw
# =========================================================================

async def attribute_pass_draw(ctx: SkillContext) -> TaskState:
    """**S3** -- Narrow the anomaly to a specific pass and draw call.

    1. Starting from ``first_bad_event``, examine nearby events.
    2. Check if pass boundaries exist (markers or inferred).
    3. Identify which pass and which specific draw(s) are responsible.
    4. Render before and after the first_bad_event to confirm the anomaly.
    5. Update ``TaskState`` with attribution info.
    """
    task = ctx.task
    task.current_skill = "S3_attribute_pass_draw"
    task.updated_at = _ts()

    if task.bisect_result is None:
        logger.warning("S3: No bisect result available; skipping")
        return task

    first_bad_eid = task.bisect_result.first_bad_event_id
    first_good_eid = task.bisect_result.first_good_event_id
    logger.info(
        "S3: Attributing anomaly around event %d (last good: %d)",
        first_bad_eid, first_good_eid,
    )

    # 1. & 2. Find the pass containing the first_bad_event
    draw_events = _flatten_draw_events(ctx.event_tree)
    bad_node: Optional[EventNode] = None
    bad_idx: int = -1

    for idx, node in enumerate(draw_events):
        if node.event_id == first_bad_eid:
            bad_node = node
            bad_idx = idx
            break

    # If exact match not found, find the nearest draw event.
    if bad_node is None:
        for idx, node in enumerate(draw_events):
            if node.event_id >= first_bad_eid:
                bad_node = node
                bad_idx = idx
                break

    if bad_node is None:
        logger.warning("S3: Could not locate first_bad_event in draw list")
        return task

    # Determine pass membership
    pass_name = bad_node.inferred_pass or "unknown_pass"
    responsible_draws: List[int] = [bad_node.event_id]

    # Collect all draws in the same pass
    for node in draw_events:
        if (node.inferred_pass == pass_name
                and node.event_id != bad_node.event_id):
            responsible_draws.append(node.event_id)
    responsible_draws.sort()

    logger.info(
        "S3: Bad event %d belongs to %s (%d draws in pass)",
        first_bad_eid, pass_name, len(responsible_draws),
    )

    # 3. Find the event immediately before the bad one for comparison
    prev_eid: Optional[int] = None
    if bad_idx > 0:
        prev_eid = draw_events[bad_idx - 1].event_id

    # 4. Render before and after to confirm the anomaly appears at this event
    before_artifact = None
    after_artifact = None

    if prev_eid is not None:
        try:
            before_artifact, _ = await ctx.render_service.render_event(
                session_id=task.session_id,
                event_id=prev_eid,
                session_manager=ctx.session_manager,
                artifact_store=ctx.artifact_store,
                source_config={"source": "final_output"},
                output_format="png",
            )
            logger.info("S3: Rendered before-event %d -> %s",
                        prev_eid, before_artifact.uri)
        except Exception as exc:
            logger.warning("S3: Before-render failed: %s", exc)

    try:
        after_artifact, _ = await ctx.render_service.render_event(
            session_id=task.session_id,
            event_id=first_bad_eid,
            session_manager=ctx.session_manager,
            artifact_store=ctx.artifact_store,
            source_config={"source": "final_output"},
            output_format="png",
        )
        logger.info("S3: Rendered at-event %d -> %s",
                    first_bad_eid, after_artifact.uri)
    except Exception as exc:
        logger.warning("S3: At-event render failed: %s", exc)

    # 5. Store attribution info in context
    ctx.attribution = {
        "first_bad_event_id": first_bad_eid,
        "first_good_event_id": first_good_eid,
        "pass_name": pass_name,
        "responsible_draws": responsible_draws,
        "prev_event_id": prev_eid,
        "before_artifact": (
            before_artifact.uri if before_artifact else None
        ),
        "after_artifact": (
            after_artifact.uri if after_artifact else None
        ),
    }

    task.updated_at = _ts()
    logger.info("S3: Attribution complete: pass=%s, draws=%s",
                pass_name, responsible_draws[:5])
    return task


# =========================================================================
# S4: Extract Pipeline and Shader
# =========================================================================

async def extract_pipeline_shader(ctx: SkillContext) -> TaskState:
    """**S4** -- Snapshot pipeline state and export shader artifacts.

    1. At ``first_bad_event``: snapshot the full pipeline state.
    2. Export shader artifacts for all active stages (at minimum PS/CS).
    3. Store ``PipelineSnapshot`` in ``TaskState.pipeline``.
    """
    task = ctx.task
    task.current_skill = "S4_extract_pipeline_shader"
    task.updated_at = _ts()

    if task.bisect_result is None:
        logger.warning("S4: No bisect result; skipping")
        return task

    target_eid = task.bisect_result.first_bad_event_id
    logger.info("S4: Extracting pipeline at event %d", target_eid)

    # 1. Snapshot pipeline state
    try:
        pipeline = await ctx.pipeline_service.snapshot_pipeline(
            session_id=task.session_id,
            event_id=target_eid,
            session_manager=ctx.session_manager,
        )
        task.pipeline = pipeline
        logger.info(
            "S4: Pipeline snapshot: %d shaders, %d RTs, %d bindings",
            len(pipeline.shaders),
            len(pipeline.render_targets),
            len(pipeline.bindings),
        )
    except Exception as exc:
        logger.error("S4: Pipeline snapshot failed: %s", exc, exc_info=True)
        return task

    # 2. Export shader artifacts for all active stages
    # Always attempt PS and CS; also export any other bound stages.
    stages_to_export: List[ShaderStage] = []
    for si in pipeline.shaders:
        stages_to_export.append(si.stage)

    # Ensure at least PS and CS are attempted even if not in the snapshot.
    for mandatory in (ShaderStage.PS, ShaderStage.CS):
        if mandatory not in stages_to_export:
            stages_to_export.append(mandatory)

    for stage in stages_to_export:
        try:
            bundle = await ctx.pipeline_service.export_shader(
                session_id=task.session_id,
                event_id=target_eid,
                stage=stage,
                session_manager=ctx.session_manager,
                artifact_store=ctx.artifact_store,
            )
            ctx.shader_exports[stage.value] = bundle
            logger.info(
                "S4: Exported %s shader -> refl=%s disasm=%s",
                stage.value,
                bundle.reflection_artifact.uri if bundle.reflection_artifact else "N/A",
                bundle.disasm_artifact.uri if bundle.disasm_artifact else "N/A",
            )
        except (ValueError, RuntimeError) as exc:
            # Expected when a stage has no bound shader.
            logger.debug("S4: Shader export for %s skipped: %s",
                         stage.value, exc)
        except Exception as exc:
            logger.warning("S4: Shader export for %s failed: %s",
                           stage.value, exc)

    task.updated_at = _ts()
    logger.info("S4: Pipeline extraction complete (%d shaders exported)",
                len(ctx.shader_exports))
    return task


# =========================================================================
# S5: Hypothesis and Patch Loop
# =========================================================================

async def hypothesis_and_patch_loop(
    ctx: SkillContext,
    max_hypotheses: int = _DEFAULT_MAX_HYPOTHESES,
) -> TaskState:
    """**S5** -- Generate, test, and rank fix hypotheses.

    1. Generate hypotheses based on bug type, pipeline state, shader
       analysis, and fingerprint matching.
    2. For each hypothesis, create a :class:`PatchSpec`.
    3. Run experiments for each hypothesis via the experiment runner.
    4. Score and rank hypotheses by result.
    5. Select the best fix candidate.
    6. Update ``TaskState.hypotheses`` and ``TaskState.experiments``.
    """
    task = ctx.task
    task.current_skill = "S5_hypothesis_and_patch_loop"
    task.updated_at = _ts()

    if task.bisect_result is None:
        logger.warning("S5: No bisect result; skipping")
        return task

    target_eid = task.bisect_result.first_bad_event_id
    pipeline = task.pipeline
    logger.info("S5: Starting hypothesis loop at event %d", target_eid)

    # Gather shader info for hypothesis generation
    shader_info: Dict[str, Any] = {}
    ps_export = ctx.shader_exports.get(ShaderStage.PS.value)
    if ps_export is None:
        ps_export = ctx.shader_exports.get(ShaderStage.CS.value)

    if ps_export is not None and ps_export.disasm_artifact is not None:
        try:
            disasm_bytes = await ctx.artifact_store.retrieve(
                ps_export.disasm_artifact.sha256,
            )
            shader_info["disasm_text"] = disasm_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.debug("S5: Could not retrieve disasm artifact: %s", exc)

    # Check fingerprint store for similar bugs
    if ctx.fingerprint_store is not None and pipeline is not None:
        try:
            fp_query = PassFingerprint(
                rt_formats=[rt.format for rt in pipeline.render_targets],
                blend_modes=[
                    bs.color_op for bs in pipeline.blend_states if bs.enabled
                ],
                depth_mode=pipeline.depth_stencil.depth_func,
            )
            matches = await ctx.fingerprint_store.search(fp_query)
            if matches:
                shader_info["fingerprint_matches"] = matches
                logger.info(
                    "S5: Found %d fingerprint matches", len(matches),
                )
        except Exception as exc:
            logger.debug("S5: Fingerprint search failed: %s", exc)

    # 1. Generate hypotheses
    all_hypotheses = _generate_hypotheses(task, pipeline, shader_info)
    hypotheses = all_hypotheses[:max_hypotheses]
    logger.info("S5: Generated %d hypotheses (capped at %d)",
                len(all_hypotheses), max_hypotheses)

    # Select verifier for experiments
    verifier_config = _select_verifier(task.input.bug_type_hints)
    if (verifier_config.type == VerifierType.IMAGE_DIFF
            and task.input.reference_image_path):
        verifier_config.params["reference_image_path"] = (
            task.input.reference_image_path
        )

    all_experiments: List[ExperimentResult] = []

    # 2. & 3. For each hypothesis, create a patch and run an experiment
    for hyp in hypotheses:
        logger.info("S5: Testing hypothesis: %s (score=%.2f)",
                     hyp.title, hyp.priority_score)

        # Create patch spec
        patch_spec = _create_patch_for_hypothesis(
            hyp, pipeline, shader_info, target_eid,
        )

        # Register patch with experiment runner
        try:
            ctx.experiment_runner.register_patch_spec(patch_spec)
        except Exception as exc:
            logger.warning("S5: Failed to register patch spec: %s", exc)
            hyp.result = VerdictResult.ERROR
            continue

        # Build experiment definition
        exp_def = ExperimentDef(
            session_id=task.session_id,
            capture_id=task.capture_id,
            event_id=target_eid,
            verifier=verifier_config,
            patch_id=patch_spec.patch_id,
            description=f"Test hypothesis: {hyp.title}",
        )

        # Run the experiment
        try:
            exp_result = await ctx.experiment_runner.run_experiment(exp_def)
            all_experiments.append(exp_result)

            # Extract verdict from evidence
            if exp_result.evidence is not None:
                hyp.result = exp_result.evidence.verdict
            else:
                hyp.result = VerdictResult.ERROR

            logger.info(
                "S5: Hypothesis '%s' -> verdict=%s (%.2fs)",
                hyp.title,
                hyp.result.value if hyp.result else "N/A",
                exp_result.duration_seconds,
            )
        except Exception as exc:
            logger.warning("S5: Experiment for '%s' failed: %s",
                           hyp.title, exc)
            hyp.result = VerdictResult.ERROR

        # Ensure patches are reverted after each experiment
        try:
            await ctx.patch_engine.revert_all(
                task.session_id, ctx.session_manager,
            )
        except Exception as exc:
            logger.debug("S5: Patch revert cleanup: %s", exc)

    # 4. Score and rank hypotheses by result
    _VERDICT_SCORES = {
        VerdictResult.FIXED: 1.0,
        VerdictResult.IMPROVED: 0.7,
        VerdictResult.INCONCLUSIVE: 0.2,
        VerdictResult.REJECTED: 0.0,
        VerdictResult.ERROR: 0.0,
    }
    for hyp in hypotheses:
        verdict_score = _VERDICT_SCORES.get(hyp.result, 0.0)
        hyp.priority_score = (hyp.priority_score * 0.3) + (verdict_score * 0.7)

    hypotheses.sort(key=lambda h: h.priority_score, reverse=True)

    # 5. Select best fix candidate (logged for the report)
    best = hypotheses[0] if hypotheses else None
    if best is not None:
        logger.info(
            "S5: Best hypothesis: '%s' (verdict=%s, final_score=%.2f)",
            best.title,
            best.result.value if best.result else "N/A",
            best.priority_score,
        )

    # 6. Update TaskState
    task.hypotheses = hypotheses
    task.experiments.extend(all_experiments)
    task.updated_at = _ts()

    logger.info("S5: Hypothesis loop complete: %d tested, %d experiments",
                len(hypotheses), len(all_experiments))
    return task


# =========================================================================
# S6: Map to Engine
# =========================================================================

async def map_to_engine(ctx: SkillContext) -> TaskState:
    """**S6** -- Map pipeline artifacts to engine source (e.g. Unreal Engine).

    1. Use ``kb_connector`` to search for UE module mappings.
    2. Use shader names, resource binding patterns, and pipeline state to
       identify the originating engine pass, ``.usf/.ush`` files, and
       material nodes.
    3. Store mapping info in ``TaskState`` (via context).
    """
    task = ctx.task
    task.current_skill = "S6_map_to_engine"
    task.updated_at = _ts()

    if ctx.kb_connector is None:
        logger.info("S6: No KB connector available; skipping engine mapping")
        return task

    pipeline = task.pipeline
    logger.info("S6: Mapping pipeline to engine source")

    # Build a query from pipeline state and shader info
    query: Dict[str, Any] = {
        "project_id": task.input.project_id or "",
    }

    if pipeline is not None:
        query["rt_formats"] = [rt.format for rt in pipeline.render_targets]
        query["binding_names"] = [
            b.resource_name for b in pipeline.bindings if b.resource_name
        ]
        query["shader_stages"] = [s.stage.value for s in pipeline.shaders]
        query["blend_enabled"] = any(
            bs.enabled for bs in pipeline.blend_states
        )
        query["depth_test"] = pipeline.depth_stencil.depth_test_enabled

    # Add shader names from exports
    for stage_val, bundle in ctx.shader_exports.items():
        if hasattr(bundle, "entry_point") and bundle.entry_point:
            query.setdefault("entry_points", []).append(bundle.entry_point)

    # 1. Search KB for UE module mappings
    mapping: Dict[str, Any] = {}

    try:
        kb_result = await ctx.kb_connector.search_ue_mapping(query)
        if kb_result:
            mapping = kb_result
            logger.info(
                "S6: KB returned mapping: pass=%s, files=%s",
                mapping.get("ue_pass", "unknown"),
                mapping.get("usf_files", []),
            )
    except Exception as exc:
        logger.warning("S6: KB search failed: %s", exc)

    # 2. Heuristic-based mapping when KB returns nothing
    if not mapping:
        mapping = _heuristic_engine_mapping(pipeline, ctx.shader_exports)
        logger.info("S6: Using heuristic engine mapping: %s",
                     mapping.get("ue_pass", "unknown"))

    # 3. Store mapping info
    ctx.engine_mapping = mapping
    task.updated_at = _ts()

    logger.info("S6: Engine mapping complete")
    return task


def _heuristic_engine_mapping(
    pipeline: Optional[PipelineSnapshot],
    shader_exports: Dict[str, Any],
) -> Dict[str, Any]:
    """Best-effort UE pass identification from pipeline state heuristics.

    Uses render target counts, depth/blend configuration, and binding
    patterns to guess which Unreal Engine rendering pass produced the
    draw call.
    """
    mapping: Dict[str, Any] = {
        "ue_pass": "Unknown",
        "usf_files": [],
        "ush_files": [],
        "material_nodes": [],
        "confidence": 0.0,
    }

    if pipeline is None:
        return mapping

    num_rts = len(pipeline.render_targets)
    has_depth = pipeline.depth_stencil.depth_test_enabled
    has_depth_write = pipeline.depth_stencil.depth_write_enabled
    has_blend = any(bs.enabled for bs in pipeline.blend_states)

    # GBuffer / BasePass: multiple RTs, depth write enabled, no blend
    if num_rts >= 3 and has_depth_write and not has_blend:
        mapping["ue_pass"] = "BasePass"
        mapping["usf_files"] = [
            "BasePassPixelShader.usf", "BasePassVertexShader.usf",
        ]
        mapping["ush_files"] = [
            "BasePassCommon.ush", "ShadingModelsMaterial.ush",
        ]
        mapping["confidence"] = 0.6

    # Translucency: blend enabled, depth test but no depth write
    elif has_blend and has_depth and not has_depth_write:
        mapping["ue_pass"] = "TranslucencyPass"
        mapping["usf_files"] = ["TranslucentLighting.usf"]
        mapping["ush_files"] = ["BasePassCommon.ush"]
        mapping["confidence"] = 0.5

    # Lighting / Deferred: single RT, no depth write, no blend
    elif num_rts == 1 and not has_depth_write and not has_blend:
        mapping["ue_pass"] = "LightingPass"
        mapping["usf_files"] = ["DeferredLightPixelShaders.usf"]
        mapping["ush_files"] = ["DeferredShadingCommon.ush"]
        mapping["confidence"] = 0.4

    # Post-process: single RT, no depth, no blend
    elif num_rts == 1 and not has_depth:
        mapping["ue_pass"] = "PostProcess"
        mapping["usf_files"] = ["PostProcessCombineLUTs.usf"]
        mapping["ush_files"] = ["PostProcessCommon.ush"]
        mapping["confidence"] = 0.3

    # Shadow pass: depth-only, no colour RTs
    elif num_rts == 0 and has_depth_write:
        mapping["ue_pass"] = "ShadowDepthPass"
        mapping["usf_files"] = ["ShadowDepthPixelShader.usf"]
        mapping["confidence"] = 0.5

    return mapping


# =========================================================================
# S7: Build Report
# =========================================================================

async def build_report(ctx: SkillContext) -> TaskState:
    """**S7** -- Assemble the final report bundle.

    1. Use ``report_builder`` to generate the full report.
    2. Store all accumulated evidence.
    3. Update ``TaskState.report``.
    4. Optionally store fingerprints if the verdict is successful.
    """
    task = ctx.task
    task.current_skill = "S7_build_report"
    task.updated_at = _ts()

    logger.info("S7: Building report for task %s", task.task_id)

    # Determine the best hypothesis result
    best_hypothesis: Optional[Hypothesis] = None
    fix_candidate: Optional[Dict[str, Any]] = None
    overall_confidence = 0.0

    if task.hypotheses:
        best_hypothesis = task.hypotheses[0]
        if best_hypothesis.result in (VerdictResult.FIXED, VerdictResult.IMPROVED):
            fix_candidate = {
                "hypothesis_id": best_hypothesis.hypothesis_id,
                "title": best_hypothesis.title,
                "verdict": best_hypothesis.result.value,
                "patches": best_hypothesis.proposed_patches,
            }
            overall_confidence = best_hypothesis.priority_score

    # Combine bisect confidence with hypothesis confidence
    if task.bisect_result is not None:
        bisect_conf = task.bisect_result.confidence
        overall_confidence = (
            bisect_conf * 0.4 + overall_confidence * 0.6
        ) if overall_confidence > 0 else bisect_conf * 0.4

    # Collect evidence artifacts
    evidence_artifacts: Dict[str, str] = {}
    for stage_val, bundle in ctx.shader_exports.items():
        if bundle.reflection_artifact:
            evidence_artifacts[f"shader_refl_{stage_val}"] = (
                bundle.reflection_artifact.uri
            )
        if bundle.disasm_artifact:
            evidence_artifacts[f"shader_disasm_{stage_val}"] = (
                bundle.disasm_artifact.uri
            )
    if ctx.attribution.get("before_artifact"):
        evidence_artifacts["before_bad_event"] = ctx.attribution["before_artifact"]
    if ctx.attribution.get("after_artifact"):
        evidence_artifacts["at_bad_event"] = ctx.attribution["after_artifact"]

    # Collect verifier metrics
    verifier_metrics: Dict[str, Any] = {}
    for anomaly in task.anomalies:
        verifier_metrics[f"anomaly_{anomaly.anomaly_id}"] = {
            "type": anomaly.type,
            "nan_count": anomaly.nan_count,
            "inf_count": anomaly.inf_count,
            "density": anomaly.density,
            "stats": anomaly.stats,
        }

    # Get first bbox from anomalies
    first_bbox = None
    for anomaly in task.anomalies:
        if anomaly.bbox is not None:
            first_bbox = anomaly.bbox
            break

    # 1. Build the report bundle
    report = ReportBundle(
        task_id=task.task_id,
        bug_types=task.input.bug_type_hints,
        capture_info=ctx.capture_info,
        first_bad_event_id=(
            task.bisect_result.first_bad_event_id
            if task.bisect_result else None
        ),
        first_good_event_id=(
            task.bisect_result.first_good_event_id
            if task.bisect_result else None
        ),
        bbox=first_bbox,
        verifier_metrics=verifier_metrics,
        hypotheses_tried=task.hypotheses,
        fix_candidate=fix_candidate,
        confidence=overall_confidence,
        evidence_artifacts=evidence_artifacts,
        pipeline_snapshot=task.pipeline,
        experiments=task.experiments,
    )

    # Try using the report_builder service if available
    if ctx.report_builder is not None:
        try:
            report = await ctx.report_builder.generate_report(
                task_state=task,
                evidence_artifacts=evidence_artifacts,
                attribution=ctx.attribution,
                engine_mapping=ctx.engine_mapping,
            )
            logger.info("S7: Report generated via report_builder service")
        except Exception as exc:
            logger.warning(
                "S7: report_builder.generate_report failed, "
                "using inline report: %s", exc,
            )

    # 2. Store report in TaskState
    task.report = report
    task.status = "completed"
    task.updated_at = _ts()

    logger.info(
        "S7: Report complete: confidence=%.3f, fix_candidate=%s",
        overall_confidence,
        fix_candidate["title"] if fix_candidate else "none",
    )

    # 3. Optionally store fingerprints for successful verdicts
    if (ctx.fingerprint_store is not None
            and best_hypothesis is not None
            and best_hypothesis.result in (VerdictResult.FIXED,
                                           VerdictResult.IMPROVED)):
        try:
            pass_fp = None
            shader_fp = None

            if task.pipeline is not None:
                pass_fp = PassFingerprint(
                    rt_formats=[
                        rt.format for rt in task.pipeline.render_targets
                    ],
                    blend_modes=[
                        bs.color_op
                        for bs in task.pipeline.blend_states
                        if bs.enabled
                    ],
                    depth_mode=task.pipeline.depth_stencil.depth_func,
                    binding_pattern=[
                        b.resource_name
                        for b in task.pipeline.bindings
                        if b.resource_name
                    ],
                )

            ps_export = ctx.shader_exports.get(ShaderStage.PS.value)
            if ps_export is not None:
                shader_fp = ShaderFingerprint(
                    shader_hash=getattr(ps_export, "shader_id", ""),
                    resource_names=[
                        b.resource_name
                        for b in (task.pipeline.bindings if task.pipeline else [])
                        if b.resource_name
                    ],
                )

            record = FingerprintRecord(
                task_id=task.task_id,
                pass_fp=pass_fp,
                shader_fp=shader_fp,
                bug_type=(
                    best_hypothesis.bug_types[0]
                    if best_hypothesis.bug_types
                    else BugType.UNKNOWN
                ),
                verdict=best_hypothesis.result,
                project_id=task.input.project_id,
            )
            await ctx.fingerprint_store.store(record)
            logger.info("S7: Stored fingerprint record %s", record.record_id)
        except Exception as exc:
            logger.warning("S7: Fingerprint storage failed: %s", exc)

    return task


# =========================================================================
# Orchestrator（编排器）
# =========================================================================

async def run_full_debug_pipeline(
    task_input: TaskInput,
    services: dict,
) -> TaskState:
    """端到端运行完整的 S0--S7 debug pipeline。

    使用提供的 *services* 字典创建 :class:`SkillContext`，
    并按顺序执行每个 skill。若某个 skill 抛出异常，将记录日志并
    以降级结果继续流程。

    Parameters
    ----------
    task_input:
        用户提供的任务输入（capture 路径、描述、提示）。
    services:
        服务名到实例的映射字典。预期键包括::

            session_manager, event_graph_service, render_service,
            pipeline_service, verifier_engine, patch_engine,
            experiment_runner, debug_service, perf_service,
            report_builder, fingerprint_store, kb_connector,
            artifact_store

    Returns
    -------
    TaskState
        所有 skills 执行完成后的最终 task state
        （若失败则为部分执行结果）。
    """
    task = TaskState(input=task_input)

    ctx = SkillContext(
        session_manager=services["session_manager"],
        event_graph_service=services["event_graph_service"],
        render_service=services["render_service"],
        pipeline_service=services["pipeline_service"],
        verifier_engine=services["verifier_engine"],
        patch_engine=services["patch_engine"],
        experiment_runner=services["experiment_runner"],
        debug_service=services["debug_service"],
        perf_service=services.get("perf_service"),
        report_builder=services.get("report_builder"),
        fingerprint_store=services.get("fingerprint_store"),
        kb_connector=services.get("kb_connector"),
        artifact_store=services["artifact_store"],
        task=task,
    )

    # Ordered skill pipeline
    skills: List[Tuple[str, Any]] = [
        ("S0_intake_and_normalize", intake_and_normalize),
        ("S1_localize_anomaly", localize_anomaly),
        ("S2_search_first_bad", search_first_bad),
        ("S3_attribute_pass_draw", attribute_pass_draw),
        ("S4_extract_pipeline_shader", extract_pipeline_shader),
        ("S5_hypothesis_and_patch_loop", hypothesis_and_patch_loop),
        ("S6_map_to_engine", map_to_engine),
        ("S7_build_report", build_report),
    ]

    for skill_name, skill_fn in skills:
        logger.info("Pipeline: starting %s", skill_name)
        t0 = _ts()
        try:
            ctx.task = await skill_fn(ctx)
        except Exception as exc:
            logger.error(
                "Pipeline: %s failed with %s: %s",
                skill_name, type(exc).__name__, exc,
                exc_info=True,
            )
            ctx.task.status = f"degraded_after_{skill_name}"
            ctx.task.updated_at = _ts()
            # Continue to next skill with whatever state we have.
        elapsed = _ts() - t0
        logger.info("Pipeline: %s completed in %.2fs", skill_name, elapsed)

    # Ensure final status is set
    if ctx.task.status == "running":
        ctx.task.status = "completed"
    ctx.task.updated_at = _ts()

    logger.info(
        "Pipeline: finished task %s with status=%s",
        ctx.task.task_id, ctx.task.status,
    )
    return ctx.task
