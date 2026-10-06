import re

# Matches [Display Name](entity:NodeType:NodeID)
TAG_REGEX = re.compile(r"\[([^\]]+)\]\(entity:([^:]+):([^)]+)\)")


def extract_entity_ids(text: str) -> list[str]:
    """
    Finds all unique Node IDs within the provided text string using regex.
    Takes a raw markdown prompt and returns a deduplicated list of graph anchor IDs.
    """
    if not text:
        return []

    matches = TAG_REGEX.findall(text)
    # matches is a list of tuples: (Display Name, Type, ID)

    # Deduplicate IDs while preserving order
    seen = set()
    ids = []
    for _, _, node_id in matches:
        if node_id not in seen:
            seen.add(node_id)
            ids.append(node_id)

    return ids
