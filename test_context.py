import requests

print("Testing TAG and Context Engine retrieval...")

payload = {
    "text": "The [Black Knight](entity:Character:char_black_knight) stood before the [Kings Gate](entity:Location:loc_kings_gate).",
    "context_type": "scene_generation"
}

try:
    response = requests.post("http://localhost:8000/rag/context", json=payload)
    print(f"Status Code: {response.status_code}")
    print(response.json())
except Exception as e:
    print(f"Failed: {e}")
