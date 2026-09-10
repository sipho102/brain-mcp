from __future__ import annotations

import re
import stat
from pathlib import Path

import frontmatter
import pytest

from brain_mcp import capture as capture_mod
from brain_mcp.vault import InvalidEnumError, VaultIndex


def test_capture_creates_note_in_inbox(vault: VaultIndex, vault_root: Path):
    result = capture_mod.capture_note(
        vault,
        title="A New Idea",
        body="Some body text.",
        topic="personal",
        when_to_open="when reviewing new ideas",
        source="conversation",
        links=["Homelab"],
    )
    assert result["path"].startswith("00-inbox/")

    full = vault_root / result["path"]
    assert full.exists()

    post = frontmatter.load(full)
    assert post.metadata["title"] == "A New Idea"
    assert post.metadata["type"] == "document"
    assert post.metadata["status"] == "current"
    assert post.metadata["topic"] == "personal"
    assert post.metadata["when-to-open"] == "when reviewing new ideas"
    assert post.metadata["source"] == "conversation"
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", post.metadata["created"])
    assert "## Related" in post.content
    assert "[[Homelab]]" in post.content
    assert "uid" not in post.metadata


def test_capture_writes_group_writable_file(vault: VaultIndex, vault_root: Path):
    # The container runs as a fixed UID (99 by default); anything else that
    # edits the vault (Obsidian synced over SMB, etc.) almost certainly
    # isn't that same UID, so captured notes must be group-writable or
    # editing them elsewhere fails with EACCES.
    result = capture_mod.capture_note(
        vault, title="Permission Check", body="body", topic="personal",
        when_to_open="for testing", source="conversation",
    )
    mode = stat.S_IMODE((vault_root / result["path"]).stat().st_mode)
    assert mode == 0o664


def test_capture_default_type_is_document(vault: VaultIndex):
    result = capture_mod.capture_note(
        vault, title="No Type Given", body="body", topic="personal",
        when_to_open="for testing", source="conversation",
    )
    note_path = capture_mod.Path(vault.root) / result["path"]
    post = frontmatter.load(note_path)
    assert post.metadata["type"] == "document"


def test_capture_rejects_invalid_topic(vault: VaultIndex):
    with pytest.raises(InvalidEnumError):
        capture_mod.capture_note(
            vault, title="Bad Topic", body="body", topic="not-a-topic",
            when_to_open="for testing", source="conversation",
        )


def test_capture_rejects_invalid_type(vault: VaultIndex):
    with pytest.raises(ValueError):
        capture_mod.capture_note(
            vault, title="Bad Type", body="body", topic="personal",
            when_to_open="for testing", source="conversation", type="price-list",
        )


def test_capture_rejects_empty_when_to_open(vault: VaultIndex):
    with pytest.raises(ValueError):
        capture_mod.capture_note(
            vault, title="No trigger", body="body", topic="personal",
            when_to_open="", source="conversation",
        )


def test_capture_rejects_empty_source(vault: VaultIndex):
    with pytest.raises(ValueError):
        capture_mod.capture_note(
            vault, title="No source", body="body", topic="personal",
            when_to_open="for testing", source="",
        )


def test_capture_memory_requires_kind(vault: VaultIndex):
    with pytest.raises(ValueError):
        capture_mod.capture_note(
            vault, title="A memory", body="body", topic="personal",
            when_to_open="for testing", source="conversation", type="memory",
        )


def test_capture_memory_inference_requires_confidence(vault: VaultIndex):
    with pytest.raises(ValueError):
        capture_mod.capture_note(
            vault, title="A memory", body="body", topic="personal",
            when_to_open="for testing", source="conversation", type="memory",
            kind="inference",
        )


def test_capture_memory_with_kind_and_confidence(vault: VaultIndex, vault_root: Path):
    result = capture_mod.capture_note(
        vault, title="A memory", body="body", topic="personal",
        when_to_open="for testing", source="conversation", type="memory",
        kind="inference", confidence="high",
    )
    post = frontmatter.load(vault_root / result["path"])
    assert post.metadata["type"] == "memory"
    assert post.metadata["kind"] == "inference"
    assert post.metadata["confidence"] == "high"


def test_capture_supersedes_field(vault: VaultIndex, vault_root: Path):
    result = capture_mod.capture_note(
        vault, title="Replacement note", body="body", topic="personal",
        when_to_open="for testing", source="conversation",
        supersedes="[[old-note]]",
    )
    post = frontmatter.load(vault_root / result["path"])
    assert post.metadata["supersedes"] == "[[old-note]]"


def _freeze_capture_clock(monkeypatch, when):
    import datetime as real_datetime

    class _FixedDT(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    monkeypatch.setattr(capture_mod, "datetime", _FixedDT)


def test_capture_filename_collision_appends_suffix(vault: VaultIndex, vault_root: Path, monkeypatch):
    import datetime as real_datetime

    _freeze_capture_clock(monkeypatch, real_datetime.datetime(2026, 3, 1, 12, 0, 0))

    r1 = capture_mod.capture_note(
        vault, title="Same Title", body="1", topic="personal",
        when_to_open="for testing", source="conversation",
    )
    r2 = capture_mod.capture_note(
        vault, title="Same Title", body="2", topic="personal",
        when_to_open="for testing", source="conversation",
    )
    r3 = capture_mod.capture_note(
        vault, title="Same Title", body="3", topic="personal",
        when_to_open="for testing", source="conversation",
    )
    assert r1["path"] == "00-inbox/2026-03-01-same-title.md"
    assert r2["path"] == "00-inbox/2026-03-01-same-title-2.md"
    assert r3["path"] == "00-inbox/2026-03-01-same-title-3.md"


def test_capture_never_overwrites(vault: VaultIndex, vault_root: Path, monkeypatch):
    import datetime as real_datetime

    inbox = vault_root / "00-inbox"
    inbox.mkdir(exist_ok=True)
    existing = inbox / "2026-01-01-pre-existing.md"
    existing.write_text("---\ntitle: x\n---\n\noriginal\n", encoding="utf-8")

    _freeze_capture_clock(monkeypatch, real_datetime.datetime(2026, 1, 1, 12, 0, 0))

    capture_mod.capture_note(
        vault, title="pre existing", body="new", topic="personal",
        when_to_open="for testing", source="conversation",
    )

    assert existing.read_text(encoding="utf-8") == "---\ntitle: x\n---\n\noriginal\n"


def test_capture_refuses_traversal_via_malicious_title(vault: VaultIndex, vault_root: Path):
    result = capture_mod.capture_note(
        vault,
        title="../../../etc/passwd",
        body="pwned?",
        topic="personal",
        when_to_open="for testing",
        source="conversation",
    )
    full = (vault_root / result["path"]).resolve()
    inbox = (vault_root / "00-inbox").resolve()
    assert inbox == full.parent
    assert inbox in full.parents or full.parent == inbox
    # nothing was written outside the vault
    assert str(full).startswith(str(vault_root.resolve()))


@pytest.mark.parametrize(
    "title,expected_slug",
    [
        ("Über Café Notes", "uber-cafe-notes"),
        ("Hello, World!!!", "hello-world"),
        ("emoji 🎉 party", "emoji-party"),
    ],
)
def test_capture_slug_matches_title(vault: VaultIndex, title: str, expected_slug: str):
    result = capture_mod.capture_note(
        vault, title=title, body="body", topic="personal",
        when_to_open="for testing", source="conversation",
    )
    assert expected_slug in result["path"]
