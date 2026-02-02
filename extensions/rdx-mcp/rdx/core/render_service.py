"""Headless render and readback service.

Wraps RenderDoc's texture display, readback, and pixel inspection APIs
into async operations that produce versioned artifacts stored via the
artifact-store abstraction.

All blocking RenderDoc calls are dispatched to a thread via
``asyncio.to_thread`` so that the service can live on an async event
loop without stalling other coroutines.

The ``renderdoc`` module is imported lazily -- it is only available
inside a RenderDoc replay context or when the shared library has been
placed on ``sys.path``.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

import numpy as np

from rdx.models import ArtifactRef

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy renderdoc import
# ---------------------------------------------------------------------------

_rd_module: Any = None


def _get_rd() -> Any:
    """Return the ``renderdoc`` module, importing it on first access.

    Raises ``ImportError`` with a descriptive message when the module
    cannot be loaded.
    """
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
# Dependency protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionManager(Protocol):
    """Minimal structural contract for the session lifecycle manager."""

    def get_controller(self, session_id: str) -> Any:
        """Return the ``ReplayController`` bound to *session_id*."""
        ...

    def get_output(self, session_id: str) -> Any:
        """Return the ``ReplayOutput`` bound to *session_id*."""
        ...


@runtime_checkable
class ArtifactStore(Protocol):
    """Minimal structural contract for the artifact persistence layer."""

    async def store(
        self,
        data: bytes,
        *,
        mime: str,
        suffix: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> ArtifactRef:
        """Persist *data* and return a tracking :class:`ArtifactRef`."""
        ...


# ---------------------------------------------------------------------------
# Constants / look-up tables
# ---------------------------------------------------------------------------

_MIME_MAP: Dict[str, str] = {
    "png": "image/png",
    "exr": "image/x-exr",
    "hdr": "image/vnd.radiance",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "npz": "application/x-npz",
}

_SUFFIX_MAP: Dict[str, str] = {
    "png": ".png",
    "exr": ".exr",
    "hdr": ".hdr",
    "jpg": ".jpg",
    "jpeg": ".jpg",
    "npz": ".npz",
}


def _resolve_overlay(name: str) -> Any:
    """Map a human-friendly overlay name to a ``rd.DebugOverlay`` value."""
    rd = _get_rd()
    key = name.lower().replace("-", "_").replace(" ", "_")
    table: Dict[str, Any] = {
        "none": rd.DebugOverlay.NoOverlay,
        "nan": rd.DebugOverlay.NaN,
        "clipping": rd.DebugOverlay.Clipping,
        "drawcall": rd.DebugOverlay.Drawcall,
        "wireframe": rd.DebugOverlay.Wireframe,
        "depth": rd.DebugOverlay.DepthTest,
        "stencil": rd.DebugOverlay.StencilTest,
        "backface_cull": rd.DebugOverlay.BackfaceCull,
        "viewport_scissor": rd.DebugOverlay.ViewportScissor,
        "quad_overdraw": rd.DebugOverlay.QuadOverdrawDraw,
        "triangle_size": rd.DebugOverlay.TriangleSizeDraw,
    }
    return table.get(key, rd.DebugOverlay.NoOverlay)


def _is_null_resource_id(resource_id: Any) -> bool:
    """Return ``True`` when *resource_id* represents a null / empty ID."""
    rd = _get_rd()
    try:
        return resource_id == rd.ResourceId()
    except Exception:
        return resource_id is None


# ---------------------------------------------------------------------------
# Image encoding
# ---------------------------------------------------------------------------


def _encode_image(
    rgba_bytes: bytes,
    width: int,
    height: int,
    fmt: str,
) -> Tuple[bytes, str]:
    """Encode raw RGBA-8 *rgba_bytes* into the requested image format.

    Returns ``(encoded_bytes, actual_format_used)``.  The actual format
    may differ from the requested one when an optional codec (imageio for
    EXR / HDR) is not installed.
    """
    from PIL import Image  # type: ignore[import-untyped]

    img = Image.frombytes("RGBA", (width, height), rgba_bytes)
    buf = io.BytesIO()
    upper = fmt.upper()

    if upper == "PNG":
        img.save(buf, format="PNG", compress_level=6)
        return buf.getvalue(), "png"

    if upper in ("JPG", "JPEG"):
        img.convert("RGB").save(buf, format="JPEG", quality=92)
        return buf.getvalue(), "jpg"

    if upper == "EXR":
        try:
            import imageio.v3 as iio  # type: ignore[import-untyped]

            arr = np.frombuffer(rgba_bytes, dtype=np.uint8).reshape(
                height, width, 4,
            )
            arr_f = arr.astype(np.float32) / 255.0
            encoded = iio.imwrite("<bytes>", arr_f, extension=".exr")
            return bytes(encoded), "exr"
        except Exception:
            logger.warning("EXR encoding unavailable; falling back to PNG")
            img.save(buf, format="PNG")
            return buf.getvalue(), "png"

    if upper == "HDR":
        try:
            import imageio.v3 as iio  # type: ignore[import-untyped]

            arr = np.frombuffer(rgba_bytes, dtype=np.uint8).reshape(
                height, width, 4,
            )
            # HDR is 3-channel only (RGB).
            arr_f = arr[:, :, :3].astype(np.float32) / 255.0
            encoded = iio.imwrite("<bytes>", arr_f, extension=".hdr")
            return bytes(encoded), "hdr"
        except Exception:
            logger.warning("HDR encoding unavailable; falling back to PNG")
            img.save(buf, format="PNG")
            return buf.getvalue(), "png"

    # Unknown format -- default to PNG.
    logger.warning("Unknown output format %r; falling back to PNG", fmt)
    img.save(buf, format="PNG")
    return buf.getvalue(), "png"


# ---------------------------------------------------------------------------
# RenderService
# ---------------------------------------------------------------------------


class RenderService:
    """Headless render, readback, and pixel inspection service.

    Every public method is ``async`` and receives explicit
    *session_manager* / *artifact_store* dependencies so the service
    itself is stateless and straightforward to unit-test with fakes.
    """

    # ------------------------------------------------------------------
    # render_event
    # ------------------------------------------------------------------

    async def render_event(
        self,
        session_id: str,
        event_id: int,
        session_manager: SessionManager,
        artifact_store: ArtifactStore,
        source_config: Optional[Dict[str, Any]] = None,
        view_config: Optional[Dict[str, Any]] = None,
        output_format: str = "png",
    ) -> Tuple[ArtifactRef, Dict[str, Any]]:
        """Render an event and store the result as an image artifact.

        Parameters
        ----------
        session_id:
            Active replay session id.
        event_id:
            Draw-call / API event to navigate to.
        session_manager:
            Provides the ``ReplayController`` and ``ReplayOutput``.
        artifact_store:
            Persistence layer for generated images.
        source_config:
            Selects **what** to render:

            * ``{"source": "final_output"}`` *(default)* -- the last
              bound colour output render-target of the current draw.
            * ``{"source": "texture", "texture_id": <int|ResourceId>}``
              -- an explicit texture.
        view_config:
            Visual presentation tweaks (all optional):

            * ``scale`` *(float)* -- zoom factor, ``0`` means fit.
            * ``channels`` *(dict)* -- ``{"r": bool, "g": bool, ...}``.
            * ``overlay`` *(str)* -- debug overlay name (``nan``,
              ``clipping``, ``wireframe``, ...).
            * ``hdr`` *(bool)* -- enable HDR multiplier.
            * ``hdr_multiplier`` *(float)* -- HDR multiplier value.
            * ``range_min`` / ``range_max`` *(float)* -- display range.
            * ``flip_y`` *(bool)* -- vertical flip.
            * ``raw_output`` *(bool)* -- skip sRGB conversion.
        output_format:
            Image file format (``png``, ``exr``, ``hdr``, ``jpg``).

        Returns
        -------
        tuple[ArtifactRef, dict]
            The stored artifact reference and a view-metadata dict.
        """
        rd = _get_rd()
        source_config = source_config or {"source": "final_output"}
        view_config = view_config or {}

        controller = session_manager.get_controller(session_id)
        output = session_manager.get_output(session_id)

        # Navigate to the requested event.
        await asyncio.to_thread(controller.SetFrameEvent, event_id, True)

        # Determine target texture.
        tex_id = await self._resolve_source_texture(controller, source_config)

        # ---- Build TextureDisplay ------------------------------------
        tex_display = rd.TextureDisplay()
        tex_display.resourceId = tex_id
        tex_display.subresource = rd.Subresource()

        # Scale: 0 = fit to output window.
        tex_display.scale = float(view_config.get("scale", 0))

        # Display range (maps to visualised min/max).
        tex_display.rangeMin = float(view_config.get("range_min", 0.0))
        tex_display.rangeMax = float(view_config.get("range_max", 1.0))

        # HDR multiplier.  A negative value disables HDR display.
        tex_display.hdrMultiplier = float(
            view_config.get("hdr_multiplier", -1.0)
        )
        if view_config.get("hdr", False) and tex_display.hdrMultiplier < 0:
            tex_display.hdrMultiplier = 4.0

        # Flip / raw output.
        tex_display.flipY = bool(view_config.get("flip_y", False))
        tex_display.rawOutput = bool(view_config.get("raw_output", False))

        # Channel visibility.
        channels = view_config.get("channels", {})
        tex_display.red = bool(channels.get("r", True))
        tex_display.green = bool(channels.get("g", True))
        tex_display.blue = bool(channels.get("b", True))
        tex_display.alpha = bool(channels.get("a", True))

        # Debug overlay.
        overlay_name = str(view_config.get("overlay", "none"))
        tex_display.overlay = _resolve_overlay(overlay_name)

        # ---- Render and readback -------------------------------------
        await asyncio.to_thread(output.SetTextureDisplay, tex_display)
        await asyncio.to_thread(output.Display)

        rgba_bytes: bytes = await asyncio.to_thread(
            output.ReadbackOutputTexture,
        )
        width, height = await asyncio.to_thread(output.GetDimensions)

        # ---- Encode --------------------------------------------------
        fmt_lower = output_format.lower()
        image_bytes, actual_fmt = await asyncio.to_thread(
            _encode_image, rgba_bytes, width, height, fmt_lower,
        )

        mime = _MIME_MAP.get(actual_fmt, "image/png")
        suffix = _SUFFIX_MAP.get(actual_fmt, ".png")

        view_meta: Dict[str, Any] = {
            "event_id": event_id,
            "texture_id": str(tex_id),
            "width": width,
            "height": height,
            "format": actual_fmt,
            "overlay": overlay_name,
            "channels": {
                "r": tex_display.red,
                "g": tex_display.green,
                "b": tex_display.blue,
                "a": tex_display.alpha,
            },
            "scale": tex_display.scale,
        }

        artifact_ref = await artifact_store.store(
            image_bytes,
            mime=mime,
            suffix=suffix,
            meta=view_meta,
        )

        logger.debug(
            "render_event: event=%d tex=%s %dx%d fmt=%s -> %s",
            event_id, tex_id, width, height, actual_fmt, artifact_ref.uri,
        )
        return artifact_ref, view_meta

    # ------------------------------------------------------------------
    # readback_texture
    # ------------------------------------------------------------------

    async def readback_texture(
        self,
        session_id: str,
        event_id: int,
        texture_id: Any,
        session_manager: SessionManager,
        artifact_store: ArtifactStore,
        subresource: Optional[Dict[str, int]] = None,
        region: Optional[Dict[str, int]] = None,
    ) -> Tuple[ArtifactRef, Dict[str, Any]]:
        """Read back raw texture data and store it as a NumPy ``.npz``.

        Parameters
        ----------
        session_id, event_id:
            Replay coordinates.
        texture_id:
            ``ResourceId`` (or integer form) of the texture to read.
        session_manager, artifact_store:
            Injected dependencies.
        subresource:
            ``{"mip": int, "slice": int, "sample": int}`` -- defaults to
            mip 0 / slice 0 / sample 0.
        region:
            ``{"x": int, "y": int, "width": int, "height": int}`` crop
            rectangle in texels.  When omitted the full mip level is
            returned.

        Returns
        -------
        tuple[ArtifactRef, dict]
            Artifact reference and a statistics dict containing shape,
            dtype, per-channel min/max, nan_count, and inf_count.
        """
        rd = _get_rd()
        controller = session_manager.get_controller(session_id)

        await asyncio.to_thread(controller.SetFrameEvent, event_id, True)

        # Build subresource descriptor.
        sub = rd.Subresource()
        if subresource:
            sub.mip = int(subresource.get("mip", 0))
            sub.slice = int(subresource.get("slice", 0))
            sub.sample = int(subresource.get("sample", 0))

        # Resolve texture dimensions from the capture metadata.
        resolved_id = await self._resolve_texture_id(controller, texture_id)
        tex_desc = await self._find_texture_desc(controller, resolved_id)
        if tex_desc is None:
            raise ValueError(
                f"Texture {texture_id} not found in capture resources"
            )

        tex_width = max(1, tex_desc.width >> sub.mip)
        tex_height = max(1, tex_desc.height >> sub.mip)

        # Fetch raw data from the GPU readback.
        raw_data: bytes = await asyncio.to_thread(
            controller.GetTextureData, resolved_id, sub,
        )

        # ---- Interpret the byte layout -------------------------------
        # RenderDoc returns tightly-packed pixel data whose component
        # layout matches the resource format.  We use simple heuristics
        # to pick float32-RGBA vs uint8-RGBA based on the buffer size.
        expected_pixels = tex_width * tex_height
        bytes_per_pixel_f32 = 16  # 4 channels x 4 bytes
        bytes_per_pixel_u8 = 4   # 4 channels x 1 byte

        if len(raw_data) >= expected_pixels * bytes_per_pixel_f32:
            arr = np.frombuffer(
                raw_data[: expected_pixels * bytes_per_pixel_f32],
                dtype=np.float32,
            ).reshape(tex_height, tex_width, 4)
        elif len(raw_data) >= expected_pixels * bytes_per_pixel_u8:
            arr = np.frombuffer(
                raw_data[: expected_pixels * bytes_per_pixel_u8],
                dtype=np.uint8,
            ).reshape(tex_height, tex_width, 4)
        else:
            # Unknown / compressed layout -- store the flat byte array.
            logger.warning(
                "readback_texture: unexpected buffer size %d for %dx%d "
                "texture; storing flat uint8 array",
                len(raw_data), tex_width, tex_height,
            )
            arr = np.frombuffer(raw_data, dtype=np.uint8)

        # ---- Optional crop -------------------------------------------
        if region and arr.ndim == 3:
            rx = max(0, min(int(region.get("x", 0)), arr.shape[1] - 1))
            ry = max(0, min(int(region.get("y", 0)), arr.shape[0] - 1))
            rw = max(
                1,
                min(int(region.get("width", arr.shape[1] - rx)),
                    arr.shape[1] - rx),
            )
            rh = max(
                1,
                min(int(region.get("height", arr.shape[0] - ry)),
                    arr.shape[0] - ry),
            )
            arr = arr[ry: ry + rh, rx: rx + rw]

        # ---- Statistics ----------------------------------------------
        stats: Dict[str, Any] = {
            "event_id": event_id,
            "texture_id": str(texture_id),
            "shape": list(arr.shape),
            "dtype": str(arr.dtype),
        }

        if np.issubdtype(arr.dtype, np.floating):
            stats["nan_count"] = int(np.isnan(arr).sum())
            stats["inf_count"] = int(np.isinf(arr).sum())
            finite = arr[np.isfinite(arr)]
            if finite.size > 0:
                stats["min"] = float(finite.min())
                stats["max"] = float(finite.max())
                stats["mean"] = float(finite.mean())
            else:
                stats["min"] = None
                stats["max"] = None
                stats["mean"] = None
        else:
            stats["nan_count"] = 0
            stats["inf_count"] = 0
            stats["min"] = int(arr.min()) if arr.size > 0 else None
            stats["max"] = int(arr.max()) if arr.size > 0 else None
            stats["mean"] = float(arr.mean()) if arr.size > 0 else None

        # ---- Serialize to .npz --------------------------------------
        npz_buf = io.BytesIO()
        np.savez_compressed(npz_buf, pixels=arr)
        npz_bytes = npz_buf.getvalue()

        artifact_ref = await artifact_store.store(
            npz_bytes,
            mime="application/x-npz",
            suffix=".npz",
            meta=stats,
        )

        logger.debug(
            "readback_texture: event=%d tex=%s shape=%s dtype=%s -> %s",
            event_id, texture_id, arr.shape, arr.dtype, artifact_ref.uri,
        )
        return artifact_ref, stats

    # ------------------------------------------------------------------
    # pick_pixel
    # ------------------------------------------------------------------

    async def pick_pixel(
        self,
        session_id: str,
        event_id: int,
        texture_id: Any,
        x: int,
        y: int,
        session_manager: SessionManager,
    ) -> Dict[str, Any]:
        """Pick a single pixel from a texture at the given event.

        Returns
        -------
        dict
            ``{x, y, event_id, texture_id, r, g, b, a,
            value_type, has_nan, has_inf}``
        """
        rd = _get_rd()
        controller = session_manager.get_controller(session_id)

        await asyncio.to_thread(controller.SetFrameEvent, event_id, True)

        resolved_id = await self._resolve_texture_id(controller, texture_id)
        sub = rd.Subresource()

        pixel_value = await asyncio.to_thread(
            controller.PickPixel,
            resolved_id,
            int(x),
            int(y),
            sub,
            rd.CompType.Typeless,
        )

        # PixelValue exposes .floatValue, .uintValue, and .intValue
        # arrays.  Float covers the vast majority of use cases.
        fv: List[float] = list(pixel_value.floatValue[:4])

        result: Dict[str, Any] = {
            "x": x,
            "y": y,
            "event_id": event_id,
            "texture_id": str(texture_id),
            "r": fv[0],
            "g": fv[1],
            "b": fv[2],
            "a": fv[3],
            "value_type": "float",
            "has_nan": any(math.isnan(v) for v in fv),
            "has_inf": any(math.isinf(v) for v in fv),
        }

        # Also expose integer interpretations for integer-format textures.
        try:
            uv = list(pixel_value.uintValue[:4])
            result["r_uint"] = uv[0]
            result["g_uint"] = uv[1]
            result["b_uint"] = uv[2]
            result["a_uint"] = uv[3]
        except Exception:
            pass

        return result

    # ------------------------------------------------------------------
    # get_texture_stats
    # ------------------------------------------------------------------

    async def get_texture_stats(
        self,
        session_id: str,
        event_id: int,
        texture_id: Any,
        session_manager: SessionManager,
    ) -> Dict[str, Any]:
        """Compute per-channel min/max statistics for a texture.

        Uses RenderDoc's GPU-accelerated ``GetMinMax`` to avoid a full
        CPU-side readback.

        Returns
        -------
        dict
            ``{event_id, texture_id, channels: {r,g,b,a: {min,max,...}},
            overall_min, overall_max, has_any_nan, has_any_inf}``
        """
        rd = _get_rd()
        controller = session_manager.get_controller(session_id)

        await asyncio.to_thread(controller.SetFrameEvent, event_id, True)

        resolved_id = await self._resolve_texture_id(controller, texture_id)
        sub = rd.Subresource()

        min_val, max_val = await asyncio.to_thread(
            controller.GetMinMax,
            resolved_id,
            sub,
            rd.CompType.Typeless,
        )

        min_f: List[float] = list(min_val.floatValue[:4])
        max_f: List[float] = list(max_val.floatValue[:4])

        channel_names = ("r", "g", "b", "a")
        channels: Dict[str, Dict[str, Any]] = {}
        for i, name in enumerate(channel_names):
            channels[name] = {
                "min": min_f[i],
                "max": max_f[i],
                "has_nan": math.isnan(min_f[i]) or math.isnan(max_f[i]),
                "has_inf": math.isinf(min_f[i]) or math.isinf(max_f[i]),
            }

        # Aggregate statistics across finite channel values.
        finite_mins = [
            v for v in min_f if not (math.isnan(v) or math.isinf(v))
        ]
        finite_maxs = [
            v for v in max_f if not (math.isnan(v) or math.isinf(v))
        ]
        overall_min = min(finite_mins) if finite_mins else float("nan")
        overall_max = max(finite_maxs) if finite_maxs else float("nan")

        return {
            "event_id": event_id,
            "texture_id": str(texture_id),
            "channels": channels,
            "overall_min": overall_min,
            "overall_max": overall_max,
            "has_any_nan": any(
                channels[c]["has_nan"] for c in channel_names
            ),
            "has_any_inf": any(
                channels[c]["has_inf"] for c in channel_names
            ),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _resolve_source_texture(
        controller: Any,
        source_config: Dict[str, Any],
    ) -> Any:
        """Determine which texture ResourceId to render.

        For ``"final_output"`` the method inspects the pipeline state's
        output targets and returns the last non-null colour attachment.
        For ``"texture"`` it looks up the explicit *texture_id*.
        """
        rd = _get_rd()
        source = source_config.get("source", "final_output")

        if source == "texture":
            tex_id_raw = source_config.get("texture_id")
            if tex_id_raw is None:
                raise ValueError(
                    "source_config with source='texture' requires a "
                    "'texture_id' field"
                )
            # If caller passed an integer, look it up in the texture list.
            if isinstance(tex_id_raw, int):
                textures = await asyncio.to_thread(controller.GetTextures)
                for tex in textures:
                    if int(tex.resourceId) == tex_id_raw:
                        return tex.resourceId
                raise ValueError(
                    f"No texture found matching id {tex_id_raw}"
                )
            # Assume it is already a ResourceId.
            return tex_id_raw

        # "final_output" -- last non-null colour output of the current
        # draw call.
        pipe_state = await asyncio.to_thread(controller.GetPipelineState)
        targets = pipe_state.GetOutputTargets()
        for target in reversed(targets):
            if not _is_null_resource_id(target.resourceId):
                return target.resourceId

        # Fallback: first texture in the capture.
        textures = await asyncio.to_thread(controller.GetTextures)
        if textures:
            logger.warning(
                "_resolve_source_texture: no output targets found; "
                "falling back to first capture texture",
            )
            return textures[0].resourceId

        raise RuntimeError(
            "No output render target or texture found for the current event"
        )

    @staticmethod
    async def _resolve_texture_id(
        controller: Any,
        texture_id: Any,
    ) -> Any:
        """Coerce *texture_id* to a RenderDoc ``ResourceId``.

        Accepts an ``int`` (scans textures) or an already-resolved
        ``ResourceId``.
        """
        if isinstance(texture_id, int):
            textures = await asyncio.to_thread(controller.GetTextures)
            for tex in textures:
                if int(tex.resourceId) == texture_id:
                    return tex.resourceId
            raise ValueError(f"No texture found matching id {texture_id}")
        return texture_id

    @staticmethod
    async def _find_texture_desc(
        controller: Any,
        resource_id: Any,
    ) -> Any:
        """Find the ``TextureDescription`` for *resource_id*.

        Returns ``None`` when the texture is not present in the capture.
        """
        textures = await asyncio.to_thread(controller.GetTextures)
        for tex in textures:
            if tex.resourceId == resource_id:
                return tex
        return None
