"""Novel Engine connector: use a model on your own computer from a Novel Engine site.

Run it next to your model server (Ollama, LM Studio, a llama.cpp server)::

    uv run python -m connector --site https://novel-engine.example.com

It needs only the Python standard library. It makes outbound HTTPS requests to the site
and plain requests to your model server; it opens no port. See ADR-025 and
``plan/runbooks/local-connector.md``.
"""

__version__ = "1.0.0"
