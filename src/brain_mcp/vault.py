"""Vault index: parsing, exclusions, path safety, wikilink resolution.

Builds and maintains an in-memory index of every markdown note in the vault.
Content search stays on ripgrep (see search.py); this module owns
frontmatter, uid resolution, and the link graph.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import frontmatter

logger = logging.getLogger("brain_mcp.vault")

# Module-level exclusion list. A future vault can adjust this in one place.
# Prefixes are vault-relative, POSIX-style, trailing slash.
EXCLUDED_DIR_PREFIXES: tuple[str, ...] = (
    "90-meta/templates/",
    "90-meta/userscripts/",
    "90-meta/reports/",
    ".obsidian/",
    ".claude/",
    ".trash/",
    "assets/",
)

# PARA folder map: keyword -> (directory prefix, human description).
# Descriptions match personal_jon's 90-meta/CONVENTIONS.md wording.
PARA_FOLDERS: dict[str, tuple[str, str]] = {
    "inbox": ("00-inbox", "Unprocessed capture. Nothing stays here permanently."),
    "projects": ("10-projects", "Has a defined outcome and an end. One folder per project."),
    "areas": ("20-areas", "Ongoing responsibility with no end date."),
    "resources": ("30-resources", "Reference material and topics of interest."),
    "archive": ("40-archive", "Completed or abandoned projects and dormant areas."),
}

FRONTMATTER_SCHEMA: list[dict[str, Any]] = [
    {"name": "uid", "type": "string (uuid4)", "required": True},
    {"name": "title", "type": "string", "required": True},
    {"name": "type", "type": "enum", "required": True},
    {"name": "status", "type": "enum", "required": True},
    {"name": "domain", "type": "enum", "required": True},
    {"name": "tags", "type": "array[string]", "required": True},
    {"name": "created", "type": "timestamp (YYYY-MM-DD HH:mm:ss)", "required": True},
    {"name": "updated", "type": "timestamp (YYYY-MM-DD HH:mm:ss)", "required": True},
    {"name": "paperless", "type": "array[integer]", "required": False},
    {"name": "aliases", "type": "array[string]", "required": False},
]

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

_UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")
_UUID_CANON_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_HEX_RE = re.compile(r"^[0-9a-f]+$")

# [[target]], [[target#heading]], [[target|display]], [[target#heading|display]]
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")


class VaultError(Exception):
    """Base class for user-facing vault errors (clean message, no traceback)."""


class PathTraversalError(VaultError):
    pass


class NoteNotFoundError(VaultError):
    def __init__(self, identifier: str):
        super().__init__(f"No note found matching identifier: {identifier!r}")
        self.identifier = identifier


class IdentifierTooShortError(VaultError):
    def __init__(self, identifier: str):
        super().__init__(
            f"uid prefix {identifier!r} is shorter than the minimum 8 characters"
        )
        self.identifier = identifier


class AmbiguousIdentifierError(VaultError):
    def __init__(self, identifier: str, candidates: list[tuple[str, str | None]]):
        listing = ", ".join(f"{uid} ({title!r})" for uid, title in candidates)
        super().__init__(
            f"uid prefix {identifier!r} matches {len(candidates)} notes: {listing}"
        )
        self.identifier = identifier
        self.candidates = candidates


class InvalidEnumError(VaultError):
    def __init__(self, field_name: str, value: str, valid: list[str]):
        super().__init__(
            f"Invalid {field_name} {value!r}. Valid values: {', '.join(sorted(valid))}"
        )
        self.field_name = field_name
        self.value = value
        self.valid = valid


@dataclass
class RawLink:
    target: str
    display: str | None
    line_no: int
    line_text: str


@dataclass
class OutboundLink:
    target: str
    display: str | None
    resolved: bool
    path: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "display": self.display,
            "resolved": self.resolved,
            "path": self.path,
        }


@dataclass
class Note:
    path: str  # vault-relative, POSIX separators
    uid: str | None
    title: str | None
    type: str | None
    status: str | None
    domain: str | None
    tags: list[str]
    created: str | None
    updated: str | None
    paperless: list[Any]
    aliases: list[str]
    frontmatter: dict[str, Any]
    body: str
    raw_links: list[RawLink] = field(default_factory=list)
    outbound_links: list[OutboundLink] = field(default_factory=list)

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "uid": self.uid,
            "title": self.title,
            "type": self.type,
            "status": self.status,
            "domain": self.domain,
            "tags": self.tags,
            "updated": self.updated,
        }

    @property
    def updated_dt(self) -> datetime:
        return parse_timestamp(self.updated) or datetime.min


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT)
    except (ValueError, TypeError):
        return None


def is_excluded(rel_posix: str) -> bool:
    return any(rel_posix.startswith(prefix) for prefix in EXCLUDED_DIR_PREFIXES)


def slugify(title: str, max_len: int = 60) -> str:
    """Mirror the vault's Templater slug logic: NFD-normalise, strip combining
    marks, lowercase, collapse non-alphanumerics to hyphens, trim."""
    normalized = unicodedata.normalize("NFD", title)
    stripped = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    lowered = stripped.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug[:max_len].strip("-")


def _extract_wikilinks(body: str) -> list[RawLink]:
    links: list[RawLink] = []
    for line_no, line in enumerate(body.splitlines(), start=1):
        for m in _WIKILINK_RE.finditer(line):
            target = m.group(1).strip()
            display = m.group(2).strip() if m.group(2) else None
            links.append(RawLink(target=target, display=display, line_no=line_no, line_text=line.strip()))
    return links


class VaultIndex:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.notes: dict[str, Note] = {}
        self.uid_to_path: dict[str, str] = {}
        self._backlinks: dict[str, list[dict[str, Any]]] = {}
        self._conventions_text: str | None = None
        self._enums: dict[str, list[str]] = {}

    # -- path safety ---------------------------------------------------

    def safe_resolve(self, rel: str, *, must_be_under: str | None = None) -> Path:
        """Resolve a vault-relative path and guarantee it stays inside root
        (or a sub-prefix of root, e.g. '00-inbox') after symlink resolution."""
        rel = rel.lstrip("/")
        candidate = (self.root / rel).resolve()
        base = self.root
        if must_be_under is not None:
            base = (self.root / must_be_under).resolve()
        if candidate != base and base not in candidate.parents:
            raise PathTraversalError(f"Path escapes vault root: {rel!r}")
        return candidate

    def _rel_posix(self, path: Path) -> str:
        return PurePosixPath(path.relative_to(self.root).as_posix()).as_posix()

    # -- indexing --------------------------------------------------------

    def build_index(self) -> None:
        logger.info("Building vault index at %s", self.root)
        self.notes.clear()
        self.uid_to_path.clear()
        self._backlinks.clear()

        for file_path in self._walk_markdown_files():
            self._index_file(file_path)

        self._resolve_all_links()
        self._load_conventions()
        logger.info("Indexed %d notes", len(self.notes))

    def _walk_markdown_files(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel_dir = PurePosixPath(os.path.relpath(dirpath, self.root)).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir + "/"
            # prune excluded directories so we never descend into them
            dirnames[:] = [
                d for d in dirnames if not is_excluded(f"{rel_dir}{d}/")
            ]
            for name in filenames:
                if not name.endswith(".md"):
                    continue
                rel_path = f"{rel_dir}{name}"
                if is_excluded(rel_path):
                    continue
                yield Path(dirpath) / name

    def _index_file(self, file_path: Path) -> None:
        rel = self._rel_posix(file_path)
        try:
            text = file_path.read_text(encoding="utf-8")
            post = frontmatter.loads(text)
        except Exception as exc:  # noqa: BLE001 - a single bad note must not crash indexing
            logger.warning("Skipping %s: failed to parse frontmatter (%s)", rel, exc)
            return

        meta = post.metadata or {}
        uid = meta.get("uid")
        uid = str(uid).strip().lower() if uid else None
        if uid and not _UUID_CANON_RE.match(uid):
            logger.warning("%s: uid %r is not a canonical UUIDv4, indexing anyway", rel, uid)

        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        paperless = meta.get("paperless") or []
        aliases = meta.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]

        note = Note(
            path=rel,
            uid=uid,
            title=meta.get("title"),
            type=meta.get("type"),
            status=meta.get("status"),
            domain=meta.get("domain"),
            tags=list(tags),
            created=_stringify_ts(meta.get("created")),
            updated=_stringify_ts(meta.get("updated")),
            paperless=list(paperless),
            aliases=list(aliases),
            frontmatter=dict(meta),
            body=post.content,
            raw_links=_extract_wikilinks(post.content),
        )

        self.notes[rel] = note
        if uid:
            if uid in self.uid_to_path and self.uid_to_path[uid] != rel:
                logger.warning(
                    "Duplicate uid %s in %s and %s; keeping %s in the uid index",
                    uid, self.uid_to_path[uid], rel, self.uid_to_path[uid],
                )
            else:
                self.uid_to_path[uid] = rel

    def _resolve_all_links(self) -> None:
        stem_map: dict[str, list[str]] = {}
        title_map: dict[str, list[str]] = {}
        alias_map: dict[str, list[str]] = {}
        for rel, note in self.notes.items():
            stem = PurePosixPath(rel).stem.lower()
            stem_map.setdefault(stem, []).append(rel)
            if note.title:
                title_map.setdefault(note.title.strip().lower(), []).append(rel)
            for alias in note.aliases:
                alias_map.setdefault(str(alias).strip().lower(), []).append(rel)

        for rel, note in self.notes.items():
            resolved: list[OutboundLink] = []
            for raw in note.raw_links:
                target_path = self._resolve_target(raw.target, stem_map, title_map, alias_map)
                resolved.append(
                    OutboundLink(
                        target=raw.target,
                        display=raw.display,
                        resolved=target_path is not None,
                        path=target_path,
                    )
                )
                if target_path:
                    self._backlinks.setdefault(target_path, []).append(
                        {
                            "path": note.path,
                            "uid": note.uid,
                            "title": note.title,
                            "domain": note.domain,
                            "context": raw.line_text,
                        }
                    )
            note.outbound_links = resolved

    def _resolve_target(
        self,
        raw_target: str,
        stem_map: dict[str, list[str]],
        title_map: dict[str, list[str]],
        alias_map: dict[str, list[str]],
    ) -> str | None:
        target = raw_target.strip()
        if not target:
            return None
        if "/" in target:
            candidate = target if target.endswith(".md") else f"{target}.md"
            candidate = candidate.lstrip("/")
            if candidate in self.notes:
                return candidate
            suffix_matches = [p for p in self.notes if p == candidate or p.endswith("/" + candidate)]
            return suffix_matches[0] if len(suffix_matches) == 1 else None

        key = target.lower()
        for mapping in (stem_map, title_map, alias_map):
            matches = mapping.get(key)
            if matches and len(matches) == 1:
                return matches[0]
        return None

    def _load_conventions(self) -> None:
        path = self.root / "90-meta" / "CONVENTIONS.md"
        if not path.is_file():
            raise VaultError(
                "90-meta/CONVENTIONS.md is missing; it is the authoritative source "
                "for the type/status/domain enums and cannot be defaulted."
            )
        text = path.read_text(encoding="utf-8")
        self._conventions_text = text
        enums: dict[str, list[str]] = {}
        for field_name in ("type", "status", "domain"):
            values = _extract_enum_values(text, field_name)
            if not values:
                raise VaultError(
                    f"Could not parse valid values for {field_name!r} out of "
                    f"90-meta/CONVENTIONS.md. Update the document or its parser."
                )
            enums[field_name] = values
        self._enums = enums

    # -- lookups -----------------------------------------------------

    @property
    def conventions_text(self) -> str:
        return self._conventions_text or ""

    @property
    def enums(self) -> dict[str, list[str]]:
        return self._enums

    def validate_enum(self, field_name: str, value: str) -> None:
        valid = self._enums.get(field_name, [])
        if value not in valid:
            raise InvalidEnumError(field_name, value, valid)

    def find_by_identifier(self, identifier: str) -> Note:
        identifier = identifier.strip()
        if not identifier:
            raise NoteNotFoundError(identifier)

        rel = identifier.lstrip("/")
        if rel in self.notes:
            return self.notes[rel]

        lowered = identifier.lower()
        if _UUID_CANON_RE.match(lowered) or _UUID_HEX_RE.match(lowered):
            canon = lowered
            path = self.uid_to_path.get(canon)
            if path:
                return self.notes[path]
            raise NoteNotFoundError(identifier)

        if _HEX_RE.match(lowered):
            if len(lowered) < 8:
                raise IdentifierTooShortError(identifier)
            candidates = sorted(uid for uid in self.uid_to_path if uid.startswith(lowered))
            if len(candidates) == 1:
                return self.notes[self.uid_to_path[candidates[0]]]
            if len(candidates) > 1:
                pairs = [(uid, self.notes[self.uid_to_path[uid]].title) for uid in candidates]
                raise AmbiguousIdentifierError(identifier, pairs)
            raise NoteNotFoundError(identifier)

        raise NoteNotFoundError(identifier)

    def get_backlinks(self, identifier: str) -> list[dict[str, Any]]:
        note = self.find_by_identifier(identifier)
        return list(self._backlinks.get(note.path, []))

    def iter_notes(
        self,
        *,
        para: list[str] | None = None,
        domain: list[str] | None = None,
        status: list[str] | None = None,
        type_: list[str] | None = None,
        tag: str | None = None,
    ):
        prefixes = None
        if para:
            prefixes = tuple(f"{PARA_FOLDERS[p][0]}/" for p in para)
        for note in self.notes.values():
            if prefixes and not note.path.startswith(prefixes):
                continue
            if domain and note.domain not in domain:
                continue
            if status and note.status not in status:
                continue
            if type_ and note.type not in type_:
                continue
            if tag and tag not in (note.tags or []):
                continue
            yield note

    # -- watching ------------------------------------------------------

    async def watch_forever(self, debounce_ms: int = 700) -> None:
        import watchfiles

        logger.info("Watching %s for changes", self.root)
        async for changes in watchfiles.awatch(self.root, debounce=debounce_ms):
            changed_rel = set()
            for _change, changed_path in changes:
                try:
                    rel = self._rel_posix(Path(changed_path))
                except ValueError:
                    continue
                if rel.endswith(".md"):
                    changed_rel.add(rel)
            if changed_rel:
                logger.info("Reindexing %d changed file(s)", len(changed_rel))
                self.reindex_paths(changed_rel)

    def reindex_paths(self, rel_paths: set[str]) -> None:
        for rel in rel_paths:
            full = self.root / rel
            if is_excluded(rel):
                continue
            if full.is_file():
                old = self.notes.get(rel)
                if old and old.uid:
                    self.uid_to_path.pop(old.uid, None)
                self._index_file(full)
            else:
                old = self.notes.pop(rel, None)
                if old and old.uid:
                    self.uid_to_path.pop(old.uid, None)
        # Link resolution and backlinks depend on the whole graph; recompute.
        self._resolve_all_links_incremental()

    def _resolve_all_links_incremental(self) -> None:
        # Simplest correct approach: rebuild the link graph over the current
        # note set. Cheap relative to a filesystem walk, and change bursts are
        # already debounced.
        self._backlinks.clear()
        self._resolve_all_links()


def _stringify_ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime(TIMESTAMP_FORMAT)
    return str(value)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
# **type:** or **type**: or **Type**, anywhere on a line.
_BOLD_LABEL_RE = re.compile(r"\*\*\s*([A-Za-z][A-Za-z0-9_ -]*?)\s*:?\s*\*\*:?")


def _dedupe(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for v in values:
        seen.setdefault(v, None)
    return list(seen.keys())


def _extract_enum_values(text: str, field_name: str) -> list[str]:
    """Pull backtick-quoted enum values out of CONVENTIONS.md.

    Two shapes are supported, tried in order:

    1. A bold inline label, e.g. "**type:** `note`, `project`, ..." (this
       vault's actual format — all three enums live under one "## Enums"
       heading, distinguished only by bold labels). The section for a label
       runs until the next bold label or the next heading, whichever comes
       first.
    2. A heading whose text contains `field_name` (e.g. "### `type`" or
       "## Valid types"), collecting every `` `value` `` token until the
       next heading of equal-or-higher level. Kept as a fallback for vaults
       that document enums under their own subheading instead.
    """
    values = _extract_via_bold_label(text, field_name)
    if values:
        return values
    return _extract_via_heading(text, field_name)


def _extract_via_bold_label(text: str, field_name: str) -> list[str]:
    labels = list(_BOLD_LABEL_RE.finditer(text))
    for i, m in enumerate(labels):
        if m.group(1).strip().lower() != field_name.lower():
            continue
        start = m.end()
        # Bound the section as tightly as possible: the enum list is a
        # single (possibly soft-wrapped) paragraph right after the label.
        # Stopping at the first blank line keeps out any bulleted
        # explanations or prose below that happen to backtick-quote an
        # unrelated word (e.g. "`domain` is the primary query axis...").
        end = len(text)
        blank_line = re.search(r"\n[ \t]*\n", text[start:])
        if blank_line:
            end = min(end, start + blank_line.start())
        if i + 1 < len(labels):
            end = min(end, labels[i + 1].start())
        heading_after = _HEADING_RE.search(text, start)
        if heading_after:
            end = min(end, heading_after.start())
        section = text[start:end]
        values = re.findall(r"`([a-zA-Z0-9_-]+)`", section)
        if values:
            return _dedupe(values)
    return []


def _extract_via_heading(text: str, field_name: str) -> list[str]:
    headings = list(_HEADING_RE.finditer(text))
    for i, h in enumerate(headings):
        level, title = len(h.group(1)), h.group(2).lower()
        if field_name not in title:
            continue
        start = h.end()
        end = len(text)
        for later in headings[i + 1 :]:
            if len(later.group(1)) <= level:
                end = later.start()
                break
        section = text[start:end]
        values = re.findall(r"`([a-zA-Z0-9_-]+)`", section)
        if values:
            return _dedupe(values)
    return []
