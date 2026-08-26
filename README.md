# brain-mcp

An MCP server that exposes a markdown, PARA-structured second-brain vault
(Obsidian-compatible) as a set of read tools plus a single constrained write
tool (`capture`, which only ever creates new notes in `00-inbox/`). Runs as a
Docker container, consumed over streamable-HTTP by Claude Code, opencode, and
other MCP clients.

Full behavioural spec: see `brain-mcp-spec.md` in this repo (or wherever you
keep it) if you need the "why" behind a design choice.

## Notes on the CONVENTIONS.md parser

`brain_structure()` reads the `type`/`status`/`domain` enums live out of
your vault's `90-meta/CONVENTIONS.md` rather than hardcoding them (see
`_extract_enum_values` in `src/brain_mcp/vault.py`). It's been checked
against a real vault's file directly: that file states the three enums as
bold inline labels under one `## Enums` heading (`**type:** \`note\`,
\`project\`, ...`) rather than each getting its own subheading, so
`_extract_enum_values` tries that shape first (bounded to the label's own
paragraph, so it doesn't pick up unrelated backtick-quoted words in the
prose below — e.g. "`domain` is the primary query axis..."), falling back
to a heading-based heuristic for vaults that document enums differently. If
you restructure `CONVENTIONS.md`'s Enums section later, re-check this
parser — the server fails loudly at startup rather than falling back to bad
defaults if it can't parse a non-empty value list for all three fields.

**`uid` format:** the vault this was validated against currently has a
self-contradiction in its own `CONVENTIONS.md` — the frontmatter example
shows a UUIDv4, the prose two lines below it says the real format is
`YYYYMMDD-HHmm` (+ a letter on same-minute collisions), and the actual
notes on disk use three different schemes between them (a UUIDv4 in one
inbox note, `00000000-000N` sentinels in the meta docs, `YYYYMMDD-000N`
sequential counters in the `_index.md` files). `capture()` generates
UUIDv4 — that's what the original spec asked for, matches the current
code, and keeps the `read_note` short-prefix lookup (≥8 chars) meaningful,
which it wouldn't be against a low-entropy date-based id where everything
from the same day shares a prefix. If your own `CONVENTIONS.md` documents
a different `uid` scheme, that's worth reconciling on the vault side —
not something this server does automatically.

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
| `BRAIN_NAME`   | yes      | —         | Instance name, e.g. `personal` or `family`     |
| `BRAIN_TOKEN`  | yes      | —         | Bearer token required on every MCP request     |
| `PORT`         | no       | `3100`    | Listen port                                    |
| `BIND_ADDRESS` | no       | `0.0.0.0` | Listen address                                 |
| `LOG_LEVEL`    | no       | `INFO`    | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`    |

Copy `.env.example` to `.env` and fill in `BRAIN_NAME`, `BRAIN_VAULT_PATH`
(the host path to your vault), and `BRAIN_TOKEN` (a random secret —
`openssl rand -hex 32` works well) before running Compose.

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
export BRAIN_NAME=personal
export BRAIN_TOKEN=dev-token
uv run brain-mcp
```

## Running on Unraid

```bash
cp .env.example .env   # fill in BRAIN_NAME, BRAIN_VAULT_PATH, BRAIN_TOKEN
docker compose up -d --build   # or: docker compose pull && docker compose up -d
curl http://<unraid-host>:3100/health
```

`--build` builds from this checkout; `pull` instead fetches the same
image prebuilt from GHCR (see "Installing as an Unraid app" below) —
either produces the `ghcr.io/sipho102/brain-mcp:latest` tag locally.

The compose file mounts `BRAIN_VAULT_PATH` read-only and re-mounts just
`00-inbox/` read-write on top of it:

```yaml
volumes:
  - ${BRAIN_VAULT_PATH}:/vault:ro
  - ${BRAIN_VAULT_PATH}/00-inbox:/vault/00-inbox:rw
```

This is intentional and load-bearing — even a bug in the write path can't
touch anything outside the inbox, regardless of what the Python code thinks
it's doing. Don't simplify it to a single read-write mount.

### Installing as an Unraid app instead

If you'd rather manage this from Unraid's Docker tab like any other app —
a form instead of editing `.env`, a Start/Stop/Update button afterward —
there's a template for that in `unraid/brain-mcp.xml`. `.github/workflows/
publish.yml` builds this repo's image and publishes it to GHCR
(`ghcr.io/sipho102/brain-mcp:latest`) on every push to `main`, and the
template pulls that directly — no cloning or building on the Unraid box
at all.

Make the template available to Unraid:

- **Recommended** — in the Docker tab, **Add Container** and paste this
  repo's raw template URL directly into the template field:
  `https://raw.githubusercontent.com/sipho102/brain-mcp/main/unraid/brain-mcp.xml`
  Nothing gets written to Unraid's local templates folder this way, so
  there's no stray file left behind to conflict with later — see the
  caveat below on the alternative method.
- Or copy it into Unraid's local templates folder over SSH first:
  ```bash
  curl -o /boot/config/plugins/dockerMan/templates-user/brain-mcp.xml \
    https://raw.githubusercontent.com/sipho102/brain-mcp/main/unraid/brain-mcp.xml
  ```
  It'll then show up under **Docker → Add Container → template
  dropdown** — but see the note right after Add Container about deleting
  this file once the container exists.

Either way, you'll get a form for the vault path, the inbox path (must be
`<vault path>/00-inbox` — the template can't derive it for you), instance
name, and bearer token; everything else is pre-filled with sane defaults
under "advanced view".

**If you used the local-copy method above, delete that seed file once the
container's been added:**
```bash
rm /boot/config/plugins/dockerMan/templates-user/brain-mcp.xml
```
When you click Apply on Add Container, Unraid saves a *second* file with
your actual values — `my-brain-mcp.xml`, alongside the blank one you
downloaded — and both declare the same container name. With two templates
claiming that name, **Update can end up recreating the container from the
blank original instead of your saved one, wiping BRAIN_NAME/BRAIN_TOKEN/
the paths and leaving it unable to start.** Once `my-brain-mcp.xml` exists
(check `ls /boot/config/plugins/dockerMan/templates-user/`), the seed file
has done its job and isn't needed — remove it so there's no ambiguity.
Clicking **Update** afterward pulls whatever's newest on
`ghcr.io/sipho102/brain-mcp:latest` using your saved config, as expected.

### Serving a second vault

One container serves one vault — there's deliberately no multi-vault
service list in `docker-compose.yml`, and no multi-vault form in the
Unraid template either. On the Unraid-app path above, that just means
running **Add Container** again from the same template with a different
name/paths/token/port. On the Compose path, copy this deployment directory
(or just `docker-compose.yml` + `.env`) elsewhere, fill in that copy's
`.env` with a different `BRAIN_NAME`,
`BRAIN_VAULT_PATH`, `BRAIN_TOKEN`, and `PORT`, and run `docker compose up
-d --build` from there too. Same image (`brain-mcp:latest`), independent
containers.

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

### Clients with no header field at all

Some MCP-client UIs only take a name, transport, and URL — no way to set a
custom `Authorization` header. For those, put the token in the URL instead:

```
http://<unraid-host>:3100/mcp?token=<token>
```

The server checks the `Authorization` header first and falls back to a
`?token=` query parameter, so this works anywhere the header-based config
above does too. Worth knowing before you rely on it: a token in a URL can
end up in more places than a header would — the client's saved config, a
browser's history if the URL is ever opened directly, shell history if
you've pasted it into a terminal. Access logs aren't a concern here
(`uvicorn`'s access log is off), but treat the URL itself as carrying the
secret, the same as you would the token itself.

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
