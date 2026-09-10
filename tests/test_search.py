from __future__ import annotations

from pathlib import Path

from brain_mcp.search import search_content


def test_search_finds_matches_with_snippet(vault_root: Path):
    hits = search_content("beacon", vault_root)
    assert "documents/personal/project-a.md" in hits
    assert "documents/personal/project-b.md" in hits
    assert "beacon" in hits["documents/personal/project-a.md"]["snippet"]


def test_search_excludes_template_dir(vault_root: Path):
    hits = search_content("tp.user", vault_root)
    assert hits == {}


def test_search_excludes_userscripts_and_reports(vault_root: Path):
    hits = search_content("Generated", vault_root)
    assert "90-meta/reports/lint-report.md" not in hits


def test_search_excludes_journal_archive(vault_root: Path):
    hits = search_content("Archived journal entry", vault_root)
    assert hits == {}


def test_search_excludes_hub(vault_root: Path):
    hits = search_content("Generated index", vault_root)
    assert "hub.md" not in hits


def test_search_empty_query_returns_empty(vault_root: Path):
    assert search_content("", vault_root) == {}


def test_search_lowercase_query_is_case_insensitive(vault_root: Path):
    # "title: Router Notes" is capitalized in the raw file; smart-case means
    # an all-lowercase query still matches it.
    hits = search_content("router notes", vault_root)
    assert "documents/homelab/router-notes.md" in hits


def test_search_mixed_case_query_is_case_sensitive(vault_root: Path):
    # A query containing an uppercase letter switches smart-case to
    # case-sensitive, so it must not match the differently-cased text.
    hits = search_content("ROUTER NOTES", vault_root)
    assert hits == {}
