"""The connector's rules and the connector program's parts, with no network (SCN-002A)."""

from __future__ import annotations

import io
from typing import Any

import pytest

from backend.domain import connector as c
from connector import __version__
from connector.core import (
    EXIT_DISCONNECTED,
    EXIT_TOO_OLD,
    Connector,
    CredentialFile,
    ModelServer,
    Site,
    TimedOut,
    Unreachable,
    check_site_url,
)


def test_user_codes_use_the_rfc_alphabet_and_typing_is_forgiven():
    code = c.generate_user_code()
    assert len(code) == 8 and set(code) <= set(c.USER_CODE_ALPHABET)
    assert c.normalize_user_code(f" {code[:4].lower()}-{code[4:]} ") == code
    assert c.normalize_user_code("ABCD-EFGH") is None  # vowels are never used
    assert c.normalize_user_code("BCD") is None
    assert c.display_user_code("BCDFGHJK") == "BCDF-GHJK"


def test_credentials_have_a_prefix_and_a_checksum():
    credential = c.generate_credential()
    assert credential.startswith("nvldev_") and len(credential) == c.CREDENTIAL_LENGTH
    assert c.credential_well_formed(credential)
    broken = credential[:-1] + ("A" if credential[-1] != "A" else "B")
    assert not c.credential_well_formed(broken)
    assert not c.credential_well_formed("nvlpat_" + credential[7:])


@pytest.mark.parametrize(
    ("version", "accepted"),
    [("1.0.0", True), ("1.2.0", True), ("0.9.9", False), ("1.0", False), ("", False)],
)
def test_versions_older_than_the_minimum_are_not_accepted(version, accepted):
    assert c.version_accepted(version, "1.0.0") is accepted


def test_a_device_address_names_its_workspace_and_device_and_nothing_else():
    address = c.device_address("ws1", "dev_1")
    assert c.parse_device_address(address) == ("ws1", "dev_1")
    assert c.parse_device_address("https://api.openai.com/v1") is None
    assert c.parse_device_address("device://ws1") is None
    assert c.parse_device_address("device://ws1/dev/extra") is None


def test_the_site_must_be_https_unless_it_is_this_computer():
    assert check_site_url("https://novel.example.com/") == "https://novel.example.com"
    assert check_site_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert check_site_url("http://localhost:5173") == "http://localhost:5173"
    with pytest.raises(ValueError, match="https"):
        check_site_url("http://novel.example.com")
    with pytest.raises(ValueError):
        check_site_url("novel.example.com")


class StubHttp:
    """Answers by method and address; anything else is unreachable."""

    def __init__(self, answers: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, Any]] = []

    def call(self, method: str, url: str, *, body: Any = None, **_: Any) -> tuple[int, Any]:
        self.calls.append((method, url, body))
        found = self.answers.get((method, url))
        if found is None:
            raise Unreachable("no route")
        if isinstance(found, BaseException):
            raise found
        return found


MODELS = (200, {"object": "list", "data": [{"id": "llama3.2:3b"}, {"id": "qwen3:8b"}]})


def test_ollama_is_recognised_and_only_a_fixed_context_is_reported():
    http = StubHttp(
        {
            ("GET", "http://h:11434/v1/models"): MODELS,
            ("GET", "http://h:11434/api/version"): (200, {"version": "0.12.0"}),
            ("POST", "http://h:11434/api/show"): (200, {"parameters": "num_ctx 8192\nstop x"}),
        }
    )
    found = ModelServer("http://h:11434/v1", http).discover()  # type: ignore[arg-type]
    assert found["server"] == "ollama" and found["structured_output"] is True
    assert found["models"][0] == {"name": "llama3.2:3b", "context_tokens": 8192}


def test_lm_studio_and_llama_cpp_report_the_context_they_loaded():
    lm = StubHttp(
        {
            ("GET", "http://h:1234/v1/models"): (200, {"data": [{"id": "phi4"}]}),
            ("GET", "http://h:1234/api/v0/models"): (
                200,
                {
                    "data": [
                        {"id": "phi4", "max_context_length": 131072, "loaded_context_length": 4096}
                    ]
                },
            ),
        }
    )
    found = ModelServer("http://h:1234/v1", lm).discover()  # type: ignore[arg-type]
    assert found["server"] == "lmstudio"
    assert found["models"] == [{"name": "phi4", "context_tokens": 4096}]

    llama = StubHttp(
        {
            ("GET", "http://h:8080/v1/models"): (200, {"data": [{"id": "model.gguf"}]}),
            ("GET", "http://h:8080/props"): (
                200,
                {"default_generation_settings": {"n_ctx": 16384}},
            ),
        }
    )
    found = ModelServer("http://h:8080/v1", llama).discover()  # type: ignore[arg-type]
    assert found["server"] == "llamacpp"
    assert found["models"] == [{"name": "model.gguf", "context_tokens": 16384}]


def test_another_server_is_asked_in_words_rather_than_with_a_schema():
    http = StubHttp({("GET", "http://h/v1/models"): MODELS})
    found = ModelServer("http://h/v1", http).discover()  # type: ignore[arg-type]
    assert found["server"] == "openai-compatible" and found["structured_output"] is False


def test_only_the_two_relayed_calls_are_sent_on_and_failures_become_codes():
    http = StubHttp(
        {
            ("POST", "http://h/v1/chat/completions"): (200, {"choices": []}),
            ("GET", "http://h/v1/models"): TimedOut("slow"),
        }
    )
    server = ModelServer("http://h/v1", http)  # type: ignore[arg-type]
    assert server.relay("POST", "/chat/completions", {"stream": True, "model": "m"}) == {
        "status": 200,
        "body": {"choices": []},
    }
    # Never a stream: one answer comes back whole.
    assert http.calls[-1][2] == {"stream": False, "model": "m"}
    assert server.relay("GET", "/models", None) == {"failure": "local_server_timeout"}
    assert server.relay("POST", "/embeddings", {}) == {"failure": "local_server_error"}
    assert ModelServer("http://gone/v1", StubHttp({})).relay(  # type: ignore[arg-type]
        "POST", "/chat/completions", {}
    ) == {"failure": "local_server_unreachable"}


def test_the_credential_file_is_private_and_belongs_to_one_site(tmp_path):
    from connector.core import Credential

    store = CredentialFile(tmp_path, "https://novel.example.com")
    store.save(Credential("https://novel.example.com", "dev_1", "nvldev_x"))
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"
    assert store.load() == Credential("https://novel.example.com", "dev_1", "nvldev_x")
    assert CredentialFile(tmp_path, "https://other.example.com").load() is None
    store.forget()
    assert store.load() is None


class StubSite(Site):
    def __init__(self, answers: dict[str, Any]) -> None:
        super().__init__("https://novel.example.com")
        self.answers = answers

    def versions(self) -> dict[str, Any]:
        return self.answers["versions"]

    def poll(self, credential: str, **_: Any) -> tuple[int, Any]:
        return self.answers["poll"]


def _connector(tmp_path, site: StubSite) -> tuple[Connector, io.StringIO]:
    from connector.core import Credential

    store = CredentialFile(tmp_path, site.url)
    store.save(Credential(site.url, "dev_1", "nvldev_x"))
    out = io.StringIO()
    server = ModelServer("http://h/v1", StubHttp({("GET", "http://h/v1/models"): MODELS}))  # type: ignore[arg-type]
    return Connector(site, server, store, name="Laptop", out=out, sleep=lambda _s: None), out


def test_the_connector_stops_when_the_site_needs_a_newer_one_and_downloads_nothing(tmp_path):
    site = StubSite({"versions": {"minimum": "99.0.0", "latest": "99.0.0"}})
    connector, out = _connector(tmp_path, site)
    assert connector.run(max_polls=1) == EXIT_TOO_OLD
    assert "Nothing was downloaded" in out.getvalue()
    assert __version__ in out.getvalue()


def test_a_disconnected_computer_forgets_its_credential(tmp_path):
    site = StubSite({"versions": {"minimum": "1.0.0", "latest": "1.0.0"}, "poll": (401, {})})
    connector, out = _connector(tmp_path, site)
    assert connector.run(max_polls=1) == EXIT_DISCONNECTED
    assert connector.store.load() is None
    assert "disconnected" in out.getvalue()


def test_the_secret_scanner_recognises_a_device_credential():
    from scripts.ci import secret_scan

    findings = secret_scan.scan_text(f"credential = {c.generate_credential()}")
    assert [name for name, _line, _masked in findings] == ["novel_engine_device_credential"]


def test_hidden_characters_are_taken_out_of_a_computers_name():
    # A right-to-left override would make "Jake's laptop‮exe." read as something else.
    assert c.clean_device_name("Jake's‮ laptop​") == "Jake's laptop"
    assert c.clean_device_name("Study\ndesk\ttop ") == "Study desk top"
    assert c.clean_device_name("⁦⁩") == ""
    assert c.clean_device_name("x" * 200) == "x" * c.MAX_DEVICE_NAME
    # Letters and marks of any language stay.
    assert c.clean_device_name("Ordinateur de Zoé") == "Ordinateur de Zoé"


def test_a_cut_off_answer_counts_as_unreachable_and_words_are_sent_unescaped(monkeypatch):
    import http.client
    import urllib.request

    from connector.core import Http

    sent: list[bytes] = []

    class Opener:
        def open(self, request: Any, timeout: float) -> Any:
            sent.append(request.data)
            raise http.client.IncompleteRead(b"{")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(Unreachable):
        Http(proxies=False, redirects=False).call("POST", "https://h/x", body={"t": "Zoé ✓"})
    assert sent == ['{"t": "Zoé ✓"}'.encode()]


class SiteDouble(StubSite):
    """A site whose answers the test sets, recording what the connector sends."""

    def __init__(self, answers: dict[str, Any]) -> None:
        super().__init__(answers)
        self.answered: list[tuple[str, dict]] = []
        self.rotations = 0

    def start(self, name: str) -> tuple[int, Any]:
        return self.answers["start"]

    def token(self, device_code: str) -> tuple[int, Any]:
        return self.answers["token"]

    def rotate(self, credential: str) -> tuple[int, Any]:
        self.rotations += 1
        found = self.answers["rotate"]
        if isinstance(found, BaseException):
            raise found
        return found

    def answer(self, credential: str, request_id: str, answer: dict[str, Any]) -> tuple[int, Any]:
        self.answered.append((request_id, answer))
        return self.answers["answer"].pop(0)


VERSIONS = {"minimum": "1.0.0", "latest": "1.0.0"}


@pytest.mark.parametrize(
    "started",
    [
        (201, {"device_code": "d"}),
        (201, {"user_code": 5, "device_code": "d"}),
        (201, {"user_code": "BCDF-GHJK", "device_code": None}),
        (201, ["not", "an", "object"]),
    ],
)
def test_a_code_answer_of_the_wrong_shape_stops_plainly(tmp_path, started):
    site = SiteDouble({"versions": VERSIONS, "start": started})
    connector, out = _connector(tmp_path, site)
    assert connector.connect() == 4
    assert "did not give a code" in out.getvalue()


def test_a_credential_answer_of_the_wrong_shape_is_not_kept(tmp_path):
    site = SiteDouble(
        {
            "versions": VERSIONS,
            "start": (201, {"user_code": "BCDF-GHJK", "device_code": "d", "interval": "x"}),
            "token": (200, {"credential": 7}),
        }
    )
    connector, out = _connector(tmp_path, site)
    connector.store.forget()
    assert connector.connect(deadline_seconds=0.05) == 4
    assert connector.store.load() is None


def test_a_rotation_that_cannot_reach_the_site_keeps_the_connector_running(tmp_path):
    site = SiteDouble(
        {
            "versions": VERSIONS,
            "poll": (200, {"request": None, "rotate": True, "minimum_version": "1.0.0"}),
            "rotate": Unreachable("RemoteDisconnected"),
        }
    )
    connector, _ = _connector(tmp_path, site)
    assert connector.run(max_polls=3) == 0
    assert site.rotations == 3
    assert connector.store.load().credential == "nvldev_x"


def test_an_answer_too_large_for_the_site_ends_the_request_plainly(tmp_path):
    site = SiteDouble({"versions": VERSIONS, "answer": [(413, {}), (404, {})]})
    connector, out = _connector(tmp_path, site)
    connector._credential = connector.store.load()
    connector.server = ModelServer(
        "http://h/v1",
        StubHttp({("POST", "http://h/v1/chat/completions"): (200, {"choices": []})}),  # type: ignore[arg-type]
    )
    connector._handle({"id": "creq_1", "method": "POST", "path": "/chat/completions", "body": {}})
    assert [answer for _, answer in site.answered] == [
        {"status": 200, "body": {"choices": []}},
        {"failure": "too_large"},
    ]
    assert "too_large" in out.getvalue()


def test_an_answer_the_site_turns_down_is_said(tmp_path):
    site = SiteDouble({"versions": VERSIONS, "answer": [(404, {})]})
    connector, out = _connector(tmp_path, site)
    connector._credential = connector.store.load()
    connector._handle({"id": "creq_1", "method": "GET", "path": "/models", "body": None})
    assert "the site did not take the answer (answer 404)" in out.getvalue()
