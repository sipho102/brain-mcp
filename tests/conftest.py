from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from brain_mcp.vault import VaultIndex

CONVENTIONS_TEXT = """\
# Vault conventions

This file is the authoritative source for frontmatter enums.

## Enums

**type:** `document`, `memory`, `journal`, `price-list`

- `document` — a durable fact, decision, procedure, or piece of know-how.
- `memory` — a small dated observation.

**status:** `current`, `superseded`

- `current` — the live version of this fact.

**topic:** `inventx`, `homelab`, `gaming`, `personal`, `finance`, `home`

`topic` is the organising axis. It matters more than any other placement
question.
"""


def write_note(
    root: Path,
    rel_path: str,
    *,
    title: str | None,
    type_: str | None = "document",
    status: str | None = "current",
    topic: str | None = "personal",
    when_to_open: str | None = "for testing",
    created: str | None = "2026-01-01",
    updated: str | None = "2026-01-01",
    source: str | None = None,
    supersedes: str | None = None,
    kind: str | None = None,
    confidence: str | None = None,
    body: str = "",
    extra_frontmatter: dict | None = None,
) -> Path:
    fm: dict = {}
    if title is not None:
        fm["title"] = title
    if type_ is not None:
        fm["type"] = type_
    if status is not None:
        fm["status"] = status
    if topic is not None:
        fm["topic"] = topic
    if when_to_open is not None:
        fm["when-to-open"] = when_to_open
    if created is not None:
        fm["created"] = created
    if updated is not None:
        fm["updated"] = updated
    if source is not None:
        fm["source"] = source
    if supersedes is not None:
        fm["supersedes"] = supersedes
    if kind is not None:
        fm["kind"] = kind
    if confidence is not None:
        fm["confidence"] = confidence
    if extra_frontmatter:
        fm.update(extra_frontmatter)

    fm_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
    text = f"---\n{fm_text}---\n\n{body}\n"

    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build_fixture_vault(root: Path) -> None:
    for d in [
        "00-inbox",
        "documents/homelab",
        "documents/personal",
        "memories",
        "journal/archive",
        "90-meta/templates",
        "90-meta/userscripts",
        "90-meta/reports",
        ".obsidian",
        ".claude",
        ".trash",
        "attachments",
    ]:
        (root / d).mkdir(parents=True, exist_ok=True)

    (root / "90-meta" / "CONVENTIONS.md").write_text(CONVENTIONS_TEXT, encoding="utf-8")
    (root / "90-meta" / "triage.md").write_text(
        "---\ntitle: Triage\ntype: document\nstatus: current\ntopic: personal\n"
        "when-to-open: when triaging inbox notes\ncreated: 2026-01-01\n"
        "updated: 2026-01-01\n---\n\nTriage notes.\n",
        encoding="utf-8",
    )

    # Two real notes, one linking to the other (plain wikilink).
    write_note(
        root,
        "documents/homelab/homelab.md",
        title="Homelab",
        type_="document",
        status="current",
        topic="homelab",
        when_to_open="when planning homelab infrastructure",
        created="2026-01-01",
        updated="2026-01-02",
        body="The homelab area. See [[Router Notes]] and [[nonexistent target]].",
    )
    write_note(
        root,
        "documents/homelab/router-notes.md",
        title="Router Notes",
        type_="document",
        status="current",
        topic="homelab",
        when_to_open="when configuring the router",
        created="2026-01-02",
        updated="2026-01-02",
        body="Notes about the router. Back to [[Homelab]].",
    )
    # A note that links via its title.
    write_note(
        root,
        "documents/personal/networking-101.md",
        title="Networking 101",
        type_="document",
        status="current",
        topic="personal",
        when_to_open="when learning networking basics",
        created="2026-01-03",
        updated="2026-01-03",
        body="Background reading, see [[Router Notes|the router setup]].",
    )
    # Two notes updated on the same date, for sort-stability tie-breaking.
    write_note(
        root,
        "documents/personal/project-a.md",
        title="Project A",
        type_="document",
        status="current",
        topic="finance",
        when_to_open="when reviewing project A",
        created="2026-01-04",
        updated="2026-01-04",
        body="Project A body, contains the word beacon.",
    )
    write_note(
        root,
        "documents/personal/project-b.md",
        title="Project B",
        type_="document",
        status="current",
        topic="finance",
        when_to_open="when reviewing project B",
        created="2026-01-04",
        updated="2026-01-04",
        body="Project B body, also contains the word beacon.",
    )
    # source field present (paperless reference).
    write_note(
        root,
        "documents/personal/old-note.md",
        title="Old Note",
        type_="document",
        status="superseded",
        topic="personal",
        when_to_open="historical reference only",
        created="2025-01-01",
        updated="2025-06-01",
        source="paperless:42",
        body="Superseded note.",
    )
    # A memory note with kind/confidence.
    write_note(
        root,
        "memories/inference-example.md",
        title="Inference Example",
        type_="memory",
        status="current",
        topic="homelab",
        when_to_open="when checking assumptions about the homelab",
        created="2026-01-05",
        updated="2026-01-05",
        source="conversation",
        kind="inference",
        confidence="medium",
        body="Probably true based on observed behavior.",
    )

    # Note with missing optional/required fields but still valid YAML.
    write_note(
        root,
        "00-inbox/missing-fields.md",
        title="Missing Fields",
        type_=None,
        status="current",
        topic=None,
        when_to_open=None,
        created="2026-01-05",
        updated="2026-01-05",
        body="This note is missing type and topic.",
    )

    # Malformed YAML frontmatter: must be skipped, not crash indexing.
    (root / "00-inbox" / "broken.md").write_text(
        "---\ntitle: [unclosed\n---\n\nBroken body.\n",
        encoding="utf-8",
    )

    # Templater syntax in frontmatter position, in the excluded templates dir.
    (root / "90-meta" / "templates" / "note-template.md").write_text(
        "---\ntitle: <% tp.file.title %>\n"
        "type: document\nstatus: current\ntopic: personal\n"
        "when-to-open: <% tp.user.trigger() %>\n"
        "created: <% tp.date.now() %>\nupdated: <% tp.date.now() %>\n---\n\n"
        "Template body.\n",
        encoding="utf-8",
    )

    # Generated hub — never a real note.
    (root / "hub.md").write_text("# Hub\n\nGenerated index.\n", encoding="utf-8")

    # journal/archive is excluded even though it holds real-shaped notes.
    write_note(
        root,
        "journal/archive/2025-12-31.md",
        title="Old Journal Entry",
        type_="journal",
        status="current",
        topic="personal",
        when_to_open="never — archived journal",
        created="2025-12-31",
        updated="2025-12-31",
        body="Archived journal entry.",
    )

    # Non-.md files that must be ignored silently.
    (root / "90-meta" / "some-view.base").write_text("{}", encoding="utf-8")
    (root / "00-inbox" / ".gitkeep").write_text("", encoding="utf-8")
    (root / "90-meta" / "userscripts" / "helper.js").write_text("// js", encoding="utf-8")
    (root / "90-meta" / "reports" / "lint-report.md").write_text(
        "---\ntitle: Lint Report\n---\n\nGenerated.\n",
        encoding="utf-8",
    )
    (root / "attachments" / "photo.png").write_bytes(b"\x89PNG\r\n")


@pytest.fixture()
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    build_fixture_vault(root)
    return root


@pytest.fixture()
def vault(vault_root: Path) -> VaultIndex:
    idx = VaultIndex(vault_root)
    idx.build_index()
    return idx


@pytest.fixture()
def note_writer():
    return write_note
