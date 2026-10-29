import requests
import json
import sys

BASE_URL = "http://localhost:8000/api/v1"


def main():
    print("--- Novel Engine: Character Creation Tester ---")

    # 1. Init Character
    name = input("Enter character name: ").strip()
    if not name:
        print("Name is required.")
        return

    print(f"\nInitializing character: {name}...")
    try:
        res = requests.post(f"{BASE_URL}/characters/init", json={"name": name})
        res.raise_for_status()
        data = res.json()
        char_id = data["node"]["id"]
        print(f"Success! Character ID: {char_id}")
    except Exception as e:
        print(f"Failed to initialize character: {e}")
        return

    # 2. Get Bio
    print("\n--- Bio Phase ---")
    bio = input("Enter a short bio for the character: ").strip()

    use_cache_q = input("Use cached questions if available? (y/n) [n]: ").strip().lower() == "y"

    print("\nGenerating questions based on bio (this may take a moment)...")
    try:
        res = requests.post(
            f"{BASE_URL}/characters/generate-questions",
            json={"id": char_id, "name": name, "bio": bio, "use_cache": use_cache_q},
        )
        res.raise_for_status()
        questions_data = res.json()
    except Exception as e:
        print(f"Failed to generate questions: {e}")
        return

    questions = questions_data.get("questions", [])
    if not questions:
        print("No questions returned.")
        return

    # 3. Answer Questions
    print("\n--- Questionnaire Phase ---")
    answers = {}
    for q in questions:
        print(f"\n[{q['category'].upper()}] {q['question']}")
        ans = input("Answer (leave blank to skip): ").strip()
        if ans:
            answers[q["question"]] = ans

    if not answers:
        print("No answers provided. Exiting.")
        return

    # 4. Process Answers
    print("\n--- Processing Answers ---")
    chapter_str = input("Current Chapter number [1]: ").strip()
    chapter = int(chapter_str) if chapter_str.isdigit() else 1

    use_cache_a = (
        input("Use cached LLM graph update if available? (y/n) [n]: ").strip().lower() == "y"
    )

    print("\nProcessing answers into Knowledge Graph nodes and edges...")
    try:
        res = requests.post(
            f"{BASE_URL}/characters/process-answers",
            json={
                "id": char_id,
                "name": name,
                "answers": answers,
                "chapter": chapter,
                "use_cache": use_cache_a,
            },
        )
        res.raise_for_status()
        update_data = res.json()

        print("\n--- Graph Update Complete ---")
        updates = update_data.get("update", {})

        print("\nNodes Created/Updated:")
        for node in updates.get("nodes", []):
            print(f" - [{node['type']}] {node['id']}")

        print("\nEdges Created:")
        for edge in updates.get("edges", []):
            print(f" - ({edge['source']})-[{edge['type']}]->({edge['target']})")

    except Exception as e:
        print(f"Failed to process answers: {e}")
        if "res" in locals() and res.status_code != 200:
            print(f"Server response: {res.text}")
        return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
