#!/usr/bin/env python3
"""FR-05 manual scoring CLI.

Walks through every raw output not yet scored, shows the rubric and the
output, and prompts for 4 scores via input(). The tool never fills in or guesses a
score itself.

Usage:
    python score_results.py --raw results/raw_outputs.json \\
        --rubric rubric.md --output results/scored_results.csv

Ctrl+C at any prompt saves progress and exits cleanly — rerun the same
command to resume where you left off.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from lib import ConfigError

CRITERIA = ("relevance", "accuracy", "tone", "completeness")
FIELDNAMES = [
    "run_id",
    "variant_id",
    "case_id",
    "task_type",
    "temperature",
    "run_index",
    "relevance",
    "accuracy",
    "tone",
    "completeness",
    "total_score",
    "scorer_notes",
    "scored_at",
]


class ScoringAborted(Exception):
    """Raised on Ctrl+C / EOF so main() can save-and-exit cleanly."""


def load_raw_outputs(path: Path) -> list[dict]:
    if not path.exists():
        raise ConfigError(
            f"{path} not found — run run_evaluation.py first to generate it."
        )
    import json

    with path.open() as f:
        return json.load(f)


def load_already_scored(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        return {row["run_id"]: row for row in reader}


def append_scored_row(path: Path, row: dict, write_header: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def prompt_int_1_5(criterion: str, input_func=input) -> int:
    while True:
        raw = input_func(f"  {criterion} (1-5): ").strip()
        if raw.lower() in ("q", "quit"):
            raise ScoringAborted()
        try:
            value = int(raw)
        except ValueError:
            print("    -> please enter a whole number from 1 to 5 (or 'q' to quit).")
            continue
        if not 1 <= value <= 5:
            print("    -> must be between 1 and 5.")
            continue
        return value


def score_one(entry: dict, input_func=input) -> dict:
    print("\n" + "=" * 72)
    print(
        f"run_id={entry['run_id']}  variant={entry['variant_id']}  "
        f"case={entry['case_id']}  task_type={entry.get('task_type')}  "
        f"temperature={entry.get('temperature')}  run_index={entry.get('run_index')}"
    )
    print("-" * 72)
    if entry.get("status") != "success":
        print(f"[STATUS: {entry.get('status')} — error: {entry.get('error')}]")
        print("(No model output to score for this run.)")
    else:
        print("OUTPUT:")
        print(entry.get("output") or "(empty output)")
    print("=" * 72)

    scores = {c: prompt_int_1_5(c, input_func) for c in CRITERIA}
    notes = input_func("  scorer_notes (optional, press enter to skip): ").strip()

    total_score = sum(scores.values())
    return {
        "run_id": entry["run_id"],
        "variant_id": entry["variant_id"],
        "case_id": entry["case_id"],
        "task_type": entry.get("task_type", ""),
        "temperature": entry.get("temperature", ""),
        "run_index": entry.get("run_index", ""),
        **scores,
        "total_score": total_score,
        "scorer_notes": notes,
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw", default="results/raw_outputs.json")
    p.add_argument("--rubric", default="rubric.md")
    p.add_argument("--output", default="results/scored_results.csv")
    p.add_argument(
        "--scores-file",
        help="Import scores from a CSV (columns: run_id, relevance, accuracy, "
        "tone, completeness, scorer_notes) instead of prompting interactively. "
        "Every value is validated against the 1-5 scale.",
    )
    p.add_argument(
        "--include-errors",
        action="store_true",
        help="Also walk through error/failed runs (rare — usually nothing "
        "meaningful to score there).",
    )
    return p.parse_args(argv)


def import_scores(scores_path: Path, raw_outputs: list[dict], output_path: Path) -> int:
    """Validate and append scores from a CSV. Returns the number of rows
    imported. Raises ConfigError on unknown run_ids or out-of-range values."""
    by_id = {r["run_id"]: r for r in raw_outputs}
    already = load_already_scored(output_path)
    imported = 0
    write_header = not output_path.exists() or output_path.stat().st_size == 0
    with scores_path.open(newline="", encoding="utf-8") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            run_id = (row.get("run_id") or "").strip()
            if run_id not in by_id:
                raise ConfigError(f"{scores_path} line {line_no}: unknown run_id {run_id!r}")
            if run_id in already:
                continue
            scores = {}
            for c in CRITERIA:
                try:
                    value = int(row.get(c, ""))
                except (TypeError, ValueError):
                    raise ConfigError(f"{scores_path} line {line_no}: {c} is not an integer")
                if not 1 <= value <= 5:
                    raise ConfigError(f"{scores_path} line {line_no}: {c}={value} outside 1-5")
                scores[c] = value
            entry = by_id[run_id]
            append_scored_row(
                output_path,
                {
                    "run_id": run_id,
                    "variant_id": entry["variant_id"],
                    "case_id": entry["case_id"],
                    "task_type": entry.get("task_type", ""),
                    "temperature": entry.get("temperature", ""),
                    "run_index": entry.get("run_index", ""),
                    **scores,
                    "total_score": sum(scores.values()),
                    "scorer_notes": (row.get("scorer_notes") or "").strip(),
                    "scored_at": datetime.now(timezone.utc).isoformat(),
                },
                write_header,
            )
            write_header = False
            imported += 1
    return imported


def main(argv=None, input_func=input) -> int:
    args = parse_args(argv)
    raw_path = Path(args.raw)
    rubric_path = Path(args.rubric)
    output_path = Path(args.output)

    try:
        raw_outputs = load_raw_outputs(raw_path)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if rubric_path.exists():
        print(rubric_path.read_text())
    else:
        print(
            f"Warning: rubric file {rubric_path} not found — scoring without "
            "a rubric displayed is not recommended.",
            file=sys.stderr,
        )

    if args.scores_file:
        try:
            n = import_scores(Path(args.scores_file), raw_outputs, output_path)
        except ConfigError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(f"Imported {n} scores into {output_path}.")
        return 0

    already_scored = load_already_scored(output_path)
    to_score = [
        r
        for r in raw_outputs
        if r["run_id"] not in already_scored
        and (args.include_errors or r.get("status") == "success")
    ]

    print(f"\n{len(already_scored)} already scored. {len(to_score)} remaining to score.\n")
    if not to_score:
        print("Nothing to score.")
        return 0

    write_header = not output_path.exists() or output_path.stat().st_size == 0
    scored_count = 0
    try:
        for i, entry in enumerate(to_score, start=1):
            print(f"\n[{i}/{len(to_score)}]", end="")
            row = score_one(entry, input_func)
            append_scored_row(output_path, row, write_header)
            write_header = False
            scored_count += 1
    except (ScoringAborted, KeyboardInterrupt, EOFError):
        print(f"\n\nStopped. Scored {scored_count} this session, saved to {output_path}.")
        print("Rerun the same command to resume where you left off.")
        return 0

    print(f"\nDone. Scored {scored_count} this session. All results in {output_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
