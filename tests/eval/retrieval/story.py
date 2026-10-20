"""The Saltglass Ferry built through the API, for measuring what a scene's context gives.

Built with the same requests an author makes (entries, facts, secrets, beliefs, a
telling, scenes with times, a text, a what-if), against the in-memory application,
so the context service reads it exactly as it would read a real story.
"""

from __future__ import annotations

from typing import Any

from tests.contract.test_canon_entities import _project_id, _publish_schema
from tests.contract.test_knowledge import _approve

ENTRIES: list[tuple[str, str, str]] = [
    ("wren", "Character", "Wren Halloway"),
    ("osric", "Character", "Osric Pell"),
    ("ada", "Character", "Ada Quill"),
    ("bram", "Character", "Bram Tolley"),
    ("corin", "Character", "Corin Vale"),
    ("quay", "Location", "Kestle Quay"),
    ("light", "Location", "Marrow Light"),
    ("fen", "Location", "the Low Fen"),
    ("house", "Location", "the Ferry House"),
    ("mill", "Location", "Saltmarsh Mill"),
    ("sextant", "Object", "the brass sextant"),
    ("ledger", "Object", "the tide ledger"),
    ("bell", "Object", "the iron bell"),
    ("wardens", "Faction", "the Tidewardens"),
    ("lamps", "Faction", "the Lampwrights"),
]

# key, subject, kind of fact, object, extra (visibility, starts/ends).
FACTS: list[tuple[str, str, str, str, dict[str, Any]]] = [
    ("wren_at_quay", "wren", "LOCATED_AT", "quay", {}),
    ("osric_at_light", "osric", "LOCATED_AT", "light", {}),
    ("ada_at_house", "ada", "LOCATED_AT", "house", {}),
    ("bram_warden", "bram", "MEMBER_OF", "wardens", {}),
    ("osric_warden", "osric", "MEMBER_OF", "wardens", {"visibility": "secret"}),
    ("wren_sextant", "wren", "OWNS", "sextant", {}),
    ("ada_ledger", "ada", "OWNS", "ledger", {}),
    ("ada_kin", "ada", "RELATED_TO", "osric", {"visibility": "secret"}),
    ("wren_knows_bram", "wren", "KNOWS", "bram", {}),
    ("bram_at_fen_once", "bram", "LOCATED_AT", "fen", {"ends": {"kind": "at", "at": 10}}),
    # Nothing to do with any scene below.
    ("corin_at_mill", "corin", "LOCATED_AT", "mill", {}),
    ("corin_lamps", "corin", "MEMBER_OF", "lamps", {}),
    ("corin_bell", "corin", "OWNS", "bell", {}),
]

# key, title, (entry, role)…, story time.
SCENES: list[tuple[str, str, list[tuple[str, str]], int]] = [
    (
        "scene_crossing",
        "A The Crossing",
        [("wren", "point_of_view"), ("quay", "place"), ("bram", "participant")],
        20,
    ),
    (
        "scene_light",
        "B The Light",
        [("osric", "point_of_view"), ("light", "place"), ("wren", "participant")],
        25,
    ),
    ("scene_house", "C The Ferry House", [("ada", "point_of_view"), ("house", "place")], 30),
    (
        "scene_low_water",
        "D Low Water",
        [("wren", "point_of_view"), ("fen", "place"), ("osric", "participant")],
        40,
    ),
]

# A longer chapter about the crossing, so a small budget has something to leave out.
TEXT_TWO = "\n\n".join(
    f"# Crossing {n}\n\nWren Halloway and Bram Tolley stood at Kestle Quay {when}, watching the "
    "water rise over the lowest step. Neither spoke of the old ferry. The gulls quarrelled over "
    "the nets, the tide bell knocked against its post, and the mist came and went across the "
    "Low Fen like breath on a cold window, until the light at last began to fail. Bram counted "
    "the boats that had not come back, and Wren counted the steps the water had taken, and "
    "between them they kept a tally of the evening that neither would ever write down."
    for n, when in enumerate(("at first light", "at noon", "at dusk", "at midnight"), start=1)
)

TEXT = (
    "# Dawn\n\nWren Halloway untied the ferry at Kestle Quay and felt for the brass sextant "
    "in her coat.\n\n"
    "# Elsewhere\n\nCorin Vale swept the floor of Saltmarsh Mill and hung the iron bell "
    "back on its hook.\n"
)


def head(client, base: str, headers: dict[str, str] | None = None) -> int:
    return int(client.get(f"{base}/projection", headers=headers or {}).json()["head_revision"])


def _post(client, url: str, body: dict, *, headers: dict[str, str] | None = None) -> dict:
    made = client.post(url, json=body, headers=headers or {})
    assert made.status_code in (200, 201), made.text
    return made.json()


def build(client, container) -> dict[str, Any]:
    """The story, and every id it was given, by key."""
    project_id = _project_id(container)
    _publish_schema(client, project_id)
    base = f"/api/v1/projects/{project_id}"
    ids: dict[str, Any] = {"base": base, "project": project_id}

    def ref(key: str) -> dict:
        return {"kind": "entity_ref", "entity_id": ids[key], "project_id": project_id}

    for key, kind, name in ENTRIES:
        made = _post(
            client,
            f"{base}/entities",
            {"type": kind, "name": name, "expected_revision": head(client, base)},
        )
        ids[key] = made["id"]
    for key, subject, predicate, obj, extra in FACTS:
        made = _post(
            client,
            f"{base}/facts",
            {
                "proposition": {
                    "subject_id": ids[subject],
                    "predicate": predicate,
                    "object": ref(obj),
                },
                "expected_revision": head(client, base),
            },
        )
        ids[key] = made["id"]
        if extra:
            _post(
                client,
                f"{base}/facts/{ids[key]}/amend",
                {**extra, "expected_revision": head(client, base)},
            )
    # Wren wrongly believes Osric is out on the fen; Ada knows her own brother; Osric
    # knows his own oath (a secret is known only to those recorded as holding it).
    for key, holder, subject, predicate, obj in (
        ("wren_wrong", "wren", "osric", "LOCATED_AT", "fen"),
        ("ada_knows_kin", "ada", "ada", "RELATED_TO", "osric"),
        ("osric_knows_oath", "osric", "osric", "MEMBER_OF", "wardens"),
    ):
        made = _post(
            client,
            f"{base}/beliefs",
            {
                "holder_entity_id": ids[holder],
                "proposition": {
                    "subject_id": ids[subject],
                    "predicate": predicate,
                    "object": ref(obj),
                },
                "source_ref": "author",
                "expected_revision": head(client, base),
            },
        )
        ids[key] = made["id"]
    for key, title, refs, at in SCENES:
        made = _post(
            client,
            f"{base}/plan",
            {
                "kind": "scene",
                "title": title,
                "summary": "",
                "refs": [{"entity_id": ids[entry], "role": role} for entry, role in refs],
                "expected_revision": head(client, base),
            },
        )
        ids[key] = made["id"]
        timed = client.put(
            f"{base}/plan/{made['id']}/when",
            json={"when": {"kind": "at", "at": at}, "expected_revision": head(client, base)},
        )
        assert timed.status_code == 200, timed.text
    # Ada tells Wren Osric's secret in year 35: after the crossing, before low water.
    told = _post(
        client,
        f"{base}/knowledge/tell",
        {
            "speaker_id": ids["ada"],
            "listener_ids": [ids["wren"]],
            "proposition": {
                "subject_id": ids["osric"],
                "predicate": "MEMBER_OF",
                "object": ref("wardens"),
            },
            "when": {"kind": "at", "at": 35},
            "expected_revision": head(client, base),
        },
    )
    _approve(client, ids, told["mutation_id"])
    _post(client, f"{base}/texts", {"title": "Chapter one", "content": TEXT})
    _post(client, f"{base}/texts", {"title": "Chapter two", "content": TEXT_TWO})

    # A what-if: Osric never takes the light; he stays on the fen.
    what_if = _post(client, f"{base}/what-ifs", {"name": "The keeper stays"})
    branch = {"X-Story-Branch": what_if["id"]}
    ids["branch"] = branch
    closed = client.post(
        f"{base}/facts/{ids['osric_at_light']}/close",
        json={"expected_revision": head(client, base, branch)},
        headers=branch,
    )
    assert closed.status_code == 200, closed.text
    stays = _post(
        client,
        f"{base}/facts",
        {
            "proposition": {
                "subject_id": ids["osric"],
                "predicate": "LOCATED_AT",
                "object": ref("fen"),
            },
            "expected_revision": head(client, base, branch),
        },
        headers=branch,
    )
    ids["osric_stays"] = stays["id"]
    return ids


def context(
    client, ids: dict[str, Any], scene_key: str, *, branch: bool = False, **params: Any
) -> dict:
    got = client.get(
        f"{ids['base']}/plan/{ids[scene_key]}/context",
        params=params,
        headers=ids["branch"] if branch else {},
    )
    assert got.status_code == 200, got.text
    return got.json()


__all__ = ["ENTRIES", "FACTS", "SCENES", "TEXT", "TEXT_TWO", "build", "context", "head"]
