from __future__ import annotations

import asyncio
from pathlib import Path

from rdx.core.artifact_publisher import ArtifactPublisher


def test_remote_mode_falls_back_to_local_without_s3_creds(tmp_path, monkeypatch) -> None:
    payload_file = tmp_path / "big.json"
    payload_file.write_text("x" * 1024, encoding="utf-8")

    monkeypatch.setenv("RDX_ARTIFACT_MODE", "remote")
    monkeypatch.setenv("RDX_REMOTE_ARTIFACT_MIN_BYTES", "1")
    monkeypatch.delenv("RDX_S3_BUCKET", raising=False)
    monkeypatch.delenv("RDX_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("RDX_S3_SECRET_KEY", raising=False)

    publisher = ArtifactPublisher()
    artifacts = asyncio.run(
        publisher.publish_candidates(
            [{"path": str(payload_file), "type": "report_path", "metadata": {"k": "v"}}],
            remote=True,
        ),
    )
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art["storage_backend"] == "local"
    assert art["path"] == str(payload_file)
    assert art["url"] is None


def test_url_candidate_is_preserved_as_remote_artifact(monkeypatch) -> None:
    monkeypatch.setenv("RDX_ARTIFACT_MODE", "remote")
    publisher = ArtifactPublisher()
    artifacts = asyncio.run(
        publisher.publish_candidates(
            [{"url": "https://example.invalid/a.png", "type": "image_path", "metadata": {}}],
            remote=True,
        ),
    )
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art["url"] == "https://example.invalid/a.png"
    assert art["path"] is None
