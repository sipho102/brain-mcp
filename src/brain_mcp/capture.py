"""The single write path: capture() creates a new note in 00-inbox/."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from .vault import TIMESTAMP_FORMAT, VaultIndex, slugify

INBOX_DIR = "00-inbox"
DEFAULT_DOMAIN = "personal"


def build_note_text(
    *,
    uid: str,
    title: str,
    domain: str,
    now: str,
    body: str,
    tags: list[str] | None,
    source: str | None,
    links: list[str] | None,
) -> str:
    frontmatter = {
        "uid": uid,
        "title": title,
        "type": "note",
        "status": "inbox",
        "domain": domain,
        "tags": list(tags) if tags else [],
        "created": now,
        "updated": now,
    }
    fm_text = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=None
    ).rstrip("\n")

    lines = ["---", fm_text, "---", "", body.rstrip(), ""]
    if source:
        lines += ["## Source", "", source.strip(), ""]
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
    domain: str | None,
    tags: list[str] | None,
    source: str | None,
    links: list[str] | None,
) -> dict[str, str]:
    if not title or not title.strip():
        raise ValueError("title must not be empty")

    resolved_domain = domain or DEFAULT_DOMAIN
    vault.validate_enum("domain", resolved_domain)

    inbox_dir = vault.safe_resolve(INBOX_DIR, must_be_under=INBOX_DIR)
    inbox_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    date_prefix = now.strftime("%Y-%m-%d")
    slug = slugify(title) or "untitled"
    stamp = now.strftime(TIMESTAMP_FORMAT)
    new_uid = str(uuid.uuid4())

    filename = _first_available_filename(inbox_dir, date_prefix, slug)
    # Defense in depth: even though the slug is already sanitized to
    # [a-z0-9-], re-validate the final path stays inside 00-inbox/.
    target = vault.safe_resolve(f"{INBOX_DIR}/{filename}", must_be_under=INBOX_DIR)

    text = build_note_text(
        uid=new_uid,
        title=title.strip(),
        domain=resolved_domain,
        now=stamp,
        body=body or "",
        tags=tags,
        source=source,
        links=links,
    )

    _write_atomic(target, text)

    rel_path = f"{INBOX_DIR}/{filename}"
    return {"path": rel_path, "uid": new_uid}


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
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
