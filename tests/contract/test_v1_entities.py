import pytest

pytestmark = pytest.mark.contract


def _create(client, entity_id="char_arin", type_="Character", properties=None):
    return client.post(
        "/api/v1/entities",
        json={"id": entity_id, "type": type_, "properties": properties or {"name": "Arin"}},
    )


def test_create_read_update_share_one_representation(api_client) -> None:
    created = _create(api_client)
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {"id", "type", "properties", "created_at"}
    assert "embedding" not in body["properties"]

    fetched = api_client.get("/api/v1/entities/char_arin")
    assert fetched.status_code == 200
    assert fetched.json() == body

    updated = api_client.put(
        "/api/v1/entities/char_arin",
        json={"type": "Character", "properties": {"name": "Arin Vale"}},
    )
    assert updated.status_code == 200
    assert updated.json()["properties"] == {"name": "Arin Vale"}


def test_created_at_is_timezone_aware_utc(api_client) -> None:
    body = _create(api_client).json()
    assert body["created_at"] is not None
    assert body["created_at"].endswith("+00:00") or body["created_at"].endswith("Z")


def test_update_rejects_path_body_id_mismatch(api_client) -> None:
    _create(api_client)

    response = api_client.put(
        "/api/v1/entities/char_arin",
        json={"id": "char_other", "type": "Character", "properties": {}},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_create_rejects_unknown_type(api_client) -> None:
    response = _create(api_client, entity_id="x_1", type_="NotAType")

    assert response.status_code == 400
    assert "Unknown node type" in response.json()["detail"]


def test_create_rejects_invalid_identifier(api_client) -> None:
    response = _create(api_client, entity_id="bad id!", type_="Character")

    assert response.status_code == 400


def test_get_missing_entity_returns_problem(api_client) -> None:
    response = api_client.get("/api/v1/entities/missing")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["correlation_id"]


def test_list_entities_supports_pagination(api_client) -> None:
    for index in range(3):
        _create(api_client, entity_id=f"char_{index}")

    response = api_client.get("/api/v1/entities", params={"type": "Character", "limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["items"]) == 2


def test_search_route_is_not_captured_as_entity_id(api_client) -> None:
    _create(api_client, entity_id="char_arin", properties={"name": "Arin"})

    response = api_client.get("/api/v1/entities/search", params={"q": "ari"})

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "char_arin"


def test_edges_roundtrip(api_client) -> None:
    _create(api_client, entity_id="char_arin")
    _create(api_client, entity_id="loc_gate", type_="Location", properties={"name": "Gate"})

    created = api_client.post(
        "/api/v1/edges",
        json={
            "source": "char_arin",
            "target": "loc_gate",
            "type": "LOCATED_AT",
            "properties": {"since": 1},
        },
    )
    assert created.status_code == 201
    assert created.json()["type"] == "LOCATED_AT"

    listed = api_client.get("/api/v1/entities/char_arin/edges")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    between = api_client.get(
        "/api/v1/edges", params={"source_id": "char_arin", "target_id": "loc_gate"}
    )
    assert between.status_code == 200
    assert between.json()["items"][0]["properties"] == {"since": 1}


def test_create_edge_rejects_unknown_type_and_missing_nodes(api_client) -> None:
    _create(api_client, entity_id="char_arin")

    unknown = api_client.post(
        "/api/v1/edges",
        json={"source": "char_arin", "target": "char_arin", "type": "NOPE", "properties": {}},
    )
    assert unknown.status_code == 400

    missing = api_client.post(
        "/api/v1/edges",
        json={
            "source": "char_arin",
            "target": "ghost",
            "type": "RELATED_TO",
            "properties": {},
        },
    )
    assert missing.status_code == 400
