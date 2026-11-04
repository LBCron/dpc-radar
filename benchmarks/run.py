"""Run methods on the benchmark and print how they score.

    uv run --group bench python -m benchmarks.run                  # every method
    uv run --group bench python -m benchmarks.run limen-max-step   # one method
    uv run --group bench python -m benchmarks.run --markdown       # the table of the README
    uv run --group bench python -m benchmarks.run --check          # CI: as in results.json
    uv run --group bench python -m benchmarks.run --record         # save today's outcomes

``--check`` fails when an outcome changes, so a new frame, a changed case or a
change in a method is always a visible decision, never an accident.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from benchmarks.bench import HERE, Result, load_cases, run
from benchmarks.methods import METHODS

RESULTS = HERE / "results.json"
MARK = {"pass": "✅", "fail": "❌", "none": "—"}


def key(r: Result) -> str:
    return f"{r.case.id} · {r.check.label()}"


def summary(results: list[Result]) -> str:
    parts = []
    for status in ("confirmed", "suspect"):
        rs = [r for r in results if r.case.status == status]
        if rs:
            n = Counter(r.outcome for r in rs)
            parts.append(f"{status}: {n['pass']}/{len(rs)}")
    return " · ".join(parts)


def text(name: str, results: list[Result]) -> str:
    lines = [f"\n{name}: {METHODS[name].description}"]
    case = None
    for r in results:
        if r.case is not case:
            case = r.case
            lines.append(f"  {case.id} [{case.status}]")
        lines.append(f"    {MARK[r.outcome]} {r.check.label():<62} got {r.got}")
    lines.append(f"  score · {summary(results)}")
    return "\n".join(lines)


def markdown(all_results: dict[str, list[Result]]) -> str:
    names = list(all_results)
    first = all_results[names[0]]
    head = "| Case | Check | " + " | ".join(f"`{n}`" for n in names) + " |"
    rule = "|---|---|" + "---|" * len(names)
    rows = [head, rule]
    for i, r in enumerate(first):
        status = " *(suspect)*" if r.case.status == "suspect" else ""
        cells = [f"{MARK[all_results[n][i].outcome]} {all_results[n][i].got}" for n in names]
        rows.append(f"| `{r.case.id}`{status} | {r.check.label()} | " + " | ".join(cells) + " |")
    rows.append(
        "| **Score** | confirmed · suspect | "
        + " | ".join(summary(all_results[n]) for n in names)
        + " |"
    )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.run")
    parser.add_argument("methods", nargs="*", help=f"one or more of: {', '.join(METHODS)}")
    parser.add_argument("--markdown", action="store_true", help="print a Markdown table")
    parser.add_argument("--check", action="store_true", help="fail if outcomes differ")
    parser.add_argument("--record", action="store_true", help="save outcomes to results.json")
    args = parser.parse_args(argv)

    names = args.methods or list(METHODS)
    unknown = [n for n in names if n not in METHODS]
    if unknown:
        parser.error(f"unknown method(s): {', '.join(unknown)}")
    cases = load_cases()
    all_results = {n: run(METHODS[n], cases) for n in names}

    if args.markdown:
        print(markdown(all_results))
    else:
        for n, results in all_results.items():
            print(text(n, results))

    outcomes = {n: {key(r): r.outcome for r in rs} for n, rs in all_results.items()}
    if args.record:
        recorded = _load(RESULTS)
        recorded.update(outcomes)
        RESULTS.write_text(json.dumps(recorded, indent=2, ensure_ascii=False) + "\n")
    if args.check:
        recorded = _load(RESULTS)
        diffs = [
            f"{n} · {k}: recorded {recorded.get(n, {}).get(k)}, now {v}"
            for n, got in outcomes.items()
            for k, v in got.items()
            if recorded.get(n, {}).get(k) != v
        ]
        diffs += [
            f"{n} · {k}: recorded, no longer run"
            for n in outcomes
            for k in recorded.get(n, {})
            if k not in outcomes[n]
        ]
        if diffs:
            print("\nOutcomes differ from results.json (run with --record if intended):")
            print("\n".join(f"  {d}" for d in diffs))
            return 1
    return 0


def _load(path: Path) -> dict[str, dict[str, str]]:
    return json.loads(path.read_text()) if path.exists() else {}


if __name__ == "__main__":
    sys.exit(main())
