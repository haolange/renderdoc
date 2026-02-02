"""
Session lifecycle management for local and remote RenderDoc replay sessions.

Provides :class:`SessionManager`, a singleton that owns every active replay
session.  Each session wraps a RenderDoc ``IReplayController`` together with
its associated ``IReplayOutput``, ``ICaptureFile``, and (for remote backends)
``IRemoteServer``.

Typical usage::

    mgr = SessionManager()
    info = await mgr.create_session(
        backend_config={"type": "local"},
        replay_config={"width": 1920, "height": 1080},
    )
    cap = await mgr.open_capture(info.session_id, "/path/to/capture.rdc")
    controller = mgr.get_controller(info.session_id)
    # ... drive the controller ...
    await mgr.close_session(info.session_id)
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from rdx.models import (
    BackendType,
    CaptureInfo,
    ErrorDetail,
    GraphicsAPI,
    SessionCapabilities,
    SessionInfo,
    _new_id,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import – renderdoc may only be available inside a replay host process
# ---------------------------------------------------------------------------


def _get_rd():
    """Return the ``renderdoc`` module, importing it on first call."""
    import renderdoc as rd
    return rd


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

_GRAPHICS_API_MAP: Dict[str, GraphicsAPI] = {
    "d3d11":    GraphicsAPI.D3D11,
    "d3d12":    GraphicsAPI.D3D12,
    "vulkan":   GraphicsAPI.VULKAN,
    "opengl":   GraphicsAPI.OPENGL,
    "opengles":  GraphicsAPI.OPENGLES,
}


def _map_graphics_api(api_props: Any) -> GraphicsAPI:
    """Translate a RenderDoc ``APIProperties.pipelineType`` to our enum."""
    try:
        raw = str(api_props.pipelineType).lower()
        for key, value in _GRAPHICS_API_MAP.items():
            if key in raw:
                return value
    except (AttributeError, TypeError):
        pass
    return GraphicsAPI.UNKNOWN


def _count_actions(actions: Any) -> int:
    """Recursively count every ``ActionDescription`` in the tree."""
    total = 0
    for action in actions:
        total += 1
        children = getattr(action, "children", None)
        if children:
            total += _count_actions(children)
    return total


def _check_status(status: Any, operation: str) -> None:
    """Raise :class:`SessionError` when *status* is not ``Succeeded``."""
    rd = _get_rd()
    if status != rd.ResultCode.Succeeded:
        raise SessionError(
            code="renderdoc_error",
            message=f"{operation} failed with status: {status}",
            details={"status": str(status), "operation": operation},
        )


# ---------------------------------------------------------------------------
# Session state container
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    """Internal mutable state for a single replay session.

    Stores every RenderDoc handle that must be kept alive for the duration
    of the session as well as book-keeping metadata.
    """

    session_id: str
    backend_type: BackendType

    # RenderDoc handles (typed as Any because the C++ types are opaque)
    controller: Any = None        # IReplayController
    output: Any = None            # IReplayOutput
    capture_file: Any = None      # ICaptureFile
    remote_server: Any = None     # IRemoteServer (remote sessions only)

    # Metadata
    capabilities: SessionCapabilities = field(default_factory=SessionCapabilities)
    capture_id: Optional[str] = None
    is_initialized: bool = False
    rdc_path: Optional[str] = None
    replay_config: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SessionError(Exception):
    """Raised when a session operation fails.

    Carries an :class:`~rdx.models.ErrorDetail` payload so callers can
    propagate structured error information to MCP clients.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.detail = ErrorDetail(code=code, message=message, details=details)


# ---------------------------------------------------------------------------
# Session Manager (singleton)
# ---------------------------------------------------------------------------

class SessionManager:
    """Singleton that owns and manages all active replay sessions.

    Every public mutating method is ``async`` so that blocking RenderDoc
    C-API calls are transparently offloaded to the default
    :mod:`asyncio` executor, keeping the event loop responsive.

    Thread-safety for the internal session dict is guaranteed by an
    :class:`asyncio.Lock`.
    """

    _instance: Optional["SessionManager"] = None
    _initialized: bool = False

    # -- Singleton plumbing -------------------------------------------------

    def __new__(cls) -> "SessionManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if SessionManager._initialized:
            return
        self._sessions: Dict[str, SessionState] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._replay_initialized: bool = False
        SessionManager._initialized = True

    @classmethod
    def reset(cls) -> None:
        """Destroy the singleton instance (intended for test harnesses)."""
        cls._instance = None
        cls._initialized = False

    # -- Executor helper ----------------------------------------------------

    @staticmethod
    async def _offload(fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a synchronous callable in the default thread-pool executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(fn, *args, **kwargs),
        )

    # -- Public API: lifecycle ----------------------------------------------

    async def create_session(
        self,
        backend_config: Dict[str, Any],
        replay_config: Dict[str, Any],
    ) -> SessionInfo:
        """Create a new replay session (local or remote).

        Parameters
        ----------
        backend_config:
            Must contain ``"type"`` (``"local"`` or ``"remote"``).
            Remote backends additionally require ``"host"`` and optionally
            ``"port"`` (default 38920).
        replay_config:
            Optional keys: ``"width"`` and ``"height"`` for the headless
            output surface (default 1920x1080).

        Returns
        -------
        SessionInfo
            Metadata describing the newly created session.

        Raises
        ------
        SessionError
            If the RenderDoc subsystem cannot be initialised or the remote
            connection cannot be established.
        """
        async with self._lock:
            session_id = _new_id("sess")
            backend_type_str = backend_config.get("type", "local")
            backend_type = (
                BackendType.REMOTE
                if backend_type_str == "remote"
                else BackendType.LOCAL
            )

            state = SessionState(
                session_id=session_id,
                backend_type=backend_type,
                replay_config=dict(replay_config),
            )

            try:
                if backend_type == BackendType.LOCAL:
                    await self._init_local(state)
                else:
                    await self._init_remote(state, backend_config)
            except SessionError:
                raise
            except Exception as exc:
                raise SessionError(
                    code="session_create_failed",
                    message=f"Failed to create {backend_type.value} session: {exc}",
                    details={"original_error": str(exc)},
                ) from exc

            state.is_initialized = True
            state.capabilities.remote = (backend_type == BackendType.REMOTE)
            self._sessions[session_id] = state

            logger.info(
                "Created %s session %s", backend_type.value, session_id,
            )
            return SessionInfo(
                session_id=session_id,
                backend_type=backend_type,
                capabilities=state.capabilities,
                created_at=state.created_at,
            )

    async def open_capture(
        self,
        session_id: str,
        rdc_path: str,
    ) -> CaptureInfo:
        """Open an ``.rdc`` capture file within an existing session.

        Sets up the ``IReplayController`` and a headless ``IReplayOutput``,
        detects the graphics API and driver details, and counts the total
        number of actions in the capture.

        Parameters
        ----------
        session_id:
            An active session previously created via :meth:`create_session`.
        rdc_path:
            Filesystem path to the ``.rdc`` capture file.

        Returns
        -------
        CaptureInfo
            Metadata about the opened capture.

        Raises
        ------
        SessionError
            If the capture file cannot be opened or the replay controller
            cannot be created.
        """
        state = self._require_session(session_id)

        try:
            if state.backend_type == BackendType.LOCAL:
                await self._open_local_capture(state, rdc_path)
            else:
                await self._open_remote_capture(state, rdc_path)
        except SessionError:
            raise
        except Exception as exc:
            raise SessionError(
                code="capture_open_failed",
                message=f"Failed to open capture {rdc_path}: {exc}",
                details={"rdc_path": rdc_path, "original_error": str(exc)},
            ) from exc

        # -- Harvest metadata from the live controller ----------------------
        capture_id = _new_id("cap")
        state.capture_id = capture_id
        state.rdc_path = rdc_path

        api = GraphicsAPI.UNKNOWN
        driver_name = ""
        driver_version = ""
        total_events = 0

        try:
            props = await self._offload(state.controller.GetAPIProperties)
            api = _map_graphics_api(props)
            state.capabilities.api = api
            driver_name = str(getattr(props, "localRenderer", ""))
            if hasattr(props, "driverVersion"):
                driver_version = str(props.driverVersion)
        except Exception as exc:
            logger.warning("Could not read API properties: %s", exc)

        try:
            actions = await self._offload(state.controller.GetRootActions)
            total_events = _count_actions(actions)
        except Exception as exc:
            logger.warning("Could not count actions: %s", exc)

        info = CaptureInfo(
            capture_id=capture_id,
            session_id=session_id,
            rdc_path=rdc_path,
            api=api,
            driver_name=driver_name,
            driver_version=driver_version,
            total_events=total_events,
        )
        logger.info(
            "Opened capture %s (%s, %d events) in session %s",
            capture_id,
            api.value,
            total_events,
            session_id,
        )
        return info

    async def close_session(self, session_id: str) -> None:
        """Tear down a session and release all associated resources.

        Shuts down the replay output, controller, capture file, and (for
        remote sessions) the server connection.  When the last local session
        is closed the global RenderDoc replay subsystem is also shut down.

        Raises
        ------
        SessionError
            If the session does not exist.
        """
        async with self._lock:
            state = self._sessions.pop(session_id, None)

        if state is None:
            raise SessionError(
                code="session_not_found",
                message=f"No session with id {session_id}",
            )

        await self._cleanup(state)
        logger.info("Closed session %s", session_id)

    # -- Public API: accessors ----------------------------------------------

    def get_controller(self, session_id: str) -> Any:
        """Return the ``IReplayController`` for *session_id*.

        Raises
        ------
        SessionError
            If the session does not exist or no capture has been opened yet.
        """
        state = self._require_session(session_id)
        if state.controller is None:
            raise SessionError(
                code="no_controller",
                message=(
                    f"Session {session_id} has no active replay controller. "
                    "Open a capture first."
                ),
            )
        return state.controller

    def get_output(self, session_id: str) -> Any:
        """Return the ``IReplayOutput`` for *session_id*.

        Raises
        ------
        SessionError
            If the session does not exist or no capture has been opened yet.
        """
        state = self._require_session(session_id)
        if state.output is None:
            raise SessionError(
                code="no_output",
                message=(
                    f"Session {session_id} has no replay output. "
                    "Open a capture first."
                ),
            )
        return state.output

    def get_session(self, session_id: str) -> SessionState:
        """Return the full :class:`SessionState` for *session_id*.

        Raises
        ------
        SessionError
            If the session does not exist.
        """
        return self._require_session(session_id)

    def list_sessions(self) -> List[SessionInfo]:
        """Return a snapshot list of all active sessions."""
        return [
            SessionInfo(
                session_id=s.session_id,
                backend_type=s.backend_type,
                capabilities=s.capabilities,
                created_at=s.created_at,
            )
            for s in self._sessions.values()
        ]

    # -- Internal: session lookup -------------------------------------------

    def _require_session(self, session_id: str) -> SessionState:
        """Retrieve a session or raise :class:`SessionError`."""
        state = self._sessions.get(session_id)
        if state is None:
            raise SessionError(
                code="session_not_found",
                message=f"No session with id {session_id}",
            )
        return state

    # -- Internal: initialisation -------------------------------------------

    async def _init_local(self, state: SessionState) -> None:
        """Initialise the global RenderDoc replay subsystem (once)."""
        if not self._replay_initialized:
            rd = _get_rd()
            await self._offload(
                rd.InitialiseReplay, rd.GlobalEnvironment(), [],
            )
            self._replay_initialized = True
            logger.debug("RenderDoc replay subsystem initialised")

    async def _init_remote(
        self,
        state: SessionState,
        backend_config: Dict[str, Any],
    ) -> None:
        """Establish a remote server connection."""
        rd = _get_rd()
        host = backend_config.get("host", "localhost")
        port = backend_config.get("port")
        url = f"{host}:{port}" if port else str(host)

        status, remote = await self._offload(
            rd.CreateRemoteServerConnection, url,
        )
        _check_status(status, f"CreateRemoteServerConnection({url})")
        state.remote_server = remote
        logger.debug("Connected to remote RenderDoc server at %s", url)

    # -- Internal: capture opening ------------------------------------------

    async def _open_local_capture(
        self,
        state: SessionState,
        rdc_path: str,
    ) -> None:
        """Open a capture file locally and create the replay controller."""
        rd = _get_rd()

        # 1. Open the capture file handle
        cap = await self._offload(rd.OpenCaptureFile)
        status = await self._offload(cap.OpenFile, rdc_path, "", None)
        _check_status(status, f"OpenFile({rdc_path})")
        state.capture_file = cap

        # 2. Create the replay controller
        status, controller = await self._offload(
            cap.OpenCapture, rd.ReplayOptions(), None,
        )
        _check_status(status, "OpenCapture")
        state.controller = controller

        # 3. Create a headless replay output
        await self._create_headless_output(state, controller)

    async def _open_remote_capture(
        self,
        state: SessionState,
        rdc_path: str,
    ) -> None:
        """Open a capture file on the remote server."""
        rd = _get_rd()

        if state.remote_server is None:
            raise SessionError(
                code="no_remote_server",
                message="Remote session has no active server connection",
            )

        proxy_id = 0
        status, controller = await self._offload(
            state.remote_server.OpenCapture,
            proxy_id,
            rdc_path,
            rd.ReplayOptions(),
            None,
        )
        _check_status(status, f"remote.OpenCapture({rdc_path})")
        state.controller = controller

        # Create a headless replay output
        await self._create_headless_output(state, controller)

    async def _create_headless_output(
        self,
        state: SessionState,
        controller: Any,
    ) -> None:
        """Attach a headless texture output to the replay controller."""
        rd = _get_rd()
        width = state.replay_config.get("width", 1920)
        height = state.replay_config.get("height", 1080)

        windowing_data = await self._offload(
            rd.CreateHeadlessWindowingData, width, height,
        )
        output = await self._offload(
            controller.CreateOutput,
            windowing_data,
            rd.ReplayOutputType.Texture,
        )
        state.output = output
        logger.debug(
            "Created headless output (%dx%d) for session %s",
            width, height, state.session_id,
        )

    # -- Internal: cleanup --------------------------------------------------

    async def _cleanup(self, state: SessionState) -> None:
        """Release all RenderDoc resources held by *state*.

        Errors during individual shutdown steps are logged but do not
        prevent subsequent steps from executing.
        """
        errors: List[str] = []

        # 1. Replay output
        if state.output is not None:
            try:
                await self._offload(state.output.Shutdown)
            except Exception as exc:
                errors.append(f"output.Shutdown: {exc}")
            state.output = None

        # 2. Replay controller
        if state.controller is not None:
            try:
                await self._offload(state.controller.Shutdown)
            except Exception as exc:
                errors.append(f"controller.Shutdown: {exc}")
            state.controller = None

        # 3. Capture file
        if state.capture_file is not None:
            try:
                await self._offload(state.capture_file.CloseFile)
            except Exception as exc:
                errors.append(f"capture_file.CloseFile: {exc}")
            state.capture_file = None

        # 4. Remote server connection
        if state.remote_server is not None:
            try:
                await self._offload(state.remote_server.ShutdownConnection)
            except Exception as exc:
                errors.append(f"remote.ShutdownConnection: {exc}")
            state.remote_server = None

        # 5. Global replay subsystem (only when the last local session closes)
        if state.backend_type == BackendType.LOCAL:
            remaining_local = any(
                s.backend_type == BackendType.LOCAL
                for s in self._sessions.values()
            )
            if not remaining_local and self._replay_initialized:
                try:
                    rd = _get_rd()
                    await self._offload(rd.ShutdownReplay)
                    self._replay_initialized = False
                    logger.debug("RenderDoc replay subsystem shut down")
                except Exception as exc:
                    errors.append(f"ShutdownReplay: {exc}")

        if errors:
            logger.warning(
                "Errors during cleanup of session %s: %s",
                state.session_id,
                "; ".join(errors),
            )
