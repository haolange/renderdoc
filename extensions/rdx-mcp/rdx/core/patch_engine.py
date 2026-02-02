"""
Shader patch engine for RDX-MCP.

Applies source-level modifications to shaders in the RenderDoc replay
environment, manages replacement resources, and tracks active patches
for clean revert.  Every applied patch is recorded so the original shader
can be restored at any time.

The engine operates on disassembled / decompiled shader text obtained from
the replay controller.  Three categories of patch operations are supported:

* **force_full_precision** -- promote reduced-precision types and add
  ``precise`` qualifiers (HLSL), upgrade precision qualifiers (GLSL),
  or strip ``RelaxedPrecision`` decorations (SPIR-V assembly).
* **insert_guard** -- wrap an expression with ``isnan`` / ``isinf`` guards
  so that NaN or Inf values are replaced by a safe fallback.
* **replace_expr** -- perform a direct textual substitution inside the
  shader source.

After modification the patched source is compiled back through the replay
controller (``BuildTargetShader``) and hot-swapped via ``ReplaceResource``.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from rdx.models import (
    PatchOp,
    PatchResult,
    PatchSpec,
    ShaderStage,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy renderdoc import
# ---------------------------------------------------------------------------

_rd_module: Any = None


def _get_rd() -> Any:
    """Return the ``renderdoc`` module, importing it lazily.

    The module is only available inside a RenderDoc host process or when
    the library path has been added to ``sys.path``.  Importing eagerly at
    module load time would break tools that merely introspect this package.
    """
    global _rd_module
    if _rd_module is None:
        import renderdoc as rd  # type: ignore[import-untyped]
        _rd_module = rd
    return _rd_module


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _new_id(prefix: str) -> str:
    """Generate a short unique identifier with the given *prefix*."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# Map our string-enum ShaderStage to the integer values used by the
# renderdoc.ShaderStage C++ enum.
_STAGE_TO_RD_INDEX: Dict[ShaderStage, int] = {
    ShaderStage.VS: 0,   # Vertex
    ShaderStage.HS: 1,   # Hull / Tessellation Control
    ShaderStage.DS: 2,   # Domain / Tessellation Evaluation
    ShaderStage.GS: 3,   # Geometry
    ShaderStage.PS: 4,   # Pixel / Fragment
    ShaderStage.CS: 5,   # Compute
}


def _to_rd_stage(stage: ShaderStage) -> Any:
    """Convert an ``rdx.models.ShaderStage`` value to a ``renderdoc.ShaderStage``.

    Raises ``ValueError`` for stages that the RenderDoc enum does not
    cover (e.g. mesh / amplification shaders).
    """
    rd = _get_rd()
    idx = _STAGE_TO_RD_INDEX.get(stage)
    if idx is None:
        raise ValueError(
            f"Shader stage '{stage.value}' is not supported for patching. "
            f"Supported stages: {sorted(s.value for s in _STAGE_TO_RD_INDEX)}"
        )
    return rd.ShaderStage(idx)


# ---------------------------------------------------------------------------
# PatchRecord
# ---------------------------------------------------------------------------

@dataclass
class PatchRecord:
    """Internal bookkeeping for a single applied shader patch.

    Stores all the information needed to revert the patch and free the
    replacement resource that was allocated by the replay controller.
    """

    patch_id: str
    session_id: str
    original_shader_id: Any       # renderdoc.ResourceId
    replacement_shader_id: Any    # renderdoc.ResourceId
    original_shader_hash: str     # SHA-256 of the original source text
    spec: PatchSpec
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# PatchEngine
# ---------------------------------------------------------------------------

class PatchEngine:
    """Apply, track, and revert shader patches in a RenderDoc replay session.

    Usage::

        engine = PatchEngine()
        result = await engine.apply_patch(
            session_id, event_id, ShaderStage.PS, session_mgr, spec,
        )
        ...
        await engine.revert_patch(session_id, result.patch_id, session_mgr)
    """

    def __init__(self) -> None:
        # patch_id -> PatchRecord
        self._patches: Dict[str, PatchRecord] = {}

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def apply_patch(
        self,
        session_id: str,
        event_id: int,
        stage: ShaderStage,
        session_manager: Any,
        patch_spec: PatchSpec,
    ) -> PatchResult:
        """Apply *patch_spec* to the shader bound at *stage* for *event_id*.

        Steps
        -----
        1. Navigate the replay to *event_id*.
        2. Read the pipeline state and obtain the bound shader + reflection.
        3. Disassemble the shader in the best editable encoding.
        4. Apply each ``PatchOp`` from *patch_spec* to the source text.
        5. Compile the modified source via ``BuildTargetShader``.
        6. Hot-swap the shader via ``ReplaceResource``.
        7. Record the patch for later revert.

        Returns a :class:`PatchResult` indicating success or failure.
        """
        try:
            controller = session_manager.get_controller(session_id)

            # 1 -- navigate to the target event
            controller.SetFrameEvent(event_id, True)

            # 2 -- pipeline state and shader identification
            pipe = controller.GetPipelineState()
            rd_stage = _to_rd_stage(stage)
            shader_id = pipe.GetShader(rd_stage)
            refl = pipe.GetShaderReflection(rd_stage)

            if refl is None:
                return PatchResult(
                    patch_id=patch_spec.patch_id,
                    success=False,
                    error_message=(
                        f"No shader bound at stage {stage.value} "
                        f"for event {event_id}"
                    ),
                )

            # 3 -- disassemble in the best editable encoding
            encoding, disasm_target = self._get_best_encoding(
                controller, session_id,
            )
            source = controller.DisassembleShader(pipe, refl, disasm_target)

            if not source:
                return PatchResult(
                    patch_id=patch_spec.patch_id,
                    success=False,
                    error_message=(
                        f"Disassembly returned empty source for "
                        f"target '{disasm_target}'"
                    ),
                )

            original_hash = hashlib.sha256(
                source.encode("utf-8"),
            ).hexdigest()
            encoding_name = self._encoding_name(encoding)

            # 4 -- apply every PatchOp sequentially
            modified = source
            for op in patch_spec.ops:
                modified = self._apply_op(modified, encoding_name, op)

            if modified == source:
                logger.warning(
                    "Patch %s: operations produced no changes to the shader "
                    "source (stage=%s, event=%d)",
                    patch_spec.patch_id, stage.value, event_id,
                )

            # 5 -- compile the modified source
            rd = _get_rd()
            entry_point = refl.entryPoint if refl.entryPoint else "main"
            source_bytes = modified.encode("utf-8")
            new_id, errors = controller.BuildTargetShader(
                entry_point,
                encoding,
                source_bytes,
                [],          # compile flags
                rd_stage,
            )

            if errors:
                # Distinguish between a hard failure (null resource) and
                # mere warnings (resource allocated, but compiler emitted
                # diagnostic text).
                null_id = rd.ResourceId()
                if new_id == null_id or new_id is None:
                    return PatchResult(
                        patch_id=patch_spec.patch_id,
                        original_shader_hash=original_hash,
                        success=False,
                        error_message=f"Shader build failed: {errors}",
                    )
                # Non-fatal warnings -- log and continue.
                logger.warning(
                    "Shader build for patch %s produced warnings: %s",
                    patch_spec.patch_id, errors,
                )

            # 6 -- hot-swap the resource
            controller.ReplaceResource(shader_id, new_id)

            applied_hash = hashlib.sha256(
                modified.encode("utf-8"),
            ).hexdigest()

            # 7 -- record for future revert
            record = PatchRecord(
                patch_id=patch_spec.patch_id,
                session_id=session_id,
                original_shader_id=shader_id,
                replacement_shader_id=new_id,
                original_shader_hash=original_hash,
                spec=patch_spec,
            )
            self._patches[patch_spec.patch_id] = record

            logger.info(
                "Applied patch %s to shader %s (stage=%s, event=%d, "
                "encoding=%s)",
                patch_spec.patch_id, shader_id, stage.value, event_id,
                encoding_name,
            )

            return PatchResult(
                patch_id=patch_spec.patch_id,
                applied_to_shader_hash=applied_hash,
                original_shader_hash=original_hash,
                success=True,
            )

        except Exception as exc:
            logger.exception(
                "apply_patch failed for patch %s", patch_spec.patch_id,
            )
            return PatchResult(
                patch_id=patch_spec.patch_id,
                success=False,
                error_message=str(exc),
            )

    async def revert_patch(
        self,
        session_id: str,
        patch_id: str,
        session_manager: Any,
    ) -> bool:
        """Revert a previously applied patch.

        Removes the resource replacement, frees the compiled replacement
        resource, and deletes the internal record.

        Returns ``True`` on success, ``False`` if the patch was not found
        or the revert operation failed.
        """
        record = self._patches.get(patch_id)
        if record is None:
            logger.warning("revert_patch: patch %s not found", patch_id)
            return False

        if record.session_id != session_id:
            logger.warning(
                "revert_patch: patch %s belongs to session %s, not %s",
                patch_id, record.session_id, session_id,
            )
            return False

        try:
            controller = session_manager.get_controller(session_id)
            controller.RemoveReplacement(record.original_shader_id)
            controller.FreeTargetResource(record.replacement_shader_id)
            del self._patches[patch_id]
            logger.info("Reverted patch %s", patch_id)
            return True
        except Exception:
            logger.exception("Failed to revert patch %s", patch_id)
            return False

    async def revert_all(
        self,
        session_id: str,
        session_manager: Any,
    ) -> int:
        """Revert every active patch for *session_id*.

        Returns the number of patches successfully reverted.  Patches that
        fail to revert are logged but do not prevent the remaining patches
        from being attempted.
        """
        target_ids = [
            pid for pid, rec in self._patches.items()
            if rec.session_id == session_id
        ]
        reverted = 0
        for pid in target_ids:
            if await self.revert_patch(session_id, pid, session_manager):
                reverted += 1
        if reverted:
            logger.info(
                "Reverted %d / %d patches for session %s",
                reverted, len(target_ids), session_id,
            )
        return reverted

    def list_patches(
        self,
        session_id: Optional[str] = None,
    ) -> List[PatchSpec]:
        """Return the :class:`PatchSpec` for every active patch.

        If *session_id* is given, only patches belonging to that session
        are returned.
        """
        return [
            rec.spec
            for rec in self._patches.values()
            if session_id is None or rec.session_id == session_id
        ]

    # ------------------------------------------------------------------
    # Patch-op dispatch
    # ------------------------------------------------------------------

    def _apply_op(
        self,
        source: str,
        encoding_name: str,
        op: PatchOp,
    ) -> str:
        """Dispatch a single :class:`PatchOp` to the appropriate handler."""
        if op.op == "force_full_precision":
            return self._apply_precision_patch(
                source, encoding_name, op.variables,
            )
        if op.op == "insert_guard":
            return self._apply_guard_patch(
                source,
                encoding_name,
                op.guard_expr or "",
                op.guard_replacement or "0.0",
            )
        if op.op == "replace_expr":
            return self._apply_expr_replace(
                source,
                op.expr_from or "",
                op.expr_to or "",
            )
        logger.warning("Unknown patch op type '%s'; skipping", op.op)
        return source

    # ------------------------------------------------------------------
    # Precision patch
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_precision_patch(
        source: str,
        encoding: str,
        variables: List[str],
    ) -> str:
        """Force full precision for the listed *variables* (or globally).

        **HLSL**
            * Replace reduced-precision types (``min16float``, ``half``,
              ``min10float``, ``min16int``, ``min12int``, ``min16uint``)
              with their full-width equivalents.
            * Insert the ``precise`` keyword before declarations of each
              variable in *variables*.

        **GLSL**
            * Replace ``mediump`` and ``lowp`` qualifiers with ``highp``
              for the listed *variables*, or globally if none are given.

        **SPIR-V assembly**
            * Remove ``OpDecorate %<var> RelaxedPrecision`` lines for the
              listed *variables*, or all such decorations if none are given.
        """
        modified = source
        enc = encoding.lower()

        # ---- HLSL ---------------------------------------------------------
        if "hlsl" in enc:
            # Promote reduced-precision types to full width.
            _hlsl_type_map = {
                "min16float": "float",
                "min10float": "float",
                "min16int":   "int",
                "min12int":   "int",
                "min16uint":  "uint",
                "half":       "float",
            }
            for old_type, new_type in _hlsl_type_map.items():
                modified = modified.replace(old_type, new_type)

            # Add ``precise`` to targeted variable declarations.
            for var in variables:
                # Matches: <type> <var>   (not already preceded by ``precise``)
                # <type> is a common HLSL numeric type, possibly with vector
                # or matrix dimension suffixes (e.g. float4, float4x4).
                pattern = re.compile(
                    r"(?<!\bprecise\s)"
                    r"(\b(?:float|double|int|uint|dword)"
                    r"(?:[1-4](?:x[1-4])?)?)"
                    rf"(\s+{re.escape(var)}\b)",
                )
                modified = pattern.sub(r"precise \1\2", modified)

        # ---- GLSL ---------------------------------------------------------
        elif "glsl" in enc:
            if variables:
                for var in variables:
                    # Replace the precision qualifier on the line where *var*
                    # is declared.
                    pattern = re.compile(
                        rf"(\b(?:mediump|lowp)\b)"
                        rf"(\s+\w+\s+{re.escape(var)}\b)",
                    )
                    modified = pattern.sub(r"highp\2", modified)
            else:
                # Global promotion -- replace every qualifier.
                modified = re.sub(r"\bmediump\b", "highp", modified)
                modified = re.sub(r"\blowp\b", "highp", modified)

        # ---- SPIR-V assembly ----------------------------------------------
        elif "spirv" in enc or "spv" in enc:
            if variables:
                for var in variables:
                    pattern = re.compile(
                        rf"^\s*OpDecorate\s+%{re.escape(var)}"
                        r"\s+RelaxedPrecision\s*$",
                        re.MULTILINE,
                    )
                    modified = pattern.sub("", modified)
            else:
                # Strip *all* RelaxedPrecision decorations.
                modified = re.sub(
                    r"^\s*OpDecorate\s+%\w+\s+RelaxedPrecision\s*$",
                    "",
                    modified,
                    flags=re.MULTILINE,
                )

        return modified

    # ------------------------------------------------------------------
    # Guard patch
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_guard_patch(
        source: str,
        encoding: str,
        expr: str,
        guard: str,
    ) -> str:
        """Wrap every occurrence of *expr* with a NaN / Inf guard.

        The guarded form replaces *expr* with::

            (isnan(expr) || isinf(expr)) ? guard : expr

        for HLSL and GLSL sources.  For SPIR-V assembly a comment marker
        is emitted because instruction-level rewriting requires external
        SPIR-V tooling.

        Parameters
        ----------
        source:
            Shader source text.
        encoding:
            Lowercase encoding identifier (``"hlsl"``, ``"glsl"``, ...).
        expr:
            The expression to guard.
        guard:
            Replacement value used when *expr* is NaN or Inf (e.g.
            ``"0.0"`` or ``"float3(0,0,0)"``).
        """
        if not expr:
            return source

        enc = encoding.lower()
        escaped = re.escape(expr)
        # Negative look-around prevents replacing inside identifiers.
        token_pattern = rf"(?<![a-zA-Z0-9_.]){escaped}(?![a-zA-Z0-9_.])"

        if "hlsl" in enc or "glsl" in enc:
            replacement = (
                f"(isnan({expr}) || isinf({expr}) ? {guard} : {expr})"
            )
            return re.sub(token_pattern, replacement, source)

        if "spirv" in enc or "spv" in enc:
            # Full instruction rewriting is outside the scope of textual
            # patching.  Emit a structured comment so that an external
            # SPIR-V assembler pass can pick it up.
            marker = f"; RDX_GUARD: {expr} -> {guard}\n"
            if marker not in source:
                idx = source.find("OpFunction")
                if idx >= 0:
                    return source[:idx] + marker + source[idx:]
                return marker + source
            return source

        # Unknown / generic encoding -- best-effort literal replacement.
        replacement = (
            f"(isnan({expr}) || isinf({expr}) ? {guard} : {expr})"
        )
        return source.replace(expr, replacement)

    # ------------------------------------------------------------------
    # Expression replacement
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_expr_replace(
        source: str,
        expr_from: str,
        expr_to: str,
    ) -> str:
        """Perform a direct textual substitution in shader source.

        Every occurrence of *expr_from* is replaced with *expr_to*.
        Returns the original *source* unchanged if *expr_from* is empty.
        """
        if not expr_from:
            return source
        return source.replace(expr_from, expr_to)

    # ------------------------------------------------------------------
    # Encoding selection
    # ------------------------------------------------------------------

    @staticmethod
    def _get_best_encoding(
        controller: Any,
        session_id: str,
    ) -> Tuple[Any, str]:
        """Choose the best editable shader encoding for the current session.

        Queries the replay controller for available disassembly targets
        (human-readable forms such as ``"HLSL"``, ``"GLSL 460"``, or
        ``"SPIR-V (Human-readable)"``) and for the shader encodings that
        ``BuildTargetShader`` accepts.

        The preference order is **HLSL > GLSL > SPIRVAsm** because
        high-level languages are easier to patch textually.

        Returns
        -------
        tuple[ShaderEncoding, str]
            A ``(ShaderEncoding, disassembly_target_name)`` pair that can
            be passed to ``DisassembleShader`` and ``BuildTargetShader``
            respectively.

        Raises
        ------
        RuntimeError
            If no suitable encoding / target pair is available.
        """
        rd = _get_rd()

        targets: List[str] = [str(t) for t in controller.GetDisassemblyTargets(True)]
        encodings = list(controller.GetTargetShaderEncodings())

        # Build a fast set of encoding values for membership tests.
        encoding_set = set(encodings)

        # (ShaderEncoding, keyword used to match a disassembly target name)
        preferences = [
            (rd.ShaderEncoding.HLSL,     "hlsl"),
            (rd.ShaderEncoding.GLSL,     "glsl"),
            (rd.ShaderEncoding.SPIRVAsm, "spir"),
        ]

        for enc, keyword in preferences:
            if enc not in encoding_set:
                continue
            for target_name in targets:
                if keyword in target_name.lower():
                    return enc, target_name

        # No preferred match found -- fall back to whatever is available.
        if encodings and targets:
            logger.warning(
                "No preferred encoding matched for session %s; falling "
                "back to encoding=%s target='%s'",
                session_id, encodings[0], targets[0],
            )
            return encodings[0], targets[0]

        raise RuntimeError(
            f"No shader encodings available for session {session_id}. "
            f"Disassembly targets={targets!r}, "
            f"Encodings={[str(e) for e in encodings]!r}"
        )

    @staticmethod
    def _encoding_name(encoding: Any) -> str:
        """Derive a lowercase name string from a ``ShaderEncoding`` enum.

        Handles both ``ShaderEncoding.HLSL`` and bare ``"HLSL"`` forms
        that different renderdoc versions may expose.
        """
        name = str(encoding)
        if "." in name:
            name = name.rsplit(".", 1)[-1]
        return name.lower()
