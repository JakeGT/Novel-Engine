"""A stand-in model for browser tests: an OpenAI-compatible chat endpoint with set answers.

Only ``scripts.e2e.serve --fake-model`` starts it, for the browser tests that need a model
(``*.model.spec.ts``). Prose requests get a short fixed scene; structured requests get a
fixed answer of the asked-for shape. Nothing is sent anywhere.
"""

from __future__ import annotations

import json
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


def _answer(body: dict[str, Any]) -> dict[str, Any]:
    response_format = body.get("response_format") or {}
    if response_format.get("type") == "json_schema":
        name = str((response_format.get("json_schema") or {}).get("name", ""))
        content = json.dumps(REVIEW if "Review" in name else {})
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


__all__ = ["DRAFT", "REVIEW", "start"]
