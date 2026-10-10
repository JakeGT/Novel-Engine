"""What a hosted site exposes: no API docs, security headers, and the bundled app shell."""

import pytest

pytestmark = pytest.mark.contract


def _client(settings_factory, tmp_path, **overrides):
    from fastapi.testclient import TestClient

    from backend.main import create_app
    from tests.conftest import _build_fake_container

    container = _build_fake_container(settings_factory(**overrides), tmp_path)
    return TestClient(create_app(container=container))


def test_hosted_site_does_not_publish_api_docs(hosted_client) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert hosted_client.get(path).status_code == 404, path


def test_local_development_still_has_docs(api_client) -> None:
    assert api_client.get("/openapi.json").status_code == 200
    assert api_client.get("/docs").status_code == 200


def test_docs_exposure_can_be_set_explicitly(settings_factory, tmp_path) -> None:
    with _client(settings_factory, tmp_path, APP_ENV="test", API_DOCS_ENABLED=False) as client:
        assert client.get("/openapi.json").status_code == 404
        # The contract document itself is still generated for tooling.
        assert client.app.openapi()["paths"]


def test_hosted_responses_carry_security_headers(hosted_client) -> None:
    response = hosted_client.get("/health/live")
    policy = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert (
        "script-src 'self'" in policy
        and "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]
    )
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "max-age=" in response.headers["Strict-Transport-Security"]
    # API and session responses are never cacheable by an intermediary.
    assert hosted_client.get("/auth/me").headers["Cache-Control"] == "no-store"


def test_local_mode_does_not_force_https_headers(api_client) -> None:
    assert "Strict-Transport-Security" not in api_client.get("/health/live").headers


@pytest.fixture
def built_frontend(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Novel Engine</title><div id=root>")
    (dist / "assets" / "index-abc123.js").write_text("console.log('app')")
    (dist / "assets" / "inter.woff2").write_bytes(b"wOF2")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside the build")
    return dist


def test_app_shell_is_served_for_ui_routes_only(settings_factory, tmp_path, built_frontend) -> None:
    with _client(
        settings_factory, tmp_path, APP_ENV="test", FRONTEND_DIST_DIR=str(built_frontend)
    ) as client:
        for path in ("/", "/projects/prj_1", "/projects/prj_1/mutations/mut_1"):
            page = client.get(path)
            assert page.status_code == 200 and "id=root" in page.text, path
            assert page.headers["Cache-Control"] == "no-cache"
        assert client.get("/assets/index-abc123.js").text == "console.log('app')"
        icon = client.get("/favicon.svg")
        assert icon.text == "<svg/>" and icon.headers["Cache-Control"] == "no-cache"
        # A missing file is a 404, never the app shell served under a file name.
        for missing in ("/favicon.ico", "/robots.txt", "/old/logo.png"):
            assert client.get(missing).status_code == 404, missing
        assert client.get("/assets/inter.woff2").headers["content-type"] == "font/woff2"

        # API routes still answer as the API, and API misses are problems, not HTML.
        assert client.get("/health/live").json() == {"status": "alive"}
        assert client.get("/api/v1/workspaces").status_code == 401
        for missing in ("/api/v1/nope", "/auth/nope", "/health/nope", "/api"):
            miss = client.get(missing)
            assert miss.status_code == 404, missing
            assert miss.headers["content-type"].startswith("application/problem+json")
            assert miss.json()["code"] == "not_found"

        # With the docs switched off they are a 404, not the app shell.
    with _client(
        settings_factory,
        tmp_path,
        APP_ENV="test",
        FRONTEND_DIST_DIR=str(built_frontend),
        API_DOCS_ENABLED=False,
    ) as client:
        for hidden in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(hidden).status_code == 404, hidden

    with _client(
        settings_factory, tmp_path, APP_ENV="test", FRONTEND_DIST_DIR=str(built_frontend)
    ) as client:
        # Nothing outside the build directory can be read through the shell route.
        for escape in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
            assert "outside the build" not in client.get(escape).text
        # The UI routes are not part of the API contract.
        assert "/{path}" not in client.app.openapi()["paths"]


def test_without_a_build_the_api_root_is_unchanged(api_client) -> None:
    assert api_client.get("/").json() == {"message": "Welcome to the Novel Engine API"}
    assert api_client.get("/projects/prj_1").status_code == 404


def test_secrets_can_be_read_from_mounted_files(tmp_path) -> None:
    """Run in a fresh interpreter: the secrets directory is read when settings load."""
    import os
    import subprocess
    import sys

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "postgres_password").write_text("from-a-file\n")
    (secrets / "oidc_client_secret").write_text("client-secret-from-file")
    program = (
        "from backend.core.config import Settings;"
        "s = Settings(_env_file=None);"
        "print(s.POSTGRES_PASSWORD == 'from-a-file', s.OIDC_CLIENT_SECRET == 'client-secret-from-file')"
    )
    base = {
        k: v for k, v in os.environ.items() if k not in {"POSTGRES_PASSWORD", "OIDC_CLIENT_SECRET"}
    }
    env = {**base, "NOVEL_ENGINE_SECRETS_DIR": str(secrets)}

    from_files = subprocess.run(
        [sys.executable, "-c", program], env=env, capture_output=True, text=True, check=True
    )
    assert from_files.stdout.split() == ["True", "True"]

    # An explicit environment variable still wins over the file.
    overridden = subprocess.run(
        [sys.executable, "-c", program],
        env={**env, "POSTGRES_PASSWORD": "from-the-environment"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert overridden.stdout.split() == ["False", "True"]
