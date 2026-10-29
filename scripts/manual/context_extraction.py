"""Manual probe for explicit-tag context retrieval against the canonical v1 API.

Not collected by pytest. Start the API (``make api``), then run
``uv run --locked python scripts/manual/context_extraction.py``.
"""

import requests

BASE_URL = "http://localhost:8000/api/v1"

ENTITY_IDS = ["char_black_knight", "loc_kings_gate", "sys_magic"]

print("Setting up test nodes...")
nodes = [
    {
        "id": "char_black_knight",
        "type": "Character",
        "properties": {"name": "The Black Knight", "hp": 100},
    },
    {
        "id": "loc_kings_gate",
        "type": "Location",
        "properties": {"name": "King's Gate", "danger_level": "High"},
    },
    {
        "id": "sys_magic",
        "type": "System",
        "properties": {"name": "Dark Magic", "rules": "Requires sacrifice"},
    },
]
for node in nodes:
    requests.post(f"{BASE_URL}/entities", json=node)

print("Setting up test edges...")
requests.post(
    f"{BASE_URL}/edges",
    json={
        "source": "char_black_knight",
        "target": "loc_kings_gate",
        "type": "LOCATED_AT",
        "properties": {"since": "yesterday"},
    },
)
requests.post(
    f"{BASE_URL}/edges",
    json={
        "source": "char_black_knight",
        "target": "sys_magic",
        "type": "USES_MAGIC",
        "properties": {"skill": 99},
    },
)

print("Testing TAG and Context Engine retrieval...")
payload = {
    "text": (
        "The [Black Knight](entity:Character:char_black_knight) stood before the "
        "[Kings Gate](entity:Location:loc_kings_gate)."
    ),
    "context_type": "scene_generation",
}

try:
    response = requests.post(f"{BASE_URL}/context", json=payload)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        returned = response.json().get("nodes", [])
        print(f"Returned {len(returned)} nodes dynamically.")
        for node in returned:
            print(f" - {node['id']} ({node['type']})")
    else:
        print(response.json())
except Exception as e:
    print(f"Failed: {e}")

print("Cleaning up...")
for entity_id in ENTITY_IDS:
    requests.delete(f"{BASE_URL}/entities/{entity_id}")
