"""Streaming prose through the OpenAI-compatible gateway (SCN-002), against the real client
library reading server-sent events from a stand-in transport: no network."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

from backend.adapters.ai import OpenAIGateway, stream_usage_option
from backend.domain.jobs import JobCancelled
from backend.domain.streaming import StreamBuffer, sse_event
from backend.ports import AIInvalidOutputError, AIProviderError

pytestmark = pytest.mark.unit


def _chunk(content: str | None = None, finish: str | None = None, usage: dict | None = None):
    choice = {"index": 0, "delta": {"content": content} if content is not None else {}}
    if finish:
        choice["finish_reason"] = finish
    body: dict = {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [choice] if content is not None or finish else [],
    }
    if usage is not None:
        body["usage"] = usage
    return "data: " + json.dumps(body) + "\n\n"


def _sse(*events: str) -> bytes:
    return ("".join(events) + "data: [DONE]\n\n").encode()


class _Server:
    """A provider that streams the given body, and records what it was asked."""

    def __init__(self, body: bytes, *, refuse_usage_option: bool = False) -> None:
        self.body = body
        self.requests: list[dict] = []
        self.refuse_usage_option = refuse_usage_option

    def handle(self, request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        self.requests.append(sent)
        if self.refuse_usage_option and "stream_options" in sent:
            return httpx.Response(
                400, json={"error": {"message": "Unrecognized request argument: stream_options"}}
            )
        return httpx.Response(200, content=self.body, headers={"content-type": "text/event-stream"})


def _gateway(server: _Server, *, streaming: bool = True) -> OpenAIGateway:
    gateway = OpenAIGateway(
        api_key="k", default_model="m", base_url="http://model.test/v1", streaming=streaming
    )
    gateway._client = AsyncOpenAI(
        api_key="k",
        base_url="http://model.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(server.handle)),
    )
    return gateway


async def _collect(gateway: OpenAIGateway, pieces: list[str]):
    async def on_text(piece: str) -> None:
        if piece:  # an empty piece is a tick: the model sent no words
            pieces.append(piece)

    return await gateway.generate_text(
        messages=[{"role": "user", "content": "write"}], max_tokens=50, on_text=on_text
    )


@pytest.mark.asyncio
async def test_pieces_arrive_in_order_and_the_whole_answer_and_usage_come_back_once():
    server = _Server(
        _sse(
            ": OPENROUTER PROCESSING\n\n",
            _chunk("Tobin "),
            _chunk("waited "),
            _chunk("alone."),
            _chunk("", finish="stop"),
            _chunk(None, usage={"prompt_tokens": 11, "completion_tokens": 3, "cost": 0.002}),
        )
    )
    pieces: list[str] = []
    response = await _collect(_gateway(server), pieces)
    assert pieces == ["Tobin ", "waited ", "alone."]
    assert response.text == "Tobin waited alone." and response.streamed
    assert response.usage_reported and response.usage.prompt_tokens == 11
    assert response.usage.completion_tokens == 3 and response.usage.cost_usd == 0.002
    assert server.requests[0]["stream"] is True
    assert server.requests[0]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_a_provider_that_refuses_the_usage_option_is_asked_again_without_it():
    server = _Server(_sse(_chunk("Words."), _chunk("", finish="stop")), refuse_usage_option=True)
    response = await _collect(_gateway(server), [])
    assert [("stream_options" in sent) for sent in server.requests] == [True, False]
    assert response.text == "Words." and not response.usage_reported


@pytest.mark.asyncio
async def test_an_error_part_way_through_fails_the_request():
    server = _Server(_sse(_chunk("Half a "), _chunk("", finish="error")))
    with pytest.raises(AIProviderError):
        await _collect(_gateway(server), [])
    # OpenRouter's top-level error event is raised by the client library, said plainly.
    errored = _Server(_sse(_chunk("Half "), 'data: {"error": {"message": "overloaded"}}\n\n'))
    with pytest.raises(AIProviderError) as raised:
        await _collect(_gateway(errored), [])
    assert "overloaded" not in str(raised.value)


@pytest.mark.asyncio
async def test_an_empty_stream_is_not_prose():
    server = _Server(_sse(_chunk("", finish="stop")))
    with pytest.raises(AIInvalidOutputError):
        await _collect(_gateway(server), [])


@pytest.mark.asyncio
async def test_a_stop_from_the_callback_closes_the_stream_at_once():
    server = _Server(_sse(*[_chunk(f"word{i} ") for i in range(50)], _chunk("", finish="stop")))
    gateway = _gateway(server)
    pieces: list[str] = []

    async def on_text(piece: str) -> None:
        pieces.append(piece)
        if len(pieces) == 2:
            raise JobCancelled("Writing the draft")

    with pytest.raises(JobCancelled):
        await gateway.generate_text(
            messages=[{"role": "user", "content": "write"}], max_tokens=50, on_text=on_text
        )
    assert pieces == ["word0 ", "word1 "]


@pytest.mark.asyncio
async def test_a_route_set_not_to_stream_answers_whole():
    holder: dict = {}

    def handle(request: httpx.Request) -> httpx.Response:
        holder["sent"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "All of it."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            },
        )

    gateway = OpenAIGateway(api_key="k", default_model="m", streaming=False)
    gateway._client = AsyncOpenAI(
        api_key="k",
        base_url="http://model.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    pieces: list[str] = []
    response = await _collect(gateway, pieces)
    assert pieces == [] and response.text == "All of it." and not response.streamed
    assert "stream" not in holder["sent"]
    assert gateway.capabilities().supports_streaming is False


def test_the_usage_option_is_asked_of_known_and_unknown_providers():
    assert stream_usage_option("https://openrouter.ai/api/v1") is True
    assert stream_usage_option("http://localhost:11434/v1") is True


def test_the_computer_route_never_streams():
    gateway = OpenAIGateway(api_key="", default_model="m")
    assert gateway.capabilities(base_url="device://wsp_1/dev_1").supports_streaming is False


# ------------------------------------------------------------------ buffer and events


def test_the_buffer_passes_pieces_on_in_batches_and_keeps_to_its_limit():
    now = [0.0]
    buffer = StreamBuffer(clock=lambda: now[0], flush_seconds=1.0, flush_chars=10, limit=25)
    assert buffer.add("abc") is False
    now[0] = 1.5
    assert buffer.add("d") is True and buffer.take() == "abcd"
    assert buffer.add("0123456789") is True and buffer.take() == "0123456789"
    buffer.add("x" * 50)
    assert buffer.take() == "x" * 11 and buffer.full and buffer.kept == 25
    assert buffer.add("more") is False and buffer.take() == ""


def test_an_event_is_one_data_line_whatever_the_words_hold():
    event = sse_event("text", {"number": 1, "text": "Line one.\n\nLine two."}).decode()
    assert event.count("\ndata: ") == 1 and event.endswith("\n\n")
    assert "\n\n" not in event[:-2]


def test_half_a_million_characters_stream_in_linear_time():
    """PERFORMANCE RULE: 500k characters in two-character pieces, through the buffer and
    the word count, in well under a second."""
    from backend.services.studio_service import DraftWriter

    piece = "a "
    text = piece * 250_000
    buffer = StreamBuffer(flush_chars=4000, flush_seconds=60.0, limit=1_000_000)
    writer = DraftWriter.__new__(DraftWriter)
    writer._in_word = False
    out: list[str] = []
    words = 0
    started = time.perf_counter()
    for _ in range(250_000):
        words += writer._count(piece)
        if buffer.add(piece):
            out.append(buffer.take())
    out.append(buffer.take())
    elapsed = time.perf_counter() - started
    assert "".join(out) == text and words == 250_000
    assert elapsed < 2.0, elapsed


# ------------------------------------------------- a stop noticed while no words come


def _thinking(text: str) -> str:
    """A chunk with no words in it: what a reasoning model sends while it thinks."""
    body = {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning": text}}],
    }
    return "data: " + json.dumps(body) + "\n\n"


class _Ctx:
    """A running job whose author has asked it to stop: every check says so."""

    def __init__(self) -> None:
        self.job = SimpleNamespace(workspace_id="w", project_id="p", id="j", attempts=1)
        self.steps = 0

    async def step(self, stage: str, progress: int) -> None:
        self.steps += 1
        raise JobCancelled(stage)


class _Store:
    def __init__(self) -> None:
        self.appended: list[str] = []

    async def begin(self, **_key) -> None:
        return None

    async def append(self, *, piece: str, **_key) -> None:
        self.appended.append(piece)

    async def end(self, **_key) -> None:
        return None


@pytest.mark.asyncio
async def test_a_stop_is_noticed_while_the_model_only_thinks(monkeypatch):
    """M4: a stream of chunks with no words (reasoning, roles) still lets the draft check
    for a stop: before, the stop was not seen until the first word, or never."""
    from backend.services.studio_service import DraftWriter

    monkeypatch.setattr("backend.services.studio_service.CANCEL_CHECK_SECONDS", 0.0)
    server = _Server(_sse(*[_thinking(f"step {i}") for i in range(40)], _chunk("", "stop")))
    ctx = _Ctx()
    store = _Store()
    writer = DraftWriter(
        store, ctx, number=1, stage="Writing the draft", progress=(10, 85), words=800
    )
    with pytest.raises(JobCancelled):
        await _gateway(server).generate_text(
            messages=[{"role": "user", "content": "write"}], max_tokens=50, on_text=writer.add
        )
    assert ctx.steps == 1 and store.appended == [] and writer.written == 0


@pytest.mark.asyncio
async def test_a_stop_is_noticed_while_the_model_sends_nothing_at_all(monkeypatch):
    """M4: a model that goes quiet part way (no chunk at all) is still checked on."""
    monkeypatch.setattr("backend.adapters.ai.TICK_SECONDS", 0.05)

    async def body():
        yield _chunk("Tobin ").encode()
        await asyncio.sleep(30)  # the model stalls
        yield _sse(_chunk("waited.", "stop"))

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})

    gateway = OpenAIGateway(api_key="k", default_model="m", base_url="http://model.test/v1")
    gateway._client = AsyncOpenAI(
        api_key="k",
        base_url="http://model.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    seen: list[str] = []

    async def on_text(piece: str) -> None:
        seen.append(piece)
        if not piece:
            raise JobCancelled("Writing the draft")

    started = time.perf_counter()
    with pytest.raises(JobCancelled):
        await asyncio.wait_for(
            gateway.generate_text(
                messages=[{"role": "user", "content": "write"}], max_tokens=50, on_text=on_text
            ),
            timeout=5.0,
        )
    assert seen == ["Tobin ", ""] and time.perf_counter() - started < 3.0


# ------------------------------------------------------------- what is asked again


@pytest.mark.asyncio
async def test_a_refusal_about_something_else_is_not_asked_again():
    """LOW: only a refusal of the usage option is asked again without it; any other (the
    story too long, say) is reported at once, not sent twice."""
    requests: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            400, json={"error": {"message": "This model's maximum context length is 8192."}}
        )

    gateway = OpenAIGateway(api_key="k", default_model="m", base_url="http://model.test/v1")
    gateway._client = AsyncOpenAI(
        api_key="k",
        base_url="http://model.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(AIProviderError):
        await _collect(gateway, [])
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_a_provider_that_will_not_stream_is_asked_once_for_the_whole_answer():
    """LOW: streaming itself refused: asked once more, whole; the words are handed on as
    one piece and the usage the provider reports is kept."""
    requests: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        requests.append(sent)
        if sent.get("stream"):
            return httpx.Response(
                400, json={"error": {"message": "Streaming is not supported for this model."}}
            )
        return httpx.Response(
            200,
            json={
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "All of it."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3},
            },
        )

    gateway = OpenAIGateway(api_key="k", default_model="m", base_url="http://model.test/v1")
    gateway._client = AsyncOpenAI(
        api_key="k",
        base_url="http://model.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    pieces: list[str] = []
    response = await _collect(gateway, pieces)
    assert [bool(sent.get("stream")) for sent in requests] == [True, False]
    assert "stream_options" not in requests[1]
    assert pieces == ["All of it."] and response.text == "All of it."
    assert not response.streamed and response.usage_reported
    assert response.usage.completion_tokens == 3


@pytest.mark.asyncio
async def test_half_a_million_characters_in_small_chunks_pass_through_in_linear_time():
    """PERFORMANCE RULE: the stream loop (a tick and a check on every chunk) costs a fixed
    amount per chunk: 500k characters in 250k two-character chunks, generously bounded."""
    from backend.adapters.ai import stream_text
    from backend.services.studio_service import DraftWriter

    chunk = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(delta=SimpleNamespace(content="a "), finish_reason=None)],
    )

    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def _chunks(self):
            for _ in range(250_000):
                yield chunk

        def __aiter__(self):
            return self._chunks()

    async def create(**_request):
        return _Stream()

    class _Quiet(_Ctx):
        async def step(self, stage: str, progress: int) -> None:
            self.steps += 1

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    store = _Store()
    writer = DraftWriter(store, _Quiet(), number=1, stage="s", progress=(10, 85), words=800)
    started = time.perf_counter()
    streamed = await stream_text(client, model="m", messages=[], max_tokens=1, on_text=writer.add)
    await writer.flush()
    elapsed = time.perf_counter() - started
    assert len(streamed.text) == 500_000 and writer.written_words == 250_000
    assert sum(len(piece) for piece in store.appended) == 200_000  # the most kept to show
    assert elapsed < 20.0, elapsed


# ------------------------------------------------------- the passing copy never fails


@pytest.mark.asyncio
async def test_a_failing_write_of_the_passing_copy_never_fails_the_draft(caplog):
    """LOW: the copy shown while writing cannot be kept (the table is unreachable): the
    draft goes on, the failure is logged by its kind, and no words reach the log."""
    caplog.set_level("WARNING")
    from backend.services.studio_service import DraftWriter

    class _Broken(_Store):
        async def begin(self, **_key) -> None:
            raise RuntimeError("Tobin waited at the quay")

        async def append(self, *, piece: str, **_key) -> None:
            raise RuntimeError(piece)

        async def end(self, **_key) -> None:
            raise RuntimeError("gone")

    class _Quiet(_Ctx):
        async def step(self, stage: str, progress: int) -> None:
            return None

    writer = DraftWriter(_Broken(), _Quiet(), number=1, stage="s", progress=(10, 85), words=10)
    await writer.begin()
    await writer.add("Tobin waited at the quay.")
    await writer.flush()
    await writer.end()
    assert writer.written_words == 5
    assert "studio.stream_write_failed" in caplog.text
    assert "Tobin" not in caplog.text and "quay" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["ReadError", "RemoteProtocolError"])
async def test_a_connection_broken_part_way_through_is_said_plainly(broken):
    """The connection drops after some words: a provider failure, said plainly, never the
    HTTP library's own error."""
    from backend.adapters.connector_transport import http_impl

    first = _chunk("Half ").encode()

    async def body():
        yield first
        raise getattr(http_impl, broken)("connection reset by peer")

    def handle(request):
        return http_impl.Response(
            200, content=body(), headers={"content-type": "text/event-stream"}
        )

    gateway = OpenAIGateway(api_key="k", default_model="m", base_url="http://model.test/v1")
    gateway._client = AsyncOpenAI(
        api_key="k",
        base_url="http://model.test/v1",
        max_retries=0,
        http_client=http_impl.AsyncClient(transport=http_impl.MockTransport(handle)),
    )
    with pytest.raises(AIProviderError) as raised:
        await _collect(gateway, [])
    assert "connection reset" not in str(raised.value)
