# brain-mcp

An MCP server that exposes a markdown, PARA-structured second-brain vault
(Obsidian-compatible) as a set of read tools plus a single constrained write
tool (`capture`, which only ever creates new notes in `00-inbox/`). Runs as a
Docker container, consumed over streamable-HTTP by Claude Code, opencode, and
other MCP clients.

Full behavioural spec: see `brain-mcp-spec.md` in this repo (or wherever you
keep it) if you need the "why" behind a design choice.

## Notes on this vault's actual CONVENTIONS.md

`brain_structure()` reads the `type`/`status`/`domain` enums live out of
`90-meta/CONVENTIONS.md` rather than hardcoding them (see
`_extract_enum_values` in `src/brain_mcp/vault.py`). It's been checked
against `personal_jon`'s real file directly: that file states the three
enums as bold inline labels under one `## Enums` heading (`**type:**
\`note\`, \`project\`, ...`) rather than each getting its own subheading, so
`_extract_enum_values` tries that shape first (bounded to the label's own
paragraph, so it doesn't pick up unrelated backtick-quoted words in the
prose below — e.g. "`domain` is the primary query axis..."), falling back
to a heading-based heuristic for vaults that document enums differently. If
you restructure `CONVENTIONS.md`'s Enums section later, re-check this
parser — the server fails loudly at startup rather than falling back to bad
defaults if it can't parse a non-empty value list for all three fields.

**`uid` format:** `personal_jon`'s `CONVENTIONS.md` currently contradicts
itself here — the frontmatter example shows a UUIDv4, the prose two lines
below it says the real format is `YYYYMMDD-HHmm` (+ a letter on same-minute
collisions), and the actual notes in the vault use three different schemes
between them (a UUIDv4 in one inbox note, `00000000-000N` sentinels in the
meta docs, `YYYYMMDD-000N` sequential counters in the two `_index.md`
files). `capture()` generates UUIDv4, confirmed — that's what the spec
brief asked for, matches the current code, and keeps the `read_note`
short-prefix lookup (≥8 chars) meaningful, which it wouldn't be against a
low-entropy date-based id where everything from the same day shares a
prefix. Worth reconciling `CONVENTIONS.md` itself at some point since it
disagrees with what's actually on disk, but that's a vault content edit,
not something this server does.

## Requirements

- The vault's PARA structure and frontmatter schema as described in the
  vault's own `90-meta/CONVENTIONS.md`.
- Docker (or Docker Compose) on the Unraid box, or Python 3.12 + `uv`
  locally for development.
- `ripgrep` on PATH (bundled in the container image; install separately for
  local dev).

## Configuration

All configuration is via environment variables — nothing about a specific
vault (path, name, token) is hardcoded, so the same image serves any number
of sibling vaults as separate containers.

| Variable       | Required | Default   | Meaning                                      |
|----------------|----------|-----------|-----------------------------------------------|
| `BRAIN_ROOT`   | yes      | —         | Absolute path to the vault root in-container   |
| `BRAIN_NAME`   | yes      | —         | Instance name, e.g. `personal_jon`             |
| `BRAIN_TOKEN`  | yes      | —         | Bearer token required on every MCP request     |
| `PORT`         | no       | `3100`    | Listen port                                    |
| `BIND_ADDRESS` | no       | `0.0.0.0` | Listen address                                 |
| `LOG_LEVEL`    | no       | `INFO`    | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`    |

Copy `.env.example` to `.env` and fill in `BRAIN_TOKEN_JON` (a random
secret — `openssl rand -hex 32` works well) before running Compose.

## Local development

```bash
uv sync --dev          # or: python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
uv run pytest          # or: .venv/bin/python -m pytest
```

Tests run entirely against a synthetic fixture vault built in
`tests/conftest.py` — never against real data.

To run the server locally against a real (or scratch) vault directory:

```bash
export BRAIN_ROOT=/path/to/vault
export BRAIN_NAME=personal_jon
export BRAIN_TOKEN=dev-token
uv run brain-mcp
```

## Running on Unraid

```bash
cp .env.example .env   # fill in BRAIN_TOKEN_JON
docker compose up -d --build
curl http://<unraid-host>:3100/health
```

The compose file mounts the vault read-only and re-mounts just `00-inbox/`
read-write on top of it:

```yaml
volumes:
  - /mnt/user/brain/personal_jon:/vault:ro
  - /mnt/user/brain/personal_jon/00-inbox:/vault/00-inbox:rw
```

This is intentional and load-bearing — even a bug in the write path can't
touch anything outside the inbox, regardless of what the Python code thinks
it's doing. Don't simplify it to a single read-write mount.

### Adding a sibling vault (e.g. `personal_jasmin`)

`docker-compose.yml` has a commented-out `brain-mcp-jasmin` service block on
port 3101 as a template. Uncomment it, set `BRAIN_TOKEN_JASMIN` in `.env`,
and point its volumes at the sibling vault's path.

### Container user / permissions

The container runs as a non-root user, UID:GID `99:100` by default (Unraid's
`nobody:users`) — override at build time with `BRAIN_UID`/`BRAIN_GID` in
`.env` if your share needs different ownership. This user must have write
access to `00-inbox/` on the host share.

## Connecting a client

### Claude Code

```bash
claude mcp add --transport http --scope user brain \
  http://<unraid-host>:3100/mcp \
  --header "Authorization: Bearer <token>"
```

Then `/mcp` in a session should list all six tools.

**Known quirk:** Claude Code has had recurring bugs where headers set via
`--header` aren't sent during session establishment, producing 401s even
though `curl` with the same token works fine. If you hit that, write the
`headers` object directly into the JSON config instead
(`~/.claude/mcp_servers.json` or the relevant scope file):

```json
{
  "mcpServers": {
    "brain": {
      "type": "http",
      "url": "http://<unraid-host>:3100/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

(`type` also accepts `streamable-http` as an alias for `http` in JSON
configs.)

### opencode

opencode attempts OAuth discovery on remote MCP servers by default and will
ignore a static bearer token unless you disable that explicitly:

```jsonc
{
  "mcp": {
    "brain": {
      "type": "remote",
      "url": "http://<unraid-host>:3100/mcp",
      "oauth": false,
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

Without `oauth: false`, opencode will try (and fail) an OAuth handshake
instead of using the header.

## Tools

Six tools, kept deliberately small (tool schemas cost client context):

- `brain_structure()` — orientation: PARA folders + counts, live enums from
  `CONVENTIONS.md`, frontmatter schema, full conventions text, note count.
  Call this first in a session.
- `search_notes(query, domain, type, status, para, tag, limit)` — full-text
  search (ripgrep) with frontmatter filtering. Returns metadata + a ~200
  char snippet, never full bodies.
- `read_note(identifier)` — full note by vault-relative path, full `uid`, or
  an unambiguous `uid` prefix (≥8 chars).
- `list_notes(para, domain, status, type, limit)` — metadata-only browsing,
  no content search.
- `get_backlinks(identifier)` — notes that link to this one, with the
  context line.
- `capture(title, body, domain, tags, source, links)` — the only write:
  creates a new note in `00-inbox/`. Never overwrites, never touches
  anything outside the inbox.

## What this deliberately doesn't do

No semantic search/embeddings, no write access outside `00-inbox/`, no
Obsidian Local REST API dependency (reads the filesystem directly), no
paperless-ngx document fetching (returns document IDs from frontmatter for a
client to chain to a separate paperless MCP server), no git operations. See
`brain-mcp-spec.md` §2 for the reasoning.
