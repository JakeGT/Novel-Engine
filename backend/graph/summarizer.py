from typing import Any


def summarize_context(nodes: list[dict[str, Any]]) -> str:
    """
    Takes the scored, deduplicated list of nodes returned by the ContextTraversal engine
    and formats them into a tight, readable string block for LLM system message injection.
    """
    if not nodes:
        return ""

    summary_lines = ["--- EXTERNAL KNOWLEDGE CONTEXT ---"]

    # Sort nodes by relevance score descending to ensure most important context is at the top
    sorted_nodes = sorted(nodes, key=lambda x: x.get("relevance_score", 0), reverse=True)

    for node in sorted_nodes:
        n_type = node.get("type", "Unknown")
        n_id = node.get("id", "Unknown")
        props = node.get("properties", {})
        score = node.get("relevance_score", 0)

        # Format the basic header for the node
        name = props.get("name") or n_id
        line = f"[{n_type}] {name} (Relevance: {score})"
        summary_lines.append(line)

        # Format the properties
        for key, value in props.items():
            if key == "name":
                continue  # Already in header
            summary_lines.append(f"  - {key}: {value}")

    summary_lines.append("----------------------------------")
    return "\n".join(summary_lines)
