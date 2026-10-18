"""A stand-in model for browser tests: an OpenAI-compatible chat endpoint with set answers.

Only ``scripts.e2e.serve --fake-model`` starts it, for the browser tests that need a model
(``*.model.spec.ts``). Prose requests get a short fixed scene; structured requests get a
fixed answer of the asked-for shape. Nothing is sent anywhere.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# Names the browser test creates, so the draft reads as part of its story.
DRAFT = (
    "Wren Hollis crossed the yard of The Old Mill as the wheel began to turn. "
    "The lamps along the quay were already lit, and the water ran dark under them.\n\n"
    "She counted the sacks twice. One was missing, and she said nothing about it."
)
REVIEW = {
    "notes": [
        {
            "quote": "The lamps along the quay were already lit",
            "category": "continuity",
            "problem": "The scene is set in the morning, but the lamps are lit.",
            "suggestion": "Say the lamps are still burning from the night.",
        },
        {
            # Not in the draft: the studio must drop it.
            "quote": "words that are nowhere in the scene",
            "category": "clarity",
            "problem": "This note cannot be pinned to the text.",
            "suggestion": "",
        },
    ]
}

# A check is one call per pass (CON-008): each pass is told what it looks for, and the
# stand-in answers by that. The continuity pass gives the notes above; the others find
# nothing, except clarity, which notes one sentence.
CLARITY = {
    "notes": [
        {
            "quote": "She counted the sacks twice",
            "problem": "It is not clear whether she counts before or after the wheel turns.",
            "suggestion": "Say when she counts them.",
        }
    ]
}


def _check(body: dict[str, Any]) -> dict[str, Any]:
    system = next(
        (str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "system"),
        "",
    )
    if "for one thing only: continuity" in system:
        return REVIEW
    if "for one thing only: clarity" in system:
        return CLARITY
    return {"notes": []}


# Reading the kept draft into the story (Phase 6): one new place, quoting the draft, and
# one event it shows happening (EXT-001), at the quay. Text without these words drops it.
READ = {
    "entities": [
        {
            "ref": "quay",
            "type": "Location",
            "name": "The quay",
            "quote": "The lamps along the quay were already lit",
        }
    ],
    "events": [
        {
            "name": "A sack goes missing",
            "kind": "discovery",
            "participants": ["quay"],
            "quote": "One was missing, and she said nothing about it.",
        }
    ],
}


# Filling in a plan card from the prose (Phase 6): the browser test's two names, by the
# ids the request lists for them, each quoting its own name in the scene.
CARD_NAMES = {"point_of_view": "Wren Hollis", "place": "The Old Mill"}
LISTED = re.compile(r"^- (\S+): (.+?) \((?:Character|Location)\)", re.MULTILINE)


def _plan_card(body: dict[str, Any]) -> dict[str, Any]:
    asked = "\n".join(str(message.get("content", "")) for message in body.get("messages", []))
    ids = {name: entry_id for entry_id, name in LISTED.findall(asked)}
    card: dict[str, Any] = {"summary": "Wren waits at the mill until dark.", "participants": []}
    for role, name in CARD_NAMES.items():
        if name in ids:
            card[role] = {"id": ids[name], "name": name, "quote": name}
    return card


# Asking the assistant for a change (the God Hand): "Add a character called Nell." becomes one
# new entry of that name, so "keep the safe ones for me" can be driven end to end.
ASKED_NAME = re.compile(r"[Aa]dd a character called ([A-Z][A-Za-z' -]*[A-Za-z])")


def _change(body: dict[str, Any]) -> dict[str, Any]:
    asked = "\n".join(
        str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "user"
    )
    found = ASKED_NAME.findall(asked)
    if not found:
        return {"understood": "Nothing to change.", "questions": ["What should change?"]}
    name = found[-1]
    return {
        "understood": f"Add {name} to the story.",
        "new_entities": [{"ref": "n", "type": "Character", "name": name}],
    }


def _structured(name: str, body: dict[str, Any]) -> dict[str, Any]:
    if name == "CheckNotes":
        return _check(body)
    if "Review" in name:
        return REVIEW
    if name in ("PassageDraft", "SceneDraft"):
        # A timed scene is read with the same answer: it has no facts, so nothing is timed.
        return READ
    if name == "PlanCardDraft":
        return _plan_card(body)
    if name == "ChangePlan":
        return _change(body)
    return {}


def _answer(body: dict[str, Any]) -> dict[str, Any]:
    response_format = body.get("response_format") or {}
    if response_format.get("type") == "json_schema":
        name = str((response_format.get("json_schema") or {}).get("name", ""))
        content = json.dumps(_structured(name, body))
    else:
        content = DRAFT
    return {
        "id": "chatcmpl-e2e",
        "object": "chat.completion",
        "created": 0,
        "model": body.get("model", "e2e-model"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": len(content.split())},
    }


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - the http.server interface
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self.send_error(404)
            return
        payload = json.dumps(_answer(body)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        payload = json.dumps({"object": "list", "data": [{"id": "e2e-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def start(host: str = "127.0.0.1") -> tuple[ThreadingHTTPServer, str]:
    """Start the stand-in on a free port; returns the server and its base URL."""
    server = ThreadingHTTPServer((host, 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{host}:{server.server_address[1]}/v1"


__all__ = ["DRAFT", "READ", "REVIEW", "start"]
