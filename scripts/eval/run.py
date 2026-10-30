"""Run the evaluation sets and write one report (QLT-003).

    uv run python -m scripts.eval.run                 # make eval: deterministic, no model
    uv run python -m scripts.eval.run --model         # make eval-model: asks before spending
    uv run python -m scripts.eval.run --model --stand-in   # the model path, local stand-in

Runs the ``eval``-marked tests under ``tests/eval`` with ``NOVEL_EVAL_OUT`` set, so
each suite writes its numbers, then writes ``report.json`` and ``report.md`` in the
output directory (``var/eval`` by default; ignored by git). Reports hold counts,
rates, timings and case ids only: no prose, captions, secrets or keys.

``--model`` adds the one measurement that needs a real model: reading the labelled
passages with the configured provider (``OPENAI_API_KEY``/``AI_BASE_URL``/``AI_MODEL``).
It says roughly how many tokens that takes and waits for "yes" (or ``--yes``), and it
refuses to run when ``CI`` is set.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "var" / "eval"

ABOUT: dict[str, dict[str, str]] = {
    "consistency": {
        "title": "Consistency checks against labelled defects (CON-012)",
        "measures": (
            "Detection, false positives, precision and leakage recall of the deterministic "
            "checks (continuity, story rules, knowledge timing, drafting checks, reveals, "
            "thread order and scene conditions) on hand-labelled defects and near misses; "
            "latency per call and on a larger generated story; cost (no model: zero tokens)."
        ),
        "not": (
            "A real model's notes; an author's real manuscript; how often these defects "
            "occur. The cases probe each check's edges, so the rates describe this set only."
        ),
    },
    "bundles": {
        "title": "Review bundles against full-context and vector-only bundling (CON-013)",
        "measures": (
            "What each strategy would show a critic: tokens, groups, the largest group, "
            "passages read more than once, secrets shown, and whether each labelled defect's "
            "passages and records are shown together. Deterministic; no model."
        ),
        "not": (
            "Whether a model notices a defect it is shown. The vector-only baseline ranks by "
            "word overlap, a stand-in for an embedding index."
        ),
    },
    "retrieval": {
        "title": "Scene context against labelled relevance (CTX-010)",
        "measures": (
            "Recall@5, Recall@10, recall, mean reciprocal rank and precision of what a "
            "scene's context gives; leaks (secrets, later knowledge, ended facts, another "
            "line of the story); omissions, said or silent; what-if correctness."
        ),
        "not": (
            "Whether a model writes better with it, or ranking on a large real story. Search "
            "by meaning is off in tests."
        ),
    },
    "extraction": {
        "title": "Reading prose into suggested changes, replayed answers (EXT-008)",
        "measures": (
            "The harness, the reading pipeline's filtering and the scorer, on answers written "
            "by hand to look like a model's (with typical mistakes). Pinned in CI."
        ),
        "not": "Any model. These numbers say nothing about how well a model reads.",
    },
    "extraction_model": {
        "title": "Reading prose with the configured model (EXT-008)",
        "measures": (
            "Precision and recall of a real model's suggested changes on the labelled "
            "passages, by kind of reading, with the tokens it used."
        ),
        "not": "More than four short passages: a first look, not a baseline to approve.",
    },
}


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        return ""


def _confirm(tokens: int, model: str, assume_yes: bool) -> bool:
    print(
        f"Reading the labelled passages with {model} uses about {tokens} tokens "
        "(an over-estimate), billed to the configured provider."
    )
    if assume_yes:
        print("--yes given: going ahead.")
        return True
    if not sys.stdin.isatty():
        print("Not asked: no terminal to answer on. Pass --yes to go ahead.")
        return False
    return input('Type "yes" to go ahead: ').strip().lower() == "yes"


def _cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows)
    return lines


def _consistency(data: dict[str, Any]) -> list[str]:
    overall = data["overall"]
    lines = [
        f"Set `{data['set']}`: {data['cases']} cases ({overall['defects']} defects, "
        f"{overall['clean']} near misses). Cost: {data['cost_tokens']} tokens.",
        "",
    ]
    rows = [
        [
            name,
            n["defects"],
            n["clean"],
            n["true_positive"],
            n["false_negative"],
            n["false_positive"],
            n["precision"],
            n["recall"],
            n["latency"]["median_ms"],
        ]
        for name, n in data["families"].items()
    ]
    rows.append(
        [
            "**all**",
            overall["defects"],
            overall["clean"],
            overall["true_positive"],
            overall["false_negative"],
            overall["false_positive"],
            overall["precision"],
            overall["recall"],
            data["latency"]["median_ms"],
        ]
    )
    lines += _table(
        [
            "Checks",
            "Defects",
            "Clean",
            "Caught",
            "Missed",
            "False alarms",
            "Precision",
            "Recall",
            "Median ms",
        ],
        rows,
    )
    leak = data["leakage"]
    lines += [
        "",
        f"Leakage (a secret or later knowledge reaching a page or a context): caught "
        f"{leak['caught']} of {leak['cases']} (recall {leak['recall']}).",
        "",
        "Missed or wrongly flagged, each with why:",
        "",
    ]
    reasons = {**data["known_limits"], **data.get("suspected_bugs", {})}
    for case_id in data["disagreements"]:
        kind = "suspected bug" if case_id in data.get("suspected_bugs", {}) else "limit"
        lines.append(f"- `{case_id}` ({kind}): {reasons.get(case_id, 'unexplained')}")
    scale = data["scale"]
    lines += [
        "",
        f"Larger story: continuity on {scale['continuity']['scenes']} scenes and "
        f"{scale['continuity']['facts']} facts took {scale['continuity']['ms']:.1f} ms; a rule "
        f"over {scale['rules']['facts']} facts {scale['rules']['ms']:.1f} ms; the drafting checks "
        f"on {scale['drafting']['words']} words {scale['drafting']['ms']:.1f} ms "
        "(this machine; not pinned).",
    ]
    return lines


def _bundles(data: dict[str, Any]) -> list[str]:
    lines = [
        f"Set `{data['set']}`: {data['passages']} passages in 6 scenes, {data['records']} records, "
        f"{data['defects']} labelled defects; vector-only takes the {data['nearest_k']} nearest "
        "records per passage.",
        "",
    ]
    for name, result in sorted(data["budgets"].items(), key=lambda pair: pair[1]["budget"]):
        lines += [f"Budget `{name}`: {result['budget']} tokens a group.", ""]
        rows = []
        for strategy in ("deterministic", "full_context", "vector_only"):
            m = result[strategy]
            rows.append(
                [
                    strategy.replace("_", " "),
                    m["groups"],
                    m["tokens"],
                    m["content_tokens"],
                    m["largest_group"],
                    m["groups_showing_a_secret"],
                    f"{m['defects_covered']}/{m['defects']}",
                    ", ".join(sorted(m["omitted"])) or "none",
                ]
            )
        lines += _table(
            [
                "Strategy",
                "Groups",
                "Tokens",
                "Passages+records",
                "Largest",
                "Groups with a secret",
                "Defects shown",
                "Omitted",
            ],
            rows,
        )
        lines.append("")
    fallback = data["fallback_without_graph"]
    lines.append(
        f"Without the story graph the plan reads passages in order with no records: "
        f"{fallback['defects_covered']}/{fallback['defects']} defects shown, and the plan "
        "says its coverage is incomplete."
    )
    return lines


def _retrieval(data: dict[str, Any]) -> list[str]:
    lines = [
        f"Set `{data['set']}`: {data['scenes']} labelled scenes; search by meaning: "
        f"{'on' if data['semantic_search'] else 'off'}.",
        "",
    ]
    lines += _table(
        [
            "Recall@5",
            "Recall@10",
            "Recall",
            "MRR",
            "Precision",
            "Leaks",
            "Silent omissions",
            "Distractors given",
            "What-if correct",
        ],
        [
            [
                data["recall_at_5"],
                data["recall_at_10"],
                data["recall"],
                data["mrr"],
                data["precision"],
                data["leaks"],
                data["silent_omissions"],
                data["distractors_given"],
                data["branch_correct"],
            ]
        ],
    )
    lines.append("")
    lines += _table(
        [
            "Scene",
            "Budget",
            "Given",
            "Recall@5",
            "Recall@10",
            "Recall",
            "Left out for room",
            "Omitted",
        ],
        [
            [
                item["id"],
                item["budget"],
                item["given"],
                item["recall_at_5"],
                item["recall_at_10"],
                item["recall"],
                item["left_out_for_room"],
                ", ".join(item["omitted"]) or "none",
            ]
            for item in data["per_scene"]
        ],
    )
    return lines


def _extraction(data: dict[str, Any]) -> list[str]:
    lines = [f"Set `{data['set']}`, {data['cases']} passages, model: **{data['model']}**.", ""]
    lines += _table(
        ["What", "Suggested", "Expected", "Right", "Precision", "Recall"],
        [
            [part, n["drafted"], n["expected"], n["correct"], n["precision"], n["recall"]]
            for part, n in (
                ("facts", data["facts"]),
                ("beliefs", data["beliefs"]),
                ("who a phrase means", data["references"]),
            )
        ],
    )
    lines += [""]
    lines += _table(
        ["Kind of reading", "Labelled", "Found", "Recall"],
        [[kind, n["labelled"], n["found"], n["recall"]] for kind, n in data["by_kind"].items()],
    )
    lines += [
        "",
        f"Things only said, or only near the truth, suggested as facts: "
        f"{data['said_or_near_made_facts']}. Items the reading dropped (quote not in the "
        f"passage): {data['discarded_by_reading']}.",
    ]
    if "usage" in data:
        used = data["usage"]
        lines.append(
            f"Tokens used: {used['prompt']} in, {used['completion']} out, over {used['calls']} "
            f"calls (estimated beforehand: {data['estimated_tokens']})."
        )
    return lines


RENDER = {
    "consistency": _consistency,
    "bundles": _bundles,
    "retrieval": _retrieval,
    "extraction": _extraction,
    "extraction_model": _extraction,
}


def write_report(out: Path, raw: dict[str, Any], meta: dict[str, Any]) -> tuple[Path, Path]:
    report = {"meta": meta, "suites": raw}
    json_path = out / "report.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Evaluation report",
        "",
        f"- When: {meta['when']}",
        f"- Code: `{meta['commit'] or 'unknown'}`{' (with uncommitted changes)' if meta['dirty'] else ''}",
        f"- Model: {meta['model']}",
        f"- Tests: exit code {meta['pytest_exit']}",
        "",
        "Numbers from deterministic parts need no model and repeat exactly; numbers that "
        "need a real model come only from `make eval-model` and are marked as such.",
    ]
    order = [name for name in ABOUT if name in raw] + sorted(set(raw) - set(ABOUT))
    for name in order:
        data = raw[name]
        about = ABOUT.get(name, {"title": name, "measures": "", "not": ""})
        lines += ["", f"## {about['title']}", ""]
        if about["measures"]:
            lines += [f"Measures: {about['measures']}", "", f"Does not measure: {about['not']}", ""]
        render = RENDER.get(name)
        if render is not None:
            lines += render(data)
        else:
            # Another area's suite: its top-level numbers as they are.
            simple = {k: v for k, v in data.items() if isinstance(v, int | float | str | bool)}
            lines += [f"- {key}: {_cell(value)}" for key, value in sorted(simple.items())]
    md_path = out / "report.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", action="store_true", help="Also read with the configured model")
    parser.add_argument("--stand-in", action="store_true", help="With --model: the local stand-in")
    parser.add_argument("--yes", action="store_true", help="With --model: agree to the spend")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    model = "none (deterministic, scripted and recorded answers only)"
    stand_in = None
    if args.model:
        if env.get("CI"):
            print("Refusing to call a real model in CI.")
            return 2
        from tests.eval.extraction.harness import estimate_tokens

        tokens = estimate_tokens()
        if args.stand_in:
            from scripts.e2e.fake_model import start

            stand_in, base_url = start()
            env.update({"AI_BASE_URL": base_url, "OPENAI_API_KEY": "not-needed"})
            model = "the local stand-in model (scripts/e2e/fake_model.py): no spend"
        else:
            from backend.core.config import Settings

            settings = Settings()
            model = f"{settings.AI_PROVIDER}:{settings.AI_MODEL}"
            if not _confirm(tokens, model, args.yes):
                print("Not run.")
                return 1
        env["NOVEL_EVAL_CONFIRMED_TOKENS"] = str(tokens)
        env["NOVEL_EVAL_MODEL_LABEL"] = model

    out = args.out.resolve()
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for old in raw_dir.glob("*.json"):
        old.unlink()
    env["NOVEL_EVAL_OUT"] = str(raw_dir)
    marker = "eval" if args.model else "eval and not llm"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-m",
        marker,
        "tests/eval",
    ]
    try:
        exit_code = subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
    finally:
        if stand_in is not None:
            stand_in.shutdown()

    raw = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(raw_dir.glob("*.json"))
    }
    meta = {
        "when": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "commit": _git("rev-parse", "--short", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "python": platform.python_version(),
        "model": model,
        "pytest_exit": exit_code,
        "suites": sorted(raw),
    }
    json_path, md_path = write_report(out, raw, meta)
    print(f"Wrote {md_path.relative_to(ROOT) if md_path.is_relative_to(ROOT) else md_path}")
    print(f"Wrote {json_path.relative_to(ROOT) if json_path.is_relative_to(ROOT) else json_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
