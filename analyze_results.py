#!/usr/bin/env python3
"""FR-06 aggregation & analysis.

Reads results/scored_results.csv and produces:
  - results/ranked_variants.csv   — mean scores per variant, best to worst
  - results/ranked_by_task_type.csv — mean scores per (variant, task_type)
  - results/consistency_deltas.csv — temp-0 vs temp-0.7 mean delta and
    within-temperature run-to-run std dev, per variant
  - results/variant_scores_chart.png — bar chart of mean total_score per variant

Usage:
    python analyze_results.py --scored results/scored_results.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

SCORE_COLS = ["relevance", "accuracy", "tone", "completeness", "total_score"]


def load_scored(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run score_results.py first to generate it."
        )
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"{path} has no scored rows yet — nothing to analyze.")
    missing = set(SCORE_COLS + ["variant_id", "task_type", "temperature"]) - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing expected column(s): {sorted(missing)}")
    return df


def ranked_by_variant(df: pd.DataFrame) -> pd.DataFrame:
    agg = (
        df.groupby("variant_id")[SCORE_COLS]
        .mean()
        .rename(columns={c: f"mean_{c}" for c in SCORE_COLS})
    )
    agg["n_scored"] = df.groupby("variant_id").size()
    agg = agg.sort_values("mean_total_score", ascending=False).reset_index()
    agg.insert(0, "rank", range(1, len(agg) + 1))
    return agg


def ranked_by_variant_and_task_type(df: pd.DataFrame) -> pd.DataFrame:
    agg = (
        df.groupby(["task_type", "variant_id"])[SCORE_COLS]
        .mean()
        .rename(columns={c: f"mean_{c}" for c in SCORE_COLS})
    )
    agg["n_scored"] = df.groupby(["task_type", "variant_id"]).size()
    agg = agg.sort_values(
        ["task_type", "mean_total_score"], ascending=[True, False]
    ).reset_index()
    return agg


def consistency_deltas(df: pd.DataFrame) -> pd.DataFrame:
    """Per variant: mean total_score at temp 0.0 vs 0.7, and the average
    (across cases) run-to-run standard deviation of total_score at each
    temperature — i.e. how much repeated runs at the same temperature
    disagreed with each other. consistency_delta = std(0.7) - std(0.0);
    positive means temp 0.7 was less consistent, as expected.
    """
    rows = []
    for variant_id, vdf in df.groupby("variant_id"):
        row = {"variant_id": variant_id}
        for temp in (0.0, 0.7):
            tdf = vdf[vdf["temperature"] == temp]
            row[f"mean_total_score_t{temp}"] = (
                tdf["total_score"].mean() if not tdf.empty else float("nan")
            )
            # std across repeated runs, per case, then averaged across cases
            per_case_std = tdf.groupby("case_id")["total_score"].std()
            row[f"run_to_run_std_t{temp}"] = (
                per_case_std.mean() if not per_case_std.empty else float("nan")
            )
        row["mean_score_delta_t0.7_minus_t0"] = (
            row.get("mean_total_score_t0.7", float("nan"))
            - row.get("mean_total_score_t0.0", float("nan"))
        )
        row["consistency_delta_t0.7_minus_t0"] = (
            row.get("run_to_run_std_t0.7", float("nan"))
            - row.get("run_to_run_std_t0.0", float("nan"))
        )
        rows.append(row)
    out = pd.DataFrame(rows).sort_values("variant_id").reset_index(drop=True)
    return out


def write_chart(ranked: pd.DataFrame, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(max(6, len(ranked) * 0.9), 5))
    ax.bar(ranked["variant_id"], ranked["mean_total_score"])
    ax.set_xlabel("variant_id")
    ax.set_ylabel("mean total_score")
    ax.set_title("Mean total_score by prompt variant")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scored", default="results/scored_results.csv")
    p.add_argument("--output-dir", default="results")
    p.add_argument("--no-chart", action="store_true", help="Skip PNG chart generation.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output_dir)

    try:
        df = load_scored(Path(args.scored))
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    ranked = ranked_by_variant(df)
    ranked_task = ranked_by_variant_and_task_type(df)
    deltas = consistency_deltas(df)

    output_dir.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(output_dir / "ranked_variants.csv", index=False)
    ranked_task.to_csv(output_dir / "ranked_by_task_type.csv", index=False)
    deltas.to_csv(output_dir / "consistency_deltas.csv", index=False)

    print("=== Ranked variants (best to worst, by mean total_score) ===")
    print(ranked.to_string(index=False))
    print("\n=== By task type ===")
    print(ranked_task.to_string(index=False))
    print("\n=== Temperature 0 vs 0.7 consistency ===")
    print(deltas.to_string(index=False))

    if not args.no_chart:
        chart_path = output_dir / "variant_scores_chart.png"
        write_chart(ranked, chart_path)
        print(f"\nChart written to {chart_path}")

    print(f"\nCSV outputs written to {output_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
