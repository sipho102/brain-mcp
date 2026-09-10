from __future__ import annotations

from pathlib import Path

import pytest

from brain_mcp.vault import (
    AmbiguousIdentifierError,
    InvalidEnumError,
    NoteNotFoundError,
    PathTraversalError,
    VaultIndex,
    slugify,
)


def test_indexes_real_notes(vault: VaultIndex):
    assert "documents/homelab/homelab.md" in vault.notes
    assert "90-meta/triage.md" in vault.notes
    assert "90-meta/CONVENTIONS.md" in vault.notes  # real note per spec section 5, must be findable
    assert len(vault.notes) > 0


def test_excludes_templates_userscripts_reports_and_dotfolders(vault: VaultIndex):
    for rel in vault.notes:
        assert not rel.startswith("90-meta/templates/")
        assert not rel.startswith("90-meta/userscripts/")
        assert not rel.startswith("90-meta/reports/")
        assert not rel.startswith(".obsidian/")
        assert not rel.startswith(".claude/")
        assert not rel.startswith(".trash/")
        assert not rel.startswith("attachments/")
        assert not rel.startswith("journal/archive/")
        assert rel != "hub.md"


def test_templater_syntax_note_never_appears(vault: VaultIndex):
    for note in vault.notes.values():
        assert note.path != "90-meta/templates/note-template.md"
        assert "tp.user" not in (note.when_to_open or "")


def test_non_markdown_files_skipped_silently(vault: VaultIndex):
    assert all(not p.endswith(".base") for p in vault.notes)
    assert all(not p.endswith(".js") for p in vault.notes)
    assert all(not p.endswith(".gitkeep") for p in vault.notes)


def test_malformed_frontmatter_is_skipped_not_crashed(vault: VaultIndex):
    assert "00-inbox/broken.md" not in vault.notes
    # everything else still indexed fine
    assert "00-inbox/missing-fields.md" in vault.notes


def test_missing_optional_fields_still_indexed(vault: VaultIndex):
    note = vault.notes["00-inbox/missing-fields.md"]
    assert note.type is None
    assert note.topic is None
    assert note.title == "Missing Fields"


def test_enums_parsed_from_conventions(vault: VaultIndex):
    assert set(vault.enums["type"]) == {"document", "memory", "journal", "price-list"}
    assert set(vault.enums["status"]) == {"current", "superseded"}
    assert set(vault.enums["topic"]) == {"inventx", "homelab", "gaming", "personal", "finance", "home"}


def test_conventions_text_is_full_file(vault: VaultIndex):
    assert "Vault conventions" in vault.conventions_text


def test_enums_parsed_from_bold_label_format():
    # Mirrors the real vault's actual CONVENTIONS.md shape: all three enums
    # live under one "## Enums" heading, distinguished by a bold inline
    # label rather than their own subheading, with bulleted explanations
    # (redundant backticks) below and prose after that incidentally
    # backtick-quotes an unrelated word ("`topic` is the primary...").
    from brain_mcp.vault import _extract_enum_values

    text = """\
## Enums

**type:** `document`, `memory`, `journal`, `price-list`

- `document` — durable fact, decision, procedure
- `memory` — small dated observation

**status:** `current`, `superseded`

- `current` — the live version of this fact.

**topic:** `inventx`, `homelab`, `gaming`, `personal`, `finance`, `home`

`topic` is the primary query axis. It matters more than any other placement
question.

## Linking

Use Obsidian wikilinks.
"""
    assert _extract_enum_values(text, "type") == [
        "document", "memory", "journal", "price-list",
    ]
    assert _extract_enum_values(text, "status") == ["current", "superseded"]
    # The stray `topic` backtick in the prose paragraph below the list must
    # not leak into the parsed enum values.
    assert _extract_enum_values(text, "topic") == [
        "inventx", "homelab", "gaming", "personal", "finance", "home",
    ]


def test_missing_conventions_file_fails_loudly(tmp_path: Path):
    from brain_mcp.vault import VaultError

    root = tmp_path / "empty-vault"
    root.mkdir()
    (root / "90-meta").mkdir()
    idx = VaultIndex(root)
    with pytest.raises(VaultError):
        idx.build_index()


def test_validate_enum_accepts_valid(vault: VaultIndex):
    vault.validate_enum("topic", "personal")  # no raise


def test_validate_enum_rejects_invalid(vault: VaultIndex):
    with pytest.raises(InvalidEnumError) as exc_info:
        vault.validate_enum("topic", "not-a-real-topic")
    assert "personal" in str(exc_info.value)


# -- wikilink resolution -------------------------------------------------


def test_wikilink_plain_resolves(vault: VaultIndex):
    homelab = vault.notes["documents/homelab/homelab.md"]
    resolved = {link.target: link for link in homelab.outbound_links}
    assert resolved["Router Notes"].resolved is True
    assert resolved["Router Notes"].path == "documents/homelab/router-notes.md"


def test_wikilink_unresolved_is_flagged(vault: VaultIndex):
    homelab = vault.notes["documents/homelab/homelab.md"]
    resolved = {link.target: link for link in homelab.outbound_links}
    assert resolved["nonexistent target"].resolved is False
    assert resolved["nonexistent target"].path is None


def test_wikilink_aliased_display_text(vault: VaultIndex):
    net = vault.notes["documents/personal/networking-101.md"]
    link = net.outbound_links[0]
    assert link.target == "Router Notes"
    assert link.display == "the router setup"
    assert link.resolved is True
    assert link.path == "documents/homelab/router-notes.md"


def test_backlinks_include_context_line(vault: VaultIndex):
    backlinks = vault.get_backlinks("documents/homelab/router-notes.md")
    paths = {b["path"] for b in backlinks}
    assert "documents/homelab/homelab.md" in paths
    assert "documents/personal/networking-101.md" in paths
    for b in backlinks:
        assert "context" in b and b["context"]


# -- identifier resolution --------------------------------------------------


def test_find_by_path(vault: VaultIndex):
    note = vault.find_by_identifier("documents/homelab/homelab.md")
    assert note.title == "Homelab"


def test_find_by_bare_filename(vault: VaultIndex):
    note = vault.find_by_identifier("homelab")
    assert note.path == "documents/homelab/homelab.md"

    note2 = vault.find_by_identifier("homelab.md")
    assert note2.path == "documents/homelab/homelab.md"


def test_find_by_ambiguous_stem_lists_candidates(vault: VaultIndex, vault_root: Path, note_writer):
    # Add a second note that happens to share a basename (shouldn't happen
    # per convention, but the resolver must not silently guess).
    note_writer(
        vault_root,
        "documents/personal/homelab.md",
        title="Homelab Duplicate",
        topic="personal",
        created="2026-01-06",
        updated="2026-01-06",
    )
    vault.build_index()
    with pytest.raises(AmbiguousIdentifierError) as exc_info:
        vault.find_by_identifier("homelab")
    assert len(exc_info.value.candidates) == 2


def test_find_by_identifier_no_match(vault: VaultIndex):
    with pytest.raises(NoteNotFoundError):
        vault.find_by_identifier("does-not-exist")

    with pytest.raises(NoteNotFoundError):
        vault.find_by_identifier("does/not/exist.md")


# -- date parsing / sort ----------------------------------------------------


def test_date_sort_same_day_falls_back_to_path(vault: VaultIndex):
    notes = sorted(
        [vault.notes["documents/personal/project-a.md"], vault.notes["documents/personal/project-b.md"]],
        key=lambda n: (n.updated_dt, n.path),
        reverse=True,
    )
    assert notes[0].path == "documents/personal/project-b.md"  # "project-b" > "project-a"


def test_date_missing_sorts_last(vault: VaultIndex):
    from brain_mcp.vault import parse_date

    assert parse_date(None) is None
    assert parse_date("not-a-date") is None
    assert parse_date("2026-01-01").day == 1


# -- path traversal ----------------------------------------------------


def test_safe_resolve_rejects_traversal(vault: VaultIndex):
    with pytest.raises(PathTraversalError):
        vault.safe_resolve("../../etc/passwd")


def test_safe_resolve_rejects_traversal_under_inbox(vault: VaultIndex):
    with pytest.raises(PathTraversalError):
        vault.safe_resolve("00-inbox/../../etc/passwd", must_be_under="00-inbox")


def test_safe_resolve_allows_inside_root(vault: VaultIndex):
    p = vault.safe_resolve("documents/homelab/homelab.md")
    assert p.exists()


def test_read_note_path_traversal_via_identifier_rejected(vault: VaultIndex):
    # find_by_identifier treats unknown strings as "not found" rather than
    # reading arbitrary filesystem paths.
    with pytest.raises(NoteNotFoundError):
        vault.find_by_identifier("../../../etc/passwd")


# -- slug generation ------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Hello World", "hello-world"),
        ("Über Café: Notes!", "uber-cafe-notes"),
        ("  leading and trailing --", "leading-and-trailing"),
        ("emoji 🎉 party", "emoji-party"),
    ],
)
def test_slugify(title: str, expected: str):
    assert slugify(title) == expected


def test_slugify_truncates_to_60_chars():
    long_title = "word " * 30
    slug = slugify(long_title)
    assert len(slug) <= 60
