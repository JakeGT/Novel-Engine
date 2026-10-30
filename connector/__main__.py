"""``python -m connector``: connect this computer's model server to a Novel Engine site."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from connector import __version__
from connector.core import (
    EXIT_BAD_SETUP,
    EXIT_OK,
    Connector,
    CredentialFile,
    ModelServer,
    Site,
    default_name,
    default_state_dir,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m connector",
        description=(
            "Use a model on this computer from a Novel Engine site. Nothing on this computer "
            "is opened to the internet: the connector only asks the site for work."
        ),
    )
    parser.add_argument(
        "--site",
        default=os.environ.get("NOVEL_ENGINE_SITE", ""),
        help="the site's address, such as https://novel-engine.example.com",
    )
    parser.add_argument(
        "--model-server",
        default=os.environ.get("NOVEL_ENGINE_MODEL_SERVER", "http://127.0.0.1:11434/v1"),
        help=(
            "your model server's OpenAI-compatible address (Ollama http://127.0.0.1:11434/v1, "
            "LM Studio http://127.0.0.1:1234/v1, llama.cpp http://127.0.0.1:8080/v1)"
        ),
    )
    parser.add_argument("--name", default=default_name(), help="what the app calls this computer")
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("NOVEL_ENGINE_CONNECTOR_DIR", ""),
        help="where the credential is kept (readable only by you)",
    )
    parser.add_argument(
        "--forget", action="store_true", help="remove the credential kept for this site, then stop"
    )
    parser.add_argument("--once", action="store_true", help="stop after one request (for testing)")
    parser.add_argument("--version", action="version", version=f"connector {__version__}")
    args = parser.parse_args(argv)

    if not args.site:
        parser.print_usage(sys.stderr)
        print("Give the site's address with --site.", file=sys.stderr)
        return EXIT_BAD_SETUP
    try:
        site = Site(args.site)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return EXIT_BAD_SETUP
    store = CredentialFile(
        Path(args.state_dir) if args.state_dir else default_state_dir(), site.url
    )
    if args.forget:
        store.forget()
        print("The credential for this site was removed. Disconnect the computer in the app too.")
        return EXIT_OK
    connector = Connector(site, ModelServer(args.model_server), store, name=args.name)
    try:
        return connector.run(once=args.once)
    except KeyboardInterrupt:
        print("Stopped. Start it again whenever you want to use this computer's model.")
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
