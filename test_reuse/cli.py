"""Command line entry point for staged reuse evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path

from test_reuse.pipeline import (
    CATEGORY_ROUTING_BASELINE,
    CATEGORY_ROUTING_MODES,
    prepare_run,
    read_json,
    run_eval,
    run_analyze_stage,
    run_hard_filter_stage,
    run_retrieve_stage,
    run_review_stage,
    run_summarize_stage,
    stage_artifact_read_path,
)

SummaryKey = str | tuple[str, str]


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


def _add_category_routing(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--category-routing",
        choices=CATEGORY_ROUTING_MODES,
        default=CATEGORY_ROUTING_BASELINE,
        help="Hard-filter category routing mode.",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Read frozen plans and write 01_prepare artifacts.")
    prepare.add_argument("--plan", type=Path, action="append", required=True, help="Frozen plan JSON path. Repeat for multiple lessons.")
    _add_goldset_paths(prepare)
    prepare.add_argument("--output-dir", type=Path, default=Path("report"), help="Base output directory for this run.")
    prepare.add_argument("--run-id", default="", help="Stable run id. Defaults to reuse_eval_YYYYMMDD_HHMMSS.")
    prepare.add_argument("--notes", default="", help="Freeform run note stored in manifest.")
    _add_llm_flags(prepare)

    hard_filter = subparsers.add_parser("hard-filter", help="Run category, subject, and aspect hard filters.")
    _add_run_dir(hard_filter)
    _add_library_dirs(hard_filter)
    _add_category_routing(hard_filter)

    retrieve = subparsers.add_parser("retrieve", help="Run BM25, embedding, hybrid ranking, and candidate score audit.")
    _add_run_dir(retrieve)
    _add_library_dirs(retrieve)
    _add_llm_flags(retrieve)

    review = subparsers.add_parser("review", help="Finalize policy candidates and optionally run LLM review.")
    _add_run_dir(review)
    _add_library_dirs(review, required=False)
    _add_llm_flags(review)
    review.add_argument("--review", action="store_true", help="Enable LLM review in the final policy stage.")

    summarize = subparsers.add_parser("summarize", help="Compute 05_summarize metrics, failure cases, and report.md.")
    _add_run_dir(summarize)

    analyze = subparsers.add_parser("analyze", help="Write floor sweep and optional LLM counterfactual artifacts.")
    _add_run_dir(analyze)
    _add_llm_flags(analyze)

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


def _print_summary_file(path: Path, *, keys: tuple[SummaryKey, ...]) -> None:
    if not path.exists():
        return
    payload = read_json(path)
    if not isinstance(payload, dict):
        return
    for item in keys:
        if isinstance(item, tuple):
            label, key = item
        else:
            label = key = item
        value = _nested_value(payload, key)
        if isinstance(value, float):
            print(f"{label}: {value:.4f}")
        elif value is not None:
            print(f"{label}: {value}")


def _print_stage_summary(run_dir: Path, *, stage: str) -> None:
    if stage == "prepare":
        _print_summary_file(
            stage_artifact_read_path(run_dir, "prepare", "target_classification_summary.json"),
            keys=(
                "total_targets",
                "target_class_accuracy",
                "c00_f1",
            ),
        )
    elif stage == "hard-filter":
        _print_summary_file(
            stage_artifact_read_path(run_dir, "hard_filter", "hard_filter_summary.json"),
            keys=(
                ("raw_gold.stage.candidate_hit_rate", "stage.candidate_hit_rate"),
                ("raw_gold.stage.best_hit_rate", "stage.best_hit_rate"),
                ("raw_gold.stage.pair_metrics.precision", "stage.pair_metrics.precision"),
                ("raw_gold.filter_ablation.size_only.candidate_hit_rate", "filter_ablation.size_only.candidate_hit_rate"),
                ("raw_gold.filter_ablation.subject_only.candidate_hit_rate", "filter_ablation.subject_only.candidate_hit_rate"),
                ("raw_gold.filter_ablation.category_only.candidate_hit_rate", "filter_ablation.category_only.candidate_hit_rate"),
                ("raw_gold.filter_ablation.subject_size.candidate_hit_rate", "filter_ablation.subject_size.candidate_hit_rate"),
                ("size_gold.stage.candidate_hit_rate", "size_compatible_gold.stage.candidate_hit_rate"),
                ("size_gold.stage.best_hit_rate", "size_compatible_gold.stage.best_hit_rate"),
                ("size_gold.stage.pair_metrics.precision", "size_compatible_gold.stage.pair_metrics.precision"),
                ("size_gold.filter_ablation.size_only.candidate_hit_rate", "size_compatible_gold.filter_ablation.size_only.candidate_hit_rate"),
                ("size_gold.filter_ablation.subject_only.candidate_hit_rate", "size_compatible_gold.filter_ablation.subject_only.candidate_hit_rate"),
                ("size_gold.filter_ablation.category_only.candidate_hit_rate", "size_compatible_gold.filter_ablation.category_only.candidate_hit_rate"),
                ("size_gold.filter_ablation.subject_size.candidate_hit_rate", "size_compatible_gold.filter_ablation.subject_size.candidate_hit_rate"),
                (
                    "size_gold.gold_adjustment.removed_acceptable_pair_count",
                    "size_compatible_gold.gold_adjustment.removed_acceptable_pair_count",
                ),
            ),
        )
    elif stage == "retrieve":
        _print_summary_file(
            stage_artifact_read_path(run_dir, "retrieve", "retrieve_summary.json"),
            keys=(
                ("raw_gold.ranking.candidate_hit_rate", "ranking.candidate_hit_rate"),
                ("raw_gold.ranking.top_8_recall", "ranking.top_8_recall"),
                ("size_gold.ranking.candidate_hit_rate", "size_compatible_gold.ranking.candidate_hit_rate"),
                ("size_gold.ranking.top_8_recall", "size_compatible_gold.ranking.top_8_recall"),
                "candidate_score_audit.candidate_pair_count",
                "candidate_score_audit.policy_input_pair_count",
            ),
        )
    elif stage == "review":
        _print_summary_file(
            stage_artifact_read_path(run_dir, "review", "llm_review_summary.json"),
            keys=(
                "policy_candidate_count",
                "review_candidate_count",
                "llm_review_required_count",
                "llm_review_performed_count",
                "reviewed_count",
                "llm_review_required_rate",
                "llm_review_performed_rate",
                "llm_accept_correctness_rate",
                "llm_false_reject_rate",
            ),
        )
    elif stage in {"summarize", "run-all"}:
        _print_summary_file(
            stage_artifact_read_path(run_dir, "summarize", "metrics.json"),
            keys=(
                ("size_gold.final.precision", "final.precision"),
                ("size_gold.final.recall", "final.recall"),
                ("size_gold.final.f1", "final.f1"),
                ("raw_gold.final.precision", "final_raw_gold_audit.precision"),
                ("raw_gold.final.recall", "final_raw_gold_audit.recall"),
                ("raw_gold.final.f1", "final_raw_gold_audit.f1"),
                "waterfall.total_count",
                "waterfall.counts.retrieval_no_candidate",
                "waterfall.counts.policy_reject",
                "waterfall.counts.llm_reject",
                "waterfall.counts.final_selected_wrong",
            ),
        )
        _print_final_aliases(stage_artifact_read_path(run_dir, "summarize", "metrics.json"))
    elif stage == "analyze":
        _print_summary_file(
            stage_artifact_read_path(run_dir, "summarize", "llm_counterfactual_summary.json"),
            keys=(
                "candidate_count",
                "reviewed_count",
                "accepted_count",
                "accept_rate",
            ),
        )


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
        output_dir = run_hard_filter_stage(
            run_dir=args.run_dir,
            library_dirs=args.library_dir,
            category_routing=args.category_routing,
        )
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

    if args.command == "analyze":
        output_dir = run_analyze_stage(
            run_dir=args.run_dir,
            allow_llm=args.allow_llm,
            env_file=args.env_file,
        )
        print(f"Analysis complete: {output_dir}")
        _print_stage_summary(output_dir, stage="analyze")
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
