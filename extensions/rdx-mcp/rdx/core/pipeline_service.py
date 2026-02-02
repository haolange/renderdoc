"""Pipeline state 检查与 shader artifact 导出 service。

将 RenderDoc 的 pipeline state introspection 与 shader disassembly
APIs 封装为 async 操作，返回结构化的 Pydantic models 与版本化 artifacts。

所有阻塞的 RenderDoc 调用都会通过 ``asyncio.to_thread`` 分派到线程。
``renderdoc`` module 采用延迟导入——仅在 RenderDoc replay context 中可用。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from rdx.models import (
    ArtifactRef,
    BlendState,
    DepthStencilState,
    GraphicsAPI,
    PipelineSnapshot,
    RenderTargetInfo,
    ResourceBindingEntry,
    ShaderExportBundle,
    ShaderInfo,
    ShaderStage,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy renderdoc import（延迟导入）
# ---------------------------------------------------------------------------

_rd_module: Any = None


def _get_rd() -> Any:
    """返回 ``renderdoc`` module，并在首次访问时导入。"""
    global _rd_module
    if _rd_module is None:
        try:
            import renderdoc as _rd  # type: ignore[import-not-found]
        except ImportError:
            raise ImportError(
                "The 'renderdoc' Python module is not available.  "
                "Make sure you are running inside a RenderDoc replay "
                "context or that the renderdoc shared library directory "
                "is on sys.path / PYTHONPATH."
            ) from None
        _rd_module = _rd
    return _rd_module


# ---------------------------------------------------------------------------
# Dependency protocols（依赖协议）
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionManager(Protocol):
    """session lifecycle manager 的最小结构化约定。"""

    def get_controller(self, session_id: str) -> Any:
        """返回绑定到 *session_id* 的 ``ReplayController``。"""
        ...

    def get_output(self, session_id: str) -> Any:
        """返回绑定到 *session_id* 的 ``ReplayOutput``。"""
        ...


@runtime_checkable
class ArtifactStore(Protocol):
    """artifact persistence layer 的最小结构化约定。"""

    async def store(
        self,
        data: bytes,
        *,
        mime: str,
        suffix: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> ArtifactRef:
        """持久化 *data* 并返回追踪用的 :class:`ArtifactRef`。"""
        ...


# ---------------------------------------------------------------------------
# Shader stage enumeration helpers（shader stage 枚举辅助）
# ---------------------------------------------------------------------------

# 枚举绑定 shader 时遍历的 graphics stages，顺序与典型 graphics pipeline 一致。
_GRAPHICS_STAGES: Tuple[str, ...] = (
    "Vertex",
    "Hull",
    "Domain",
    "Geometry",
    "Pixel",
)

_COMPUTE_STAGES: Tuple[str, ...] = ("Compute",)


def _rd_shader_stages() -> List[Any]:
    """返回所有 graphics 与 compute stages 的 ``rd.ShaderStage`` 列表。"""
    rd = _get_rd()
    return [
        rd.ShaderStage.Vertex,
        rd.ShaderStage.Hull,
        rd.ShaderStage.Domain,
        rd.ShaderStage.Geometry,
        rd.ShaderStage.Pixel,
        rd.ShaderStage.Compute,
    ]


def _map_shader_stage(rd_stage: Any) -> ShaderStage:
    """将 RenderDoc ``ShaderStage`` enum 映射为我们的 ``ShaderStage``。"""
    rd = _get_rd()
    mapping: Dict[Any, ShaderStage] = {
        rd.ShaderStage.Vertex: ShaderStage.VS,
        rd.ShaderStage.Hull: ShaderStage.HS,
        rd.ShaderStage.Domain: ShaderStage.DS,
        rd.ShaderStage.Geometry: ShaderStage.GS,
        rd.ShaderStage.Pixel: ShaderStage.PS,
        rd.ShaderStage.Compute: ShaderStage.CS,
    }
    return mapping.get(rd_stage, ShaderStage.PS)


def _our_stage_to_rd(stage: ShaderStage) -> Any:
    """将我们的 ``ShaderStage`` 映射回 RenderDoc ``ShaderStage``。"""
    rd = _get_rd()
    mapping: Dict[ShaderStage, Any] = {
        ShaderStage.VS: rd.ShaderStage.Vertex,
        ShaderStage.HS: rd.ShaderStage.Hull,
        ShaderStage.DS: rd.ShaderStage.Domain,
        ShaderStage.GS: rd.ShaderStage.Geometry,
        ShaderStage.PS: rd.ShaderStage.Pixel,
        ShaderStage.CS: rd.ShaderStage.Compute,
    }
    result = mapping.get(stage)
    if result is None:
        raise ValueError(f"Unsupported shader stage for RenderDoc: {stage}")
    return result


# ---------------------------------------------------------------------------
# Graphics API mapping（Graphics API 映射）
# ---------------------------------------------------------------------------


def _map_graphics_api(rd_api: Any) -> GraphicsAPI:
    """将 ``rd.GraphicsAPI`` 映射为我们的 ``GraphicsAPI`` enum。"""
    rd = _get_rd()
    mapping: Dict[Any, GraphicsAPI] = {
        rd.GraphicsAPI.D3D11: GraphicsAPI.D3D11,
        rd.GraphicsAPI.D3D12: GraphicsAPI.D3D12,
        rd.GraphicsAPI.Vulkan: GraphicsAPI.VULKAN,
        rd.GraphicsAPI.OpenGL: GraphicsAPI.OPENGL,
    }
    return mapping.get(rd_api, GraphicsAPI.UNKNOWN)


# ---------------------------------------------------------------------------
# Null ResourceId helper（空 ResourceId 判断）
# ---------------------------------------------------------------------------


def _is_null_id(resource_id: Any) -> bool:
    """当 *resource_id* 为空/null 时返回 ``True``。"""
    rd = _get_rd()
    try:
        return resource_id == rd.ResourceId()
    except Exception:
        return resource_id is None


# ---------------------------------------------------------------------------
# API-specific state retrieval（API 特定 state 获取）
# ---------------------------------------------------------------------------


def _get_api_specific_state(controller: Any, rd_api: Any) -> Any:
    """返回 API 特定的 pipeline state 对象。

    若 API 无法识别则返回 ``None``。
    """
    rd = _get_rd()
    if rd_api == rd.GraphicsAPI.D3D11:
        return controller.GetD3D11PipelineState()
    if rd_api == rd.GraphicsAPI.D3D12:
        return controller.GetD3D12PipelineState()
    if rd_api == rd.GraphicsAPI.Vulkan:
        return controller.GetVulkanPipelineState()
    if rd_api == rd.GraphicsAPI.OpenGL:
        return controller.GetOpenGLPipelineState()
    return None


# ---------------------------------------------------------------------------
# Blend state extraction（API 特定）
# ---------------------------------------------------------------------------


def _extract_blend_state(api_state: Any, api: GraphicsAPI) -> List[BlendState]:
    """从 API 特定 state 中提取每个 RT 的 blend 配置。

    每个 render target slot 返回一个 :class:`BlendState`。
    """
    blends: List[BlendState] = []
    if api_state is None:
        return blends

    try:
        if api in (GraphicsAPI.D3D11, GraphicsAPI.D3D12):
            raw_blends = api_state.outputMerger.blendState.blends
        elif api == GraphicsAPI.VULKAN:
            raw_blends = api_state.colorBlend.blends
        elif api == GraphicsAPI.OPENGL:
            raw_blends = api_state.framebuffer.blendState.blends
        else:
            return blends

        for b in raw_blends:
            blends.append(BlendState(
                enabled=bool(b.enabled),
                src_color=str(b.colorBlend.source),
                dst_color=str(b.colorBlend.destination),
                color_op=str(b.colorBlend.operation),
                src_alpha=str(b.alphaBlend.source),
                dst_alpha=str(b.alphaBlend.destination),
                alpha_op=str(b.alphaBlend.operation),
            ))
    except (AttributeError, TypeError) as exc:
        logger.debug("_extract_blend_state: %s", exc)

    return blends


# ---------------------------------------------------------------------------
# Depth / stencil extraction（API 特定）
# ---------------------------------------------------------------------------


def _extract_depth_stencil(
    api_state: Any,
    api: GraphicsAPI,
) -> DepthStencilState:
    """从 API 特定 state 中提取 depth/stencil 配置。"""
    ds = DepthStencilState()
    if api_state is None:
        return ds

    try:
        if api in (GraphicsAPI.D3D11, GraphicsAPI.D3D12):
            raw = api_state.outputMerger.depthStencilState
            ds.depth_test_enabled = bool(raw.depthEnable)
            ds.depth_write_enabled = bool(raw.depthWrites)
            ds.depth_func = str(raw.depthFunction)
            ds.stencil_enabled = bool(raw.stencilEnable)
        elif api == GraphicsAPI.VULKAN:
            raw = api_state.depthStencil
            ds.depth_test_enabled = bool(raw.depthTestEnable)
            ds.depth_write_enabled = bool(raw.depthWriteEnable)
            ds.depth_func = str(raw.depthFunction)
            ds.stencil_enabled = bool(raw.stencilTestEnable)
        elif api == GraphicsAPI.OPENGL:
            ds.depth_test_enabled = bool(api_state.depthState.depthEnable)
            ds.depth_write_enabled = bool(api_state.depthState.depthWrites)
            ds.depth_func = str(api_state.depthState.depthFunction)
            ds.stencil_enabled = bool(api_state.stencilState.stencilEnable)
    except (AttributeError, TypeError) as exc:
        logger.debug("_extract_depth_stencil: %s", exc)

    return ds


# ---------------------------------------------------------------------------
# Render target extraction（渲染目标提取）
# ---------------------------------------------------------------------------


async def _extract_render_targets(
    pipe_state: Any,
    api: GraphicsAPI,
    controller: Any,
) -> Tuple[List[RenderTargetInfo], Optional[RenderTargetInfo]]:
    """提取 colour render targets 与 depth target。

    使用抽象接口 ``PipeState.GetOutputTargets()`` 与
    ``PipeState.GetDepthTarget()``，因此逻辑与 API 无关。

    返回 ``(colour_targets, depth_target)``。
    """
    colour_targets: List[RenderTargetInfo] = []
    depth_target: Optional[RenderTargetInfo] = None

    textures = await asyncio.to_thread(controller.GetTextures)
    tex_by_id: Dict[Any, Any] = {tex.resourceId: tex for tex in textures}

    # ---- colour outputs ----
    try:
        output_descriptors = pipe_state.GetOutputTargets()
        for desc in output_descriptors:
            rid = desc.resourceId
            if _is_null_id(rid):
                continue
            tex = tex_by_id.get(rid)
            rt = RenderTargetInfo(resource_id=str(rid))
            if tex is not None:
                rt.format = str(tex.format.Name()) if hasattr(tex.format, "Name") else str(tex.format)
                rt.width = int(tex.width)
                rt.height = int(tex.height)
                # 经验判断：检查 format 名称中是否包含 sRGB。
                rt.is_srgb = "srgb" in rt.format.lower()
            colour_targets.append(rt)
    except (AttributeError, TypeError) as exc:
        logger.debug("_extract_render_targets (colour): %s", exc)

    # ---- depth target ----
    try:
        depth_desc = pipe_state.GetDepthTarget()
        rid = depth_desc.resourceId
        if not _is_null_id(rid):
            tex = tex_by_id.get(rid)
            dt = RenderTargetInfo(resource_id=str(rid))
            if tex is not None:
                dt.format = str(tex.format.Name()) if hasattr(tex.format, "Name") else str(tex.format)
                dt.width = int(tex.width)
                dt.height = int(tex.height)
            depth_target = dt
    except (AttributeError, TypeError) as exc:
        logger.debug("_extract_render_targets (depth): %s", exc)

    return colour_targets, depth_target


# ---------------------------------------------------------------------------
# Viewport / scissor extraction（API 特定）
# ---------------------------------------------------------------------------


def _extract_viewport(api_state: Any, api: GraphicsAPI) -> Dict[str, float]:
    """返回第一个 viewport，格式为 ``{x, y, width, height, minDepth, maxDepth}``。"""
    try:
        if api in (GraphicsAPI.D3D11, GraphicsAPI.D3D12, GraphicsAPI.OPENGL):
            vp = api_state.rasterizer.viewports[0]
            return {
                "x": float(vp.x),
                "y": float(vp.y),
                "width": float(vp.width),
                "height": float(vp.height),
                "min_depth": float(vp.minDepth),
                "max_depth": float(vp.maxDepth),
            }
        if api == GraphicsAPI.VULKAN:
            vs = api_state.viewportScissor.viewportScissors[0]
            vp = vs.vp
            return {
                "x": float(vp.x),
                "y": float(vp.y),
                "width": float(vp.width),
                "height": float(vp.height),
                "min_depth": float(vp.minDepth),
                "max_depth": float(vp.maxDepth),
            }
    except (AttributeError, IndexError, TypeError) as exc:
        logger.debug("_extract_viewport: %s", exc)
    return {}


def _extract_scissor(api_state: Any, api: GraphicsAPI) -> Dict[str, int]:
    """返回第一个 scissor rect，格式为 ``{x, y, width, height}``。"""
    try:
        if api in (GraphicsAPI.D3D11, GraphicsAPI.D3D12, GraphicsAPI.OPENGL):
            sc = api_state.rasterizer.scissors[0]
            return {
                "x": int(sc.x),
                "y": int(sc.y),
                "width": int(sc.width),
                "height": int(sc.height),
            }
        if api == GraphicsAPI.VULKAN:
            vs = api_state.viewportScissor.viewportScissors[0]
            sc = vs.scissor
            return {
                "x": int(sc.x),
                "y": int(sc.y),
                "width": int(sc.width),
                "height": int(sc.height),
            }
    except (AttributeError, IndexError, TypeError) as exc:
        logger.debug("_extract_scissor: %s", exc)
    return {}


# ---------------------------------------------------------------------------
# Topology extraction（API 特定）
# ---------------------------------------------------------------------------


def _extract_topology(api_state: Any, api: GraphicsAPI) -> str:
    """返回可读的 primitive topology 字符串。"""
    try:
        if api in (GraphicsAPI.D3D11, GraphicsAPI.D3D12, GraphicsAPI.VULKAN):
            return str(api_state.inputAssembly.topology)
        if api == GraphicsAPI.OPENGL:
            return str(api_state.vertexInput.topology)
    except (AttributeError, TypeError) as exc:
        logger.debug("_extract_topology: %s", exc)
    return ""


# ---------------------------------------------------------------------------
# Resource binding helpers（资源绑定辅助）
# ---------------------------------------------------------------------------


def _collect_bindings_for_stage(
    pipe: Any,
    rd_stage: Any,
    our_stage: ShaderStage,
) -> List[ResourceBindingEntry]:
    """提取单个 shader stage 的所有资源绑定。"""
    entries: List[ResourceBindingEntry] = []

    # ---- Read-only resources（SRVs, textures, samplers）----
    try:
        for bound_array in pipe.GetReadOnlyResources(rd_stage):
            bind_point = bound_array.bindPoint
            for res in bound_array.resources:
                rid = res.resourceId if hasattr(res, "resourceId") else None
                entries.append(ResourceBindingEntry(
                    set_or_space=int(bind_point.bindset) if hasattr(bind_point, "bindset") else 0,
                    binding=int(bind_point.bind) if hasattr(bind_point, "bind") else 0,
                    resource_id=str(rid) if rid is not None else "",
                    resource_name="",
                    type="SRV",
                    format="",
                ))
    except (AttributeError, TypeError):
        pass

    # ---- Read-write resources（UAVs, storage buffers）----
    try:
        for bound_array in pipe.GetReadWriteResources(rd_stage):
            bind_point = bound_array.bindPoint
            for res in bound_array.resources:
                rid = res.resourceId if hasattr(res, "resourceId") else None
                entries.append(ResourceBindingEntry(
                    set_or_space=int(bind_point.bindset) if hasattr(bind_point, "bindset") else 0,
                    binding=int(bind_point.bind) if hasattr(bind_point, "bind") else 0,
                    resource_id=str(rid) if rid is not None else "",
                    resource_name="",
                    type="UAV",
                    format="",
                ))
    except (AttributeError, TypeError):
        pass

    # ---- Constant buffers（常量缓冲）----
    try:
        for cb_array in pipe.GetConstantBlocks(rd_stage):
            bind_point = cb_array.bindPoint
            for buf in cb_array.buffers:
                rid = buf.resourceId if hasattr(buf, "resourceId") else None
                entries.append(ResourceBindingEntry(
                    set_or_space=int(bind_point.bindset) if hasattr(bind_point, "bindset") else 0,
                    binding=int(bind_point.bind) if hasattr(bind_point, "bind") else 0,
                    resource_id=str(rid) if rid is not None else "",
                    resource_name="",
                    type="CBV",
                    format="",
                ))
    except (AttributeError, TypeError):
        pass

    return entries


# ---------------------------------------------------------------------------
# Shader reflection serialisation（序列化）
# ---------------------------------------------------------------------------


def _reflection_to_dict(refl: Any) -> Dict[str, Any]:
    """将 ``ShaderReflection`` 转换为 JSON-safe 的字典。

    仅保留对调试最有价值的部分：signatures、constant blocks，
    以及 resource bindings（names、types、bind points）。
    """
    result: Dict[str, Any] = {}

    # ---- Input / output signatures ----
    for sig_name in ("inputSignature", "outputSignature"):
        try:
            sig_list = getattr(refl, sig_name, None)
            if sig_list is not None:
                result[sig_name] = [
                    {
                        "varName": str(s.varName),
                        "semanticName": str(s.semanticName),
                        "semanticIndex": int(s.semanticIndex),
                        "regIndex": int(s.regIndex),
                        "compCount": int(s.compCount),
                        "compType": str(s.compType),
                    }
                    for s in sig_list
                ]
        except (AttributeError, TypeError):
            pass

    # ---- Constant blocks ----
    try:
        cb_list = refl.constantBlocks
        result["constantBlocks"] = [
            {
                "name": str(cb.name),
                "bindPoint": int(cb.bindPoint),
                "byteSize": int(cb.byteSize),
                "variables": [
                    {
                        "name": str(v.name),
                        "type": str(v.type.descriptor.name) if hasattr(v.type, "descriptor") else str(v.type),
                        "byteOffset": int(v.byteOffset),
                    }
                    for v in (cb.variables or [])
                ],
            }
            for cb in cb_list
        ]
    except (AttributeError, TypeError):
        pass

    # ---- Read-only resources ----
    try:
        ro_list = refl.readOnlyResources
        result["readOnlyResources"] = [
            {
                "name": str(r.name),
                "bindPoint": int(r.bindPoint),
                "isTexture": bool(r.isTexture),
                "resType": str(r.resType),
            }
            for r in ro_list
        ]
    except (AttributeError, TypeError):
        pass

    # ---- Read-write resources ----
    try:
        rw_list = refl.readWriteResources
        result["readWriteResources"] = [
            {
                "name": str(r.name),
                "bindPoint": int(r.bindPoint),
                "isTexture": bool(r.isTexture),
                "resType": str(r.resType),
            }
            for r in rw_list
        ]
    except (AttributeError, TypeError):
        pass

    return result


# ---------------------------------------------------------------------------
# PipelineService
# ---------------------------------------------------------------------------


class PipelineService:
    """Pipeline state 检查与 shader artifact 导出。

    所有公开方法均为 ``async``，接收显式依赖，并返回结构化的 Pydantic models。
    """

    # ------------------------------------------------------------------
    # snapshot_pipeline
    # ------------------------------------------------------------------

    async def snapshot_pipeline(
        self,
        session_id: str,
        event_id: int,
        session_manager: SessionManager,
    ) -> PipelineSnapshot:
        """在指定 event 捕获完整的 pipeline state snapshot。

        Parameters
        ----------
        session_id:
            活跃 replay session id。
        event_id:
            需要检查的 API event。
        session_manager:
            提供 ``ReplayController``。

        Returns
        -------
        PipelineSnapshot
            包含 shaders、render targets、blend、depth/stencil、
            viewport、scissor、topology 与 bindings 的完整 pipeline state。
        """
        rd = _get_rd()
        controller = session_manager.get_controller(session_id)

        await asyncio.to_thread(controller.SetFrameEvent, event_id, True)

        pipe = await asyncio.to_thread(controller.GetPipelineState)
        api_props = await asyncio.to_thread(controller.GetAPIProperties)
        api = _map_graphics_api(api_props.pipelineType)

        # API-specific state for blend / depth / viewport / topology.
        api_state = await asyncio.to_thread(
            _get_api_specific_state, controller, api_props.pipelineType,
        )

        # ---- Shaders（着色器）-----------------------------------------
        shaders: List[ShaderInfo] = []
        for rd_stage in _rd_shader_stages():
            try:
                shader_id = pipe.GetShader(rd_stage)
                if _is_null_id(shader_id):
                    continue

                refl = pipe.GetShaderReflection(rd_stage)
                entry_point = "main"
                encoding = ""
                if refl is not None:
                    if hasattr(refl, "entryPoint"):
                        entry_point = str(refl.entryPoint)
                    if hasattr(refl, "encoding"):
                        encoding = str(refl.encoding)

                shaders.append(ShaderInfo(
                    resource_id=str(shader_id),
                    stage=_map_shader_stage(rd_stage),
                    entry_point=entry_point,
                    encoding=encoding,
                ))
            except Exception as exc:
                logger.debug(
                    "snapshot_pipeline: skipping stage %s: %s",
                    rd_stage, exc,
                )

        # ---- Render targets 与 depth target -------------------------
        render_targets, depth_target = await _extract_render_targets(
            pipe, api, controller,
        )

        # ---- Blend state（混合状态）-----------------------------------
        blend_states = _extract_blend_state(api_state, api)

        # ---- Depth / stencil state -----------------------------------
        depth_stencil = _extract_depth_stencil(api_state, api)

        # ---- Viewport / scissor --------------------------------------
        viewport = _extract_viewport(api_state, api)
        scissor = _extract_scissor(api_state, api)

        # ---- Topology ------------------------------------------------
        topology = _extract_topology(api_state, api)

        # ---- Resource bindings（all stages）--------------------------
        bindings: List[ResourceBindingEntry] = []
        for rd_stage in _rd_shader_stages():
            try:
                shader_id = pipe.GetShader(rd_stage)
                if _is_null_id(shader_id):
                    continue
                stage_bindings = _collect_bindings_for_stage(
                    pipe, rd_stage, _map_shader_stage(rd_stage),
                )
                bindings.extend(stage_bindings)
            except Exception:
                pass

        return PipelineSnapshot(
            event_id=event_id,
            api=api,
            shaders=shaders,
            render_targets=render_targets,
            depth_target=depth_target,
            blend_states=blend_states,
            depth_stencil=depth_stencil,
            bindings=bindings,
            viewport=viewport,
            scissor=scissor,
            topology=topology,
        )

    # ------------------------------------------------------------------
    # export_shader
    # ------------------------------------------------------------------

    async def export_shader(
        self,
        session_id: str,
        event_id: int,
        stage: ShaderStage,
        session_manager: SessionManager,
        artifact_store: ArtifactStore,
    ) -> ShaderExportBundle:
        """导出 *stage* 绑定的 shader：包含 reflection + disassembly artifacts。

        Parameters
        ----------
        session_id:
            活跃 replay session id。
        event_id:
            需要检查的 API event。
        stage:
            我们的 ``ShaderStage`` enum 值（例如 ``ShaderStage.PS``）。
        session_manager:
            提供 ``ReplayController``。
        artifact_store:
            生成 artifacts 的持久化层。

        Returns
        -------
        ShaderExportBundle
            包含 shader reflection JSON 与 disassembly 文本的 artifact 引用，
            以及相关 metadata。
        """
        rd = _get_rd()
        controller = session_manager.get_controller(session_id)

        await asyncio.to_thread(controller.SetFrameEvent, event_id, True)

        pipe = await asyncio.to_thread(controller.GetPipelineState)
        rd_stage = _our_stage_to_rd(stage)

        shader_id = pipe.GetShader(rd_stage)
        if _is_null_id(shader_id):
            raise ValueError(
                f"No shader bound at stage {stage.value} for event {event_id}"
            )

        refl = pipe.GetShaderReflection(rd_stage)
        if refl is None:
            raise RuntimeError(
                f"Shader reflection unavailable for stage {stage.value} "
                f"at event {event_id}"
            )

        # 确定用于 disassembly 的 pipeline object ResourceId。
        pipeline_rid = rd.ResourceId()
        try:
            if stage == ShaderStage.CS:
                pipeline_rid = pipe.GetComputePipelineObject()
            else:
                pipeline_rid = pipe.GetGraphicsPipelineObject()
        except (AttributeError, TypeError):
            # 回退：null pipeline（多数 API 可用）。
            pass

        # 解析 entry point。
        entry_point = "main"
        encoding = ""
        if hasattr(refl, "entryPoint"):
            entry_point = str(refl.entryPoint)
        if hasattr(refl, "encoding"):
            encoding = str(refl.encoding)

        # ---- Reflection JSON artifact（反射）---------------------------
        refl_dict = _reflection_to_dict(refl)
        refl_dict["_meta"] = {
            "event_id": event_id,
            "stage": stage.value,
            "shader_id": str(shader_id),
            "entry_point": entry_point,
            "encoding": encoding,
        }
        refl_bytes = json.dumps(refl_dict, indent=2, default=str).encode()
        refl_artifact = await artifact_store.store(
            refl_bytes,
            mime="application/json",
            suffix=".refl.json",
            meta={
                "event_id": event_id,
                "stage": stage.value,
                "kind": "shader_reflection",
            },
        )

        # ---- Disassembly artifact（反汇编）------------------------------
        disasm_artifact: Optional[ArtifactRef] = None
        try:
            targets: List[str] = await asyncio.to_thread(
                controller.GetDisassemblyTargets, True,
            )

            if targets:
                # 优先使用第一个可用的 disassembly target；同时将所有
                # 成功的反汇编合并成一个带分区标题的文本，便于下游选择。
                sections: List[str] = []
                for target_name in targets:
                    try:
                        disasm_text: str = await asyncio.to_thread(
                            controller.DisassembleShader,
                            pipeline_rid,
                            refl,
                            target_name,
                        )
                        if disasm_text:
                            sections.append(
                                f";;; === {target_name} ===\n"
                                f"{disasm_text}"
                            )
                    except Exception as exc:
                        logger.debug(
                            "export_shader: disassembly target %r failed: %s",
                            target_name, exc,
                        )

                if sections:
                    combined = "\n\n".join(sections)
                    disasm_bytes = combined.encode("utf-8")
                    disasm_artifact = await artifact_store.store(
                        disasm_bytes,
                        mime="text/plain",
                        suffix=".disasm.txt",
                        meta={
                            "event_id": event_id,
                            "stage": stage.value,
                            "kind": "shader_disassembly",
                            "targets": [t for t in targets],
                        },
                    )
        except Exception as exc:
            logger.warning("export_shader: disassembly failed: %s", exc)

        # ---- Compute a content hash for the shader -------------------
        shader_hash = ""
        try:
            # 将 reflection JSON 的 hash 作为稳定 identity。
            shader_hash = hashlib.sha256(refl_bytes).hexdigest()[:16]
        except Exception:
            pass

        return ShaderExportBundle(
            shader_id=str(shader_id),
            stage=stage,
            entry_point=entry_point,
            encoding=encoding,
            reflection_artifact=refl_artifact,
            disasm_artifact=disasm_artifact,
        )

    # ------------------------------------------------------------------
    # get_resource_bindings
    # ------------------------------------------------------------------

    async def get_resource_bindings(
        self,
        session_id: str,
        event_id: int,
        session_manager: SessionManager,
    ) -> List[ResourceBindingEntry]:
        """返回所有活跃 shader stages 的资源绑定。

        Parameters
        ----------
        session_id:
            活跃 replay session id。
        event_id:
            需要检查的 API event。
        session_manager:
            提供 ``ReplayController``。

        Returns
        -------
        list[ResourceBindingEntry]
            所有活跃 stages 的绑定资源扁平列表。
        """
        rd = _get_rd()
        controller = session_manager.get_controller(session_id)

        await asyncio.to_thread(controller.SetFrameEvent, event_id, True)

        pipe = await asyncio.to_thread(controller.GetPipelineState)

        entries: List[ResourceBindingEntry] = []
        for rd_stage in _rd_shader_stages():
            try:
                shader_id = pipe.GetShader(rd_stage)
                if _is_null_id(shader_id):
                    continue

                our_stage = _map_shader_stage(rd_stage)
                stage_entries = _collect_bindings_for_stage(
                    pipe, rd_stage, our_stage,
                )

                # 若可用，则用 reflection 填充 resource names。
                refl = pipe.GetShaderReflection(rd_stage)
                if refl is not None:
                    _enrich_binding_names(stage_entries, refl)

                entries.extend(stage_entries)
            except Exception as exc:
                logger.debug(
                    "get_resource_bindings: stage %s: %s", rd_stage, exc,
                )

        return entries


# ---------------------------------------------------------------------------
# Post-processing helpers（后处理辅助）
# ---------------------------------------------------------------------------


def _enrich_binding_names(
    entries: List[ResourceBindingEntry],
    refl: Any,
) -> None:
    """从 shader reflection 填充 ``resource_name`` 与 ``format``。

    通过 binding index 与 reflection 的资源列表匹配，并原地修改 *entries*。
    """
    # 从 reflection 构建快速查找表。
    ro_by_bind: Dict[int, Any] = {}
    rw_by_bind: Dict[int, Any] = {}
    cb_by_bind: Dict[int, Any] = {}

    try:
        for r in (refl.readOnlyResources or []):
            ro_by_bind[int(r.bindPoint)] = r
    except (AttributeError, TypeError):
        pass
    try:
        for r in (refl.readWriteResources or []):
            rw_by_bind[int(r.bindPoint)] = r
    except (AttributeError, TypeError):
        pass
    try:
        for cb in (refl.constantBlocks or []):
            cb_by_bind[int(cb.bindPoint)] = cb
    except (AttributeError, TypeError):
        pass

    for entry in entries:
        b = entry.binding
        if entry.type == "SRV" and b in ro_by_bind:
            r = ro_by_bind[b]
            entry.resource_name = str(r.name)
            if hasattr(r, "resType"):
                entry.format = str(r.resType)
        elif entry.type == "UAV" and b in rw_by_bind:
            r = rw_by_bind[b]
            entry.resource_name = str(r.name)
            if hasattr(r, "resType"):
                entry.format = str(r.resType)
        elif entry.type == "CBV" and b in cb_by_bind:
            entry.resource_name = str(cb_by_bind[b].name)
