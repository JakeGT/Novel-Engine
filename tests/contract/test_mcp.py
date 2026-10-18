"""The MCP endpoint (QRY-003): access keys, both protocol eras, read parity, proposal-only writes."""

from __future__ import annotations

import base64
from datetime import timedelta

import pytest

from backend.domain.identity import utcnow

pytestmark = pytest.mark.contract

MODERN = "2026-07-28"
WRITE_TOOLS = {"suggest_changes"}
READ_TOOLS = {
    "list_stories",
    "search_entries",
    "read_entry",
    "list_plan",
    "read_scene",
    "timeline",
    "record_history",
    "open_reviews",
}
_EMPTY_DEFINITION = {
    "label": "core",
    "description": "",
    "entity_extensions": [],
    "predicate_extensions": [],
}


def _project(container) -> str:
    return container.settings.AUTH_LOCAL_PROJECT_ID


def _sample_story(client, container) -> dict[str, str]:
    base = f"/api/v1/projects/{_project(container)}"
    draft = client.post(f"{base}/schemas/drafts", json={"definition": _EMPTY_DEFINITION})
    version = draft.json()["version"]["id"]
    assert client.post(f"{base}/schemas/{version}/publish").status_code == 200
    assert client.post(f"{base}/entities/sample", json={"expected_revision": 0}).status_code == 201
    entries = client.get(f"{base}/entities", params={"limit": 50}).json()["items"]
    return {item["name"]: item["id"] for item in entries}


def _make_key(client, **body) -> tuple[str, dict]:
    response = client.post("/api/v1/access-keys", json={"name": "My assistant", **body})
    assert response.status_code == 201, response.text
    created = response.json()
    return created["key"], created["access_key"]


def _rpc(client, key: str | None, method: str, params: dict | None = None, *, headers=None):
    body = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}
    sent = {"Accept": "application/json, text/event-stream"}
    if key is not None:
        sent["Authorization"] = f"Bearer {key}"
    sent.update(headers or {})
    return client.post("/mcp", json=body, headers=sent)


def _modern(client, key: str, method: str, params: dict | None = None, **headers):
    params = dict(params or {})
    params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    sent = {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
    if method == "tools/call":
        sent["Mcp-Name"] = params["name"]
    sent.update(headers)
    return _rpc(client, key, method, params, headers=sent)


def _call(client, key: str, tool: str, arguments: dict | None = None) -> dict:
    response = _rpc(
        client,
        key,
        "tools/call",
        {"name": tool, "arguments": arguments or {}},
        headers={"MCP-Protocol-Version": "2025-11-25"},
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_the_endpoint_needs_a_working_access_key(auth_client) -> None:
    # A signed-in browser session is not enough: keys only.
    refused = _rpc(auth_client, None, "tools/list")
    assert refused.status_code == 401
    assert refused.headers["WWW-Authenticate"].startswith("Bearer ")
    assert refused.json()["error"]["message"] == "An access key from Novel Engine is required."
    assert _rpc(auth_client, "nvlpat_" + "a" * 49, "tools/list").status_code == 401
    assert auth_client.get("/mcp").status_code == 405
    assert auth_client.delete("/mcp").status_code == 405
    assert auth_client.get("/.well-known/oauth-protected-resource").status_code == 404


def test_legacy_handshake_and_tools_without_a_session(auth_client) -> None:
    key, _ = _make_key(auth_client)
    started = _rpc(
        auth_client,
        key,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}},
    )
    assert started.status_code == 200
    result = started.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "novel-engine"
    assert "mcp-session-id" not in {name.lower() for name in started.headers}
    # Asking for a version this server does not speak gets its newest of that era.
    newest = _rpc(auth_client, key, "initialize", {"protocolVersion": "2024-11-05"})
    assert newest.json()["result"]["protocolVersion"] == "2025-11-25"

    notified = auth_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert notified.status_code == 202 and notified.content == b""

    listed = _rpc(auth_client, key, "tools/list", headers={"MCP-Protocol-Version": "2025-11-25"})
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert set(names) == READ_TOOLS
    assert names == sorted(names, key=names.index)  # a fixed order
    for tool in listed.json()["result"]["tools"]:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["inputSchema"]["type"] == "object"


def test_no_tool_commits_approves_or_changes_rules(auth_client) -> None:
    key, _ = _make_key(auth_client, may_suggest=True)
    listed = _rpc(auth_client, key, "tools/list").json()["result"]["tools"]
    names = {tool["name"] for tool in listed}
    assert names == READ_TOOLS | WRITE_TOOLS
    for forbidden in ("commit", "approve", "rule", "policy", "delete", "role", "delegat"):
        assert not any(forbidden in name for name in names)
    for name in ("commit_changes", "approve_proposal", "set_rule"):
        response = _rpc(auth_client, key, "tools/call", {"name": name, "arguments": {}})
        assert response.json()["error"]["code"] == -32602


def test_modern_requests_are_stateless_and_headers_must_match(auth_client) -> None:
    key, _ = _make_key(auth_client)
    discovered = _modern(auth_client, key, "server/discover")
    assert discovered.status_code == 200, discovered.text
    result = discovered.json()["result"]
    assert result["resultType"] == "complete"
    assert MODERN in result["supportedVersions"] and "2025-11-25" in result["supportedVersions"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "novel-engine"

    listed = _modern(auth_client, key, "tools/list").json()["result"]
    assert listed["cacheScope"] == "private" and listed["ttlMs"] > 0
    assert {tool["name"] for tool in listed["tools"]} == READ_TOOLS

    mismatch = _modern(auth_client, key, "tools/list", **{"Mcp-Method": "tools/call"})
    assert mismatch.status_code == 400 and mismatch.json()["error"]["code"] == -32020
    wrong_name = _modern(
        auth_client,
        key,
        "tools/call",
        {"name": "list_plan", "arguments": {}},
        **{"Mcp-Name": "read_scene"},
    )
    assert wrong_name.status_code == 400 and wrong_name.json()["error"]["code"] == -32020
    encoded = "=?base64?" + base64.b64encode(b"list_stories").decode() + "?="
    named = _modern(
        auth_client,
        key,
        "tools/call",
        {"name": "list_stories", "arguments": {}},
        **{"Mcp-Name": encoded},
    )
    assert named.status_code == 200 and named.json()["result"]["isError"] is False

    params = {
        "_meta": {"io.modelcontextprotocol/protocolVersion": "1900-01-01"},
    }
    unsupported = _rpc(
        auth_client,
        key,
        "tools/list",
        params,
        headers={"MCP-Protocol-Version": "1900-01-01", "Mcp-Method": "tools/list"},
    )
    assert unsupported.status_code == 400
    error = unsupported.json()["error"]
    assert error["code"] == -32022 and MODERN in error["data"]["supported"]

    unknown = _modern(auth_client, key, "resources/list")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == -32601


def test_a_foreign_web_page_cannot_use_the_endpoint(auth_client) -> None:
    key, _ = _make_key(auth_client)
    refused = _rpc(auth_client, key, "tools/list", headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403
    # Under DNS rebinding a hostile page's Origin and Host agree: Host is never trusted.
    rebound = _rpc(
        auth_client,
        key,
        "tools/list",
        headers={"Origin": "http://evil.example:8000", "Host": "evil.example:8000"},
    )
    assert rebound.status_code == 403
    on_this_machine = _rpc(
        auth_client, key, "tools/list", headers={"Origin": "http://localhost:5173"}
    )
    assert on_this_machine.status_code == 200


def test_reads_match_the_http_api(auth_client, fake_container) -> None:
    names = _sample_story(auth_client, fake_container)
    key, _ = _make_key(auth_client)
    base = f"/api/v1/projects/{_project(fake_container)}"

    stories = _call(auth_client, key, "list_stories")["structuredContent"]["stories"]
    assert [story["id"] for story in stories] == [_project(fake_container)]

    found = _call(auth_client, key, "search_entries", {"query": "Ines"})["structuredContent"]
    http = auth_client.get(f"{base}/entities/search", params={"q": "Ines", "limit": 20}).json()
    assert found["entries"] == http["items"]

    entry = _call(auth_client, key, "read_entry", {"entry_id": names["Ines Varga"]})
    entry = entry["structuredContent"]
    assert entry["entry"] == auth_client.get(f"{base}/entities/{names['Ines Varga']}").json()
    http_facts = auth_client.get(
        f"{base}/facts", params={"subject_id": names["Ines Varga"], "limit": 200}
    ).json()["items"]
    assert {fact["id"] for fact in http_facts} <= {fact["id"] for fact in entry["facts"]}

    plan = _call(auth_client, key, "list_plan")["structuredContent"]
    assert plan["items"] == auth_client.get(f"{base}/plan").json()["items"]

    timeline = _call(auth_client, key, "timeline")["structuredContent"]
    http_timeline = auth_client.get(f"{base}/timeline").json()
    assert {k: v for k, v in timeline.items() if k != "story"} == http_timeline

    history = _call(
        auth_client,
        key,
        "record_history",
        {"record_type": "entity", "record_id": names["Ines Varga"]},
    )["structuredContent"]
    http_history = auth_client.get(f"{base}/provenance/entity/{names['Ines Varga']}").json()
    assert history["changes"] == http_history["items"] and history["changes"]

    waiting = _call(auth_client, key, "open_reviews")["structuredContent"]
    assert waiting["waiting"] == []


def test_a_point_of_view_keeps_to_what_that_character_knows(auth_client, fake_container) -> None:
    names = _sample_story(auth_client, fake_container)
    base = f"/api/v1/projects/{_project(fake_container)}"
    benedek, rooke, amsel = names["Benedek Varga"], names["Captain Rooke"], names["Dr. Amsel"]

    def fact(subject: str, target: str, visibility: str, revision: int) -> str:
        response = auth_client.post(
            f"{base}/facts",
            json={
                "proposition": {
                    "subject_id": subject,
                    "predicate": "KNOWS",
                    "object": {
                        "kind": "entity_ref",
                        "entity_id": target,
                        "project_id": _project(fake_container),
                    },
                },
                "expected_revision": revision,
                "visibility": visibility,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    common = fact(benedek, amsel, "common", 1)
    secret = fact(benedek, rooke, "secret", 2)
    key, _ = _make_key(auth_client)

    seen = _call(auth_client, key, "read_entry", {"entry_id": benedek, "as_known_by": rooke})
    seen = seen["structuredContent"]
    http = auth_client.get(f"{base}/knowledge/characters/{rooke}").json()

    # Parity: exactly the facts the HTTP API says Rooke knows that are about Benedek.
    known_about = {
        item["fact"]["id"]
        for item in http["knows"]
        if benedek in (item["fact"]["subject_id"], item["fact"]["object"].get("entity_id"))
    }
    assert {item["id"] for item in seen["facts"]} == known_about == {common}
    assert secret in {item["id"] for item in http["unaware"]}
    assert secret not in seen.__repr__()
    # Rooke's mistaken belief is his belief; nothing says it is mistaken.
    assert [belief["subject_id"] for belief in seen["beliefs"]] == [benedek]
    assert "wrong" not in seen and "attributes" not in seen["entry"]

    # The author's own view has everything, the secret too.
    everything = _call(auth_client, key, "read_entry", {"entry_id": benedek})
    assert secret in {item["id"] for item in everything["structuredContent"]["facts"]}


def test_reading_a_scene_gives_its_card_and_the_author_s_text(auth_client, fake_container) -> None:
    base = f"/api/v1/projects/{_project(fake_container)}"
    _sample_story(auth_client, fake_container)
    scene = auth_client.post(
        f"{base}/plan", json={"kind": "scene", "title": "The lamp", "expected_revision": 1}
    )
    assert scene.status_code == 201, scene.text
    scene_id = scene.json()["id"]
    saved = auth_client.put(
        f"{base}/plan/{scene_id}/studio/text", json={"content": "The lamp went out at nine."}
    )
    assert saved.status_code == 200, saved.text
    key, _ = _make_key(auth_client)

    read = _call(auth_client, key, "read_scene", {"scene_id": scene_id})["structuredContent"]
    assert read["scene"]["title"] == "The lamp"
    assert read["text"]["content"] == "The lamp went out at nine."
    assert read["text"]["words"] == 6

    missing = _call(auth_client, key, "read_scene", {"scene_id": "plan_missing"})
    assert missing["isError"] is True
    bad = _call(auth_client, key, "read_scene", {"scene": scene_id})
    assert bad["isError"] is True and "scene" in bad["content"][0]["text"]


def test_suggestions_only_go_to_review(auth_client, fake_container) -> None:
    names = _sample_story(auth_client, fake_container)
    base = f"/api/v1/projects/{_project(fake_container)}"
    head = auth_client.get(f"{base}/revisions").json()["items"][0]["revision"]

    reader, _ = _make_key(auth_client)
    change = {"op": "create_entity", "id": "tmp:ada", "type": "Character", "name": "Ada"}
    refused = _call(auth_client, reader, "suggest_changes", {"changes": [change]})
    assert refused["isError"] is True
    assert refused["content"][0]["text"] == "This key may only read."

    suggester, _ = _make_key(auth_client, may_suggest=True, name="Suggests")
    made = _call(
        auth_client,
        suggester,
        "suggest_changes",
        {"changes": [change], "note": "Ada is mentioned in chapter two."},
    )
    assert made["isError"] is False, made
    proposal = made["structuredContent"]
    assert proposal["status"] == "needs_review"

    detail = auth_client.get(f"{base}/mutations/{proposal['proposal_id']}").json()
    assert detail["origin"] == "machine"
    # Traceable to the key that made it, by its id and never by the key itself.
    suggester_id = next(
        item["id"]
        for item in auth_client.get("/api/v1/access-keys").json()
        if item["name"] == "Suggests"
    )
    submitted = [item["reason"] for item in detail["transitions"]]
    assert any(f"(key {suggester_id})" in reason for reason in submitted), submitted
    assert suggester not in auth_client.get(f"{base}/mutations/{proposal['proposal_id']}").text
    assert detail["requires_review"] is True
    assert "protected_origin" in detail["review_reasons"]
    assert detail["commit_id"] is None
    # Nothing in the story changed.
    assert auth_client.get(f"{base}/revisions").json()["items"][0]["revision"] == head
    assert "Ada" not in {
        item["name"] for item in auth_client.get(f"{base}/entities").json()["items"]
    }
    assert names  # the sample is there
    waiting = _call(auth_client, reader, "open_reviews")["structuredContent"]["waiting"]
    assert [item["id"] for item in waiting] == [proposal["proposal_id"]]

    malformed = _call(auth_client, suggester, "suggest_changes", {"changes": [{"op": "commit"}]})
    assert (
        malformed["isError"] is True and "not in the right form" in malformed["content"][0]["text"]
    )


def test_withdrawn_and_expired_keys_are_refused_and_use_is_counted(
    auth_client, fake_container
) -> None:
    key, made = _make_key(auth_client)
    assert _call(auth_client, key, "list_stories")["isError"] is False
    assert _call(auth_client, key, "read_entry", {"entry_id": "ent_none"})["isError"] is True

    uses = auth_client.get(f"/api/v1/access-keys/{made['id']}/uses").json()
    assert {(use["tool"], use["calls"], use["refused"]) for use in uses} == {
        ("list_stories", 1, 0),
        ("read_entry", 1, 1),
    }
    listed = auth_client.get("/api/v1/access-keys").json()
    assert listed[0]["last_used_at"] is not None and "key" not in listed[0]
    assert key not in auth_client.get("/api/v1/access-keys").text

    assert auth_client.post(f"/api/v1/access-keys/{made['id']}/revoke").status_code == 200
    gone = _rpc(auth_client, key, "tools/list")
    assert gone.status_code == 401 and "withdrawn" in gone.json()["error"]["message"]

    other, other_made = _make_key(auth_client, name="Short", expires_in_days=1)
    fake_container.access_keys.keys[other_made["id"]].expires_at = utcnow() - timedelta(seconds=1)
    expired = _rpc(auth_client, other, "tools/list")
    assert expired.status_code == 401 and "expired" in expired.json()["error"]["message"]


def test_a_busy_key_is_told_to_wait(auth_client, fake_container) -> None:
    key, _ = _make_key(auth_client)
    fake_container.access_key_service._calls_per_minute = 2
    assert _rpc(auth_client, key, "tools/list").status_code == 200
    assert _rpc(auth_client, key, "tools/list").status_code == 200
    busy = _rpc(auth_client, key, "tools/list")
    assert busy.status_code == 429 and busy.headers["Retry-After"] == "60"


def test_making_a_key_takes_a_signed_in_member(api_client) -> None:
    assert api_client.get("/api/v1/access-keys").status_code == 401
    assert api_client.post("/api/v1/access-keys", json={"name": "x"}).status_code in {401, 403}


def test_a_what_if_must_be_this_story_s(auth_client, fake_container) -> None:
    _sample_story(auth_client, fake_container)
    key, _ = _make_key(auth_client)
    result = _call(auth_client, key, "list_plan", {"what_if": "br_elsewhere"})
    assert result["isError"] is True
    assert result["content"][0]["text"] == "That what-if was not found."
    wrong_story = _call(auth_client, key, "list_plan", {"story": "prj_elsewhere"})
    assert wrong_story["isError"] is True


def test_hosted_keys_stay_in_their_workspace_and_story(hosted_client, hosted_container) -> None:
    from tests.contract.test_workspace_scope import (
        _create_project,
        _create_workspace,
        _hosted_login,
    )

    csrf = _hosted_login(hosted_client, hosted_container)
    mine = _create_workspace(hosted_client, csrf, "mine")
    theirs = _create_workspace(hosted_client, csrf, "theirs")
    first = _create_project(hosted_client, csrf, mine, "first")
    second = _create_project(hosted_client, csrf, mine, "second")
    elsewhere = _create_project(hosted_client, csrf, theirs, "elsewhere")
    headers = {"X-CSRF-Token": csrf, "X-Workspace-Id": mine}

    # A key cannot be made for a story in another workspace.
    refused = hosted_client.post(
        "/api/v1/access-keys", json={"name": "x", "project_id": elsewhere}, headers=headers
    )
    assert refused.status_code == 404

    one = hosted_client.post(
        "/api/v1/access-keys", json={"name": "one", "project_id": first}, headers=headers
    ).json()["key"]
    every = hosted_client.post("/api/v1/access-keys", json={"name": "all"}, headers=headers).json()[
        "key"
    ]

    def stories(key: str) -> set[str]:
        result = _call(hosted_client, key, "list_stories")["structuredContent"]
        return {story["id"] for story in result["stories"]}

    assert stories(one) == {first}
    assert stories(every) == {first, second}
    # Arguments only narrow what a key reads; they never widen it.
    for key, story in ((one, second), (one, elsewhere), (every, elsewhere)):
        result = _call(hosted_client, key, "list_plan", {"story": story})
        assert result["isError"] is True, (key, story)
    assert _call(hosted_client, every, "list_plan", {"story": second})["isError"] is False
    assert _call(hosted_client, every, "list_plan")["isError"] is True  # say which story

    site = "https://novel-engine.jaketalling.com"
    allowed = _rpc(hosted_client, one, "tools/list", headers={"Origin": site})
    assert allowed.status_code == 200
    local_page = _rpc(hosted_client, one, "tools/list", headers={"Origin": "http://localhost:3000"})
    assert local_page.status_code == 403


def test_an_oversized_request_is_refused_before_it_is_read(auth_client) -> None:
    key, _ = _make_key(auth_client)
    big = auth_client.post(
        "/mcp",
        content=b"{" + b" " * 1_000_001 + b"}",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    assert big.status_code == 413
    broken = auth_client.post(
        "/mcp", content=b"{not json", headers={"Authorization": f"Bearer {key}"}
    )
    assert broken.status_code == 400 and broken.json()["error"]["code"] == -32700
    batch = auth_client.post(
        "/mcp",
        json=[{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}],
        headers={"Authorization": f"Bearer {key}"},
    )
    assert batch.status_code == 400 and batch.json()["error"]["code"] == -32600


def test_a_point_of_view_entry_gives_no_other_names(auth_client, fake_container) -> None:
    names = _sample_story(auth_client, fake_container)
    base = f"/api/v1/projects/{_project(fake_container)}"
    head = auth_client.get(f"{base}/revisions").json()["items"][0]["revision"]
    made = auth_client.post(
        f"{base}/entities",
        json={
            "type": "Character",
            "name": "Mr. Hollis",
            "slug": "the-night-thief",
            "aliases": ["The Night Thief"],
            "external_ids": {"wiki": "night-thief"},
            "attributes": {"secret": "is the Night Thief"},
            "expected_revision": head,
        },
    )
    assert made.status_code == 201, made.text
    hollis = made.json()["id"]
    key, _ = _make_key(auth_client)

    seen = _call(
        auth_client,
        key,
        "read_entry",
        {"entry_id": hollis, "as_known_by": names["Captain Rooke"]},
    )["structuredContent"]
    assert seen["entry"] == {"id": hollis, "type": "Character", "name": "Mr. Hollis"}
    assert "Night Thief" not in repr(seen) and "night-thief" not in repr(seen)
    # The author's own view still has them.
    everything = _call(auth_client, key, "read_entry", {"entry_id": hollis})["structuredContent"]
    assert everything["entry"]["aliases"] == ["The Night Thief"]


def test_a_chunked_body_is_not_read_past_the_limit(auth_client) -> None:
    key, _ = _make_key(auth_client)
    chunk = b" " * 65_536
    total = 50  # over 3 MB in all, with no length given up front
    state = {"sent": 0}
    messages: list[dict] = []

    async def receive() -> dict:
        state["sent"] += 1
        if state["sent"] > total:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": chunk, "more_body": state["sent"] < total}

    async def send(message: dict) -> None:
        messages.append(message)

    async def run() -> None:
        await auth_client.app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/mcp",
                "raw_path": b"/mcp",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"host", b"testserver"),
                    (b"authorization", f"Bearer {key}".encode()),
                    (b"content-type", b"application/json"),
                    (b"transfer-encoding", b"chunked"),
                ],
                "client": ("127.0.0.1", 50000),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )

    auth_client.portal.call(run)
    started = next(message for message in messages if message["type"] == "http.response.start")
    assert started["status"] == 413
    assert state["sent"] < total


def test_malformed_messages_get_json_rpc_errors(auth_client) -> None:
    key, _ = _make_key(auth_client)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    deep = auth_client.post("/mcp", content=b"[" * 100_000 + b"]" * 100_000, headers=headers)
    assert deep.status_code == 400 and deep.json()["error"]["code"] == -32700

    # A client's answer to a request (this server sends none): accepted, nothing to say.
    answer = auth_client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 3, "result": {}}, headers=headers
    )
    assert answer.status_code == 202 and answer.content == b""

    for bad_id in ({"a": 1}, [1], True, 1.5, None):
        refused = auth_client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": bad_id, "method": "tools/list"},
            headers=headers,
        )
        assert refused.status_code == 400, bad_id
        assert refused.json()["error"]["code"] == -32600
        assert refused.json()["id"] is None


def test_a_tool_that_breaks_is_still_counted(auth_client, fake_container, monkeypatch) -> None:
    key, made = _make_key(auth_client)

    async def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(fake_container.plan_service, "outline", broken)
    failed = _rpc(auth_client, key, "tools/call", {"name": "list_plan", "arguments": {}})
    assert failed.status_code == 500
    assert "boom" not in failed.text
    uses = auth_client.get(f"/api/v1/access-keys/{made['id']}/uses").json()
    assert {(use["tool"], use["calls"], use["refused"]) for use in uses} == {("list_plan", 1, 1)}


def test_a_web_client_cannot_make_a_machine_proposal(auth_client, fake_container) -> None:
    _sample_story(auth_client, fake_container)
    base = f"/api/v1/projects/{_project(fake_container)}"
    head = auth_client.get(f"{base}/revisions").json()["items"][0]["revision"]
    change = {"op": "create_entity", "id": "tmp:ada", "type": "Character", "name": "Ada"}
    refused = auth_client.post(
        f"{base}/mutations",
        json={"operations": [change], "expected_revision": head, "origin": "machine"},
    )
    assert 400 <= refused.status_code < 500, refused.text
    allowed = auth_client.post(
        f"{base}/mutations",
        json={"operations": [change], "expected_revision": head, "origin": "author"},
    )
    assert allowed.status_code == 201, allowed.text
