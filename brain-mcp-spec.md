# brain-mcp — implementation brief

Build a Model Context Protocol server that exposes a markdown second-brain
vault (Obsidian, PARA-structured) as a set of read tools plus a single
constrained write tool. It runs as a Docker container on Unraid and is
consumed over HTTP by Claude Code, opencode, and other MCP clients.

This document is the specification. Read it fully before writing code, and
raise disagreements rather than silently deviating.

---

## 1. Context

The vault lives on an Unraid share at `/mnt/user/brain/personal_jon`. It is
also mounted on a desktop workstation for Obsidian. Its structure and
metadata schema are defined authoritatively in the vault's own
`90-meta/CONVENTIONS.md` — read that file, it is the contract this server
serves.

Summary of what matters for implementation:

- PARA folders: `00-inbox/`, `10-projects/`, `20-areas/`, `30-resources/`,
  `40-archive/`, plus `90-meta/` and `assets/`.
- Every note is markdown with YAML frontmatter carrying `uid`, `title`,
  `type`, `status`, `domain`, `tags`, `created`, `updated`, and optionally
  `paperless` (an array of paperless-ngx document IDs) and `aliases`.
- `uid` is a **UUIDv4** string, generated at note creation and never changed
  thereafter. It survives renames and moves and is the permanent handle for
  a note.
- `created` and `updated` are **timestamps, not dates**, in the format
  `YYYY-MM-DD HH:mm:ss`. Parse them as naive local time — there is no
  timezone suffix and none should be added.
- `type`, `status`, and `domain` are closed enums, defined in
  `CONVENTIONS.md`.
- Notes link to each other with Obsidian wikilinks: `[[target]]` or
  `[[target|display text]]`.

There will eventually be sibling vaults (`personal_jasmin`, `family`), each
served by a separate container running this same image. **Nothing about
`personal_jon` may be hardcoded.** Vault path, instance name, and auth token
all come from environment variables.

---

## 2. Non-goals

Do not build these. They are deliberately excluded.

- **No semantic search, embeddings, or vector store.** Lexical only in this
  version. Keep the tool signatures stable enough that a semantic backend
  could be swapped in later without changing the tool contracts.
- **No write access outside `00-inbox/`.** The server cannot move, rename,
  edit, or delete existing notes. Triage happens in a separate local
  session, not through this server.
- **No Obsidian dependency.** Do not use the Obsidian Local REST API
  plugin. Read the filesystem directly so the server works whether or not
  Obsidian is running anywhere.
- **No paperless-ngx integration.** Return document IDs from frontmatter and
  let the client chain to the existing paperless MCP server. Do not fetch
  document content.
- **No git operations.**

---

## 3. Stack

- Python 3.12
- `mcp` Python SDK (FastMCP), streamable HTTP transport
- `python-frontmatter` for YAML frontmatter parsing
- `watchfiles` for filesystem change detection
- `ripgrep` binary, installed in the image, invoked as a subprocess for
  content search
- `uv` for dependency management

If you have a strong reason to prefer a different library, say so before
substituting.

---

## 4. Configuration

All via environment variables. Fail fast at startup with a clear error if a
required variable is missing or the vault root is not readable.

| Variable       | Required | Default | Meaning                                    |
|----------------|----------|---------|--------------------------------------------|
| `BRAIN_ROOT`   | yes      | —       | Absolute path to the vault root in-container |
| `BRAIN_NAME`   | yes      | —       | Instance name, e.g. `personal_jon`. Used in server identity and log lines |
| `BRAIN_TOKEN`  | yes      | —       | Bearer token required on every request      |
| `PORT`         | no       | `3100`  | Listen port                                 |
| `BIND_ADDRESS` | no       | `0.0.0.0` | Listen address                            |
| `LOG_LEVEL`    | no       | `INFO`  | Standard Python log levels                  |

---

## 5. What counts as a note

Only `.md` files, and only outside the exclusion list below. This matters
more than it sounds — several things in this vault are markdown-shaped but
are not notes, and indexing them produces garbage results.

**Excluded from indexing and search entirely:**

| Path                  | Why                                                        |
|-----------------------|------------------------------------------------------------|
| `90-meta/templates/`  | Contain unevaluated Templater syntax (`<% tp.user.uuid() %>`) in the frontmatter position. They will not parse as valid YAML and must never surface in results. |
| `90-meta/userscripts/`| JavaScript, not notes                                       |
| `90-meta/reports/`    | Generated lint output, regenerated constantly               |
| `.obsidian/`          | Application state                                           |
| `.claude/`            | Agent skill definitions                                     |
| `.trash/`             | Obsidian's soft-delete                                      |
| `assets/`             | Binary attachments                                          |

`90-meta/CONVENTIONS.md` and `90-meta/triage.md` **are** indexed — they are
real notes and should be findable.

Non-`.md` files anywhere (`.base`, `.gitkeep`, `.js`, images) are ignored.
Note that `.base` files are Obsidian database view definitions and will
appear in `90-meta/` — skip them silently, they are not an error condition.

Exclusions should be a module-level constant, not scattered through the
code, so a future vault can adjust them in one place.

---

## 6. Tools

Six tools. Keep the surface exactly this size — MCP tool schemas consume
client context, and this server will be enabled alongside others.

Every tool must validate that resolved paths stay inside `BRAIN_ROOT` after
symlink resolution. Reject traversal attempts with a clear error rather than
a stack trace.

### 6.1 `brain_structure()`

No arguments. Returns orientation for a client that has never seen this
vault. Clients should call it first in a session.

Returns:

- `name` — the `BRAIN_NAME` value
- `folders` — each PARA folder with its purpose and current note count
- `enums` — the current valid values for `type`, `status`, `domain`, read
  live from `CONVENTIONS.md` rather than hardcoded in Python
- `frontmatter_schema` — field names, types, which are required
- `conventions` — the full text of `90-meta/CONVENTIONS.md`
- `total_notes`

Parsing the enums out of `CONVENTIONS.md` means editing that one file
updates both the human documentation and the server behaviour. If the file
is missing or unparseable, fail loudly at startup — do not fall back to
hardcoded defaults.

### 6.2 `search_notes(query, domain=None, type=None, status=None, para=None, tag=None, limit=20)`

Full-text search across note bodies with frontmatter filtering applied on
top.

- `query` — string, passed to ripgrep. Case-insensitive, smart-case.
- `domain`, `type`, `status` — must be valid enum values; reject invalid
  ones with an error naming the valid set. Accept a single value or a list.
- `para` — one of `inbox`, `projects`, `areas`, `resources`, `archive`, or a
  list. Maps to folder prefixes.
- `tag` — matches against the `tags` frontmatter array.
- `limit` — default 20, cap at 100.

An empty or omitted `query` with filters set is valid and means "everything
matching these filters".

Returns a list of: `path` (vault-relative), `uid`, `title`, `type`,
`status`, `domain`, `tags`, `updated`, and `snippet` — roughly 200
characters of matching context with the match in it. Never return full note
bodies from search; that is `read_note`'s job.

Sort by relevance if ripgrep gives you a usable signal, otherwise by
`updated` descending. Since `updated` now carries seconds, that sort is
stable and meaningful for notes touched on the same day.

### 6.3 `read_note(identifier)`

`identifier` is one of:

- a vault-relative path (`20-areas/homelab/_index.md`)
- a full `uid` UUID
- an unambiguous `uid` prefix of at least 8 characters

The prefix case exists because full UUIDs are painful for a model to carry
around accurately between tool calls. If a prefix matches more than one
note, return an error listing the candidates with their titles — do not
guess.

Returns: `path`, full parsed `frontmatter`, `content` (body without the
frontmatter block), `outbound_links` (resolved wikilink targets, with
unresolved ones flagged), and `paperless` (the document ID array, present
even when empty, so clients notice the affordance).

Error clearly if the identifier matches nothing.

### 6.4 `list_notes(para=None, domain=None, status=None, type=None, limit=50)`

Metadata-only enumeration. No content search, no snippets — this exists so a
client can browse cheaply without paying for a search.

Returns the same metadata fields as `search_notes` minus `snippet`. Sort by
`updated` descending. Cap `limit` at 200.

### 6.5 `get_backlinks(identifier)`

Notes whose bodies contain a wikilink resolving to the target note. Accepts
the same identifier forms as `read_note`.

Returns a list of: `path`, `uid`, `title`, `domain`, and `context` — the
line the link appears on, so the caller can see *why* it links.

### 6.6 `capture(title, body, domain=None, tags=None, source=None, links=None)`

The only write. Creates a new note in `00-inbox/`.

- Filename: `YYYY-MM-DD-<slug>.md`, slug derived from `title` — lowercased,
  Unicode-normalised (NFD, combining marks stripped, so `ü` becomes `u`),
  non-alphanumerics collapsed to hyphens, leading and trailing hyphens
  trimmed, truncated to 60 characters. This mirrors the slug logic in the
  vault's Templater note template; keep the two consistent.
- On filename collision, append `-2`, `-3`, and so on. **Never overwrite an
  existing file under any circumstance.**
- Frontmatter: generate `uid` as a **UUIDv4**. Set `type: note`,
  `status: inbox`, `created` and `updated` to now in
  `YYYY-MM-DD HH:mm:ss`, `domain` to the supplied value or `personal` as
  fallback.
- `source` — free text recording where this came from (a URL, a
  conversation, a client name). Written into the body under a `## Source`
  heading, not into frontmatter.
- `links` — a list of wikilink targets appended under a `## Related`
  heading.
- Validate `domain` against the enum. Reject invalid values with an error
  listing valid ones — do not silently coerce.

Returns the created `path` and `uid`.

Write atomically: write to a temp file in the same directory, then rename.
The vault is on a network share with Obsidian potentially watching it, and a
half-written file will be picked up.

---

## 7. Index

Build an in-memory index at startup: for every markdown file that passes the
exclusion rules in section 5, its frontmatter, its `uid`, and its outbound
wikilinks. This serves `list_notes`, `get_backlinks`, uid resolution, and
the frontmatter filtering layer of `search_notes`.

Maintain a `uid` → path map for exact lookups, and support prefix matching
over its keys for the abbreviated-uid case in `read_note`.

Keep it current with `watchfiles` on `BRAIN_ROOT`. Re-parse only files that
changed. Debounce — Obsidian writes in bursts, and Linter rewrites
frontmatter on every save, so the same file will fire repeatedly within a
second.

Content search stays on ripgrep against the filesystem. Do not build a
content index; ripgrep over a few thousand markdown files is effectively
instant and the vault is nowhere near needing more. Pass ripgrep explicit
glob exclusions matching section 5 so excluded paths never appear even in
raw content hits.

Startup must not block on indexing a large vault. Either index before
accepting connections and log progress, or serve immediately and return a
clear "index building" error. Prefer the former for simplicity.

---

## 8. Auth

Every MCP request requires `Authorization: Bearer <BRAIN_TOKEN>`. Reject
anything else with 401.

Use a constant-time comparison for the token. Log auth failures with the
source IP at WARNING level, but never log the token itself, not even
partially.

A health endpoint at `/health` may be unauthenticated — return only a
status, never vault contents or counts.

---

## 9. Container

Write a `Dockerfile` and a `docker-compose.yml`.

The compose file must express the write constraint through mounts:

```yaml
services:
  brain-mcp-jon:
    image: brain-mcp:latest
    container_name: brain-mcp-jon
    restart: unless-stopped
    ports:
      - "3100:3100"
    environment:
      BRAIN_ROOT: /vault
      BRAIN_NAME: personal_jon
      BRAIN_TOKEN: ${BRAIN_TOKEN_JON}
      PORT: "3100"
    volumes:
      - /mnt/user/brain/personal_jon:/vault:ro
      - /mnt/user/brain/personal_jon/00-inbox:/vault/00-inbox:rw
```

The nested bind with differing flags is intentional and load-bearing: even a
bug in the write path cannot touch anything outside the inbox. Do not
"simplify" it to a single read-write mount.

Run as a non-root user. The UID must be able to write to the inbox on an
Unraid share — make it configurable via build arg or environment, defaulting
to `99:100` (Unraid's `nobody:users`).

Include a commented-out second service block showing how a `personal_jasmin`
instance would be added on port 3101, to prove the multi-instance design
works.

Add a healthcheck hitting `/health`.

---

## 10. Testing

`pytest`, against a temporary fixture vault built in the test setup — never
against real data.

Cover at minimum:

- Frontmatter parsing, including malformed frontmatter and missing fields.
  A single broken note must not crash indexing; log it and continue.
- **Templater syntax in frontmatter position is excluded.** Put a file
  containing `uid: <% tp.user.uuid() %>` in the fixture's
  `90-meta/templates/` and assert it never appears in any tool's output.
  This is the single most likely regression.
- Non-`.md` files (`.base`, `.gitkeep`) are skipped without error.
- Enum validation rejecting invalid values on every tool that accepts them.
- Wikilink resolution: plain, aliased (`[[target|text]]`), and unresolved.
- uid resolution: full UUID, valid unambiguous prefix, ambiguous prefix
  (must error and list candidates), prefix shorter than 8 characters (must
  be rejected), no match.
- Timestamp parsing of `YYYY-MM-DD HH:mm:ss`, and correct descending sort
  for two notes updated in the same minute.
- Path traversal attempts via both `path` and `uid` identifiers.
- `capture` filename collision handling, and slug generation for titles
  containing umlauts, punctuation, and emoji.
- `capture` refusing to write outside `00-inbox/` even when handed a
  malicious title.
- Auth: missing token, wrong token, correct token.

---

## 11. Acceptance

Done when:

1. `docker compose up` starts cleanly and `/health` responds.
2. `curl` with a valid bearer token completes an MCP `initialize` handshake;
   without one it gets a 401.
3. `claude mcp add --transport http --scope user brain http://<host>:3100/mcp --header "Authorization: Bearer <token>"` connects, and `/mcp` in a Claude Code session lists all six tools.
4. An opencode config with `type: "remote"` and `oauth: false` connects and
   lists the same six tools.
5. `search_notes` with an empty query returns real notes and **zero**
   template files.
6. `read_note` resolves a note by full UUID and by its first 8 characters.
7. A `capture` call from a client produces a correctly-formed note in
   `00-inbox/` with a valid UUIDv4 `uid` and timestamped `created`, which
   Obsidian renders without complaint and which the vault's inbox `.base`
   view picks up.
8. Editing a note in Obsidian is reflected in `get_backlinks` output within
   a few seconds, without restarting the container.
9. Test suite passes.

---

## 12. Deliverables

```
brain-mcp/
├── src/brain_mcp/
│   ├── __init__.py
│   ├── server.py       # MCP tool definitions and transport
│   ├── vault.py        # index, parsing, path safety, exclusions
│   ├── search.py       # ripgrep wrapper
│   ├── capture.py      # the write path
│   └── config.py       # env parsing and validation
├── tests/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── README.md           # build, run, client config for Claude Code and opencode
```

---

## 13. Known client quirks

Worth knowing before debugging these from scratch:

- **opencode** attempts OAuth discovery on remote MCP servers by default and
  will ignore a static bearer token unless the config sets `oauth: false`
  explicitly. Document this in the README.
- **Claude Code** has had recurring bugs where headers configured via
  `--header` are not sent during session establishment, producing 401s even
  though `curl` with the same token works. If this surfaces, the workaround
  is writing the `headers` object directly into the JSON config. Document
  it.
- In JSON client configs, `type` accepts `streamable-http` as an alias for
  `http`.
