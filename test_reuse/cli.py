"""Command line entry point for staged reuse evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path

from test_reuse.pipeline import (
    prepare_run,
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


def _add_llm_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", default=".env", help="Environment file for optional LLM client.")
    parser.add_argument("--allow-llm", action="store_true", help="Allow external LLM calls when credentials are configured.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Read frozen plans and write plan_needs.jsonl and targets.jsonl.")
    prepare.add_argument("--plan", type=Path, action="append", required=True, help="Frozen plan JSON path. Repeat for multiple lessons.")
    prepare.add_argument("--output-dir", type=Path, default=Path("report"), help="Base output directory for this run.")
    prepare.add_argument("--run-id", default="", help="Stable run id. Defaults to reuse_eval_YYYYMMDD_HHMMSS.")
    prepare.add_argument("--notes", default="", help="Freeform run note stored in manifest.")

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
    _add_library_dirs(run_all)
    run_all.add_argument("--output-dir", type=Path, default=Path("report"), help="Base output directory for this run.")
    run_all.add_argument("--run-id", default="", help="Stable run id. Defaults to reuse_eval_YYYYMMDD_HHMMSS.")
    _add_llm_flags(run_all)
    run_all.add_argument("--review", action="store_true", help="Enable LLM review in the final policy stage.")
    run_all.add_argument("--notes", default="", help="Freeform run note stored in manifest.")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "prepare":
        output_dir = prepare_run(
            plan_paths=args.plan,
            output_dir=args.output_dir,
            run_id=args.run_id,
            notes=args.notes,
        )
        print(f"Prepared reuse eval run: {output_dir}")
        return 0

    if args.command == "hard-filter":
        output_dir = run_hard_filter_stage(run_dir=args.run_dir, library_dirs=args.library_dir)
        print(f"Hard filter complete: {output_dir}")
        return 0

    if args.command == "retrieve":
        output_dir = run_retrieve_stage(
            run_dir=args.run_dir,
            library_dirs=args.library_dir,
            allow_llm=args.allow_llm,
            env_file=args.env_file,
        )
        print(f"Retrieval complete: {output_dir}")
        return 0

    if args.command == "review":
        output_dir = run_review_stage(
            run_dir=args.run_dir,
            review_enabled=args.review,
            allow_llm=args.allow_llm,
            env_file=args.env_file,
        )
        print(f"Review complete: {output_dir}")
        return 0

    if args.command == "summarize":
        output_dir = run_summarize_stage(run_dir=args.run_dir)
        print(f"Summary complete: {output_dir}")
        return 0

    if args.command == "run-all":
        output_dir = run_eval(
            plan_paths=args.plan,
            library_dirs=args.library_dir,
            output_dir=args.output_dir,
            run_id=args.run_id,
            review_enabled=args.review,
            allow_llm=args.allow_llm,
            env_file=args.env_file,
            notes=args.notes,
        )
        print(f"Reuse eval complete: {output_dir}")
        return 0

    raise ValueError(f"unknown command: {args.command}")
