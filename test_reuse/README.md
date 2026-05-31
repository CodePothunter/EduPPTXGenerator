# test_reuse

Independent staged evaluation flow for AI-image material reuse.

This folder does not select lessons or generate a test set. It consumes frozen
plan JSON files and existing material libraries, then writes reusable evaluation
artifacts for each stage.

## Step-by-step Run

```powershell
python -m test_reuse prepare `
  --plan path\to\lesson_plan.json `
  --output-dir report `
  --run-id reuse_eval_manual

python -m test_reuse hard-filter `
  --run-dir report\reuse_eval_manual `
  --library-dir materials_library_ppt

python -m test_reuse retrieve `
  --run-dir report\reuse_eval_manual `
  --library-dir materials_library_ppt

python -m test_reuse review `
  --run-dir report\reuse_eval_manual

python -m test_reuse summarize `
  --run-dir report\reuse_eval_manual
```

Add `--review --allow-llm` only when you want the final policy stage to make
LLM review calls and the environment has LLM credentials configured:

```powershell
python -m test_reuse review `
  --run-dir report\reuse_eval_manual `
  --review `
  --allow-llm
```

To run every stage in one command:

```powershell
python -m test_reuse run-all `
  --plan path\to\lesson_plan.json `
  --library-dir materials_library_ppt `
  --output-dir report `
  --run-id reuse_eval_manual
```

## Outputs

Each run writes `report/<run_id>/`:

- `manifest.json`
- `plan_needs.jsonl`
- `targets.jsonl`
- `hard_filter_pairs.jsonl`
- `hard_filter_summary.json`
- `candidate_collections.jsonl`
- `scored_candidates.jsonl`
- `threshold_candidates.jsonl`
- `threshold_summary.json`
- `llm_reviews.jsonl`
- `final_matches.jsonl`
- `failure_cases.jsonl`
- `prompt_issue_log.jsonl`
- `metrics.json`
- `report.md`

`test_set.json` is intentionally not generated here.
