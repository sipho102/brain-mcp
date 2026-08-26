"""ripgrep wrapper for full-text content search over the vault."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path, PurePosixPath

from .vault import EXCLUDED_DIR_PREFIXES

logger = logging.getLogger("brain_mcp.search")

SNIPPET_RADIUS = 100  # ~200 chars total around the match


class SearchError(Exception):
    pass


def _exclude_globs() -> list[str]:
    globs = []
    for prefix in EXCLUDED_DIR_PREFIXES:
        globs.extend(["-g", f"!/{prefix}**"])
    return globs


def search_content(query: str, root: Path, limit: int = 100) -> dict[str, dict]:
    """Run ripgrep over the vault and return {rel_path: {"count": int, "snippet": str}}.

    Only .md files outside the exclusion list are considered. Empty query
    returns an empty dict; callers treat that as "no content filter".
    """
    if not query:
        return {}

    cmd = [
        "rg",
        "--json",
        "--smart-case",
        "-g", "*.md",
        *_exclude_globs(),
        "--",
        query,
        str(root),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(root), timeout=30
        )
    except FileNotFoundError as exc:
        raise SearchError("ripgrep (rg) binary not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SearchError("ripgrep search timed out") from exc

    # rg exit code 1 means "no matches" - not an error. >=2 is a real error.
    if proc.returncode not in (0, 1):
        raise SearchError(f"ripgrep failed (exit {proc.returncode}): {proc.stderr.strip()}")

    results: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj["data"]
        abs_path = Path(data["path"]["text"])
        try:
            rel = PurePosixPath(abs_path.relative_to(root).as_posix()).as_posix()
        except ValueError:
            continue

        line_text = data["lines"]["text"]
        submatches = data.get("submatches", [])
        entry = results.setdefault(rel, {"count": 0, "snippet": None})
        entry["count"] += len(submatches) or 1
        if entry["snippet"] is None and submatches:
            start = submatches[0]["start"]
            end = submatches[0]["end"]
            entry["snippet"] = _build_snippet(line_text, start, end)

    return results


def _build_snippet(line_text: str, start: int, end: int) -> str:
    line_text = line_text.rstrip("\n")
    lo = max(0, start - SNIPPET_RADIUS)
    hi = min(len(line_text), end + SNIPPET_RADIUS)
    snippet = line_text[lo:hi].strip()
    if lo > 0:
        snippet = "…" + snippet
    if hi < len(line_text):
        snippet = snippet + "…"
    return snippet
