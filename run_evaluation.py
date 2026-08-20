#!/usr/bin/env python3
"""FR-04 evaluation runner.

For every (variant x matching test case) pair, calls the Anthropic API at
BOTH temperature 0.0 and 0.7, `--runs-per-case` times each, and appends every
raw output — including errors/refusals — to results/raw_outputs.json.
Never discards a failed call: it's recorded with status="error" instead.

Usage:
    python run_evaluation.py --variants prompts.yaml --cases test_cases.csv \\
        --runs-per-case 1

    # See the plan (call counts) without spending any budget or needing
    # ANTHROPIC_API_KEY set:
    python run_evaluation.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lib import (
    ConfigError,
    TestCase,
    Variant,
    estimate_cost_usd,
    load_dotenv_file,
    load_pricing,
    load_test_cases,
    load_variants,
    price_for_model,
)

TEMPERATURES = (0.0, 0.7)


@dataclass
class PlannedCall:
    run_id: str
    variant: Variant
    case: TestCase
    temperature: float
    run_index: int


@dataclass
class RunResult:
    run_id: str
    variant_id: str
    case_id: int
    task_type: str
    temperature: float
    run_index: int
    model: str
    prompt: str
    output: Optional[str]
    status: str  # "success" | "error"
    error: Optional[str]
    stop_reason: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    latency_ms: Optional[int]
    timestamp: str


def build_plan(
    variants: list[Variant], cases: list[TestCase], runs_per_case: int
) -> list[PlannedCall]:
    plan: list[PlannedCall] = []
    for variant in variants:
        matching_cases = [c for c in cases if c.task_type == variant.task_type]
        for case in matching_cases:
            for temperature in TEMPERATURES:
                for run_index in range(runs_per_case):
                    run_id = (
                        f"{variant.variant_id}__case{case.case_id}"
                        f"__t{temperature}__r{run_index}"
                    )
                    plan.append(
                        PlannedCall(
                            run_id=run_id,
                            variant=variant,
                            case=case,
                            temperature=temperature,
                            run_index=run_index,
                        )
                    )
    return plan


def load_existing_results(output_path: Path) -> dict[str, dict]:
    if not output_path.exists():
        return {}
    with output_path.open() as f:
        data = json.load(f)
    return {row["run_id"]: row for row in data}


def save_results(output_path: Path, results_by_id: dict[str, dict]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = list(results_by_id.values())
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp_path.open("w") as f:
        json.dump(ordered, f, indent=2)
    tmp_path.replace(output_path)


def call_with_retry(
    client,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    max_retries: int,
):
    """Returns (response_or_None, latency_ms_or_None, error_str_or_None).

    Retries with exponential backoff + jitter on rate limits, transient
    5xx errors, and connection errors. Non-retryable client errors (bad
    request, auth, etc.) fail fast without burning retries.
    """
    import anthropic  # imported lazily so `--dry-run` never needs the SDK importable

    delay = 1.0
    last_err: Optional[BaseException] = None
    for attempt in range(max_retries + 1):
        start = time.monotonic()
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                # passed via extra_body: the SDK's create() has no temperature
                # argument, but models that support it accept it in the body
                extra_body={"temperature": temperature},
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            return resp, latency_ms, None
        except anthropic.RateLimitError as e:
            last_err = e
        except anthropic.APIConnectionError as e:
            last_err = e
        except anthropic.APIStatusError as e:
            status_code = getattr(e, "status_code", None)
            if status_code is not None and status_code >= 500:
                last_err = e
            else:
                return None, None, f"{type(e).__name__}: {e}"
        if attempt < max_retries:
            sleep_for = delay + random.uniform(0, 0.5)
            time.sleep(sleep_for)
            delay *= 2
    return None, None, f"Exhausted {max_retries} retries: {last_err}"


def run_one(
    client, model: str, max_tokens: int, max_retries: int, call: PlannedCall
) -> RunResult:
    prompt = call.variant.template.format(input=call.case.input)
    resp, latency_ms, err = call_with_retry(
        client, model, prompt, call.temperature, max_tokens, max_retries
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    if err is not None:
        return RunResult(
            run_id=call.run_id,
            variant_id=call.variant.variant_id,
            case_id=call.case.case_id,
            task_type=call.variant.task_type,
            temperature=call.temperature,
            run_index=call.run_index,
            model=model,
            prompt=prompt,
            output=None,
            status="error",
            error=err,
            stop_reason=None,
            input_tokens=None,
            output_tokens=None,
            latency_ms=None,
            timestamp=timestamp,
        )
    output_text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    return RunResult(
        run_id=call.run_id,
        variant_id=call.variant.variant_id,
        case_id=call.case.case_id,
        task_type=call.variant.task_type,
        temperature=call.temperature,
        run_index=call.run_index,
        model=model,
        prompt=prompt,
        output=output_text,
        status="success",
        error=None,
        stop_reason=resp.stop_reason,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        latency_ms=latency_ms,
        timestamp=timestamp,
    )


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--variants", default="prompts.yaml")
    p.add_argument("--cases", default="test_cases.csv")
    p.add_argument("--runs-per-case", type=int, default=1)
    p.add_argument("--max-calls", type=int, default=160)
    p.add_argument("--model", default="claude-haiku-4-5-20251001")
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--max-retries", type=int, default=5)
    p.add_argument("--output", default="results/raw_outputs.json")
    p.add_argument("--pricing", default="config/pricing.yaml")
    p.add_argument(
        "--retry-errors",
        action="store_true",
        help="Re-attempt run_ids previously recorded with status=error.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the call plan and exit without touching the API or "
        "requiring ANTHROPIC_API_KEY.",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        variants = load_variants(args.variants)
        cases = load_test_cases(args.cases)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    plan = build_plan(variants, cases, args.runs_per_case)
    if not plan:
        print(
            "No (variant x case) pairs to run — check that task_type values "
            "in prompts.yaml match task_type values in test_cases.csv.",
            file=sys.stderr,
        )
        return 1

    output_path = Path(args.output)
    existing = load_existing_results(output_path)

    to_run = []
    for call in plan:
        prior = existing.get(call.run_id)
        if prior is None:
            to_run.append(call)
        elif prior.get("status") == "error" and args.retry_errors:
            to_run.append(call)
        # else: already have a result for this run_id, skip (resume behavior)

    # `to_run` contains run_ids that are new, plus any error retries (which
    # replace their existing entry rather than adding one). So cumulative
    # calls after this invocation = everything already recorded + new ones.
    new_ids = {c.run_id for c in to_run if c.run_id not in existing}
    projected_total = len(existing) + len(new_ids)

    print(f"Plan: {len(plan)} total (variant x case x temp x run) combinations")
    print(f"Already recorded: {len(existing)}")
    print(f"To run this invocation: {len(to_run)}")
    print(f"Projected cumulative calls after this run: {projected_total}")

    if projected_total > args.max_calls:
        print(
            f"\nABORTING: projected cumulative calls ({projected_total}) would "
            f"exceed --max-calls ({args.max_calls}).\n"
            "Reduce variants/cases/--runs-per-case, or pass a higher "
            "--max-calls if you deliberately want a bigger budget.",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print("\n--dry-run: not calling the API. Plan looks valid.")
        return 0

    if not to_run:
        print("\nNothing new to run (all planned calls already recorded).")
        return 0

    import anthropic  # noqa: F401  (import here so --dry-run doesn't require the package)

    load_dotenv_file(Path(__file__).parent / ".env")
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add "
            "your key, or export it in your shell.",
            file=sys.stderr,
        )
        return 1
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    pricing = load_pricing(args.pricing)
    price = price_for_model(pricing, args.model)

    results_by_id = dict(existing)
    total_cost = sum(
        estimate_cost_usd(r.get("input_tokens") or 0, r.get("output_tokens") or 0, price)
        for r in existing.values()
    )

    for i, call in enumerate(to_run, start=1):
        result = run_one(client, args.model, args.max_tokens, args.max_retries, call)
        results_by_id[result.run_id] = asdict(result)
        save_results(output_path, results_by_id)  # flush after every call

        if result.status == "success":
            cost = estimate_cost_usd(result.input_tokens, result.output_tokens, price)
            total_cost += cost
            print(
                f"[{i}/{len(to_run)}] {result.run_id}: OK "
                f"({result.input_tokens}in/{result.output_tokens}out tok, "
                f"{result.latency_ms}ms, ~${cost:.4f}, cumulative ~${total_cost:.4f})"
            )
        else:
            print(f"[{i}/{len(to_run)}] {result.run_id}: ERROR — {result.error}")

    print(f"\nDone. {len(results_by_id)} total results in {output_path}")
    print(f"Estimated cumulative cost: ~${total_cost:.4f} (approximate — see config/pricing.yaml)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
