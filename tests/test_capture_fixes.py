"""Acceptance checks for the 2026-10 capture fixes and capture_update."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import frontmatter
import pytest

from brain_mcp import capture as capture_mod
from brain_mcp.vault import InvalidEnumError, PathTraversalError, VaultIndex

BASE = dict(topic="homelab", when_to_open="when testing capture", source="conversation")


def _capture(vault, title, body="body", **kw):
    return capture_mod.capture_note(vault, title=title, body=body, **{**BASE, **kw})


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "00-inbox" not in p.parts
    }


def test_long_title_slug_truncates_on_word(vault: VaultIndex):
    title = ("tooling blockers around the rebar flag rollout " * 4)[:120]
    result = _capture(vault, title)
    slug = result["filename"][len("YYYY-MM-DD-"):]
    assert len(slug) <= 80
    assert ("tooling-blockers-around-the-rebar-flag-rollout-" * 3).startswith(slug + "-")
    assert result["path"] == f"00-inbox/{result['filename']}.md"


def test_trailing_date_not_repeated(vault: VaultIndex):
    result = _capture(vault, "Router reboot schedule (2026-10-01)")
    assert result["filename"].endswith("-router-reboot-schedule")


def test_duplicate_title_rejected(vault: VaultIndex, vault_root: Path):
    first = _capture(vault, "Rebar flag rollout")
    before = set((vault_root / "00-inbox").iterdir())
    with pytest.raises(capture_mod.CaptureError, match=first["filename"]) as exc:
        _capture(vault, "  rebar FLAG rollout! ")
    assert "capture_update" in str(exc.value)
    assert set((vault_root / "00-inbox").iterdir()) == before


def test_literal_newlines_unescaped(vault: VaultIndex, vault_root: Path):
    result = _capture(vault, "Escaped body", body="line one\\nline two\\n\\tindented")
    post = frontmatter.load(vault_root / result["path"])
    assert post.content == "line one\nline two\n\tindented"


def test_block_style_frontmatter_in_order(vault: VaultIndex, vault_root: Path):
    result = _capture(vault, "Ordered", type="memory", kind="inference", confidence="low")
    head = (vault_root / result["path"]).read_text().split("---")[1]
    keys = [line.split(":")[0] for line in head.strip().splitlines()]
    assert keys == ["type", "title", "topic", "when-to-open", "created", "updated",
                    "status", "kind", "confidence", "source"]
    assert "{" not in head


def test_schema_rejections(vault: VaultIndex, vault_root: Path):
    with pytest.raises(InvalidEnumError):
        _capture(vault, "Bad topic", topic="foo")
    with pytest.raises(capture_mod.CaptureError, match="kind"):
        _capture(vault, "Memory without kind", type="memory")
    # A legacy uid can only arrive through a frontmatter merge.
    fn = _capture(vault, "Has no uid")["filename"]
    with pytest.raises(capture_mod.CaptureError, match="uid"):
        capture_mod.update_note(vault, filename=fn, content="x", mode="append",
                                frontmatter={"uid": "abc"})


def test_update_append_bumps_updated(vault: VaultIndex, vault_root: Path):
    result = _capture(vault, "Diagnosis")
    path = vault_root / result["path"]
    text = path.read_text().replace("updated: '", "updated: '2000-01-01' #", 1)
    text = text.replace("created: '", "created: '2000-01-01' #", 1)
    path.write_text(text)

    capture_mod.update_note(vault, filename=result["filename"], content="It was DNS.", mode="append")
    post = frontmatter.load(path)
    today = datetime.now().strftime("%Y-%m-%d")
    assert f"## Update {today}\n\nIt was DNS." in post.content
    assert post.content.startswith("body")
    assert post.metadata["updated"] == today
    assert post.metadata["created"] == "2000-01-01"


def test_update_replace_merges_frontmatter(vault: VaultIndex, vault_root: Path):
    result = _capture(vault, "Replace me")
    capture_mod.update_note(vault, filename=result["filename"], content="new body", mode="replace",
                            frontmatter={"type": "memory", "kind": "evidence"})
    post = frontmatter.load(vault_root / result["path"])
    assert post.content == "new body"
    assert post.metadata["kind"] == "evidence"


@pytest.mark.parametrize("name", ["../hub", "../hub.md", "documents/x", "/etc/passwd", "does-not-exist"])
def test_update_rejects_outside_or_missing(vault: VaultIndex, name: str):
    with pytest.raises((capture_mod.CaptureError, PathTraversalError)):
        capture_mod.update_note(vault, filename=name, content="x", mode="append")


def test_no_write_outside_inbox(vault: VaultIndex, vault_root: Path):
    before = _snapshot(vault_root)
    outside = next(p for p in vault_root.rglob("*.md") if "00-inbox" not in p.parts)
    os.symlink(outside, vault_root / "00-inbox" / "sneaky.md")

    with pytest.raises(PathTraversalError):
        capture_mod.update_note(vault, filename="sneaky", content="x", mode="replace")
    for title in ("../../escape", "..", "/etc/passwd"):
        _capture(vault, title)
    assert _snapshot(vault_root) == before
