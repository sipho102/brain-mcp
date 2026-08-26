from __future__ import annotations

from pathlib import Path

import pytest

from brain_mcp.vault import (
    AmbiguousIdentifierError,
    IdentifierTooShortError,
    InvalidEnumError,
    NoteNotFoundError,
    PathTraversalError,
    VaultIndex,
    slugify,
)


def test_indexes_real_notes(vault: VaultIndex):
    assert "20-areas/homelab/_index.md" in vault.notes
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
        assert not rel.startswith("assets/")


def test_templater_syntax_note_never_appears(vault: VaultIndex):
    for note in vault.notes.values():
        assert "tp.user.uuid" not in (note.uid or "")
        assert note.path != "90-meta/templates/note-template.md"


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
    assert note.domain is None
    assert note.title == "Missing Fields"


def test_enums_parsed_from_conventions(vault: VaultIndex):
    assert set(vault.enums["type"]) == {"note", "project", "area", "resource", "person"}
    assert set(vault.enums["status"]) == {"inbox", "active", "done", "archived"}
    assert set(vault.enums["domain"]) == {"personal", "work", "health", "finance"}


def test_conventions_text_is_full_file(vault: VaultIndex):
    assert "Vault conventions" in vault.conventions_text


def test_enums_parsed_from_bold_label_format():
    # Mirrors the real vault's actual CONVENTIONS.md shape: all three enums
    # live under one "## Enums" heading, distinguished by a bold inline
    # label rather than their own subheading, with bulleted explanations
    # (redundant backticks) below and prose after that incidentally
    # backtick-quotes an unrelated word ("`domain` is the primary...").
    from brain_mcp.vault import _extract_enum_values

    text = """\
## Enums

**type:** `note`, `project`, `area`, `resource`, `runbook`, `decision`,
`meeting`, `source`

- `note` — general atomic note, the default
- `project` / `area` — reserved for `_index.md` files

**status:** `inbox`, `active`, `paused`, `done`, `archived`

- `inbox` — captured but not yet triaged.

**domain:** `inventx`, `homelab`, `gaming`, `personal`, `finance`, `home`

`domain` is the primary query axis. It matters more than PARA placement.

## Linking

Use Obsidian wikilinks.
"""
    assert _extract_enum_values(text, "type") == [
        "note", "project", "area", "resource", "runbook", "decision", "meeting", "source",
    ]
    assert _extract_enum_values(text, "status") == ["inbox", "active", "paused", "done", "archived"]
    # The stray `domain` backtick in the prose paragraph below the list must
    # not leak into the parsed enum values.
    assert _extract_enum_values(text, "domain") == [
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
    vault.validate_enum("domain", "personal")  # no raise


def test_validate_enum_rejects_invalid(vault: VaultIndex):
    with pytest.raises(InvalidEnumError) as exc_info:
        vault.validate_enum("domain", "not-a-real-domain")
    assert "personal" in str(exc_info.value)


# -- wikilink resolution -------------------------------------------------


def test_wikilink_plain_resolves(vault: VaultIndex):
    homelab = vault.notes["20-areas/homelab/_index.md"]
    resolved = {link.target: link for link in homelab.outbound_links}
    assert resolved["Router Notes"].resolved is True
    assert resolved["Router Notes"].path == "20-areas/homelab/router-notes.md"


def test_wikilink_unresolved_is_flagged(vault: VaultIndex):
    homelab = vault.notes["20-areas/homelab/_index.md"]
    resolved = {link.target: link for link in homelab.outbound_links}
    assert resolved["nonexistent target"].resolved is False
    assert resolved["nonexistent target"].path is None


def test_wikilink_aliased_display_text(vault: VaultIndex):
    net = vault.notes["30-resources/networking-101.md"]
    link = net.outbound_links[0]
    assert link.target == "Router"
    assert link.display == "the router setup"
    assert link.resolved is True
    assert link.path == "20-areas/homelab/router-notes.md"


def test_backlinks_include_context_line(vault: VaultIndex):
    backlinks = vault.get_backlinks("20-areas/homelab/router-notes.md")
    paths = {b["path"] for b in backlinks}
    assert "20-areas/homelab/_index.md" in paths
    assert "30-resources/networking-101.md" in paths
    for b in backlinks:
        assert "context" in b and b["context"]


# -- uid resolution --------------------------------------------------------


def test_find_by_full_uid(vault: VaultIndex):
    note = vault.find_by_identifier("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert note.path == "20-areas/homelab/_index.md"


def test_find_by_path(vault: VaultIndex):
    note = vault.find_by_identifier("20-areas/homelab/_index.md")
    assert note.uid == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_find_by_unambiguous_prefix(vault: VaultIndex):
    note = vault.find_by_identifier("aaaaaaaa")
    assert note.path == "20-areas/homelab/_index.md"


def test_find_by_ambiguous_prefix_lists_candidates(vault: VaultIndex, vault_root: Path, note_writer):
    # Add a second note whose uid shares the same 8-char prefix.
    note_writer(
        vault_root,
        "30-resources/twin.md",
        uid="aaaaaaaa-1111-4111-8111-111111111111",
        title="Twin",
        created="2026-01-06 09:00:00",
        updated="2026-01-06 09:00:00",
    )
    vault.build_index()
    with pytest.raises(AmbiguousIdentifierError) as exc_info:
        vault.find_by_identifier("aaaaaaaa")
    assert len(exc_info.value.candidates) == 2


def test_find_by_prefix_too_short_rejected(vault: VaultIndex):
    with pytest.raises(IdentifierTooShortError):
        vault.find_by_identifier("aaaaaaa")  # 7 hex chars


def test_find_by_identifier_no_match(vault: VaultIndex):
    with pytest.raises(NoteNotFoundError):
        vault.find_by_identifier("99999999-9999-4999-8999-999999999999")  # well-formed, not indexed

    with pytest.raises(NoteNotFoundError):
        vault.find_by_identifier("does/not/exist.md")


# -- timestamp parsing / sort ----------------------------------------------


def test_timestamp_sort_same_minute_uses_seconds(vault: VaultIndex):
    notes = sorted(
        [vault.notes["10-projects/project-a.md"], vault.notes["10-projects/project-b.md"]],
        key=lambda n: n.updated_dt,
        reverse=True,
    )
    assert notes[0].path == "10-projects/project-b.md"  # :40 > :10


def test_timestamp_missing_sorts_last(vault: VaultIndex):
    from brain_mcp.vault import parse_timestamp

    assert parse_timestamp(None) is None
    assert parse_timestamp("not-a-timestamp") is None
    assert parse_timestamp("2026-01-01 09:00:00").second == 0


# -- path traversal ----------------------------------------------------


def test_safe_resolve_rejects_traversal(vault: VaultIndex):
    with pytest.raises(PathTraversalError):
        vault.safe_resolve("../../etc/passwd")


def test_safe_resolve_rejects_traversal_under_inbox(vault: VaultIndex):
    with pytest.raises(PathTraversalError):
        vault.safe_resolve("00-inbox/../../etc/passwd", must_be_under="00-inbox")


def test_safe_resolve_allows_inside_root(vault: VaultIndex):
    p = vault.safe_resolve("20-areas/homelab/_index.md")
    assert p.exists()


def test_read_note_path_traversal_via_identifier_rejected(vault: VaultIndex):
    # find_by_identifier treats unknown non-uid strings as "not found" rather
    # than reading arbitrary filesystem paths.
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
