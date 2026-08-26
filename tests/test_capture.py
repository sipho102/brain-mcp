from __future__ import annotations

import re
from pathlib import Path

import frontmatter
import pytest

from brain_mcp import capture as capture_mod
from brain_mcp.vault import InvalidEnumError, VaultIndex

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def test_capture_creates_note_in_inbox(vault: VaultIndex, vault_root: Path):
    result = capture_mod.capture_note(
        vault,
        title="A New Idea",
        body="Some body text.",
        domain="personal",
        tags=["idea"],
        source="conversation with Claude",
        links=["Homelab"],
    )
    assert result["path"].startswith("00-inbox/")
    assert UUID4_RE.match(result["uid"])

    full = vault_root / result["path"]
    assert full.exists()

    post = frontmatter.load(full)
    assert post.metadata["uid"] == result["uid"]
    assert post.metadata["title"] == "A New Idea"
    assert post.metadata["type"] == "note"
    assert post.metadata["status"] == "inbox"
    assert post.metadata["domain"] == "personal"
    assert post.metadata["tags"] == ["idea"]
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", post.metadata["created"])
    assert "## Source" in post.content
    assert "conversation with Claude" in post.content
    assert "## Related" in post.content
    assert "[[Homelab]]" in post.content


def test_capture_default_domain_is_personal(vault: VaultIndex):
    result = capture_mod.capture_note(
        vault, title="No Domain Given", body="body", domain=None, tags=None, source=None, links=None
    )
    note_path = capture_mod.Path(vault.root) / result["path"]
    post = frontmatter.load(note_path)
    assert post.metadata["domain"] == "personal"


def test_capture_rejects_invalid_domain(vault: VaultIndex):
    with pytest.raises(InvalidEnumError):
        capture_mod.capture_note(
            vault, title="Bad Domain", body="body", domain="not-a-domain", tags=None, source=None, links=None
        )


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
        vault, title="Same Title", body="1", domain=None, tags=None, source=None, links=None
    )
    r2 = capture_mod.capture_note(
        vault, title="Same Title", body="2", domain=None, tags=None, source=None, links=None
    )
    r3 = capture_mod.capture_note(
        vault, title="Same Title", body="3", domain=None, tags=None, source=None, links=None
    )
    assert r1["path"] == "00-inbox/2026-03-01-same-title.md"
    assert r2["path"] == "00-inbox/2026-03-01-same-title-2.md"
    assert r3["path"] == "00-inbox/2026-03-01-same-title-3.md"
    assert r1["uid"] != r2["uid"] != r3["uid"]


def test_capture_never_overwrites(vault: VaultIndex, vault_root: Path, monkeypatch):
    import datetime as real_datetime

    inbox = vault_root / "00-inbox"
    inbox.mkdir(exist_ok=True)
    existing = inbox / "2026-01-01-pre-existing.md"
    existing.write_text("---\nuid: x\n---\n\noriginal\n", encoding="utf-8")

    _freeze_capture_clock(monkeypatch, real_datetime.datetime(2026, 1, 1, 12, 0, 0))

    capture_mod.capture_note(
        vault, title="pre existing", body="new", domain=None, tags=None, source=None, links=None
    )

    assert existing.read_text(encoding="utf-8") == "---\nuid: x\n---\n\noriginal\n"


def test_capture_refuses_traversal_via_malicious_title(vault: VaultIndex, vault_root: Path):
    result = capture_mod.capture_note(
        vault,
        title="../../../etc/passwd",
        body="pwned?",
        domain=None,
        tags=None,
        source=None,
        links=None,
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
        vault, title=title, body="body", domain=None, tags=None, source=None, links=None
    )
    assert expected_slug in result["path"]
