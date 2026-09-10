"""The single write path: capture() creates a new note in 00-inbox/."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from .vault import DATE_FORMAT, VaultIndex, slugify

INBOX_DIR = "00-inbox"

# CAPTURE.md restricts capture() to these three types even though the full
# vault `type` enum (see CONVENTIONS.md) also has `price-list` — that type
# is for large structured reference data, never something an LLM captures.
CAPTURE_TYPES = ("document", "memory", "journal")
KINDS = ("evidence", "inference")
CONFIDENCES = ("high", "medium", "low")


def build_note_text(
    *,
    title: str,
    type: str,
    topic: str,
    when_to_open: str,
    now: str,
    body: str,
    source: str,
    kind: str | None,
    confidence: str | None,
    supersedes: str | None,
    links: list[str] | None,
) -> str:
    frontmatter: dict[str, object] = {
        "type": type,
        "title": title,
        "topic": topic,
        "when-to-open": when_to_open,
        "created": now,
        "updated": now,
        "status": "current",
    }
    if supersedes:
        frontmatter["supersedes"] = supersedes
    if kind:
        frontmatter["kind"] = kind
    if confidence:
        frontmatter["confidence"] = confidence
    frontmatter["source"] = source

    fm_text = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=None
    ).rstrip("\n")

    lines = ["---", fm_text, "---", "", body.rstrip(), ""]
    if links:
        lines += ["## Related", ""]
        lines += [f"- [[{link}]]" for link in links]
        lines += [""]
    return "\n".join(lines)


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
    if not title or not title.strip():
        raise ValueError("title must not be empty")
    if not when_to_open or not when_to_open.strip():
        raise ValueError("when_to_open must not be empty")
    if not source or not source.strip():
        raise ValueError("source must not be empty")
    if type not in CAPTURE_TYPES:
        raise ValueError(f"Invalid type {type!r}. Valid values: {', '.join(CAPTURE_TYPES)}")
    if type == "memory" and kind not in KINDS:
        raise ValueError(f"type: memory requires kind to be one of {', '.join(KINDS)}")
    if kind == "inference" and confidence not in CONFIDENCES:
        raise ValueError(f"kind: inference requires confidence to be one of {', '.join(CONFIDENCES)}")

    vault.validate_enum("topic", topic)

    inbox_dir = vault.safe_resolve(INBOX_DIR, must_be_under=INBOX_DIR)
    inbox_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    date_prefix = now.strftime("%Y-%m-%d")
    slug = slugify(title) or "untitled"
    stamp = now.strftime(DATE_FORMAT)

    filename = _first_available_filename(inbox_dir, date_prefix, slug)
    # Defense in depth: even though the slug is already sanitized to
    # [a-z0-9-], re-validate the final path stays inside 00-inbox/.
    target = vault.safe_resolve(f"{INBOX_DIR}/{filename}", must_be_under=INBOX_DIR)

    text = build_note_text(
        title=title.strip(),
        type=type,
        topic=topic,
        when_to_open=when_to_open.strip(),
        now=stamp,
        body=body or "",
        source=source.strip(),
        kind=kind,
        confidence=confidence,
        supersedes=supersedes,
        links=links,
    )

    _write_atomic(target, text)

    rel_path = f"{INBOX_DIR}/{filename}"
    return {"path": rel_path}


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
