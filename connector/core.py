"""The connector's work: connect once with a code, then relay requests to the model server.

Standard library only, so it runs with any Python 3.11 or later.

What it does, and what it never does:

- It talks to the site over HTTPS (plain HTTP only to this computer itself, for testing)
  and never follows a redirect, so its credential cannot be sent anywhere else.
- It sends the model server only the two calls the site relays (listing models and a
  chat completion), and only to the address it was given.
- It prints what it is doing in counts and states, never the words of a request.
- It never downloads or installs anything. When the site says it is too old, it stops.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

from connector import __version__

# The calls relayed to the model server: (method, path below /v1).
RELAYED = {("POST", "/chat/completions"), ("GET", "/models")}
LONG_POLL_SECONDS = 25
HEARTBEAT_SECONDS = 20
DISCOVER_EVERY_SECONDS = 300
LOCAL_TIMEOUT_SECONDS = 600
MAX_ANSWER_BYTES = 4 * 1024 * 1024
MAX_MODELS = 200

EXIT_OK = 0
EXIT_TOO_OLD = 2
EXIT_DISCONNECTED = 3
EXIT_REFUSED = 4
EXIT_BAD_SETUP = 5


class Unreachable(Exception):
    """The site or the model server could not be reached at all."""


class TimedOut(Unreachable):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.strip().split("."))
    except ValueError:
        return (0,)


def _loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_site_url(site: str) -> str:
    """The site's address, refused unless it is HTTPS (or this computer, for testing)."""
    parts = urlsplit(site.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("Give the site's address, such as https://novel-engine.example.com")
    if parts.scheme == "http" and not _loopback(parts.hostname):
        raise ValueError("The site must be reached over https:// so your credential is safe.")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


class Http:
    """JSON over HTTP with the standard library. ``proxies`` false for the model server."""

    def __init__(self, *, proxies: bool, redirects: bool) -> None:
        handlers: list[Any] = []
        if not proxies:
            handlers.append(urllib.request.ProxyHandler({}))
        if not redirects:
            handlers.append(_NoRedirect())
        self._opener = urllib.request.build_opener(*handlers)

    def call(
        self,
        method: str,
        url: str,
        *,
        body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
        limit: int = MAX_ANSWER_BYTES,
    ) -> tuple[int, Any]:
        # Letters as they are, not escaped: the same text in fewer bytes.
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/json",
                "User-Agent": f"novel-engine-connector/{__version__}",
                **({"Content-Type": "application/json"} if data is not None else {}),
                **(headers or {}),
            },
        )
        try:
            try:
                with self._opener.open(request, timeout=timeout) as response:
                    status, raw = response.status, response.read(limit + 1)
            except urllib.error.HTTPError as error:
                status, raw = error.code, error.read(limit + 1)
        except TimeoutError as error:
            raise TimedOut(str(type(error).__name__)) from error
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            # HTTPException: an answer cut off part way (IncompleteRead) or not HTTP at all.
            if isinstance(getattr(error, "reason", None), TimeoutError):
                raise TimedOut("timeout") from error
            raise Unreachable(type(error).__name__) from error
        if len(raw) > limit:
            return status, {"__too_large__": True}
        if not raw:
            return status, None
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {"__not_json__": True}


# --- the credential on this computer ------------------------------------------------------


@dataclass
class Credential:
    site: str
    device_id: str
    credential: str


def default_state_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "novel-engine-connector"


class CredentialFile:
    """One file per site, readable only by this user, written atomically."""

    def __init__(self, directory: Path, site: str) -> None:
        host = urlsplit(site).netloc.replace(":", "_") or "site"
        self.path = directory / f"{host}.json"
        self._site = site

    def load(self) -> Credential | None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("site") != self._site or not data.get("credential"):
            return None
        return Credential(
            site=data["site"], device_id=str(data["device_id"]), credential=data["credential"]
        )

    def save(self, credential: Credential) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".credential-")
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(credential.__dict__, file)
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def forget(self) -> None:
        self.path.unlink(missing_ok=True)


# --- the model server -----------------------------------------------------------------------


def _root(base: str) -> str:
    trimmed = base.rstrip("/")
    return trimmed[: -len("/v1")] if trimmed.endswith("/v1") else trimmed


def _ollama_context(parameters: Any) -> int | None:
    """``num_ctx`` when the model's settings fix it; otherwise unknown (the server's default
    applies, which is usually far below the model's trained length)."""
    if not isinstance(parameters, str):
        return None
    for line in parameters.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "num_ctx" and parts[1].isdigit():
            return int(parts[1])
    return None


class ModelServer:
    """The OpenAI-compatible model server on this computer (or one the person chose)."""

    def __init__(self, base_url: str, http: Http | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http or Http(proxies=False, redirects=False)

    def discover(self) -> dict[str, Any]:
        """Which models it has, how much context each was given, and whether it keeps to a
        JSON schema. Raises ``Unreachable`` when the server is not there."""
        status, body = self._http.call("GET", f"{self.base_url}/models", timeout=10)
        names: list[str] = []
        if status == 200 and isinstance(body, dict) and isinstance(body.get("data"), list):
            for item in body["data"]:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    names.append(item["id"])
        names = names[:MAX_MODELS]
        contexts: dict[str, int | None] = {}
        server = "openai-compatible"
        root = _root(self.base_url)
        if self._probe(f"{root}/api/version", "version"):
            server = "ollama"
            for name in names[:20]:
                found = self._quiet("POST", f"{root}/api/show", {"model": name})
                if isinstance(found, dict):
                    contexts[name] = _ollama_context(found.get("parameters"))
        elif (listed := self._quiet("GET", f"{root}/api/v0/models")) and isinstance(
            listed.get("data") if isinstance(listed, dict) else None, list
        ):
            server = "lmstudio"
            for item in listed["data"]:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    loaded = item.get("loaded_context_length")
                    contexts[item["id"]] = loaded if isinstance(loaded, int) else None
        elif isinstance(props := self._quiet("GET", f"{root}/props"), dict) and isinstance(
            props.get("default_generation_settings"), dict
        ):
            server = "llamacpp"
            n_ctx = props["default_generation_settings"].get("n_ctx") or props.get("n_ctx")
            for name in names:
                contexts[name] = n_ctx if isinstance(n_ctx, int) else None
        return {
            "server": server,
            "models": [{"name": n, "context_tokens": contexts.get(n)} for n in names],
            # Ollama (0.5+), LM Studio and llama.cpp's server constrain output to a schema.
            "structured_output": server != "openai-compatible",
        }

    def _quiet(self, method: str, url: str, body: Any = None) -> Any:
        try:
            status, found = self._http.call(method, url, body=body, timeout=5)
        except Unreachable:
            return None
        return found if status == 200 else None

    def _probe(self, url: str, key: str) -> bool:
        found = self._quiet("GET", url)
        return isinstance(found, dict) and key in found

    def relay(self, method: str, path: str, body: Any) -> dict[str, Any]:
        """Send one relayed call; the answer to return to the site."""
        if (method, path) not in RELAYED:
            return {"failure": "local_server_error"}
        if isinstance(body, dict):
            body = {**body, "stream": False}
        try:
            status, answer = self._http.call(
                method,
                f"{self.base_url}{path}",
                body=body if method == "POST" else None,
                timeout=LOCAL_TIMEOUT_SECONDS,
            )
        except TimedOut:
            return {"failure": "local_server_timeout"}
        except Unreachable:
            return {"failure": "local_server_unreachable"}
        if isinstance(answer, dict) and answer.get("__too_large__"):
            return {"failure": "too_large"}
        if not isinstance(answer, dict) or answer.get("__not_json__"):
            return {"failure": "local_server_error"}
        return {"status": status, "body": answer}


# --- the site --------------------------------------------------------------------------------


class Site:
    def __init__(self, url: str, http: Http | None = None) -> None:
        self.url = check_site_url(url)
        self._http = http or Http(proxies=True, redirects=False)

    def _api(self, path: str) -> str:
        return f"{self.url}/api/v1{path}"

    def versions(self) -> dict[str, Any]:
        status, body = self._http.call("GET", self._api("/connector/version"), timeout=15)
        if status != 200 or not isinstance(body, dict):
            raise Unreachable(f"version {status}")
        return body

    def start(self, name: str) -> tuple[int, Any]:
        return self._http.call(
            "POST",
            self._api("/connector/codes"),
            body={"name": name, "version": __version__},
            timeout=15,
        )

    def token(self, device_code: str) -> tuple[int, Any]:
        return self._http.call(
            "POST", self._api("/connector/token"), body={"device_code": device_code}, timeout=15
        )

    def _auth(self, credential: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {credential}", "X-Connector-Version": __version__}

    def poll(
        self, credential: str, *, capabilities: dict | None, wait: float, take: bool = True
    ) -> tuple[int, Any]:
        body: dict[str, Any] = {"wait": wait, "take": take}
        if capabilities is not None:
            body["capabilities"] = capabilities
        return self._http.call(
            "POST",
            self._api("/connector/poll"),
            body=body,
            headers=self._auth(credential),
            timeout=wait + 20,
        )

    def answer(self, credential: str, request_id: str, answer: dict[str, Any]) -> tuple[int, Any]:
        return self._http.call(
            "POST",
            self._api(f"/connector/requests/{request_id}/answer"),
            body=answer,
            headers=self._auth(credential),
            timeout=60,
        )

    def rotate(self, credential: str) -> tuple[int, Any]:
        return self._http.call(
            "POST", self._api("/connector/rotate"), headers=self._auth(credential), timeout=15
        )


# --- running -----------------------------------------------------------------------------------


class Connector:
    def __init__(
        self,
        site: Site,
        server: ModelServer,
        store: CredentialFile,
        *,
        name: str,
        out: TextIO = sys.stdout,
        sleep: Any = time.sleep,
    ) -> None:
        self.site = site
        self.server = server
        self.store = store
        self.name = name
        self._out = out
        self._sleep = sleep
        self._credential: Credential | None = None
        self._lock = threading.Lock()
        self.handled = 0

    def say(self, message: str) -> None:
        print(message, file=self._out, flush=True)

    # The site's verdict on this version, before anything else.
    def check_version(self) -> int | None:
        versions = self.site.versions()
        minimum = str(versions.get("minimum", "0"))
        if _version_tuple(__version__) < _version_tuple(minimum):
            self.say(
                f"This connector is version {__version__}, and the site needs {minimum} or "
                "later. Get the new version from the site's instructions, then start it again. "
                "Nothing was downloaded."
            )
            return EXIT_TOO_OLD
        latest = str(versions.get("latest", __version__))
        if _version_tuple(__version__) < _version_tuple(latest):
            self.say(f"A newer connector ({latest}) is available. This one still works.")
        return None

    def connect(self, *, deadline_seconds: float | None = None) -> int | None:
        """Register with a code the person confirms in the app (RFC 8628)."""
        status, body = self.site.start(self.name)
        if status == 426:
            self.say("This connector is too old for the site. Update it, then start it again.")
            return EXIT_TOO_OLD
        started = _started(body) if status == 201 else None
        if started is None:
            self.say(f"The site did not give a code (answer {status}). Try again later.")
            return EXIT_REFUSED
        code, device_code, interval, lifetime = started
        expires = time.monotonic() + lifetime
        self.say("")
        self.say("To connect this computer, open Novel Engine, go to AI assistant, choose")
        self.say("“Use a model on your own computer” and type this code:")
        self.say("")
        self.say(f"    Code: {code}")
        self.say("")
        self.say("Only type it into your own Novel Engine site. The code works for 10 minutes.")
        stop_at = time.monotonic() + deadline_seconds if deadline_seconds else None
        while time.monotonic() < expires and (stop_at is None or time.monotonic() < stop_at):
            self._sleep(interval)
            try:
                status, answer = self.site.token(device_code)
            except Unreachable:
                interval = min(60, interval * 2)
                continue
            if (
                status == 200
                and isinstance(answer, dict)
                and isinstance(answer.get("credential"), str)
                and isinstance(answer.get("device_id"), str)
                and answer["credential"]
                and answer["device_id"]
            ):
                self._credential = Credential(
                    site=self.site.url,
                    device_id=answer["device_id"],
                    credential=answer["credential"],
                )
                self.store.save(self._credential)
                self.say("Connected. Keep this window open while you use the assistant.")
                return None
            error = answer.get("error") if isinstance(answer, dict) else None
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval = _seconds(answer.get("interval"), interval + 5)
                continue
            if error == "access_denied":
                self.say("The code was turned down in the app. Nothing was connected.")
                return EXIT_REFUSED
            self.say("The code ran out or was already used. Start the connector again.")
            return EXIT_REFUSED
        self.say("The code ran out. Start the connector again for a new one.")
        return EXIT_REFUSED

    def _rotate(self) -> None:
        with self._lock:
            assert self._credential is not None
            try:
                status, body = self.site.rotate(self._credential.credential)
            except Unreachable:
                # The old credential keeps working for a while; the site asks again.
                return
            if status == 200 and isinstance(body, dict) and isinstance(body.get("credential"), str):
                self._credential = Credential(
                    site=self._credential.site,
                    device_id=self._credential.device_id,
                    credential=str(body["credential"]),
                )
                self.store.save(self._credential)

    def _current(self) -> str:
        with self._lock:
            assert self._credential is not None
            return self._credential.credential

    def _handle(self, request: dict[str, Any]) -> None:
        method, path = str(request.get("method", "")), str(request.get("path", ""))
        done = threading.Event()

        def heartbeat() -> None:
            # Say "still here" while a slow model works, without taking more work.
            while not done.wait(HEARTBEAT_SECONDS):
                try:
                    self.site.poll(self._current(), capabilities=None, wait=0, take=False)
                except Unreachable:
                    continue

        beating = threading.Thread(target=heartbeat, daemon=True)
        beating.start()
        started = time.monotonic()
        try:
            answer = self.server.relay(method, path, request.get("body"))
        finally:
            done.set()
        outcome = answer.get("failure") or f"answered {answer.get('status')}"
        request_id = str(request.get("id", ""))
        try:
            status, _ = self.site.answer(self._current(), request_id, answer)
            if status == 413 and "failure" not in answer:
                # Too large for the site: say so, so the request ends plainly at once rather
                # than when the site stops waiting (already ended there when it said 404).
                outcome = "too_large"
                self.site.answer(self._current(), request_id, {"failure": "too_large"})
            elif status != 204:
                outcome += f", but the site did not take the answer (answer {status})"
        except Unreachable:
            outcome += ", but the answer could not be sent back"
        self.handled += 1
        self.say(f"{method} {path}: {outcome} in {time.monotonic() - started:.1f}s.")

    def run(self, *, once: bool = False, max_polls: int | None = None) -> int:
        """Connect if needed, then relay requests until stopped."""
        try:
            too_old = self.check_version()
        except Unreachable:
            self.say("The site could not be reached. Check its address and your connection.")
            return EXIT_BAD_SETUP
        if too_old is not None:
            return too_old
        self._credential = self.store.load()
        if self._credential is None:
            result = self.connect()
            if result is not None:
                return result
        capabilities: dict | None = None
        discovered_at = 0.0
        backoff = 1.0
        polls = 0
        self.say(f"Waiting for requests. Model server: {self.server.base_url}")
        while max_polls is None or polls < max_polls:
            polls += 1
            sent: dict | None = None
            if time.monotonic() - discovered_at >= DISCOVER_EVERY_SECONDS or capabilities is None:
                try:
                    found = self.server.discover()
                    if found != capabilities:
                        count = len(found["models"])
                        self.say(f"Found {count} model{'s' if count != 1 else ''} on the server.")
                    capabilities, sent = found, found
                except Unreachable:
                    self.say(
                        "The model server could not be reached. Is it running at "
                        f"{self.server.base_url}?"
                    )
                    capabilities, sent = {"server": "openai-compatible", "models": []}, None
                discovered_at = time.monotonic()
            try:
                status, body = self.site.poll(
                    self._current(), capabilities=sent, wait=LONG_POLL_SECONDS
                )
            except Unreachable:
                self._sleep(backoff)
                backoff = min(60.0, backoff * 2)
                continue
            backoff = 1.0
            if status == 401:
                self.store.forget()
                self.say(
                    "This computer was disconnected from the site. Start the connector again "
                    "to connect it with a new code."
                )
                return EXIT_DISCONNECTED
            if status == 426:
                self.say("This connector is too old for the site. Update it, then start again.")
                return EXIT_TOO_OLD
            if status != 200 or not isinstance(body, dict):
                self._sleep(5)
                continue
            if _version_tuple(__version__) < _version_tuple(str(body.get("minimum_version", "0"))):
                self.say("The site now needs a newer connector. Update it, then start again.")
                return EXIT_TOO_OLD
            if body.get("rotate"):
                self._rotate()
            request = body.get("request")
            if isinstance(request, dict):
                self._handle(request)
                if once:
                    return EXIT_OK
        return EXIT_OK


def _seconds(value: Any, default: int) -> int:
    """A whole number of seconds from the site, kept to a sensible range."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return max(1, min(3600, int(value)))


def _started(body: Any) -> tuple[str, str, int, int] | None:
    """The user code, device code, poll interval and lifetime from the site's answer, or
    None when it is not the shape RFC 8628 section 3.2 gives."""
    if not isinstance(body, dict):
        return None
    user_code, device_code = body.get("user_code"), body.get("device_code")
    if not isinstance(user_code, str) or not isinstance(device_code, str):
        return None
    if not user_code.strip() or not device_code or len(user_code) > 40:
        return None
    return (
        user_code,
        device_code,
        _seconds(body.get("interval"), 5),
        _seconds(body.get("expires_in"), 600),
    )


def default_name() -> str:
    return (socket.gethostname() or "My computer")[:80]


__all__ = [
    "Connector",
    "CredentialFile",
    "Http",
    "ModelServer",
    "Site",
    "check_site_url",
    "default_name",
    "default_state_dir",
]
