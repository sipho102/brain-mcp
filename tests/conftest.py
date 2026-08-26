from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from brain_mcp.vault import VaultIndex

CONVENTIONS_TEXT = """\
# Vault conventions

This file is the authoritative source for frontmatter enums.

## Frontmatter fields

### `type`

Valid values: `note`, `project`, `area`, `resource`, `person`

### `status`

Valid values: `inbox`, `active`, `done`, `archived`

### `domain`

Valid values: `personal`, `work`, `health`, `finance`
"""


def write_note(
    root: Path,
    rel_path: str,
    *,
    uid: str | None,
    title: str | None,
    type_: str | None = "note",
    status: str | None = "active",
    domain: str | None = "personal",
    tags: list[str] | None = None,
    created: str | None = "2026-01-01 09:00:00",
    updated: str | None = "2026-01-01 09:00:00",
    aliases: list[str] | None = None,
    paperless: list[int] | None = None,
    body: str = "",
    extra_frontmatter: dict | None = None,
) -> Path:
    fm: dict = {}
    if uid is not None:
        fm["uid"] = uid
    if title is not None:
        fm["title"] = title
    if type_ is not None:
        fm["type"] = type_
    if status is not None:
        fm["status"] = status
    if domain is not None:
        fm["domain"] = domain
    if tags is not None:
        fm["tags"] = tags
    if created is not None:
        fm["created"] = created
    if updated is not None:
        fm["updated"] = updated
    if aliases is not None:
        fm["aliases"] = aliases
    if paperless is not None:
        fm["paperless"] = paperless
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
        "10-projects",
        "20-areas/homelab",
        "30-resources",
        "40-archive",
        "90-meta/templates",
        "90-meta/userscripts",
        "90-meta/reports",
        ".obsidian",
        ".claude",
        ".trash",
        "assets",
    ]:
        (root / d).mkdir(parents=True, exist_ok=True)

    (root / "90-meta" / "CONVENTIONS.md").write_text(CONVENTIONS_TEXT, encoding="utf-8")
    (root / "90-meta" / "triage.md").write_text(
        "---\nuid: 11111111-1111-4111-8111-111111111111\ntitle: Triage\ntype: note\n"
        "status: active\ndomain: personal\ntags: []\ncreated: 2026-01-01 09:00:00\n"
        "updated: 2026-01-01 09:00:00\n---\n\nTriage notes.\n",
        encoding="utf-8",
    )

    # Two real notes, one linking to the other (plain wikilink).
    write_note(
        root,
        "20-areas/homelab/_index.md",
        uid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Homelab",
        type_="area",
        status="active",
        domain="personal",
        tags=["infra"],
        created="2026-01-01 09:00:00",
        updated="2026-01-02 10:30:15",
        body="The homelab area. See [[Router Notes]] and [[nonexistent target]].",
    )
    write_note(
        root,
        "20-areas/homelab/router-notes.md",
        uid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        title="Router Notes",
        type_="note",
        status="active",
        domain="personal",
        tags=["infra", "networking"],
        aliases=["Router"],
        created="2026-01-02 08:00:00",
        updated="2026-01-02 10:30:20",
        body="Notes about the router. Back to [[Homelab]].",
    )
    # A note that links via its alias.
    write_note(
        root,
        "30-resources/networking-101.md",
        uid="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        title="Networking 101",
        type_="resource",
        status="active",
        domain="personal",
        tags=["networking"],
        created="2026-01-03 09:00:00",
        updated="2026-01-03 09:00:00",
        body="Background reading, see [[Router|the router setup]].",
    )
    # Two notes updated in the same minute, different seconds, for sort stability.
    write_note(
        root,
        "10-projects/project-a.md",
        uid="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        title="Project A",
        type_="project",
        status="active",
        domain="work",
        tags=["urgent"],
        created="2026-01-04 09:00:00",
        updated="2026-01-04 09:05:10",
        body="Project A body, contains the word beacon.",
    )
    write_note(
        root,
        "10-projects/project-b.md",
        uid="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        title="Project B",
        type_="project",
        status="active",
        domain="work",
        tags=["urgent"],
        created="2026-01-04 09:00:00",
        updated="2026-01-04 09:05:40",
        body="Project B body, also contains the word beacon.",
    )
    # paperless doc IDs present.
    write_note(
        root,
        "40-archive/old-note.md",
        uid="ffffffff-ffff-4fff-8fff-ffffffffffff",
        title="Old Note",
        type_="note",
        status="archived",
        domain="personal",
        tags=[],
        created="2025-01-01 09:00:00",
        updated="2025-06-01 09:00:00",
        paperless=[42, 43],
        body="Archived note.",
    )

    # Note with missing optional/required fields but still valid YAML.
    write_note(
        root,
        "00-inbox/missing-fields.md",
        uid="12345678-1234-4123-8123-123456789012",
        title="Missing Fields",
        type_=None,
        status="inbox",
        domain=None,
        tags=None,
        created="2026-01-05 09:00:00",
        updated="2026-01-05 09:00:00",
        body="This note is missing type and domain.",
    )

    # Malformed YAML frontmatter: must be skipped, not crash indexing.
    (root / "00-inbox" / "broken.md").write_text(
        "---\nuid: [unclosed\ntitle: Broken\n---\n\nBroken body.\n",
        encoding="utf-8",
    )

    # Templater syntax in frontmatter position, in the excluded templates dir.
    (root / "90-meta" / "templates" / "note-template.md").write_text(
        "---\nuid: <% tp.user.uuid() %>\ntitle: <% tp.file.title %>\n"
        "type: note\nstatus: inbox\ndomain: personal\ntags: []\n"
        "created: <% tp.date.now() %>\nupdated: <% tp.date.now() %>\n---\n\n"
        "Template body.\n",
        encoding="utf-8",
    )

    # Non-.md files that must be ignored silently.
    (root / "90-meta" / "some-view.base").write_text("{}", encoding="utf-8")
    (root / "00-inbox" / ".gitkeep").write_text("", encoding="utf-8")
    (root / "90-meta" / "userscripts" / "helper.js").write_text("// js", encoding="utf-8")
    (root / "90-meta" / "reports" / "lint-report.md").write_text(
        "---\nuid: rep-not-a-real-uuid\ntitle: Lint Report\n---\n\nGenerated.\n",
        encoding="utf-8",
    )
    (root / "assets" / "photo.png").write_bytes(b"\x89PNG\r\n")


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
