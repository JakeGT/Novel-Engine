"""A stand-in model for browser tests: an OpenAI-compatible chat endpoint with set answers.

Only ``scripts.e2e.serve --fake-model`` starts it, for the browser tests that need a model
(``*.model.spec.ts``). Prose requests get a short fixed scene; structured requests get a
fixed answer of the asked-for shape. Nothing is sent anywhere.
"""

from __future__ import annotations

import json
import re
import threading
import time
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


# The optional "bodies, objects and cause" pass (CON-008, batch 10) notes one sentence.
PHYSICAL = {
    "notes": [
        {
            "quote": "She counted the sacks twice",
            "problem": "The yard is dark by now and no lamp is near her, so she could not count.",
            "suggestion": "Give her a lamp, or have her count by touch.",
        }
    ]
}


# The voice check of flagged passages (CON-018): one note on a first-person passage.
VOICE_PASSAGES = {
    "notes": [
        {
            "quote": "because counting is the only thing I can do",
            "problem": "This is told with “I” in the present tense; your scenes use “she” and the past.",
            "suggestion": "Tell it as “she counted”, as in your other scenes.",
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
    if "for one thing only: bodies, objects, places and cause" in system:
        return PHYSICAL
    if "for one thing only: the author's voice, in a few passages" in system:
        return VOICE_PASSAGES
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


# Changes of state and a new thread (EXT-001), answered only for the words the browser test
# writes for it, so no other test's reading changes: "<someone> came to <a place> at dusk."
# is a location that begins (from the scene's time in a timed scene), and "Nobody could say
# who had taken <something>." opens a thread when a scene is read (only a scene's reading
# asks for new threads).
ARRIVES = re.compile(
    r"([A-Z][\w']*(?: [A-Z][\w']*)*) came to ((?:The )?[A-Z][\w']*(?: [A-Z][\w']*)*) at dusk"
)
OPENS = re.compile(r"Nobody could say who had taken [^.]+\.")
# How the reading request lists an entry: "- id | Type | Name".
READ_LISTED = re.compile(
    r"^- (\S+) \| (?:Character|Location) \| (.+?)(?: \(also called: .*\))?$", re.MULTILINE
)


def _read(body: dict[str, Any]) -> dict[str, Any]:
    """The reading answer: ``READ``, plus the changes of state and the thread above."""
    messages = [str(message.get("content", "")) for message in body.get("messages", [])]
    asked = "\n".join(messages)
    answer: dict[str, Any] = dict(READ)
    ids = {name: entry_id for entry_id, name in READ_LISTED.findall(asked)}
    arrives = ARRIVES.search(asked)
    if arrives and arrives.group(1) in ids and arrives.group(2) in ids:
        answer["state_changes"] = [
            {
                "kind": "location",
                "change": "starts",
                "subject": ids[arrives.group(1)],
                "predicate": "LOCATED_AT",
                "object": ids[arrives.group(2)],
                "name": f"{arrives.group(1)} comes to {arrives.group(2)}",
                "quote": arrives.group(0),
            }
        ]
    opens = OPENS.search(asked)
    if opens and messages and "add it to new_threads" in messages[0]:
        answer["new_threads"] = [
            {
                "title": "The missing sack",
                "summary": "Someone has taken a sack, and nobody knows who.",
                "quote": opens.group(0),
            }
        ]
    return answer


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


# "Merge Wren H. into Wren Hollis." merges the first named entry into the second, by the
# ids the story bible lists for them (AST-EXP-017).
ASKED_MERGE = re.compile(r"[Mm]erge (.+?) into (.+?)[.!]?$", re.MULTILINE)
LISTED_ID = re.compile(r"^- (\S+): (.+?) \([A-Za-z]+\)", re.MULTILINE)


def _change(body: dict[str, Any]) -> dict[str, Any]:
    asked = "\n".join(
        str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "user"
    )
    # Only the new instruction is acted on; earlier turns of the kept conversation are
    # material above it.
    instruction = asked.rsplit("Instruction:\n", 1)[-1]
    merge = ASKED_MERGE.search(instruction)
    if merge is not None:
        ids = {name: entry_id for entry_id, name in LISTED_ID.findall(asked)}
        gone, kept = merge.group(1).strip(), merge.group(2).strip()
        if gone in ids and kept in ids:
            return {
                "understood": f"{gone} and {kept} are the same; keep {kept}.",
                "merge_entities": [{"merge": ids[gone], "keep": ids[kept]}],
            }
        return {"understood": "Unclear.", "questions": [f"Which entry is {gone}?"]}
    found = ASKED_NAME.findall(instruction)
    if not found:
        return {"understood": "Nothing to change.", "questions": ["What should change?"]}
    name = found[-1]
    return {
        "understood": f"Add {name} to the story.",
        "new_entities": [{"ref": "n", "type": "Character", "name": name}],
    }


# Describing a picture (AST-005): pictures come as ``image_url`` parts of the last user
# message (CTX-013). The stand-in never looks at them; it answers in fixed words, about
# the first entry listed, so a browser test can accept a detail and offer it as a fact.
LISTED_ENTRY = re.compile(r"^- (.+) \(([a-z ]+)\)$", re.MULTILINE)
MARKED_PART = re.compile(r"^- (part \d+):", re.MULTILINE)


def _pictures_in(body: dict[str, Any]) -> int:
    count = 0
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            count += sum(1 for part in content if part.get("type") == "image_url")
    return count


def _text_of(body: dict[str, Any]) -> str:
    parts: list[str] = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            parts.extend(
                str(part.get("text", "")) for part in content if part.get("type") == "text"
            )
        else:
            parts.append(str(content or ""))
    return "\n".join(parts)


def _describe(body: dict[str, Any]) -> dict[str, Any]:
    if not _pictures_in(body):
        # Never pretend: with no picture sent, there is nothing to describe.
        return {"caption": "", "confidence": 0, "details": []}
    asked = _text_of(body)
    entries = asked.split("ENTRIES-1>>>", 1)[1] if "ENTRIES-1>>>" in asked else ""
    listed = LISTED_ENTRY.findall(entries)
    about = listed[0][0] if listed else ""
    return {
        "caption": "A woman in a long blue coat stands on a quay at dusk.",
        "confidence": 0.82,
        "details": [
            {
                "detail": "She wears a long blue coat.",
                "about": about,
                "region": {"x": 0.25, "y": 0.2, "width": 0.5, "height": 0.7},
                "confidence": 0.8,
            },
            {"detail": "Her hair is short and grey.", "about": about, "confidence": 0.6},
        ],
        # Each marked part listed gets a caption; one fixed piece of writing is "read".
        "parts": [
            {
                "part": number,
                "caption": "A close view of this part of the picture.",
                "confidence": 0.7,
            }
            for number in MARKED_PART.findall(
                asked.split("PARTS-1>>>", 1)[1] if "PARTS-1>>>" in asked else ""
            )
        ],
        "writing": [
            {
                "text": "THE BLACK SWAN",
                "region": {"x": 0.1, "y": 0.05, "width": 0.4, "height": 0.1},
                "confidence": 0.9,
            }
        ],
    }


def _picture_check(body: dict[str, Any]) -> dict[str, Any]:
    if not _pictures_in(body) and "A picture of" not in _text_of(body):
        return {"notes": []}
    return {
        "notes": [
            {
                "quote": "Wren Hollis crossed the yard",
                "problem": "Her picture shows her in a long blue coat; the scene does not.",
                "suggestion": "Mention the coat.",
                "picture": "picture 1" if _pictures_in(body) else "",
                # The first marked part listed with the pictures, when there is one.
                "part": next(iter(MARKED_PART.findall(_text_of(body))), ""),
            }
        ]
    }


# A scene's intent (STY-003): the goal is the plan card's "What happens" line, quoted;
# what follows "before" or "but" in it is what stands in the way. No outcome is guessed.
HAPPENS = re.compile(r"^What happens: (.+)$", re.MULTILINE)


def _intent(body: dict[str, Any]) -> dict[str, Any]:
    asked = "\n".join(str(m.get("content", "")) for m in body.get("messages", []))
    found = HAPPENS.search(asked)
    if found is None:
        return {}
    happens = found.group(1).strip().rstrip(".")
    parts = re.split(r" before | but ", happens, maxsplit=1)
    goal, rest = parts[0], (parts[1] if len(parts) > 1 else "")

    # A quote of fewer than three words is thrown away: a short part quotes the whole line.
    def shown(part: str) -> str:
        return part if len(part.split()) >= 3 else happens

    answer: dict[str, Any] = {"goal": goal, "goal_words": shown(goal)}
    if rest:
        answer.update(obstacle=rest, obstacle_words=shown(rest))
    return answer


# Bridge scenes (STY-004): one scene, seen through the first character listed, at the
# first place listed, each by the id and name the request gives.
LISTED_TYPED = re.compile(r"^- (\S+): (.+?) \((Character|Location)\)$", re.MULTILINE)


def _bridges(body: dict[str, Any]) -> dict[str, Any]:
    asked = "\n".join(str(m.get("content", "")) for m in body.get("messages", []))
    people = [(i, n) for i, n, kind in LISTED_TYPED.findall(asked) if kind == "Character"]
    places = [(i, n) for i, n, kind in LISTED_TYPED.findall(asked) if kind == "Location"]
    bridge: dict[str, Any] = {
        "title": "On the road",
        "summary": "The journey that gets them where the next scene needs them.",
        "how_it_helps": "It brings them to where the next scene begins.",
    }
    if "<<<GAP-" in asked:
        # A gap that is not a condition (STY-004): a scene of its own name, so the
        # browser tests that count "On the road" are not thrown by it.
        bridge = {
            "title": "A torn sack on the stair",
            "summary": "Someone finds a torn sack, and the question comes back.",
            "how_it_helps": "It keeps the question open between where it starts and ends.",
        }
    if people:
        bridge["point_of_view"] = {"id": people[0][0], "name": people[0][1]}
    if places:
        bridge["place"] = {"id": places[0][0], "name": places[0][1]}
    return {"bridges": [bridge]}


# An arc's stages (STY-006): where the character starts, quoting what the arc is about,
# and where they end, in the last listed scene they are in, quoting what happens there.
ARC_ABOUT = re.compile(r"^What it is about: (.+)$", re.MULTILINE)
ARC_SCENE = re.compile(r"^- (\S+): “(.+?)” \(in it\)(?: — (.+))?$", re.MULTILINE)


def _stages(body: dict[str, Any]) -> dict[str, Any]:
    asked = "\n".join(str(m.get("content", "")) for m in body.get("messages", []))
    about = ARC_ABOUT.search(asked)
    scenes = ARC_SCENE.findall(asked)
    if about is None or not scenes:
        return {"stages": []}
    scene_id, title, happens = scenes[-1]
    return {
        "stages": [
            {"name": "Keeps to herself", "words": about.group(1).strip()},
            {"name": "Asks for help", "scene_id": scene_id, "words": happens or title},
        ]
    }


# Starting from a premise (AST-EXP-005): the first round suggests the people and places
# the premise names and asks two questions; a round with answers suggests a person for
# each name given in them and asks nothing more.
PREMISE_PERSON = re.compile(r"(?<!The )\b((?!The\b)[A-Z][a-z]+ [A-Z][a-z]+)\b")
PREMISE_PLACE = re.compile(r"\b(The [A-Z][a-z]+(?: [A-Z][a-z]+)?)\b")
ANSWER_LINE = re.compile(r"^A: (.+)$", re.MULTILINE)
CAPITALISED = re.compile(r"\b[A-Z][a-z]+\b")


def _answer_names(answers: str) -> list[str]:
    """Capitalised words in the answers that do not start a sentence."""
    names: list[str] = []
    for line in ANSWER_LINE.findall(answers):
        for found in CAPITALISED.finditer(line):
            before = line[: found.start()].rstrip()
            if before and not before.endswith((".", "!", "?")):
                names.append(found.group(0))
    return names


def _between(text: str, label: str) -> str:
    start = re.search(rf"<<<{label}-\d+>>>\n", text)
    end = re.search(rf"\n<<<END {label}-\d+>>>", text)
    return text[start.end() : end.start()] if start and end else ""


def _premise(body: dict[str, Any]) -> dict[str, Any]:
    asked = "\n".join(
        str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "user"
    )
    premise = _between(asked, "PREMISE")
    answers = _between(asked, "ANSWERS")
    if answers:
        names = [name for name in _answer_names(answers) if name not in premise]
        return {
            "understood": "The answers name more of the people in the story.",
            "questions": [],
            "entities": [
                {"ref": f"p{i}", "type": "Character", "name": name, "needs_more": True}
                for i, name in enumerate(dict.fromkeys(names))
            ],
        }
    people = list(dict.fromkeys(PREMISE_PERSON.findall(premise)))
    places = list(dict.fromkeys(PREMISE_PLACE.findall(premise)))
    entities = [
        {"ref": f"c{i}", "type": "Character", "name": name} for i, name in enumerate(people)
    ] + [{"ref": f"l{i}", "type": "Location", "name": name} for i, name in enumerate(places)]
    facts = (
        [{"subject_ref": "c0", "predicate": "LOCATED_AT", "object_ref": "l0"}]
        if people and places
        else []
    )
    return {
        "understood": "A story about " + (people[0] if people else "someone") + ".",
        "questions": [
            "What is her brother's name?",
            "Who owns " + (places[0] if places else "the place") + "?",
        ],
        "entities": entities,
        "facts": facts,
    }


# Missing connections (AST-EXP-007): the first two characters listed know each other.
LISTED_CHARACTER = re.compile(r"^- (\S+): (.+?) \(Character\)", re.MULTILINE)


def _links(body: dict[str, Any]) -> dict[str, Any]:
    asked = "\n".join(str(m.get("content", "")) for m in body.get("messages", []))
    people = LISTED_CHARACTER.findall(asked)
    if len(people) < 2:
        return {"links": []}
    return {
        "links": [
            {
                "subject_ref": people[0][0],
                "predicate": "KNOWS",
                "object_ref": people[1][0],
                "why": f"{people[0][1]} and {people[1][1]} are named together.",
            }
        ]
    }


# Planning (AST-EXP-013): one arc that quotes the author's first sentence, and one beat
# that is the stand-in's own idea, so a page can show both kinds.
SAYS = re.compile(r"What the author says:\n(.+?)(?:\n|$)")


def _plan_draft(body: dict[str, Any]) -> dict[str, Any]:
    asked = "\n".join(str(m.get("content", "")) for m in body.get("messages", []))
    said = SAYS.search(asked)
    first = said.group(1).split(". ")[0].strip().rstrip(".") if said else ""
    return {
        "understood": "An arc and its first turn.",
        "items": [
            {"ref": "a1", "kind": "arc", "title": "The search", "quote": first},
            {
                "ref": "b1",
                "kind": "beat",
                "title": "A letter arrives",
                "parent_ref": "a1",
                "quote": "",
            },
        ],
        "gaps": ["How the search ends."],
    }


def _structured(name: str, body: dict[str, Any]) -> dict[str, Any]:
    if name == "IntentDraft":
        return _intent(body)
    if name == "BridgeSheet":
        return _bridges(body)
    if name == "StageSheet":
        return _stages(body)
    if name == "PictureDescription":
        return _describe(body)
    if name == "PictureNotes":
        return _picture_check(body)
    if name == "CheckNotes":
        return _check(body)
    if "Review" in name:
        return REVIEW
    if name in ("PassageDraft", "SceneDraft"):
        return _read(body)
    if name == "PlanCardDraft":
        return _plan_card(body)
    if name == "ChangePlan":
        return _change(body)
    if name == "PremiseRound":
        return _premise(body)
    if name == "LinkPlan":
        return _links(body)
    if name == "PlanDraft":
        return _plan_draft(body)
    if name == "SceneSummaryDraft":
        # A fixed, plain summary (CTX-006): the author keeps, corrects or turns it down.
        return {"summary": "Someone arrives at the harbour and is not welcomed."}
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
        if self.path.rstrip("/").endswith("/embeddings"):
            self._embeddings(body)
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self.send_error(404)
            return
        if body.get("stream"):
            self._stream(body)
            return
        payload = json.dumps(_answer(body)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _embeddings(self, body: dict[str, Any]) -> None:
        """An OpenAI-compatible embedding service (site search set up on the page): each
        input becomes a deterministic vector of the size asked for, so nearness by
        meaning ranks the same way every run."""
        from backend.adapters.embedding_fake import DeterministicEmbedding
        from backend.domain.embedding import EmbeddingTask, TextInput

        inputs = body.get("input") or []
        texts = [inputs] if isinstance(inputs, str) else [str(item) for item in inputs]
        size = int(body.get("dimensions") or 64)
        vectors = DeterministicEmbedding(dimension=size, model="e2e-embed").embed(
            [TextInput(text=text) for text in texts], task=EmbeddingTask.DOCUMENT
        )
        data = [
            {"object": "embedding", "index": index, "embedding": list(vector.values)}
            for index, vector in enumerate(vectors)
        ]
        payload = json.dumps({"object": "list", "data": data, "model": body.get("model")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _stream(self, body: dict[str, Any]) -> None:
        """SCN-002: prose as server-sent events, a few words at a time, then what it
        used (as ``stream_options.include_usage`` asks). A direction asking for it to be
        written slowly ("write it slowly") makes each piece wait, so a browser test can
        watch the words arrive and stop part way."""
        answer = _answer(body)
        content = answer["choices"][0]["message"]["content"]
        slow = "write it slowly" in json.dumps(body.get("messages", [])).lower()
        delay = 0.6 if slow else 0.02
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        words = content.split(" ")

        def send(chunk: dict[str, Any]) -> None:
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()

        base = {"id": "chatcmpl-e2e", "object": "chat.completion.chunk", "created": 0}
        base["model"] = body.get("model", "e2e-model")
        try:
            for start in range(0, len(words), 3):
                piece = " ".join(words[start : start + 3])
                if start + 3 < len(words):
                    piece += " "
                send({**base, "choices": [{"index": 0, "delta": {"content": piece}}]})
                time.sleep(delay)
            send({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            if (body.get("stream_options") or {}).get("include_usage"):
                send({**base, "choices": [], "usage": answer["usage"]})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # The site stopped reading: the author pressed Stop.
            return

    def do_GET(self) -> None:  # noqa: N802
        payload = json.dumps({"object": "list", "data": [{"id": "e2e-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def start(host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start the stand-in (on a free port unless one is given); returns the server and
    its base URL."""
    server = ThreadingHTTPServer((host, port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{host}:{server.server_address[1]}/v1"


__all__ = ["DRAFT", "READ", "REVIEW", "start"]
