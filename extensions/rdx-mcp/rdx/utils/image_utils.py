"""
Image processing utilities for GPU debug visualisation.

Every function operates on NumPy arrays in ``(H, W, C)`` layout and/or PIL
Images.  Both HDR (``float32``) and LDR (``uint8``) pixel formats are
handled transparently.
"""

from __future__ import annotations

import io
from typing import Dict, Optional, Tuple

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_float32(pixels: np.ndarray) -> np.ndarray:
    """Promote *pixels* to ``float32`` if they are integer-typed.

    ``uint8`` values are normalised to the ``[0, 1]`` range.
    """
    if pixels.dtype == np.float32:
        return pixels
    if pixels.dtype == np.float64:
        return pixels.astype(np.float32)
    if np.issubdtype(pixels.dtype, np.integer):
        return pixels.astype(np.float32) / np.float32(np.iinfo(pixels.dtype).max)
    return pixels.astype(np.float32)


def _ensure_uint8(pixels: np.ndarray) -> np.ndarray:
    """Convert *pixels* to ``uint8`` clamped to ``[0, 255]``."""
    if pixels.dtype == np.uint8:
        return pixels
    arr = np.clip(pixels, 0.0, 1.0) * 255.0
    return arr.astype(np.uint8)


def _ensure_3ch(pixels: np.ndarray) -> np.ndarray:
    """Ensure *pixels* has exactly 3 colour channels (H, W, 3).

    - Single-channel (H, W) or (H, W, 1) data is replicated to greyscale RGB.
    - RGBA (H, W, 4) data has its alpha channel stripped.
    """
    if pixels.ndim == 2:
        return np.stack([pixels, pixels, pixels], axis=-1)
    c = pixels.shape[2]
    if c == 1:
        return np.concatenate([pixels, pixels, pixels], axis=-1)
    if c == 4:
        return pixels[:, :, :3]
    return pixels


def _bbox_from_mask(mask: np.ndarray) -> Optional[Dict[str, int]]:
    """Compute a tight axis-aligned bounding box from a boolean mask.

    Returns ``None`` when the mask is entirely ``False``.
    """
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return None
    return {
        "x0": int(xs.min()),
        "y0": int(ys.min()),
        "x1": int(xs.max()),
        "y1": int(ys.max()),
    }


# ---------------------------------------------------------------------------
# NaN / Inf detection
# ---------------------------------------------------------------------------


def compute_naninf_mask(
    pixels: np.ndarray,
) -> Tuple[np.ndarray, Dict]:
    """Generate a colour-coded mask highlighting NaN and Inf pixels.

    Parameters
    ----------
    pixels:
        Image array ``(H, W, C)`` in any numeric dtype.  For integer images
        no NaN/Inf values can exist, so the result will be an empty mask.

    Returns
    -------
    mask_image:
        ``uint8`` RGBA array ``(H, W, 4)`` where NaN pixels are drawn in red
        ``(255, 0, 0, 255)``, Inf pixels in blue ``(0, 0, 255, 255)``, and
        all other pixels are fully transparent ``(0, 0, 0, 0)``.
    stats:
        Dictionary with ``nan_count``, ``inf_count``, ``total_pixels``,
        ``density`` (fraction of bad pixels), and ``bbox`` (tight bounding
        box dict ``{x0, y0, x1, y1}`` or ``None``).
    """
    h, w = pixels.shape[:2]
    total_pixels = h * w

    mask_image = np.zeros((h, w, 4), dtype=np.uint8)

    fpix = pixels.astype(np.float64, copy=False)

    # Collapse across channels: a pixel is NaN if *any* channel is NaN.
    if fpix.ndim == 3:
        nan_mask = np.any(np.isnan(fpix), axis=2)
        inf_mask = np.any(np.isinf(fpix), axis=2)
    else:
        nan_mask = np.isnan(fpix)
        inf_mask = np.isinf(fpix)

    nan_count = int(nan_mask.sum())
    inf_count = int(inf_mask.sum())

    # Paint the mask (NaN takes precedence over Inf when both are present).
    mask_image[inf_mask] = [0, 0, 255, 255]
    mask_image[nan_mask] = [255, 0, 0, 255]

    bad_mask = nan_mask | inf_mask
    density = float(bad_mask.sum()) / total_pixels if total_pixels > 0 else 0.0
    bbox = _bbox_from_mask(bad_mask)

    stats = {
        "nan_count": nan_count,
        "inf_count": inf_count,
        "total_pixels": total_pixels,
        "density": density,
        "bbox": bbox,
    }
    return mask_image, stats


# ---------------------------------------------------------------------------
# Diff map
# ---------------------------------------------------------------------------


def compute_diff_map(
    img_a: np.ndarray,
    img_b: np.ndarray,
    threshold: float = 0.01,
) -> Tuple[np.ndarray, Dict]:
    """Compute a per-pixel L2-distance heatmap between two images.

    Both images are promoted to ``float32`` and must have the same spatial
    dimensions.  If the channel counts differ they are both reduced to 3
    channels first.

    Parameters
    ----------
    img_a, img_b:
        Image arrays ``(H, W, C)``.
    threshold:
        Per-pixel L2 distance below which a pixel is considered identical.

    Returns
    -------
    heatmap:
        ``uint8`` RGB heatmap ``(H, W, 3)`` ranging from black (no diff) to
        bright red (maximum diff).
    stats:
        Dictionary with ``mean_diff``, ``max_diff``, ``diff_pixel_count``,
        ``diff_ratio``, and ``bbox``.

    Raises
    ------
    ValueError
        If the spatial dimensions of the two images do not match.
    """
    a = _ensure_float32(img_a)
    b = _ensure_float32(img_b)

    # Normalise channel count.
    a = _ensure_3ch(a)
    b = _ensure_3ch(b)

    if a.shape[:2] != b.shape[:2]:
        raise ValueError(
            f"Spatial dimensions must match: {a.shape[:2]} vs {b.shape[:2]}"
        )

    h, w = a.shape[:2]
    total_pixels = h * w

    # Per-pixel L2 distance across channels.
    diff = np.sqrt(np.sum((a - b) ** 2, axis=2))  # (H, W)
    max_diff = float(diff.max()) if diff.size > 0 else 0.0
    mean_diff = float(diff.mean()) if diff.size > 0 else 0.0

    above_threshold = diff > threshold
    diff_pixel_count = int(above_threshold.sum())
    diff_ratio = diff_pixel_count / total_pixels if total_pixels > 0 else 0.0

    # Normalise diff to [0, 1] for the heatmap.
    if max_diff > 0:
        norm = np.clip(diff / max_diff, 0.0, 1.0)
    else:
        norm = np.zeros_like(diff)

    # Build an RGB heatmap: black -> red -> yellow -> white.
    heatmap = np.zeros((h, w, 3), dtype=np.float32)
    heatmap[:, :, 0] = np.clip(norm * 3.0, 0.0, 1.0)             # red ramp
    heatmap[:, :, 1] = np.clip((norm - 0.33) * 3.0, 0.0, 1.0)    # green ramp
    heatmap[:, :, 2] = np.clip((norm - 0.66) * 3.0, 0.0, 1.0)    # blue ramp

    heatmap_u8 = _ensure_uint8(heatmap)

    bbox = _bbox_from_mask(above_threshold)

    stats = {
        "mean_diff": mean_diff,
        "max_diff": max_diff,
        "diff_pixel_count": diff_pixel_count,
        "diff_ratio": diff_ratio,
        "bbox": bbox,
    }
    return heatmap_u8, stats


# ---------------------------------------------------------------------------
# Bounding-box overlay
# ---------------------------------------------------------------------------


def overlay_bbox(
    img: np.ndarray,
    bbox: Dict[str, int],
    color: Tuple[int, ...] = (255, 0, 0, 180),
    thickness: int = 2,
) -> np.ndarray:
    """Draw an axis-aligned bounding box rectangle onto an image.

    Parameters
    ----------
    img:
        Source image ``(H, W, C)`` in ``uint8``.  The input is **not**
        mutated; a copy is returned.
    bbox:
        Dictionary with integer keys ``x0``, ``y0``, ``x1``, ``y1``.
    color:
        RGBA tuple for the rectangle colour.
    thickness:
        Line thickness in pixels.

    Returns
    -------
    np.ndarray
        A copy of *img* with the rectangle drawn.
    """
    out = img.copy()
    h, w = out.shape[:2]
    has_alpha = out.ndim == 3 and out.shape[2] == 4

    x0 = max(0, int(bbox["x0"]))
    y0 = max(0, int(bbox["y0"]))
    x1 = min(w - 1, int(bbox["x1"]))
    y1 = min(h - 1, int(bbox["y1"]))

    # Determine the draw colour matching the image channel count.
    if has_alpha:
        draw_color = np.array(color[:4], dtype=np.uint8)
    elif out.ndim == 3 and out.shape[2] == 3:
        draw_color = np.array(color[:3], dtype=np.uint8)
    else:
        # Greyscale: use luminance of the colour.
        draw_color = np.uint8(
            0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
        )

    # Alpha blending factor (only meaningful when colour has an alpha).
    alpha = color[3] / 255.0 if len(color) >= 4 else 1.0

    def _draw_hline(y: int, xa: int, xb: int) -> None:
        if 0 <= y < h:
            xa = max(0, xa)
            xb = min(w - 1, xb)
            if alpha < 1.0 and out.ndim == 3:
                existing = out[y, xa : xb + 1].astype(np.float32)
                blended = existing * (1.0 - alpha) + draw_color[:existing.shape[1]].astype(np.float32) * alpha
                out[y, xa : xb + 1] = np.clip(blended, 0, 255).astype(np.uint8)
            else:
                out[y, xa : xb + 1] = draw_color

    def _draw_vline(x: int, ya: int, yb: int) -> None:
        if 0 <= x < w:
            ya = max(0, ya)
            yb = min(h - 1, yb)
            if alpha < 1.0 and out.ndim == 3:
                existing = out[ya : yb + 1, x].astype(np.float32)
                blended = existing * (1.0 - alpha) + draw_color[:existing.shape[1]].astype(np.float32) * alpha
                out[ya : yb + 1, x] = np.clip(blended, 0, 255).astype(np.uint8)
            else:
                out[ya : yb + 1, x] = draw_color

    for t in range(thickness):
        _draw_hline(y0 + t, x0, x1)         # top edge
        _draw_hline(y1 - t, x0, x1)         # bottom edge
        _draw_vline(x0 + t, y0, y1)         # left edge
        _draw_vline(x1 - t, y0, y1)         # right edge

    return out


# ---------------------------------------------------------------------------
# Pixel statistics
# ---------------------------------------------------------------------------


def pixel_stats(
    pixels: np.ndarray,
    region: Optional[Dict[str, int]] = None,
) -> Dict:
    """Compute per-channel statistics for pixel data.

    Parameters
    ----------
    pixels:
        Image array ``(H, W, C)`` or ``(H, W)``.
    region:
        Optional sub-region ``{x0, y0, x1, y1}``.  When provided only the
        pixels inside this axis-aligned rectangle are considered.

    Returns
    -------
    dict
        Keys: ``min``, ``max``, ``mean``, ``std`` (each a list of per-channel
        values), ``has_nan``, ``has_inf``.
    """
    arr = pixels
    if region is not None:
        x0 = max(0, int(region["x0"]))
        y0 = max(0, int(region["y0"]))
        x1 = min(arr.shape[1], int(region["x1"]) + 1)
        y1 = min(arr.shape[0], int(region["y1"]) + 1)
        arr = arr[y0:y1, x0:x1]

    arr_f = arr.astype(np.float64, copy=False)

    if arr_f.ndim == 2:
        arr_f = arr_f[:, :, np.newaxis]

    num_channels = arr_f.shape[2]
    ch_min = []
    ch_max = []
    ch_mean = []
    ch_std = []

    for c in range(num_channels):
        channel = arr_f[:, :, c]
        # Use nanmin/nanmax so that NaN values do not poison the result.
        ch_min.append(float(np.nanmin(channel)))
        ch_max.append(float(np.nanmax(channel)))
        ch_mean.append(float(np.nanmean(channel)))
        ch_std.append(float(np.nanstd(channel)))

    has_nan = bool(np.any(np.isnan(arr_f)))
    has_inf = bool(np.any(np.isinf(arr_f)))

    return {
        "min": ch_min,
        "max": ch_max,
        "mean": ch_mean,
        "std": ch_std,
        "has_nan": has_nan,
        "has_inf": has_inf,
    }


# ---------------------------------------------------------------------------
# PNG serialisation
# ---------------------------------------------------------------------------


def array_to_png_bytes(arr: np.ndarray) -> bytes:
    """Convert a NumPy image array to PNG bytes.

    Parameters
    ----------
    arr:
        Image array ``(H, W)``, ``(H, W, 1)``, ``(H, W, 3)``, or
        ``(H, W, 4)`` in ``uint8`` or ``float32`` format.

    Returns
    -------
    bytes
        PNG-encoded image.
    """
    # Sanitise float images: clamp NaN/Inf to finite range, then to [0, 1].
    if np.issubdtype(arr.dtype, np.floating):
        arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=0.0)
        arr = _ensure_uint8(arr)

    if arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]

    mode_map = {2: "L", 3: "RGB", 4: "RGBA"}
    mode = mode_map.get(arr.ndim if arr.ndim == 2 else arr.shape[2], "RGB")
    if arr.ndim == 2:
        mode = "L"

    img = Image.fromarray(arr, mode=mode)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png_bytes_to_array(data: bytes) -> np.ndarray:
    """Decode PNG bytes into a NumPy ``uint8`` array.

    Parameters
    ----------
    data:
        Raw PNG bytes.

    Returns
    -------
    np.ndarray
        Image array ``(H, W, C)`` with ``dtype=uint8``.
    """
    img = Image.open(io.BytesIO(data))
    arr = np.asarray(img, dtype=np.uint8)
    # Ensure 3-D for consistency (single-channel images become (H, W, 1)).
    if arr.ndim == 2:
        arr = arr[:, :, np.newaxis]
    return arr


# ---------------------------------------------------------------------------
# HDR tonemapping
# ---------------------------------------------------------------------------


def tonemap_hdr(
    pixels: np.ndarray,
    exposure: float = 1.0,
) -> np.ndarray:
    """Apply simple Reinhard tonemapping to HDR pixel data.

    .. math::

        L_{\\text{out}} = \\frac{L_{\\text{in}} \\cdot e}{1 + L_{\\text{in}} \\cdot e}

    where *e* is the *exposure* multiplier.

    Parameters
    ----------
    pixels:
        HDR image array ``(H, W, C)`` in ``float32``.  Non-float input is
        returned unchanged (it is already LDR).
    exposure:
        Exposure multiplier applied before tonemapping.

    Returns
    -------
    np.ndarray
        ``uint8`` LDR image ``(H, W, C)`` after tonemapping.
    """
    if not np.issubdtype(pixels.dtype, np.floating):
        # Already LDR; nothing to do.
        return pixels

    fp = pixels.astype(np.float32, copy=True)

    # Replace NaN/Inf with safe values so tonemapping does not propagate them.
    fp = np.nan_to_num(fp, nan=0.0, posinf=1e4, neginf=0.0)

    # Clamp negatives (can occur in HDR data from certain compute shaders).
    fp = np.maximum(fp, 0.0)

    # Apply exposure.
    fp *= exposure

    # Reinhard operator.
    fp = fp / (1.0 + fp)

    return _ensure_uint8(fp)
