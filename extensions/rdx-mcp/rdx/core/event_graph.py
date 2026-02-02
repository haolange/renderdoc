"""
Event tree construction and navigation service.

Converts the flat/recursive ``ActionDescription`` tree from a RenderDoc replay
controller into a serialisable :class:`~rdx.models.EventNode` tree, and
provides helpers for querying, flattening, and inferring render-pass
boundaries when explicit debug markers are absent.

Usage::

    from rdx.core.event_graph import EventGraphService

    svc = EventGraphService()
    tree = svc.build_event_tree(session_id, session_manager)
    draws = svc.get_draw_events(tree)
    lo, hi = svc.get_event_range(tree)
    tree = svc.infer_passes(tree, session_id, session_manager)
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

from rdx.models import EventFlags, EventNode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import
# ---------------------------------------------------------------------------


def _get_rd():
    """Return the ``renderdoc`` module, importing it on first call."""
    import renderdoc as rd
    return rd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _has_flag(flags: Any, flag: Any) -> bool:
    """Safely test whether a bitfield *flags* contains *flag*.

    Works with both Python :class:`enum.IntFlag` and the SWIG-generated
    ``ActionFlags`` type exposed by the renderdoc module.
    """
    try:
        return bool(flags & flag)
    except TypeError:
        return False


def _map_action_flags(flags: Any) -> EventFlags:
    """Convert a RenderDoc ``ActionFlags`` bitfield to :class:`EventFlags`."""
    rd = _get_rd()
    af = rd.ActionFlags
    return EventFlags(
        is_draw=_has_flag(flags, af.Draw),
        is_dispatch=_has_flag(flags, af.Dispatch),
        is_marker=(
            _has_flag(flags, af.SetMarker)
            or _has_flag(flags, af.PushMarker)
            or _has_flag(flags, af.PopMarker)
        ),
        is_copy=_has_flag(flags, af.Copy),
        is_resolve=_has_flag(flags, af.Resolve),
        is_clear=_has_flag(flags, af.Clear),
        is_pass_boundary=_has_flag(flags, af.Present),
    )


def _resource_id_str(rid: Any) -> str:
    """Convert a RenderDoc ``ResourceId`` to a deterministic string key."""
    try:
        return str(int(rid))
    except (TypeError, ValueError):
        return str(rid)


def _query_output_targets(controller: Any, event_id: int) -> Tuple[str, ...]:
    """Navigate to *event_id* and read the current output targets.

    Falls back gracefully when the pipeline-state accessors are not
    available (e.g. on older RenderDoc builds or for certain API backends).
    """
    try:
        rd = _get_rd()
        controller.SetFrameEvent(event_id, True)
        pipe = controller.GetPipelineState()
        null_id = rd.ResourceId.Null()
        targets: list[str] = []

        # Colour / render targets
        try:
            for desc in pipe.GetOutputTargets():
                rid = getattr(desc, "resourceId", None)
                if rid is not None and rid != null_id:
                    targets.append(_resource_id_str(rid))
        except (AttributeError, TypeError):
            pass

        # Depth target
        try:
            depth = pipe.GetDepthTarget()
            if depth is not None:
                rid = getattr(depth, "resourceId", None)
                if rid is not None and rid != null_id:
                    targets.append(_resource_id_str(rid))
        except (AttributeError, TypeError):
            pass

        return tuple(sorted(targets))
    except Exception as exc:
        logger.debug(
            "Could not query output targets for event %d: %s",
            event_id, exc,
        )
        return ()


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------

class EventGraphService:
    """Builds and queries the event tree for a RenderDoc capture.

    All methods are synchronous; the caller is expected to offload them to
    a thread pool if needed (the ``SessionManager`` already does this for
    its own internal calls).
    """

    # -- Tree construction --------------------------------------------------

    def build_event_tree(
        self,
        session_id: str,
        session_manager: Any,
    ) -> List[EventNode]:
        """Build an :class:`EventNode` tree from the replay controller.

        Parameters
        ----------
        session_id:
            Active session that already has an open capture.
        session_manager:
            A :class:`~rdx.core.session_manager.SessionManager` instance
            used to obtain the ``IReplayController``.

        Returns
        -------
        list[EventNode]
            Top-level nodes whose ``children`` form the full tree.
        """
        controller = session_manager.get_controller(session_id)
        root_actions = controller.GetRootActions()
        nodes = [
            self._build_node(action, depth=0)
            for action in root_actions
        ]
        total = self._count_nodes(nodes)
        logger.info(
            "Built event tree for session %s: %d total nodes", session_id, total,
        )
        return nodes

    # -- Querying -----------------------------------------------------------

    def get_draw_events(self, event_tree: List[EventNode]) -> List[EventNode]:
        """Flatten the tree and return only draw and dispatch events.

        The returned list preserves document order (depth-first pre-order).
        """
        result: List[EventNode] = []
        self._collect_draws(event_tree, result)
        return result

    def get_event_range(
        self,
        event_tree: List[EventNode],
    ) -> Tuple[int, int]:
        """Return ``(min_event_id, max_event_id)`` across the whole tree.

        Returns ``(0, 0)`` for an empty tree.
        """
        ids: List[int] = []
        self._collect_ids(event_tree, ids)
        if not ids:
            return (0, 0)
        return (min(ids), max(ids))

    def find_event(
        self,
        event_tree: List[EventNode],
        event_id: int,
    ) -> Optional[EventNode]:
        """Depth-first search for an :class:`EventNode` by *event_id*.

        Returns ``None`` if the event is not present in the tree.
        """
        for node in event_tree:
            if node.event_id == event_id:
                return node
            found = self.find_event(node.children, event_id)
            if found is not None:
                return found
        return None

    def get_event_path(
        self,
        event_tree: List[EventNode],
        event_id: int,
    ) -> List[int]:
        """Return the path from the tree root to *event_id*.

        The returned list contains the ``event_id`` of every ancestor from
        the top-level node down to (and including) the target.  Returns an
        empty list when *event_id* is not found.
        """
        path: List[int] = []
        if self._build_path(event_tree, event_id, path):
            return path
        return []

    # -- Pass inference -----------------------------------------------------

    def infer_passes(
        self,
        event_tree: List[EventNode],
        session_id: str,
        session_manager: Any,
    ) -> List[EventNode]:
        """Infer render-pass boundaries from output-target changes.

        When a capture lacks explicit debug markers, this method groups
        consecutive draw/dispatch calls that share the same set of render
        targets into logical passes.  Each group is assigned a synthetic
        label (``pass_1``, ``pass_2``, ...) written to
        :attr:`EventNode.inferred_pass`.

        The *event_tree* is mutated in-place and also returned for
        convenience.

        Parameters
        ----------
        event_tree:
            Tree previously built by :meth:`build_event_tree`.
        session_id:
            Active session (used to query pipeline state when the
            ``output_targets`` list on a node is empty).
        session_manager:
            A :class:`~rdx.core.session_manager.SessionManager` instance.

        Returns
        -------
        list[EventNode]
            The same *event_tree* with ``inferred_pass`` fields populated.
        """
        draws = self.get_draw_events(event_tree)
        if not draws:
            return event_tree

        controller = session_manager.get_controller(session_id)

        # Resolve the effective output-target set for every draw.
        resolved: List[Tuple[EventNode, Tuple[str, ...]]] = []
        for node in draws:
            targets = tuple(sorted(node.output_targets))
            if not targets:
                targets = _query_output_targets(controller, node.event_id)
            resolved.append((node, targets))

        # Walk the list and bump the pass counter whenever the target set
        # changes between consecutive draws.
        pass_index = 0
        prev_targets: Optional[Tuple[str, ...]] = None
        for node, targets in resolved:
            if targets != prev_targets:
                pass_index += 1
                prev_targets = targets
            node.inferred_pass = f"pass_{pass_index}"

        logger.info(
            "Inferred %d passes from %d draw events in session %s",
            pass_index, len(draws), session_id,
        )
        return event_tree

    # -- Private helpers: tree walking --------------------------------------

    def _build_node(self, action: Any, depth: int) -> EventNode:
        """Recursively convert an ``ActionDescription`` to an ``EventNode``."""
        flags = _map_action_flags(action.flags)

        raw_outputs = getattr(action, "outputs", None) or []
        output_targets = [_resource_id_str(rid) for rid in raw_outputs]

        raw_children = getattr(action, "children", None) or []
        children = [
            self._build_node(child, depth=depth + 1)
            for child in raw_children
        ]

        name = getattr(action, "customName", None) or ""

        return EventNode(
            event_id=int(action.eventId),
            name=str(name),
            flags=flags,
            children=children,
            depth=depth,
            output_targets=output_targets,
        )

    @staticmethod
    def _collect_draws(
        nodes: List[EventNode],
        result: List[EventNode],
    ) -> None:
        """Depth-first collection of draw/dispatch nodes."""
        for node in nodes:
            if node.flags.is_draw or node.flags.is_dispatch:
                result.append(node)
            EventGraphService._collect_draws(node.children, result)

    @staticmethod
    def _collect_ids(
        nodes: List[EventNode],
        ids: List[int],
    ) -> None:
        """Depth-first collection of every event ID."""
        for node in nodes:
            ids.append(node.event_id)
            EventGraphService._collect_ids(node.children, ids)

    @staticmethod
    def _count_nodes(nodes: List[EventNode]) -> int:
        """Return the total number of nodes in the tree."""
        total = 0
        for node in nodes:
            total += 1
            total += EventGraphService._count_nodes(node.children)
        return total

    def _build_path(
        self,
        nodes: List[EventNode],
        target_id: int,
        path: List[int],
    ) -> bool:
        """Populate *path* with event IDs from root to *target_id*.

        Returns ``True`` when the target is found, ``False`` otherwise.
        The caller should treat *path* as valid only when the return
        value is ``True``.
        """
        for node in nodes:
            path.append(node.event_id)
            if node.event_id == target_id:
                return True
            if self._build_path(node.children, target_id, path):
                return True
            path.pop()
        return False
