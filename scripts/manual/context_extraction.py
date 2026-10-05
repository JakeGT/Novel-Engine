import requests

BASE_URL = "http://localhost:8000/api"

print("Setting up test nodes...")
nodes = [
    {"id": "char_black_knight", "type": "Character", "properties": {"name": "The Black Knight", "hp": 100}},
    {"id": "loc_kings_gate", "type": "Location", "properties": {"name": "King's Gate", "danger_level": "High"}},
    {"id": "sys_magic", "type": "System", "properties": {"name": "Dark Magic", "rules": "Requires sacrifice"}}
]
for n in nodes:
    requests.post(f"{BASE_URL}/graph/nodes/", json=n)

print("Setting up test edges...")
requests.post(f"{BASE_URL}/graph/edges/", json={
    "source": "char_black_knight", "target": "loc_kings_gate", "type": "LOCATED_AT", "properties": {"since": "yesterday"}
})
requests.post(f"{BASE_URL}/graph/edges/", json={
    "source": "char_black_knight", "target": "sys_magic", "type": "USES_MAGIC", "properties": {"skill": 99}
})

print("Testing TAG and Context Engine retrieval...")

payload = {
    "text": "The [Black Knight](entity:Character:char_black_knight) stood before the [Kings Gate](entity:Location:loc_kings_gate).",
    "context_type": "scene_generation"
}

try:
    response = requests.post(f"{BASE_URL}/rag/context", json=payload)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Returned {len(data.get('nodes', []))} nodes dynamically.")
        for node in data.get('nodes', []):
            print(f" - {node['id']} ({node['type']}) | Score: {node['relevance_score']}")
    else:
        print(response.json())
except Exception as e:
    print(f"Failed: {e}")

# Teardown
print("Cleaning up...")
requests.delete(f"{BASE_URL}/graph/nodes/char_black_knight")
requests.delete(f"{BASE_URL}/graph/nodes/loc_kings_gate")
requests.delete(f"{BASE_URL}/graph/nodes/sys_magic")

