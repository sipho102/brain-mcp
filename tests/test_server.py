from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.memory import create_connected_server_and_client_session

from brain_mcp.config import Config
from brain_mcp.server import build_app, build_mcp_server
from brain_mcp.vault import VaultIndex


class _LifespanManager:
    """Manually drives the ASGI lifespan protocol so streamable-http's
    session manager task group is entered, since httpx.ASGITransport does
    not do this on its own."""

    def __init__(self, app):
        self.app = app
        self._input_queue: asyncio.Queue = asyncio.Queue()
        self._startup_complete = asyncio.Event()
        self._shutdown_complete = asyncio.Event()

    async def _receive(self):
        return await self._input_queue.get()

    async def _send(self, message):
        if message["type"] == "lifespan.startup.complete":
            self._startup_complete.set()
        elif message["type"] == "lifespan.shutdown.complete":
            self._shutdown_complete.set()

    async def __aenter__(self):
        self._task = asyncio.create_task(self.app({"type": "lifespan"}, self._receive, self._send))
        await self._input_queue.put({"type": "lifespan.startup"})
        await self._startup_complete.wait()
        return self

    async def __aexit__(self, *exc_info):
        await self._input_queue.put({"type": "lifespan.shutdown"})
        await self._shutdown_complete.wait()
        await self._task


def _config(vault_root: Path, token: str = "test-token") -> Config:
    return Config(
        root=vault_root,
        name="test-vault",
        token=token,
        port=3100,
        bind_address="127.0.0.1",
        log_level="WARNING",
    )


async def _call(mcp, name, args):
    async with create_connected_server_and_client_session(mcp._mcp_server, raise_exceptions=False) as client:
        return await client.call_tool(name, args)


def _dict_result(result):
    """Dict-returning tools: structuredContent *is* the dict."""
    assert not result.isError, result.content
    return result.structuredContent


def _list_result(result):
    """List-returning tools: structuredContent wraps the list under 'result'
    (content is split into one TextContent block per item, so we don't use
    it here)."""
    assert not result.isError, result.content
    return result.structuredContent["result"]


async def test_brain_structure_lists_tools_and_enums(vault: VaultIndex, vault_root: Path):
    mcp = build_mcp_server(_config(vault_root), vault)
    res = await _call(mcp, "brain_structure", {})
    data = _dict_result(res)
    assert data["name"] == "test-vault"
    assert data["total_notes"] == len(vault.notes)
    assert set(data["enums"]["domain"]) == {"personal", "work", "health", "finance"}
    assert any(f["path"] == "00-inbox" for f in data["folders"])


async def test_search_notes_empty_query_excludes_templates(vault: VaultIndex, vault_root: Path):
    mcp = build_mcp_server(_config(vault_root), vault)
    res = await _call(mcp, "search_notes", {"query": "", "limit": 100})
    results = _list_result(res)
    paths = [r["path"] for r in results]
    assert len(paths) > 0
    assert all("90-meta/templates" not in p for p in paths)
    assert all(not p.endswith("note-template.md") for p in paths)


async def test_search_notes_invalid_enum_errors_clearly(vault: VaultIndex, vault_root: Path):
    mcp = build_mcp_server(_config(vault_root), vault)
    res = await _call(mcp, "search_notes", {"query": "", "domain": "not-a-domain"})
    assert res.isError
    assert "not-a-domain" in res.content[0].text


async def test_read_note_by_full_uid_and_prefix(vault: VaultIndex, vault_root: Path):
    mcp = build_mcp_server(_config(vault_root), vault)
    full = await _call(mcp, "read_note", {"identifier": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"})
    data = _dict_result(full)
    assert data["path"] == "20-areas/homelab/_index.md"
    assert "paperless" in data
    assert data["paperless"] == []

    prefix = await _call(mcp, "read_note", {"identifier": "aaaaaaaa"})
    data2 = _dict_result(prefix)
    assert data2["path"] == "20-areas/homelab/_index.md"


async def test_read_note_not_found_is_clear_error(vault: VaultIndex, vault_root: Path):
    mcp = build_mcp_server(_config(vault_root), vault)
    res = await _call(mcp, "read_note", {"identifier": "nope/does-not-exist.md"})
    assert res.isError


async def test_get_backlinks(vault: VaultIndex, vault_root: Path):
    mcp = build_mcp_server(_config(vault_root), vault)
    res = await _call(mcp, "get_backlinks", {"identifier": "20-areas/homelab/router-notes.md"})
    results = _list_result(res)
    paths = {r["path"] for r in results}
    assert "20-areas/homelab/_index.md" in paths


async def test_capture_end_to_end(vault: VaultIndex, vault_root: Path):
    mcp = build_mcp_server(_config(vault_root), vault)
    res = await _call(
        mcp,
        "capture",
        {"title": "From a test", "body": "hello", "source": "pytest"},
    )
    data = _dict_result(res)
    assert data["path"].startswith("00-inbox/")
    assert (vault_root / data["path"]).exists()
    # Immediately visible without waiting for the watcher.
    assert data["path"] in vault.notes


# -- auth (raw ASGI, no MCP client machinery) -------------------------------


async def test_health_is_unauthenticated(vault: VaultIndex, vault_root: Path):
    app = build_app(_config(vault_root), vault)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_mcp_endpoint_rejects_missing_token(vault: VaultIndex, vault_root: Path):
    app = build_app(_config(vault_root), vault)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
    assert resp.status_code == 401


async def test_mcp_endpoint_rejects_wrong_token(vault: VaultIndex, vault_root: Path):
    app = build_app(_config(vault_root), vault)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert resp.status_code == 401


async def test_mcp_endpoint_rejects_wrong_token_in_query(vault: VaultIndex, vault_root: Path):
    app = build_app(_config(vault_root), vault)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/mcp?token=wrong-token",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        )
    assert resp.status_code == 401


async def test_mcp_initialize_handshake_with_token_in_query_param(vault: VaultIndex, vault_root: Path):
    # For clients (e.g. some MCP-client GUIs) that only take a URL and can't
    # set a custom Authorization header.
    app = build_app(_config(vault_root), vault)

    def factory(headers=None, timeout=None, auth=None):
        kwargs = {"follow_redirects": True, "transport": httpx.ASGITransport(app=app), "base_url": "http://testserver"}
        if headers:
            kwargs["headers"] = headers
        return httpx.AsyncClient(**kwargs)

    async with _LifespanManager(app):
        async with streamablehttp_client(
            "http://testserver/mcp?token=test-token",
            httpx_client_factory=factory,
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                assert result.serverInfo.name.startswith("brain-mcp")


async def test_mcp_initialize_handshake_with_correct_token(vault: VaultIndex, vault_root: Path):
    app = build_app(_config(vault_root), vault)

    def factory(headers=None, timeout=None, auth=None):
        kwargs = {"follow_redirects": True, "transport": httpx.ASGITransport(app=app), "base_url": "http://testserver"}
        if headers:
            kwargs["headers"] = headers
        return httpx.AsyncClient(**kwargs)

    async with _LifespanManager(app):
        async with streamablehttp_client(
            "http://testserver/mcp",
            headers={"Authorization": "Bearer test-token"},
            httpx_client_factory=factory,
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                assert result.serverInfo.name.startswith("brain-mcp")
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert names == {
                    "brain_structure",
                    "search_notes",
                    "read_note",
                    "list_notes",
                    "get_backlinks",
                    "capture",
                }

