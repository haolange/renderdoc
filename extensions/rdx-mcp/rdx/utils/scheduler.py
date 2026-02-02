"""
GPU worker scheduler for RDX-MCP.

Manages replay worker slots as scarce resources.  Each worker slot represents
exclusive access to a GPU replay context (either local or remote).  The
scheduler uses :class:`asyncio.Semaphore` per backend type so that callers
block until a slot becomes available, respecting a simple priority scheme:

    0 = interactive  (highest -- user-initiated single actions)
    1 = batch        (medium  -- automated experiment loops)
    2 = regression   (lowest  -- background regression sweeps)

Lower numeric values are served first.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Priority constants
# ---------------------------------------------------------------------------

PRIORITY_INTERACTIVE: int = 0
PRIORITY_BATCH: int = 1
PRIORITY_REGRESSION: int = 2

# ---------------------------------------------------------------------------
# WorkerSlot
# ---------------------------------------------------------------------------


@dataclass
class WorkerSlot:
    """Represents a single GPU replay worker.

    Attributes
    ----------
    slot_id:
        Unique identifier for this slot (auto-generated if not supplied).
    gpu_index:
        Index of the GPU device this slot is bound to.
    backend_type:
        ``"local"`` or ``"remote"``.
    busy:
        Whether the slot is currently acquired.
    current_task_id:
        Identifier of the task currently holding the slot, or ``None``.
    device_info:
        Arbitrary device metadata (driver version, device name, etc.).
    """

    slot_id: str = field(default_factory=lambda: f"slot_{uuid.uuid4().hex[:8]}")
    gpu_index: int = 0
    backend_type: str = "local"
    busy: bool = False
    current_task_id: Optional[str] = None
    device_info: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal priority-aware waiter queue
# ---------------------------------------------------------------------------


class _PriorityWaiter:
    """A single waiter in the priority queue.

    Each waiter carries a *priority* (lower is higher priority) and an
    :class:`asyncio.Event` that is set when a slot becomes available.
    """

    __slots__ = ("priority", "event", "_id")

    # Monotonic counter used to break ties in FIFO order.
    _counter: int = 0

    def __init__(self, priority: int) -> None:
        self.priority = priority
        self.event = asyncio.Event()
        _PriorityWaiter._counter += 1
        self._id = _PriorityWaiter._counter

    def __lt__(self, other: _PriorityWaiter) -> bool:  # type: ignore[override]
        if self.priority != other.priority:
            return self.priority < other.priority
        return self._id < other._id


# ---------------------------------------------------------------------------
# WorkerScheduler
# ---------------------------------------------------------------------------


class WorkerScheduler:
    """Async-safe scheduler for GPU replay worker slots.

    Parameters
    ----------
    max_local_workers:
        Maximum number of concurrently acquired *local* worker slots.
    max_remote_workers:
        Maximum number of concurrently acquired *remote* worker slots.
    """

    def __init__(
        self,
        max_local_workers: int = 1,
        max_remote_workers: int = 1,
    ) -> None:
        self._max: Dict[str, int] = {
            "local": max_local_workers,
            "remote": max_remote_workers,
        }

        # Semaphores limit overall concurrency per backend type.
        self._semaphores: Dict[str, asyncio.Semaphore] = {
            "local": asyncio.Semaphore(max_local_workers),
            "remote": asyncio.Semaphore(max_remote_workers),
        }

        # All registered slots keyed by slot_id.
        self._slots: Dict[str, WorkerSlot] = {}

        # Protects internal mutations.
        self._lock = asyncio.Lock()

        # Per-backend priority waiter queues.
        self._waiters: Dict[str, List[_PriorityWaiter]] = {
            "local": [],
            "remote": [],
        }

    # -- slot registration --------------------------------------------------

    def register_slot(self, slot: WorkerSlot) -> None:
        """Add a worker slot to the pool.

        Parameters
        ----------
        slot:
            The :class:`WorkerSlot` to register.  Its ``backend_type`` must
            be ``"local"`` or ``"remote"``.

        Raises
        ------
        ValueError
            If the backend type is unsupported or a slot with the same
            ``slot_id`` is already registered.
        """
        if slot.backend_type not in self._semaphores:
            raise ValueError(
                f"Unsupported backend_type: {slot.backend_type!r}. "
                f"Expected one of {list(self._semaphores)}"
            )
        if slot.slot_id in self._slots:
            raise ValueError(f"Slot already registered: {slot.slot_id}")
        self._slots[slot.slot_id] = slot
        logger.info(
            "Registered worker slot %s (backend=%s, gpu=%d)",
            slot.slot_id,
            slot.backend_type,
            slot.gpu_index,
        )

    # -- acquire / release --------------------------------------------------

    async def acquire(
        self,
        backend_type: str = "local",
        priority: int = PRIORITY_INTERACTIVE,
    ) -> WorkerSlot:
        """Acquire a free worker slot, blocking until one is available.

        Higher-priority waiters (lower numeric value) are served before
        lower-priority ones.

        Parameters
        ----------
        backend_type:
            ``"local"`` or ``"remote"``.
        priority:
            Priority level (0 = interactive, 1 = batch, 2 = regression).

        Returns
        -------
        WorkerSlot
            The acquired slot with ``busy=True``.

        Raises
        ------
        ValueError
            If *backend_type* is not recognised.
        RuntimeError
            If no slots of the requested backend type have been registered.
        """
        if backend_type not in self._semaphores:
            raise ValueError(
                f"Unknown backend_type: {backend_type!r}. "
                f"Expected one of {list(self._semaphores)}"
            )

        sem = self._semaphores[backend_type]

        # Fast path: try to acquire without blocking.
        acquired = sem._value > 0  # noqa: SLF001 -- peek at semaphore value
        if not acquired:
            # Slow path: register a priority waiter and wait.
            waiter = _PriorityWaiter(priority)
            async with self._lock:
                self._waiters[backend_type].append(waiter)
                self._waiters[backend_type].sort()
            # Wait until we are the *first* waiter and a slot is free.
            while True:
                await waiter.event.wait()
                waiter.event.clear()
                async with self._lock:
                    # Only proceed if we are the highest-priority waiter.
                    queue = self._waiters[backend_type]
                    if queue and queue[0] is waiter and sem._value > 0:  # noqa: SLF001
                        queue.pop(0)
                        break
                    # Spurious wake or overtaken -- keep waiting.

        await sem.acquire()

        # Find a free slot of the matching backend type.
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        async with self._lock:
            for slot in self._slots.values():
                if slot.backend_type == backend_type and not slot.busy:
                    slot.busy = True
                    slot.current_task_id = task_id
                    logger.debug(
                        "Acquired slot %s for task %s (priority=%d)",
                        slot.slot_id,
                        task_id,
                        priority,
                    )
                    return slot

            # No registered slot is free -- this should only happen when
            # more semaphore permits exist than registered slots.  Create a
            # transient virtual slot so the caller is not stuck.
            virtual = WorkerSlot(
                backend_type=backend_type,
                busy=True,
                current_task_id=task_id,
            )
            self._slots[virtual.slot_id] = virtual
            logger.warning(
                "No free registered slot; created virtual slot %s",
                virtual.slot_id,
            )
            return virtual

    async def release(self, slot_id: str) -> None:
        """Release a previously acquired worker slot back to the pool.

        Parameters
        ----------
        slot_id:
            The ``slot_id`` of the slot to release.

        Raises
        ------
        KeyError
            If the slot id is not known.
        RuntimeError
            If the slot is not currently busy.
        """
        async with self._lock:
            slot = self._slots.get(slot_id)
            if slot is None:
                raise KeyError(f"Unknown slot_id: {slot_id}")
            if not slot.busy:
                raise RuntimeError(
                    f"Slot {slot_id} is not currently acquired"
                )

            prev_task = slot.current_task_id
            slot.busy = False
            slot.current_task_id = None

            backend = slot.backend_type
            self._semaphores[backend].release()

            logger.debug(
                "Released slot %s (was task %s)", slot_id, prev_task
            )

            # Wake the highest-priority waiter for this backend type.
            queue = self._waiters[backend]
            if queue:
                queue[0].event.set()

    # -- status -------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return current pool status per backend type.

        Returns
        -------
        dict
            Mapping of backend type to ``{"total", "busy", "free"}`` counts,
            plus an ``"all"`` aggregate.

        Example::

            {
                "local":  {"total": 2, "busy": 1, "free": 1},
                "remote": {"total": 1, "busy": 0, "free": 1},
                "all":    {"total": 3, "busy": 1, "free": 2},
            }
        """
        result: Dict[str, Dict[str, int]] = {}
        all_total = 0
        all_busy = 0

        for backend in self._semaphores:
            slots = [
                s for s in self._slots.values()
                if s.backend_type == backend
            ]
            total = len(slots)
            busy = sum(1 for s in slots if s.busy)
            free = total - busy
            result[backend] = {"total": total, "busy": busy, "free": free}
            all_total += total
            all_busy += busy

        result["all"] = {
            "total": all_total,
            "busy": all_busy,
            "free": all_total - all_busy,
        }
        return result
