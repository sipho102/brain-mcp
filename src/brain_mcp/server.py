"""MCP tool definitions and HTTP transport wiring."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from typing import Any
from urllib.parse import parse_qsl

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import capture as capture_mod
from .config import Config, ConfigError
from .vault import PARA_FOLDERS, FRONTMATTER_SCHEMA, VaultIndex, VaultError
from .search import search_content

logger = logging.getLogger("brain_mcp.server")

_Multi = str | list[str] | None


def _normalize_multi(value: _Multi) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return list(value)


def _validate_multi(vault: VaultIndex, field_name: str, values: list[str] | None) -> None:
    if not values:
        return
    for v in values:
        vault.validate_enum(field_name, v)


def _validate_para(values: list[str] | None) -> None:
    if not values:
        return
    invalid = [v for v in values if v not in PARA_FOLDERS]
    if invalid:
        raise VaultError(
            f"Invalid para value(s) {invalid}. Valid values: {', '.join(sorted(PARA_FOLDERS))}"
        )


def build_mcp_server(config: Config, vault: VaultIndex) -> FastMCP:
    mcp = FastMCP(
        name=f"brain-mcp ({config.name})",
        instructions=(
            "Read/search tools over a markdown PARA vault, plus a single "
            "constrained capture() write tool that only creates notes in "
            "00-inbox/. Call brain_structure() first in a session."
        ),
        # We drive uvicorn ourselves (see build_app/_amain), so FastMCP's
        # host/port are irrelevant here. DNS-rebinding host-header checking
        # is FastMCP's protection for a server bound to localhost; ours is
        # reached over a container network / reverse proxy under whatever
        # Host header a client sends, and is already gated by BRAIN_TOKEN
        # (see BearerAuthASGIMiddleware), so we turn it off explicitly
        # rather than fight it via the host= heuristic.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    async def brain_structure() -> dict[str, Any]:
        """Orientation for this vault: PARA folders, enums, frontmatter schema,
        and the full CONVENTIONS.md text. Call this first in a session, and
        again at the start of any new conversation before answering questions
        about the user's own notes, projects, plans, or past decisions —
        don't rely on conversation memory or general knowledge for anything
        that might already be written down here."""
        folders = []
        for key, (prefix, description) in PARA_FOLDERS.items():
            count = sum(1 for n in vault.notes.values() if n.path.startswith(f"{prefix}/"))
            folders.append({"key": key, "path": prefix, "description": description, "note_count": count})
        return {
            "name": config.name,
            "folders": folders,
            "enums": vault.enums,
            "frontmatter_schema": FRONTMATTER_SCHEMA,
            "conventions": vault.conventions_text,
            "total_notes": len(vault.notes),
        }

    @mcp.tool()
    async def search_notes(
        query: str = "",
        domain: _Multi = None,
        type: _Multi = None,
        status: _Multi = None,
        para: _Multi = None,
        tag: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Full-text search across note bodies (ripgrep) with frontmatter
        filtering. Empty query + filters means 'everything matching these
        filters'. Returns metadata plus a ~200-char snippet, never full bodies.
        Use this proactively, without being asked, whenever the user
        references a project, task, person, or topic that could already be
        documented in their vault — check here before answering from general
        knowledge or assuming you'd remember it from earlier in the chat."""
        limit = max(1, min(limit, 100))
        domain_l = _normalize_multi(domain)
        type_l = _normalize_multi(type)
        status_l = _normalize_multi(status)
        para_l = _normalize_multi(para)
        _validate_multi(vault, "domain", domain_l)
        _validate_multi(vault, "type", type_l)
        _validate_multi(vault, "status", status_l)
        _validate_para(para_l)

        content_hits = await asyncio.to_thread(search_content, query, vault.root)
        if query and not content_hits:
            return []

        results = []
        for note in vault.iter_notes(para=para_l, domain=domain_l, status=status_l, type_=type_l, tag=tag):
            hit = content_hits.get(note.path)
            if query and hit is None:
                continue
            snippet = hit["snippet"] if hit else (note.body.strip()[:200] or "")
            rank = hit["count"] if hit else 0
            results.append((rank, note, snippet))

        if query:
            results.sort(key=lambda r: (-r[0], r[1].path))
        else:
            results.sort(key=lambda r: r[1].updated_dt, reverse=True)

        out = []
        for _rank, note, snippet in results[:limit]:
            entry = note.metadata_dict()
            entry["snippet"] = snippet
            out.append(entry)
        return out

    @mcp.tool()
    async def read_note(identifier: str) -> dict[str, Any]:
        """Read a full note by vault-relative path, full uid, or an
        unambiguous uid prefix (>=8 chars)."""
        note = vault.find_by_identifier(identifier)
        return {
            "path": note.path,
            "frontmatter": note.frontmatter,
            "content": note.body,
            "outbound_links": [link.as_dict() for link in note.outbound_links],
            "paperless": note.paperless,
        }

    @mcp.tool()
    async def list_notes(
        para: _Multi = None,
        domain: _Multi = None,
        status: _Multi = None,
        type: _Multi = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Metadata-only enumeration (no content search) for cheap browsing."""
        limit = max(1, min(limit, 200))
        para_l = _normalize_multi(para)
        domain_l = _normalize_multi(domain)
        status_l = _normalize_multi(status)
        type_l = _normalize_multi(type)
        _validate_multi(vault, "domain", domain_l)
        _validate_multi(vault, "type", type_l)
        _validate_multi(vault, "status", status_l)
        _validate_para(para_l)

        notes = list(vault.iter_notes(para=para_l, domain=domain_l, status=status_l, type_=type_l))
        notes.sort(key=lambda n: n.updated_dt, reverse=True)
        return [n.metadata_dict() for n in notes[:limit]]

    @mcp.tool()
    async def get_backlinks(identifier: str) -> list[dict[str, Any]]:
        """Notes whose bodies contain a wikilink resolving to this note."""
        return vault.get_backlinks(identifier)

    @mcp.tool()
    async def capture(
        title: str,
        body: str,
        domain: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
        links: list[str] | None = None,
    ) -> dict[str, str]:
        """Create a new note in 00-inbox/. The only write this server can do."""
        result = await asyncio.to_thread(
            capture_mod.capture_note,
            vault,
            title=title,
            body=body,
            domain=domain,
            tags=tags,
            source=source,
            links=links,
        )
        # Make the new note immediately visible without waiting for the watcher.
        await asyncio.to_thread(vault.reindex_paths, {result["path"]})
        return result

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return mcp


class BearerAuthASGIMiddleware:
    """Raw ASGI middleware: constant-time bearer check on every request
    except the exempt paths (health check). Never logs the token.

    Accepts the token either as a standard `Authorization: Bearer <token>`
    header or as a `?token=<token>` query parameter, for clients that can't
    set custom headers on a remote MCP server (URL-only config forms). The
    header is checked first; the query parameter is a fallback for when a
    client offers no way to send one."""

    def __init__(self, app, token: str, exempt_paths: set[str]):
        self.app = app
        self.token = token
        self.exempt_paths = exempt_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        source_ip = client[0] if client else "unknown"
        supplied = self._extract_token(scope)

        if supplied is None:
            logger.warning("auth rejected: no token supplied from %s", source_ip)
            await self._reject(send)
            return

        if not hmac.compare_digest(supplied, self.token):
            logger.warning("auth rejected: invalid token from %s", source_ip)
            await self._reject(send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _extract_token(scope) -> str | None:
        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        if auth_header.startswith("Bearer "):
            return auth_header[len("Bearer ") :]

        query_string = (scope.get("query_string") or b"").decode("latin-1")
        for key, value in parse_qsl(query_string):
            if key == "token":
                return value
        return None

    @staticmethod
    async def _reject(send):
        body = b'{"error":"unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def build_app(config: Config, vault: VaultIndex):
    mcp = build_mcp_server(config, vault)
    inner = mcp.streamable_http_app()
    return BearerAuthASGIMiddleware(inner, token=config.token, exempt_paths={"/health"})


async def _amain() -> None:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        logging.basicConfig(level="ERROR")
        logging.getLogger("brain_mcp").error("Configuration error: %s", exc)
        raise SystemExit(1) from exc

    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("Starting brain-mcp instance %r, vault root %s", config.name, config.root)

    vault = VaultIndex(config.root)
    try:
        vault.build_index()
    except VaultError as exc:
        logger.error("Failed to build vault index: %s", exc)
        raise SystemExit(1) from exc

    watch_task = asyncio.create_task(vault.watch_forever())
    try:
        if config.transport == "stdio":
            # stdio has no bearer-auth middleware or /health route to wire
            # up - it's a local pipe to a single parent process, not a
            # network listener - so we drive the FastMCP object directly
            # instead of going through build_app()/uvicorn.
            mcp = build_mcp_server(config, vault)
            await mcp.run_stdio_async()
        else:
            app = build_app(config, vault)

            import uvicorn

            uv_config = uvicorn.Config(
                app,
                host=config.bind_address,
                port=config.port,
                log_level=config.log_level.lower(),
                access_log=False,
            )
            server = uvicorn.Server(uv_config)
            await server.serve()
    finally:
        watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch_task


def run() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    run()
