"""The only write paths: capture() creates a new note in 00-inbox/, and
capture_update() edits a note that is still in 00-inbox/."""

from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime
from pathlib import Path

import frontmatter as fm_lib
import yaml

from .vault import DATE_FORMAT, VaultIndex, slugify

INBOX_DIR = "00-inbox"

# CAPTURE.md restricts capture() to these three types even though the full
# vault `type` enum (see CONVENTIONS.md) also has `price-list` — that type
# is for large structured reference data, never something an LLM captures.
# topic/status come from CONVENTIONS.md at startup via vault.validate_enum.
CAPTURE_TYPES = ("document", "memory", "journal")
KINDS = ("evidence", "inference")
CONFIDENCES = ("high", "medium", "low")

REQUIRED_KEYS = ("type", "title", "topic", "when-to-open", "created", "updated", "status", "source")
# Write order for frontmatter keys; anything else is appended after these.
KEY_ORDER = REQUIRED_KEYS[:7] + ("supersedes", "kind", "confidence", "source")
# Pre-restructure schema fields. CONVENTIONS.md: "There is no uid field and
# no domain/tags/aliases fields in this schema".
LEGACY_KEYS = ("uid", "domain", "tags")

# "Title (2026-10-01)", "Title 2026-10-01", "Title - [2026-10-01]"
_TRAILING_DATE_RE = re.compile(r"[\s\-:,]*[(\[]?\d{4}-\d{2}-\d{2}[)\]]?\s*$")


class CaptureError(ValueError):
    pass


def validate_frontmatter(vault: VaultIndex, fm: dict[str, object]) -> None:
    legacy = [k for k in LEGACY_KEYS if k in fm]
    if legacy:
        raise CaptureError(f"Legacy fields not allowed: {', '.join(legacy)}. See 90-meta/CONVENTIONS.md.")
    missing = [k for k in REQUIRED_KEYS if not str(fm.get(k) or "").strip()]
    if missing:
        raise CaptureError(f"Missing required fields: {', '.join(missing)}")
    if fm["type"] not in CAPTURE_TYPES:
        raise CaptureError(f"Invalid type {fm['type']!r}. Valid values: {', '.join(CAPTURE_TYPES)}")
    vault.validate_enum("topic", fm["topic"])
    vault.validate_enum("status", fm["status"])
    kind = fm.get("kind")
    if fm["type"] == "memory" and kind not in KINDS:
        raise CaptureError(f"type: memory requires kind to be one of {', '.join(KINDS)}")
    if kind is not None and kind not in KINDS:
        raise CaptureError(f"Invalid kind {kind!r}. Valid values: {', '.join(KINDS)}")
    if kind == "inference" and fm.get("confidence") not in CONFIDENCES:
        raise CaptureError(f"kind: inference requires confidence to be one of {', '.join(CONFIDENCES)}")


def unescape_body(body: str) -> str:
    """Undo double-escaped input: literal \\n with (almost) no real newlines."""
    if "\\n" in body and body.count("\n") <= 1:
        return body.replace("\\n", "\n").replace("\\t", "\t")
    return body


def normalize_title(title: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", title.lower()).split())


def render_note(fm: dict[str, object], body: str) -> str:
    ordered = {k: fm[k] for k in KEY_ORDER if k in fm}
    ordered.update({k: v for k, v in fm.items() if k not in ordered})
    fm_text = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{fm_text}---\n\n{body.strip()}\n"


def capture_note(
    vault: VaultIndex,
    *,
    title: str,
    body: str,
    topic: str,
    when_to_open: str,
    source: str,
    type: str = "document",
    kind: str | None = None,
    confidence: str | None = None,
    supersedes: str | None = None,
    links: list[str] | None = None,
) -> dict[str, str]:
    title = (title or "").strip()
    stamp = datetime.now().strftime(DATE_FORMAT)
    fm: dict[str, object] = {
        "type": type,
        "title": title,
        "topic": topic,
        "when-to-open": (when_to_open or "").strip(),
        "created": stamp,
        "updated": stamp,
        "status": "current",
        "supersedes": supersedes,
        "kind": kind,
        "confidence": confidence,
        "source": (source or "").strip(),
    }
    fm = {k: v for k, v in fm.items() if v is not None}
    validate_frontmatter(vault, fm)

    inbox_dir = vault.safe_resolve(INBOX_DIR, must_be_under=INBOX_DIR)
    inbox_dir.mkdir(parents=True, exist_ok=True)

    existing = _find_inbox_title(inbox_dir, title)
    if existing:
        raise CaptureError(
            f"A note titled {title!r} already exists in {INBOX_DIR}/: {existing}. "
            f"Use capture_update(filename={existing!r}, ...) to correct or extend it."
        )

    # The filename already carries the date; don't repeat one from the title.
    slug = slugify(_TRAILING_DATE_RE.sub("", title)) or "untitled"
    filename = _first_available_filename(inbox_dir, stamp, slug)
    # Defense in depth: even though the slug is already sanitized to
    # [a-z0-9-], re-validate the final path stays inside 00-inbox/.
    target = vault.safe_resolve(f"{INBOX_DIR}/{filename}", must_be_under=INBOX_DIR)

    body = unescape_body(body or "").rstrip()
    if links:
        body += "\n\n## Related\n\n" + "\n".join(f"- [[{link}]]" for link in links)
    _write_atomic(target, render_note(fm, body))

    return {"path": f"{INBOX_DIR}/{filename}", "filename": filename.removesuffix(".md")}


def update_note(
    vault: VaultIndex,
    *,
    filename: str,
    content: str,
    mode: str,
    frontmatter: dict[str, object] | None = None,
) -> dict[str, str]:
    if mode not in ("append", "replace"):
        raise CaptureError("mode must be 'append' or 'replace'")
    name = (filename or "").strip().removesuffix(".md")
    if not name or "/" in name or "\\" in name or ".." in name:
        raise CaptureError(f"filename must be a bare inbox filename as returned by capture(), got {filename!r}")
    target = vault.safe_resolve(f"{INBOX_DIR}/{name}.md", must_be_under=INBOX_DIR)
    if not target.is_file():
        raise CaptureError(f"No note {name!r} in {INBOX_DIR}/. Only inbox notes can be updated.")

    post = fm_lib.loads(target.read_text(encoding="utf-8"))
    fm = {k: v.strftime(DATE_FORMAT) if isinstance(v, date) else v for k, v in post.metadata.items()}
    created = fm.get("created")
    # Merge: underscores accepted for hyphenated keys, None removes a key
    # (e.g. clearing a legacy uid left on an old capture).
    for k, v in (frontmatter or {}).items():
        k = k.replace("_", "-")
        if v is None:
            fm.pop(k, None)
        else:
            fm[k] = v
    today = datetime.now().strftime(DATE_FORMAT)
    fm["created"] = created or today
    fm["updated"] = today
    validate_frontmatter(vault, fm)

    content = unescape_body(content or "").strip()
    if mode == "append":
        body = f"{post.content.rstrip()}\n\n## Update {today}\n\n{content}"
    else:
        body = content
    _write_atomic(target, render_note(fm, body))
    return {"path": f"{INBOX_DIR}/{name}.md", "filename": name}


def _find_inbox_title(inbox_dir: Path, title: str) -> str | None:
    wanted = normalize_title(title)
    if not wanted:
        return None
    for path in sorted(inbox_dir.glob("*.md")):
        try:
            existing = str(fm_lib.loads(path.read_text(encoding="utf-8")).metadata.get("title") or "")
        except Exception:  # noqa: BLE001 - a broken inbox note must not block capture
            continue
        if normalize_title(existing) == wanted:
            return path.stem
    return None


def _first_available_filename(inbox_dir: Path, date_prefix: str, slug: str) -> str:
    base = f"{date_prefix}-{slug}"
    candidate = f"{base}.md"
    if not (inbox_dir / candidate).exists():
        return candidate
    n = 2
    while True:
        candidate = f"{base}-{n}.md"
        if not (inbox_dir / candidate).exists():
            return candidate
        n += 1


def _write_atomic(target: Path, text: str) -> None:
    tmp_path = target.with_name(f".tmp-{uuid.uuid4().hex}-{target.name}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Group-writable regardless of the container's umask: the container
        # runs as a fixed UID (99 by default, Unraid's nobody), but whatever
        # else edits the vault - Obsidian synced over SMB, another process -
        # is very unlikely to be that exact UID. It just needs to share the
        # GID (100, Unraid's users) to edit notes capture() has written,
        # which requires the group bit to actually include write.
        os.chmod(tmp_path, 0o664)
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
