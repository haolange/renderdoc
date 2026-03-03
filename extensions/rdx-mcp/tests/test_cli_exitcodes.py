from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = Path(tempfile.gettempdir()) / "rdx_cli_exitcodes"
TMP_DIR.mkdir(parents=True, exist_ok=True)


def _run_cli(*parts: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("RDX_LOG_LEVEL", "WARNING")
    return subprocess.run(
        [sys.executable, "-m", "rdx.cli", *parts],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _write_png(path: Path, rgba: tuple[int, int, int, int]) -> None:
    pil = pytest.importorskip("PIL.Image")
    img = pil.new("RGBA", (2, 2), rgba)
    img.save(path)


def _parse_stdout_json(proc: subprocess.CompletedProcess[str]) -> dict:
    text = (proc.stdout or "").strip()
    assert text, f"empty stdout (stderr={proc.stderr})"
    return json.loads(text)


def test_assert_image_exit_codes_are_0_1_2() -> None:
    a = TMP_DIR / "a.png"
    b = TMP_DIR / "b.png"
    missing = TMP_DIR / "missing.png"
    _write_png(a, (255, 0, 0, 255))
    _write_png(b, (255, 0, 0, 255))

    ok_proc = _run_cli(
        "assert",
        "image",
        "--image-a",
        str(a),
        "--image-b",
        str(b),
        "--mse-max",
        "0.0",
    )
    assert ok_proc.returncode == 0
    ok_payload = _parse_stdout_json(ok_proc)
    assert ok_payload.get("ok") is True
    assert ok_payload.get("data", {}).get("pass") is True

    _write_png(b, (0, 0, 255, 255))
    fail_proc = _run_cli(
        "assert",
        "image",
        "--image-a",
        str(a),
        "--image-b",
        str(b),
        "--mse-max",
        "0.0",
    )
    assert fail_proc.returncode == 1
    fail_payload = _parse_stdout_json(fail_proc)
    assert fail_payload.get("ok") is True
    assert fail_payload.get("data", {}).get("pass") is False

    err_proc = _run_cli(
        "assert",
        "image",
        "--image-a",
        str(missing),
        "--image-b",
        str(b),
        "--mse-max",
        "0.0",
    )
    assert err_proc.returncode == 2
    err_payload = _parse_stdout_json(err_proc)
    assert err_payload.get("ok") is False
    assert isinstance(err_payload.get("error"), dict)
