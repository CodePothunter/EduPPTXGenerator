"""Command line entry point for staged reuse evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path

from test_reuse.pipeline import (
    prepare_run,
    read_json,
    run_eval,
    run_hard_filter_stage,
    run_retrieve_stage,
    run_review_stage,
    run_summarize_stage,
)


def _add_run_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", type=Path, required=True, help="Existing run directory created by prepare.")


def _add_library_dirs(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument(
        "--library-dir",
        type=Path,
        action="append",
        required=required,
        help="Reusable material library directory. Repeat to search multiple libraries.",
    )


def _add_goldset_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--goldset",
        type=Path,
        action="append",
        default=[],
        help="Goldset JSON path used only for evaluation labels. Repeat to merge multiple goldsets.",
    )


def _add_llm_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", default=".env", help="Environment file for optional LLM client.")
    parser.add_argument("--allow-llm", action="store_true", help="Allow external LLM calls when credentials are configured.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Read frozen plans and write plan_needs.jsonl and targets.jsonl.")
    prepare.add_argument("--plan", type=Path, action="append", required=True, help="Frozen plan JSON path. Repeat for multiple lessons.")
    _add_goldset_paths(prepare)
    prepare.add_argument("--output-dir", type=Path, default=Path("report"), help="Base output directory for this run.")
    prepare.add_argument("--run-id", default="", help="Stable run id. Defaults to reuse_eval_YYYYMMDD_HHMMSS.")
    prepare.add_argument("--notes", default="", help="Freeform run note stored in manifest.")
    _add_llm_flags(prepare)

    hard_filter = subparsers.add_parser("hard-filter", help="Run category, subject, and aspect hard filters.")
    _add_run_dir(hard_filter)
    _add_library_dirs(hard_filter)

    retrieve = subparsers.add_parser("retrieve", help="Run BM25, embedding, hybrid ranking, and threshold selection.")
    _add_run_dir(retrieve)
    _add_library_dirs(retrieve)
    _add_llm_flags(retrieve)

    review = subparsers.add_parser("review", help="Finalize threshold candidates and optionally run LLM review.")
    _add_run_dir(review)
    _add_library_dirs(review, required=False)
    _add_llm_flags(review)
    review.add_argument("--review", action="store_true", help="Enable LLM review in the final policy stage.")

    summarize = subparsers.add_parser("summarize", help="Compute metrics, failure cases, and report.md.")
    _add_run_dir(summarize)

    run_all = subparsers.add_parser("run-all", help="Run prepare, hard-filter, retrieve, review, and summarize.")
    run_all.add_argument("--plan", type=Path, action="append", required=True, help="Frozen plan JSON path. Repeat for multiple lessons.")
    _add_goldset_paths(run_all)
    _add_library_dirs(run_all)
    run_all.add_argument("--output-dir", type=Path, default=Path("report"), help="Base output directory for this run.")
    run_all.add_argument("--run-id", default="", help="Stable run id. Defaults to reuse_eval_YYYYMMDD_HHMMSS.")
    _add_llm_flags(run_all)
    run_all.add_argument("--review", action="store_true", help="Enable LLM review in the final policy stage.")
    run_all.add_argument("--notes", default="", help="Freeform run note stored in manifest.")

    return parser.parse_args()


def _nested_value(payload: dict[str, object], key: str) -> object | None:
    value: object | None = payload
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _print_summary_file(path: Path, *, keys: tuple[str, ...]) -> None:
    if not path.exists():
        return
    payload = read_json(path)
    if not isinstance(payload, dict):
        return
    for key in keys:
        value = _nested_value(payload, key)
        if isinstance(value, float):
            print(f"{key}: {value:.4f}")
        elif value is not None:
            print(f"{key}: {value}")


def _print_stage_summary(run_dir: Path, *, stage: str) -> None:
    if stage == "prepare":
        _print_summary_file(
            run_dir / "target_classification_summary.json",
            keys=("target_class_accuracy", "c00_f1"),
        )
    elif stage == "hard-filter":
        _print_summary_file(
            run_dir / "hard_filter_summary.json",
            keys=("stage.candidate_hit_rate", "stage.best_hit_rate", "stage.pair_metrics.precision"),
        )
    elif stage == "retrieve":
        _print_summary_file(
            run_dir / "threshold_summary.json",
            keys=("stage.candidate_hit_rate", "stage.best_hit_rate", "stage.top_8_acceptable_recall"),
        )
    elif stage == "review":
        _print_summary_file(
            run_dir / "llm_review_summary.json",
            keys=("reviewed_count", "llm_accept_correctness_rate", "llm_false_reject_rate"),
        )
    elif stage in {"summarize", "run-all"}:
        _print_summary_file(
            run_dir / "metrics.json",
            keys=("final.precision", "final.recall", "final.f1"),
        )
        _print_final_aliases(run_dir / "metrics.json")


def _print_final_aliases(metrics_path: Path) -> None:
    if not metrics_path.exists():
        return
    metrics = read_json(metrics_path)
    final = metrics.get("final", {}) if isinstance(metrics, dict) else {}
    if not isinstance(final, dict):
        final = {}
    print(f"Final precision: {float(final.get('precision') or 0.0):.4f}")
    print(f"Final recall: {float(final.get('recall') or 0.0):.4f}")
    print(f"Final f1: {float(final.get('f1') or 0.0):.4f}")


def main() -> int:
    args = parse_args()
    if args.command == "prepare":
        output_dir = prepare_run(
            plan_paths=args.plan,
            output_dir=args.output_dir,
            run_id=args.run_id,
            goldset_paths=args.goldset,
            allow_llm=args.allow_llm,
            env_file=args.env_file,
            notes=args.notes,
        )
        print(f"Prepared reuse eval run: {output_dir}")
        _print_stage_summary(output_dir, stage="prepare")
        return 0

    if args.command == "hard-filter":
        output_dir = run_hard_filter_stage(run_dir=args.run_dir, library_dirs=args.library_dir)
        print(f"Hard filter complete: {output_dir}")
        _print_stage_summary(output_dir, stage="hard-filter")
        return 0

    if args.command == "retrieve":
        output_dir = run_retrieve_stage(
            run_dir=args.run_dir,
            library_dirs=args.library_dir,
            allow_llm=args.allow_llm,
            env_file=args.env_file,
        )
        print(f"Retrieval complete: {output_dir}")
        _print_stage_summary(output_dir, stage="retrieve")
        return 0

    if args.command == "review":
        output_dir = run_review_stage(
            run_dir=args.run_dir,
            review_enabled=args.review,
            allow_llm=args.allow_llm,
            env_file=args.env_file,
        )
        print(f"Review complete: {output_dir}")
        _print_stage_summary(output_dir, stage="review")
        return 0

    if args.command == "summarize":
        output_dir = run_summarize_stage(run_dir=args.run_dir)
        print(f"Summary complete: {output_dir}")
        _print_stage_summary(output_dir, stage="summarize")
        return 0

    if args.command == "run-all":
        output_dir = run_eval(
            plan_paths=args.plan,
            library_dirs=args.library_dir,
            output_dir=args.output_dir,
            run_id=args.run_id,
            goldset_paths=args.goldset,
            review_enabled=args.review,
            allow_llm=args.allow_llm,
            env_file=args.env_file,
            notes=args.notes,
        )
        print(f"Reuse eval complete: {output_dir}")
        _print_stage_summary(output_dir, stage="run-all")
        return 0

    raise ValueError(f"unknown command: {args.command}")
