"""RDX daemon server using Windows named pipes."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from multiprocessing.connection import Listener
from typing import Any, Dict

from rdx.server import dispatch_operation, runtime_shutdown, runtime_startup

logger = logging.getLogger("rdx.daemon")


class DaemonRuntime:
    def __init__(self, *, pipe_name: str, token: str) -> None:
        self.pipe_name = pipe_name
        self.token = token
        self.address = rf"\\.\pipe\{pipe_name}"
        self.running = True
        self.state: Dict[str, Any] = {
            "pipe_name": pipe_name,
            "session_id": "",
            "capture_file_id": "",
            "active_event_id": 0,
        }

    def _auth(self, request: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
        if str(request.get("token", "")) != self.token:
            return False, {"ok": False, "error": {"code": "unauthorized", "message": "invalid daemon token"}}
        return True, {}

    def _status_payload(self) -> Dict[str, Any]:
        return {"ok": True, "result": {"running": self.running, "state": dict(self.state)}}

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(request, dict):
            return {"ok": False, "error": {"code": "bad_request", "message": "request must be an object"}}
        authorized, auth_error = self._auth(request)
        if not authorized:
            return auth_error

        method = str(request.get("method") or "").strip().lower()
        logger.info("daemon.request method=%s", method)
        params = request.get("params")
        if not isinstance(params, dict):
            params = {}

        if method == "ping":
            return {"ok": True, "result": {"pong": True}}
        if method == "status":
            return self._status_payload()
        if method == "shutdown":
            self.running = False
            return {"ok": True, "result": {"stopping": True}}
        if method == "set_state":
            self.state.update(dict(params))
            return {"ok": True, "result": {"state": dict(self.state)}}
        if method == "get_state":
            return {"ok": True, "result": {"state": dict(self.state)}}
        if method == "exec":
            operation = str(params.get("operation") or "").strip()
            args = params.get("args")
            if not isinstance(args, dict):
                args = {}
            transport = str(params.get("transport") or "daemon")
            remote = bool(params.get("remote", False))
            arg_keys = ",".join(sorted(args.keys())) if args else "-"
            logger.info(
                "daemon.exec op=%s transport=%s remote=%s arg_keys=%s",
                operation,
                transport,
                remote,
                arg_keys,
            )
            result = asyncio.run(dispatch_operation(operation, args, transport=transport, remote=remote))
            if isinstance(result, dict):
                meta = result.get("meta", {})
                logger.info(
                    "daemon.exec.done op=%s trace_id=%s ok=%s duration_ms=%s",
                    operation,
                    (meta.get("trace_id") if isinstance(meta, dict) else ""),
                    bool(result.get("ok")),
                    (meta.get("duration_ms") if isinstance(meta, dict) else None),
                )
            return {"ok": True, "result": result}
        return {"ok": False, "error": {"code": "unknown_method", "message": f"unknown method: {method}"}}

    def serve_forever(self) -> int:
        listener = Listener(address=self.address, family="AF_PIPE")
        logger.info("daemon listening on %s", self.address)
        try:
            while self.running:
                conn = listener.accept()
                try:
                    request = conn.recv()
                    conn.send(self.handle_request(request))
                except Exception as exc:  # noqa: BLE001
                    conn.send({"ok": False, "error": {"code": "daemon_error", "message": str(exc)}})
                finally:
                    conn.close()
        finally:
            listener.close()
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="RDX named-pipe daemon")
    parser.add_argument("--pipe-name", required=True, help="Named pipe suffix (without \\\\.\\pipe\\)")
    parser.add_argument("--token", required=True, help="Authentication token")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    daemon = DaemonRuntime(pipe_name=str(args.pipe_name), token=str(args.token))

    def _stop(*_a: Any) -> None:
        daemon.running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    asyncio.run(runtime_startup())
    try:
        raise SystemExit(daemon.serve_forever())
    finally:
        asyncio.run(runtime_shutdown())


if __name__ == "__main__":
    main()
