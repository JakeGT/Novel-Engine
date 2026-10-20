"""Run the picture and words search evaluation and write a JSON report (CTX-014).

    uv run python -m tests.eval.multimodal.run --report var/eval/multimodal.json
    uv run python -m tests.eval.multimodal.run --provider embeddinggemma \\
        --revision <commit> --dimensions 768,512 --report var/eval/multimodal-gemma.json

Without ``--provider`` the deterministic stand-in is used: the report then measures
the harness, gates and ranking, never a model. ``embeddinggemma`` needs the embeddings
extra (``uv sync --extra embeddings``), a pinned revision (``--revision`` or
``EMBEDDING_GEMMA_REVISION``) and downloads the model on first use; the model is loaded
once and measured at each size by shortening and normalizing again.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tests.eval.multimodal.harness import Shortened, default_configurations, evaluate


def _gemma(revision: str, dimensions: list[int], dtype: str) -> list[tuple[str, Any, bool]]:
    from backend.adapters.embedding import EmbeddingGemmaEmbedding

    base = EmbeddingGemmaEmbedding(revision=revision, dimension=768, dtype=dtype)
    if not base.available():
        raise SystemExit(f"embeddinggemma is not available: {base.health().reason}")
    configurations: list[tuple[str, Any, bool]] = []
    for size in dimensions:
        configurations.append(
            (
                f"embeddinggemma-{size}",
                (lambda s=size: base if s == 768 else Shortened(base, s)),
                False,
            )
        )
    return configurations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tests.eval.multimodal.run")
    parser.add_argument("--report", default="var/eval/multimodal.json")
    parser.add_argument("--provider", choices=("fake", "embeddinggemma"), default="fake")
    parser.add_argument("--revision", default=os.environ.get("EMBEDDING_GEMMA_REVISION", ""))
    parser.add_argument("--dimensions", default="768,512")
    parser.add_argument("--dtype", default=os.environ.get("EMBEDDING_DTYPE", "auto"))
    args = parser.parse_args(argv)
    if args.provider == "embeddinggemma":
        sizes = [int(part) for part in args.dimensions.split(",") if part.strip()]
        configurations = _gemma(args.revision, sizes, args.dtype)
    else:
        configurations = default_configurations()
    measured = evaluate(configurations)
    measured["provider"] = args.provider
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for item in measured["configurations"]:
        words = item["words_to_picture"]
        print(
            f"{item['configuration']}: words->picture R@5={words['recall_at_5']} "
            f"MRR={words['mrr']} hard={words['beats_hard_negatives']} "
            f"links_first={item['links_first']} leaks={item['leaks']} "
            f"rebuild_equal={item['rebuild_equivalent']}"
        )
    print(f"report: {path}")
    return 0 if all(item["leaks"] == 0 for item in measured["configurations"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
