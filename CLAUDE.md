# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP server (streamable-HTTP transport) that exposes a markdown,
PARA-structured Obsidian vault as six tools: five read tools plus one
constrained write tool (`capture`, which only ever creates new notes in
`00-inbox/`). One running container serves exactly one vault; a second
vault is a second, independent container built from the same image. Full
behavioral spec is in `brain-mcp-spec.md`.

## Commands

```bash
# Install (uv is the primary path; a plain venv works too)
uv sync --dev
# or: python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Run the full test suite
uv run pytest
# or: .venv/bin/python -m pytest

# Run a single test file or test
uv run pytest tests/test_vault.py
uv run pytest tests/test_vault.py::test_find_by_full_uid

# Run the server locally (requires BRAIN_ROOT, BRAIN_NAME, BRAIN_TOKEN)
export BRAIN_ROOT=/path/to/vault BRAIN_NAME=personal BRAIN_TOKEN=dev-token
uv run brain-mcp

# Build and run in Docker
cp .env.example .env   # fill in BRAIN_NAME, BRAIN_VAULT_PATH, BRAIN_TOKEN
docker compose up -d --build
curl http://localhost:3100/health
```

Tests run entirely against a synthetic fixture vault built fresh per test
in `tests/conftest.py` — never against real vault data. There's no linter
configured in this repo.

## Module responsibilities

- `config.py` — env var parsing/validation, fails fast with a clear error.
- `vault.py` — the index: frontmatter parsing, exclusions, path safety,
  uid resolution, wikilink resolution, CONVENTIONS.md enum parsing,
  `watchfiles`-driven live reindexing.
- `search.py` — ripgrep subprocess wrapper for content search. No content
  index is built; ripgrep runs against the filesystem on every call.
- `capture.py` — the one write path (note creation in `00-inbox/`).
- `server.py` — the six `@mcp.tool()` definitions, the bearer-auth ASGI
  middleware, and process startup/shutdown wiring.

## Architecture notes that aren't obvious from any single file

**Index build is two-phase, and reindexing is vault-wide even for a
single-file change.** `VaultIndex.build_index()` first parses every note's
frontmatter and raw (unresolved) wikilinks, *then* does a second pass to
resolve links and build the backlinks map — link resolution needs the
stem/title/alias maps built from the *entire* note set before it can
resolve even one note's outbound links. Because of this, `reindex_paths()`
(called both by the `watchfiles` watcher and directly after `capture()`
writes a new note) re-parses only the changed files but always rebuilds the
whole link graph afterward.

**Startup indexing and the watcher are deliberately NOT wired through
FastMCP's `lifespan=` parameter.** That callback runs once per MCP
*session* (it's passed down into the low-level `Server.run()`, which fires
per incoming connection), not once per process — using it for index-build
would silently re-walk the vault on every new client session. Instead
`server._amain()` builds the index and starts the watcher as a bare
`asyncio.create_task()` before calling `uvicorn.Server.serve()`. If you're
tempted to move startup logic into `build_mcp_server()`'s `FastMCP(...)`
constructor, don't — check `_amain()` instead.

**Auth is a raw ASGI middleware wrapping `mcp.streamable_http_app()`,
not FastMCP's built-in `TokenVerifier`/`AuthSettings`.** FastMCP's auth
machinery is OAuth-resource-server shaped (issuer URLs, scopes,
`AuthenticationMiddleware` from Starlette) and doesn't fit a static bearer
token cleanly. `BearerAuthASGIMiddleware` in `server.py` does a
constant-time comparison itself, passes non-`"http"` scope types (notably
`"lifespan"`) straight through untouched so the inner app's own startup/
shutdown still fires, and exempts `/health` by path. DNS-rebinding
host-header protection is explicitly disabled in the `FastMCP(...)`
constructor (`transport_security=TransportSecuritySettings(
enable_dns_rebinding_protection=False)`) since this server is reached over
a container network / reverse proxy under arbitrary Host headers, and the
bearer token is the real gate — leave it off rather than fighting it via
the `host=` heuristic.

**Path exclusions (`EXCLUDED_DIR_PREFIXES` in `vault.py`) are checked by
path prefix before any file is opened**, not by trying-and-catching a YAML
parse failure. This is the actual defense against Templater-syntax notes
(`90-meta/templates/`) leaking into results — malformed-YAML skipping is a
second, independent safety net for notes that are broken for other
reasons, not the primary mechanism.

**The CONVENTIONS.md enum parser (`_extract_enum_values`) is a heuristic,
not a fixed-format assumption**, and it fails loudly (raises `VaultError`
at startup) rather than falling back to defaults if it can't parse
non-empty `type`/`status`/`domain` values. It tries a bold-inline-label
shape first (`**type:** \`note\`, \`project\`, ...` all under one `##
Enums` heading — confirmed against the real reference vault, bounded to
just the label's own paragraph so it doesn't pick up unrelated
backtick-quoted words in the prose below), then falls back to a
per-field-heading shape. If a vault's `CONVENTIONS.md` restructures its
Enums section, this is the function to revisit.

**`capture()` always generates a UUIDv4 `uid`, regardless of what any given
vault's `CONVENTIONS.md` says.** This was a deliberate choice (see README
for the full reasoning) — it's also what keeps `read_note`'s unambiguous
≥8-char uid-prefix lookup meaningful, which it wouldn't be against a
low-entropy, date-based id scheme.

**Path safety is checked in code, independent of the Docker mount.**
`VaultIndex.safe_resolve()` re-resolves symlinks and checks containment on
every path touch; `capture()` re-checks even after slug-sanitizing the
title. The nested read-only-vault / read-write-inbox mount in
`docker-compose.yml` is a second, independent enforcement layer (so a bug
in the Python write path still can't touch anything outside the inbox) —
don't collapse it into a single read-write mount, and don't treat the
in-code checks as redundant with it either.

**Test helpers worth knowing before adding to `tests/test_server.py`:**
tool-call assertions should read `result.structuredContent`, not
`result.content` — for a tool returning a list, `content` is split into one
`TextContent` block *per list item* (not one block containing the whole
list), while `structuredContent` wraps the list under a `"result"` key and
a dict-returning tool's `structuredContent` *is* the dict directly. The one
test that does a real `streamablehttp_client` handshake over
`httpx.ASGITransport` needs the `_LifespanManager` helper to manually drive
the ASGI lifespan protocol, since `ASGITransport` doesn't send
startup/shutdown events on its own and the streamable-HTTP session manager
requires its task group to be entered before it'll handle a request.
