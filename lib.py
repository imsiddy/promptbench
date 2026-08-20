"""Shared loaders/validators for run_evaluation.py, score_results.py, and
analyze_results.py, factored out so the three CLI scripts don't duplicate
parsing/validation logic and so that logic can be unit tested directly.
"""
from __future__ import annotations

import csv
import dataclasses
from pathlib import Path
from typing import Union

import yaml

PathLike = Union[str, Path]


def load_dotenv_file(path: PathLike = ".env") -> None:
    """Load KEY=VALUE lines from a .env file into os.environ without
    overriding variables that are already set. Silently does nothing if the
    file does not exist."""
    import os

    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)


class ConfigError(ValueError):
    """Raised when prompts.yaml / test_cases.csv / pricing.yaml fail validation."""


@dataclasses.dataclass
class Variant:
    variant_id: str
    task_type: str
    template: str
    temperature: float
    notes: str


@dataclasses.dataclass
class TestCase:
    case_id: int
    input: str
    task_type: str


def load_variants(path: PathLike) -> list[Variant]:
    """Load and validate prompts.yaml. Raises ConfigError with a specific,
    actionable message on any problem — these files are user-edited, so
    failures here should point at exactly what to fix, not stack-trace.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Variants file not found: {path}")
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    raw_variants = data.get("variants")
    if not raw_variants:
        raise ConfigError(
            f"{path} has no 'variants' list. Fill in prompts.yaml with real "
            "prompt variants before running evaluations (see the template "
            "comments in that file)."
        )

    seen_ids: set[str] = set()
    variants: list[Variant] = []
    for i, raw in enumerate(raw_variants):
        if not isinstance(raw, dict):
            raise ConfigError(f"Variant #{i} in {path} is not a mapping: {raw!r}")
        missing = [k for k in ("variant_id", "task_type", "template") if not raw.get(k)]
        if missing:
            raise ConfigError(
                f"Variant #{i} in {path} is missing required field(s): {missing}"
            )
        variant_id = str(raw["variant_id"])
        if variant_id in seen_ids:
            raise ConfigError(f"Duplicate variant_id '{variant_id}' in {path}")
        if variant_id == "EXAMPLE_DELETE_ME":
            raise ConfigError(
                f"{path} still contains the placeholder example variant "
                "'EXAMPLE_DELETE_ME'. Replace it with real variants before running."
            )
        seen_ids.add(variant_id)

        template = raw["template"]
        if "{input}" not in template:
            raise ConfigError(
                f"Variant '{variant_id}' template does not contain the "
                "literal '{input}' placeholder."
            )
        try:
            temperature = float(raw.get("temperature", 0.0))
        except (TypeError, ValueError):
            raise ConfigError(
                f"Variant '{variant_id}' has a non-numeric temperature: "
                f"{raw.get('temperature')!r}"
            )
        variants.append(
            Variant(
                variant_id=variant_id,
                task_type=str(raw["task_type"]),
                template=template,
                temperature=temperature,
                notes=str(raw.get("notes", "")),
            )
        )
    return variants


def load_test_cases(path: PathLike) -> list[TestCase]:
    """Load and validate test_cases.csv."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Test cases file not found: {path}")

    cases: list[TestCase] = []
    seen_ids: set[int] = set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required_cols = {"case_id", "input", "task_type"}
        fieldnames = set(reader.fieldnames or [])
        if not required_cols.issubset(fieldnames):
            raise ConfigError(
                f"{path} must have columns {sorted(required_cols)}; found "
                f"{sorted(fieldnames) or 'no header row'}"
            )
        for row_num, row in enumerate(reader, start=2):  # header is line 1
            raw_id = (row.get("case_id") or "").strip()
            raw_input = (row.get("input") or "").strip()
            raw_task = (row.get("task_type") or "").strip()
            if not raw_id or not raw_input or not raw_task:
                raise ConfigError(
                    f"{path} line {row_num}: case_id/input/task_type cannot be blank."
                )
            try:
                case_id = int(raw_id)
            except ValueError:
                raise ConfigError(
                    f"{path} line {row_num}: case_id '{raw_id}' is not an integer."
                )
            if case_id in seen_ids:
                raise ConfigError(f"{path} line {row_num}: duplicate case_id {case_id}.")
            seen_ids.add(case_id)
            cases.append(TestCase(case_id=case_id, input=raw_input, task_type=raw_task))

    if not cases:
        raise ConfigError(
            f"{path} has no test cases. Fill in test_cases.csv with real "
            "content before running evaluations."
        )
    return cases


def load_pricing(path: PathLike) -> dict:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Pricing config not found: {path}")
    with path.open() as f:
        return yaml.safe_load(f) or {}


def price_for_model(pricing: dict, model: str) -> dict:
    """Returns {'input_per_mtok': float, 'output_per_mtok': float} for a
    model, falling back to pricing['default'] so an unrecognized model id
    never silently reports $0.00.
    """
    models = pricing.get("models", {}) or {}
    return models.get(
        model, pricing.get("default", {"input_per_mtok": 0.0, "output_per_mtok": 0.0})
    )


def estimate_cost_usd(input_tokens: int, output_tokens: int, price: dict) -> float:
    return (
        input_tokens / 1_000_000 * price.get("input_per_mtok", 0.0)
        + output_tokens / 1_000_000 * price.get("output_per_mtok", 0.0)
    )
