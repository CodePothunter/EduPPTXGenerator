"""CLI entry point for edupptx V2."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import click
from loguru import logger

from edupptx.agent import PPTXAgent
from edupptx.config import Config
from edupptx.materials.ai_image_asset_db import DEFAULT_KEYWORD_BATCH_SIZE

try:
    from edupptx import __version__ as _VERSION
except Exception:
    _VERSION = "0.0.0"


def _configure_logging(verbose: bool, quiet: bool) -> None:
    logger.remove()
    if quiet:
        return
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
        level="DEBUG" if verbose else "INFO",
    )


def _emit_result(payload: dict, *, as_json: bool, human_lines: list[str]) -> None:
    """Print result either as JSON (machine) or human-readable lines."""
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False))
    else:
        for line in human_lines:
            click.echo(line)


def _emit_error(message: str, *, as_json: bool, **extra) -> None:
    """Surface an error in JSON form when requested, then exit non-zero.

    For JSON mode, write the structured payload to stdout (so an agent piping
    the command captures it) and exit 1 directly — Click's ClickException would
    print a duplicate human message to stderr.
    """
    if as_json:
        payload = {"ok": False, "error": message, **extra}
        click.echo(json.dumps(payload, ensure_ascii=False))
        sys.exit(1)
    raise click.ClickException(message)


def _optional_keyword_client(config: Config, *, enabled: bool):
    if not enabled:
        return None, "disabled"
    if not config.llm_api_key or not config.llm_model:
        return None, "missing_config"
    from edupptx.llm_client import create_llm_client

    return create_llm_client(config, web_search=False), "enabled"


LLM_PROFILE_CHOICES = ("deepseek", "doubao")


def _llm_profile_option(func):
    return click.option(
        "--llm",
        type=click.Choice(LLM_PROFILE_CHOICES, case_sensitive=False),
        default=None,
        help="LLM profile override: deepseek or doubao",
    )(func)


def _asset_library_vlm_review_option(func):
    return click.option(
        "--vlm-review",
        "vlm_review",
        is_flag=True,
        default=False,
        show_default=True,
        help="Run VLM review before generated assets enter the reusable library",
    )(func)


def _debug_artifacts_option(func):
    return click.option(
        "--debug-artifacts",
        is_flag=True,
        default=None,
        help="Persist intermediate reuse/debug artifacts for segmented testing and replay",
    )(func)


def _asset_ingest_option(func):
    return click.option(
        "--no-asset-ingest",
        is_flag=True,
        default=False,
        help="Skip background asset-library ingest for this debug run",
    )(func)


def _clean_env_value(value: str | None) -> str:
    return (value or "").split("#", 1)[0].strip()


def _env_value(*names: str) -> str:
    for name in names:
        value = _clean_env_value(os.getenv(name))
        if value:
            return value
    return ""


def _profile_value(prefix: str, key: str, *aliases: str) -> str:
    names = [f"{prefix}_{key}", *aliases]
    return _env_value(*names)


def _normalize_base_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    suffix = "/chat/completions"
    if base_url.endswith(suffix):
        return base_url[: -len(suffix)]
    return base_url


def _apply_llm_profile(config: Config, llm: str | None) -> Config:
    if not llm:
        return config

    profile = llm.lower()
    if profile == "deepseek":
        prefix = "DEEPSEEK"
        config.llm_model = _profile_value(prefix, "GEN_MODEL", "DEEPSEEK_MODEL")
        config.llm_api_key = _profile_value(
            prefix,
            "GEN_APIKEY",
            "DEEPSEEK_APIKEY",
            "DEEPSEEK_API_KEY",
        )
        config.llm_base_url = _normalize_base_url(
            _profile_value(prefix, "GEN_BASE_URL", "DEEPSEEK_BASE_URL")
        )
        config.llm_provider = _profile_value(prefix, "LLM_PROVIDER")
        config.llm_thinking = _profile_value(prefix, "GEN_THINKING", "DEEPSEEK_THINKING")
        config.llm_reasoning_effort = _profile_value(
            prefix,
            "GEN_REASONING_EFFORT",
            "DEEPSEEK_REASONING_EFFORT",
        )
    elif profile == "doubao":
        prefix = "DOUBAO"
        config.llm_model = _profile_value(prefix, "GEN_MODEL", "DOUBAO_MODEL", "ARK_MODEL")
        config.llm_api_key = _profile_value(
            prefix,
            "GEN_APIKEY",
            "DOUBAO_APIKEY",
            "DOUBAO_API_KEY",
            "ARK_API_KEY",
        )
        config.llm_base_url = _normalize_base_url(
            _profile_value(prefix, "GEN_BASE_URL", "DOUBAO_BASE_URL", "ARK_BASE_URL")
        )
        config.llm_provider = _profile_value(prefix, "LLM_PROVIDER")
        config.llm_thinking = _profile_value(prefix, "GEN_THINKING", "DOUBAO_THINKING")
        config.llm_reasoning_effort = _profile_value(
            prefix,
            "GEN_REASONING_EFFORT",
            "DOUBAO_REASONING_EFFORT",
        )
    return config


def _load_config(env_file: str, llm: str | None = None) -> Config:
    return _apply_llm_profile(Config.from_env(env_file), llm)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def _run_plan_reuse_match_check(
    *,
    plan_path: Path,
    config: Config,
    keywords: bool = True,
    materialize_matches: bool = False,
) -> tuple[dict, Path, str]:
    from edupptx.materials.ai_image_asset_db import evaluate_ai_image_reuse_matches_from_plan

    keyword_client, keyword_status = _optional_keyword_client(config, enabled=keywords)
    report_path = plan_path.parent / "ai_image_reuse_plan_check.json"
    debug_path = plan_path.parent / "ai_image_reuse_plan_check_debug.json"
    logger.info(
        "Reuse check starting: plan={}, libraries={}, keywords={}, materialize={}, search_concurrency={}",
        plan_path,
        [str(path) for path in (config.reuse_library_dirs or (config.library_dir,))],
        keyword_status,
        materialize_matches,
        config.materials_concurrency,
    )
    report = evaluate_ai_image_reuse_matches_from_plan(
        plan_path=plan_path,
        library_dir=config.reuse_library_dirs or (config.library_dir,),
        keyword_client=keyword_client,
        debug_path=debug_path,
        materialize_matches=materialize_matches,
        reuse_search_concurrency=config.materials_concurrency,
    )
    report["keyword_status"] = keyword_status
    report["debug_path"] = str(debug_path)
    _write_json_atomic(report_path, report)
    logger.info(
        "Reuse check finished: matched={}/{}, report={}, debug={}",
        report["matched_count"],
        report["check_count"],
        report_path,
        debug_path,
    )
    return report, report_path, keyword_status


@click.group()
@click.version_option(_VERSION, prog_name="edupptx")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.option("--quiet", "-q", is_flag=True, help="Suppress all log output (agent-friendly)")
@click.pass_context
def main(ctx: click.Context, verbose: bool, quiet: bool):
    """EduPPTX - AI 驱动的教育演示文稿生成器 (V2 SVG Pipeline)

    \b
    Agent usage:
        edupptx --quiet gen "topic" --debug --json
        edupptx --quiet styles --json
    """
    if verbose and quiet:
        raise click.UsageError("--verbose 和 --quiet 不能同时使用")
    _configure_logging(verbose, quiet)
    ctx.ensure_object(dict)
    ctx.obj["quiet"] = quiet


@main.command()
@click.argument("topic")
@click.option("--requirements", "-r", default="", help="附加要求")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="输入文档 (PDF/Word/MD/TXT)")
@click.option("--research", is_flag=True, help="启用联网搜索充实内容")
@click.option("--style", "-s", default="edu_emerald", help="风格模板名称")
@click.option("--review", is_flag=True, help="策划稿生成后暂停，供审核编辑")
@click.option("--debug", is_flag=True, help="Debug 模式：跳过素材图片生成，保留背景和 LLM 流程")
@click.option("--web-search", is_flag=True, help="启用 LLM 联网搜索 (仅 Responses API provider)")
@click.option("--output", "-o", default="./output", type=click.Path(), help="输出目录")
@click.option("--env-file", default=".env", help=".env 文件路径")
@click.option("--exercise-policy/--no-exercise-policy", default=None, help="启用/关闭题库 A/B/C 习题规划策略")
@click.option("--exercise-bank", default=None, type=click.Path(exists=True), help="题库 JSON 文件路径")
@click.option("--exercise-db", default=None, type=click.Path(exists=True, dir_okay=False), help="teach-kb SQLite 题库 DB 路径")
@click.option("--exercise-image-root", default=None, type=click.Path(exists=True, file_okay=False), help="teach-kb uploads 图片根目录")
@_llm_profile_option
@_asset_library_vlm_review_option
@_debug_artifacts_option
@_asset_ingest_option
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出结果（agent 友好）")
@click.option("--qa", is_flag=True, help="生成完成后运行视觉 QA，把摘要附加到结果")
def gen(topic: str, requirements: str, file_path: str | None, research: bool,
        style: str, review: bool, debug: bool, web_search: bool, output: str,
        env_file: str, exercise_policy: bool | None, exercise_bank: str | None,
        exercise_db: str | None, exercise_image_root: str | None, llm: str | None,
        vlm_review: bool, debug_artifacts: bool | None, no_asset_ingest: bool, as_json: bool, qa: bool):
    """从主题生成教育演示文稿。

    \b
    示例：
      edupptx gen "勾股定理"
      edupptx gen "光合作用" -r "适合高中生" --style edu_academic
      edupptx gen "光合作用" --debug          # 跳过素材，快速预览布局
      edupptx gen --file report.pdf "基于报告做汇报" --research
      edupptx gen "年度总结" --review
      edupptx gen "量子计算" --web-search       # LLM 联网搜索
      edupptx --quiet gen "电磁感应" --debug --json  # agent 调用模式
    """
    # Up-front style validation — fail fast before burning LLM tokens.
    styles_dir = Path(__file__).parent / "design" / "style_templates"
    if styles_dir.exists() and style != "edu_emerald":
        available = sorted(f.stem for f in styles_dir.glob("*.svg"))
        if style not in available:
            opts = ", ".join(available)
            _emit_error(
                f"未知风格 '{style}'。可用: {opts}。运行 `edupptx styles` 查看说明。",
                as_json=as_json,
                kind="UnknownStyle",
                available=available,
            )

    try:
        config = _load_config(env_file, llm)
        config.output_dir = Path(output)
        config.web_search = web_search
        config.asset_library_vlm_review = vlm_review
        if exercise_policy is not None:
            config.exercise_policy_enabled = exercise_policy
        if exercise_bank:
            config.exercise_bank_path = Path(exercise_bank)
        if exercise_db:
            config.exercise_db_path = Path(exercise_db)
        if exercise_image_root:
            config.exercise_image_root = Path(exercise_image_root)
        if debug_artifacts is not None:
            config.debug_artifacts = debug_artifacts
        config.asset_library_ingest_enabled = not no_asset_ingest

        agent = PPTXAgent(config)
        session_dir = agent.run(
            topic, requirements,
            file_path=file_path,
            research=research,
            style=style,
            review=review,
            debug=debug,
        )

        plan_path = session_dir / "plan.json"
        pptx_path = session_dir / "output.pptx"
        slides_dir = session_dir / "slides"

        if review:
            payload = {
                "ok": True,
                "mode": "review",
                "session_dir": str(session_dir),
                "plan_path": str(plan_path),
                "next_step": f"edupptx render {plan_path}",
            }
            human = [
                f"策划稿已生成: {plan_path}",
                f"审核编辑后运行: edupptx render {plan_path}",
            ]
        else:
            slide_count = sum(1 for _ in slides_dir.glob("*.svg")) if slides_dir.exists() else 0
            payload = {
                "ok": True,
                "mode": "full",
                "session_dir": str(session_dir),
                "pptx_path": str(pptx_path),
                "plan_path": str(plan_path),
                "slides_dir": str(slides_dir),
                "slide_count": slide_count,
            }
            human = [
                f"输出: {pptx_path}",
                f"SVG: {slides_dir}/",
            ]

            if qa and pptx_path.exists():
                try:
                    from tests.visual_qa import analyze_pptx
                    qa_report = analyze_pptx(pptx_path)
                    summary = {
                        "slide_count": qa_report["slide_count"],
                        "severity_counts": qa_report["severity_counts"],
                    }
                    payload["qa"] = summary
                    human.append(
                        f"QA: {qa_report['slide_count']} slides, "
                        f"counts={qa_report['severity_counts']}"
                    )
                except Exception as exc:
                    payload["qa_error"] = str(exc)
                    human.append(f"QA 失败: {exc}")
        _emit_result(payload, as_json=as_json, human_lines=human)

    except click.ClickException:
        raise
    except Exception as e:
        logger.error("Generation failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command()
@click.argument("plan_path", type=click.Path(exists=True))
@click.option("--style", "-s", default="edu_emerald", help="风格模板名称")
@click.option("--debug", is_flag=True, help="Debug 模式：跳过素材图片生成")
@click.option("--env-file", default=".env", help=".env 文件路径")
@_llm_profile_option
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出结果")
@_asset_library_vlm_review_option
@_debug_artifacts_option
@_asset_ingest_option
def render(plan_path: str, style: str, debug: bool, env_file: str, llm: str | None, vlm_review: bool, debug_artifacts: bool | None, no_asset_ingest: bool, as_json: bool):
    """从策划稿 JSON 渲染 SVG + PPTX。

    \b
    示例：
      edupptx render output/session_xxx/plan.json
      edupptx render plan.json --style edu_tech --debug
    """
    try:
        config = _load_config(env_file, llm)
        config.asset_library_vlm_review = vlm_review
        if debug_artifacts is not None:
            config.debug_artifacts = debug_artifacts
        config.asset_library_ingest_enabled = not no_asset_ingest
        agent = PPTXAgent(config)
        session_dir = asyncio.run(agent.run_from_plan(Path(plan_path), style, debug=debug))
        pptx_path = session_dir / "output.pptx"
        slides_dir = session_dir / "slides"
        slide_count = sum(1 for _ in slides_dir.glob("*.svg")) if slides_dir.exists() else 0
        payload = {
            "ok": True,
            "mode": "render",
            "session_dir": str(session_dir),
            "pptx_path": str(pptx_path),
            "slides_dir": str(slides_dir),
            "slide_count": slide_count,
        }
        _emit_result(payload, as_json=as_json, human_lines=[f"输出: {pptx_path}"])
    except Exception as e:
        logger.error("Render failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("images")
@click.argument("topic")
@click.option("--requirements", "-r", default="", help="附加要求")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="输入文档 (PDF/Word/MD/TXT)")
@click.option("--research", is_flag=True, help="启用联网搜索充实内容")
@click.option("--style", "-s", default="edu_emerald", help="风格模板名称")
@click.option("--web-search", is_flag=True, help="启用 LLM 联网搜索 (仅 Responses API provider)")
@click.option("--output", "-o", default="./output", type=click.Path(), help="输出目录")
@click.option("--env-file", default=".env", help=".env 文件路径")
@_llm_profile_option
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
@_asset_library_vlm_review_option
@_debug_artifacts_option
@_asset_ingest_option
def images(topic: str, requirements: str, file_path: str | None, research: bool,
           style: str, web_search: bool, output: str, env_file: str, llm: str | None, vlm_review: bool, debug_artifacts: bool | None, no_asset_ingest: bool, as_json: bool):
    """Run planning and image/material phases, then queue background asset-library ingest."""
    try:
        config = _load_config(env_file, llm)
        config.output_dir = Path(output)
        config.web_search = web_search
        config.asset_library_vlm_review = vlm_review
        if debug_artifacts is not None:
            config.debug_artifacts = debug_artifacts
        config.asset_library_ingest_enabled = not no_asset_ingest
        agent = PPTXAgent(config)
        session_dir = agent.run(
            topic,
            requirements,
            file_path=file_path,
            research=research,
            style=style,
            review=False,
            debug=False,
            stop_after_asset_library=True,
        )
        plan_path = session_dir / "plan.json"
        materials_dir = session_dir / "materials"
        image_suffixes = {".png", ".jpg", ".jpeg", ".webp"}
        image_count = (
            sum(1 for path in materials_dir.iterdir() if path.suffix.lower() in image_suffixes)
            if materials_dir.exists()
            else 0
        )
        match_index_path = Path(config.library_dir) / "strict_reuse_indexes"
        payload = {
            "ok": True,
            "mode": "images",
            "session_dir": str(session_dir),
            "plan_path": str(plan_path),
            "materials_dir": str(materials_dir),
            "image_count": image_count,
            "match_index_path": str(match_index_path),
        }
        human = [
            f"Materials: {materials_dir}",
            f"Images: {image_count}",
            f"Split indexes: {match_index_path}",
        ]
        _emit_result(payload, as_json=as_json, human_lines=human)
    except Exception as e:
        logger.error("Image/material generation failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("images-from-plan")
@click.argument("plan_path", type=click.Path(exists=True))
@click.option("--env-file", default=".env", help=".env file path")
@_llm_profile_option
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
@_asset_library_vlm_review_option
@_debug_artifacts_option
@_asset_ingest_option
def images_from_plan(plan_path: str, env_file: str, llm: str | None, vlm_review: bool, debug_artifacts: bool | None, no_asset_ingest: bool, as_json: bool):
    """Run image/material phases from an existing plan, then queue background asset-library ingest."""
    try:
        config = _load_config(env_file, llm)
        config.asset_library_vlm_review = vlm_review
        if debug_artifacts is not None:
            config.debug_artifacts = debug_artifacts
        config.asset_library_ingest_enabled = not no_asset_ingest
        agent = PPTXAgent(config)
        session_dir = asyncio.run(agent.run_images_from_plan(Path(plan_path)))
        materials_dir = session_dir / "materials"
        image_suffixes = {".png", ".jpg", ".jpeg", ".webp"}
        image_count = (
            sum(1 for path in materials_dir.iterdir() if path.suffix.lower() in image_suffixes)
            if materials_dir.exists()
            else 0
        )
        match_index_path = Path(config.library_dir) / "strict_reuse_indexes"
        payload = {
            "ok": True,
            "mode": "images-from-plan",
            "session_dir": str(session_dir),
            "plan_path": str(Path(plan_path)),
            "materials_dir": str(materials_dir),
            "image_count": image_count,
            "match_index_path": str(match_index_path),
        }
        human = [
            f"Materials: {materials_dir}",
            f"Images: {image_count}",
            f"Split indexes: {match_index_path}",
        ]
        _emit_result(payload, as_json=as_json, human_lines=human)
    except Exception as e:
        logger.error("Image/material generation from plan failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command()
@click.argument("topic")
@click.option("--requirements", "-r", default="", help="附加要求")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="输入文档")
@click.option("--research", is_flag=True, help="启用联网搜索")
@click.option("--output", "-o", default="./output", type=click.Path(), help="输出目录")
@click.option("--env-file", default=".env", help=".env 文件路径")
@click.option("--exercise-policy/--no-exercise-policy", default=None, help="启用/关闭题库 A/B/C 习题规划策略")
@click.option("--exercise-bank", default=None, type=click.Path(exists=True), help="题库 JSON 文件路径")
@click.option("--exercise-db", default=None, type=click.Path(exists=True, dir_okay=False), help="teach-kb SQLite 题库 DB 路径")
@click.option("--exercise-image-root", default=None, type=click.Path(exists=True, file_okay=False), help="teach-kb uploads 图片根目录")
@_llm_profile_option
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出结果")
def plan(topic: str, requirements: str, file_path: str | None, research: bool,
         output: str, env_file: str, exercise_policy: bool | None, exercise_bank: str | None,
         exercise_db: str | None, exercise_image_root: str | None, llm: str | None, as_json: bool):
    """只生成策划稿，不渲染。

    \b
    示例：
      edupptx plan "量子计算"
      edupptx plan "人工智能" --research
    """
    try:
        config = _load_config(env_file, llm)
        config.output_dir = Path(output)
        if exercise_policy is not None:
            config.exercise_policy_enabled = exercise_policy
        if exercise_bank:
            config.exercise_bank_path = Path(exercise_bank)
        if exercise_db:
            config.exercise_db_path = Path(exercise_db)
        if exercise_image_root:
            config.exercise_image_root = Path(exercise_image_root)

        agent = PPTXAgent(config)
        session_dir = agent.run(
            topic, requirements,
            file_path=file_path,
            research=research,
            review=True,  # stops after planning
        )
        plan_path = session_dir / "plan.json"
        payload = {
            "ok": True,
            "session_dir": str(session_dir),
            "plan_path": str(plan_path),
        }
        human = [f"Plan: {plan_path}"]
        _emit_result(payload, as_json=as_json, human_lines=human)
    except Exception as e:
        logger.error("Planning failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("reuse-check")
@click.argument("topic")
@click.option("--requirements", "-r", default="", help="闄勫姞瑕佹眰")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="杈撳叆鏂囨。")
@click.option("--research", is_flag=True, help="鍚敤鑱旂綉鎼滅储")
@click.option("--output", "-o", default="./output", type=click.Path(), help="杈撳嚭鐩綍")
@click.option("--env-file", default=".env", help=".env file path")
@click.option("--keywords/--no-keywords", default=True, show_default=True, help="Use LLM keyword enrichment for reuse targets")
@_llm_profile_option
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def reuse_check(
    topic: str,
    requirements: str,
    file_path: str | None,
    research: bool,
    output: str,
    env_file: str,
    keywords: bool,
    llm: str | None,
    as_json: bool,
):
    """Generate a plan, then check AI image reuse without image generation or rendering."""
    try:
        config = _load_config(env_file, llm)
        config.output_dir = Path(output)

        agent = PPTXAgent(config)
        session_dir = agent.run(
            topic,
            requirements,
            file_path=file_path,
            research=research,
            review=True,
        )
        plan_path = session_dir / "plan.json"
        report, report_path, keyword_status = _run_plan_reuse_match_check(
            plan_path=plan_path,
            config=config,
            keywords=keywords,
        )
        payload = {
            "ok": True,
            "mode": "reuse-check",
            "session_dir": str(session_dir),
            "plan_path": str(plan_path),
            "report_path": str(report_path),
            "check_count": report["check_count"],
            "matched_count": report["matched_count"],
            "unmatched_count": report["unmatched_count"],
            "keyword_status": keyword_status,
            "reuse_search_concurrency": report.get("reuse_search_concurrency"),
            "generated_images": False,
            "updated_asset_library": False,
        }
        human = [
            f"Plan: {plan_path}",
            f"Reuse check: {report_path}",
            f"Matched: {report['matched_count']}/{report['check_count']}",
            "Images: not generated",
            "Asset library: not updated",
        ]
        _emit_result(payload, as_json=as_json, human_lines=human)
    except Exception as e:
        logger.error("Reuse check failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("reuse-check-plan")
@click.argument("plan_path", type=click.Path(exists=True))
@click.option("--env-file", default=".env", help=".env file path")
@click.option("--keywords/--no-keywords", default=True, show_default=True, help="Use LLM keyword enrichment for reuse targets")
@_llm_profile_option
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def reuse_check_plan(plan_path: str, env_file: str, keywords: bool, llm: str | None, as_json: bool):
    """Check AI image reuse matches from an existing plan without generating images or ingesting assets."""
    try:
        config = _load_config(env_file, llm)
        report, report_path, keyword_status = _run_plan_reuse_match_check(
            plan_path=Path(plan_path),
            config=config,
            keywords=keywords,
        )
        payload = {
            "ok": True,
            "mode": "reuse-check-plan",
            "plan_path": str(Path(plan_path)),
            "report_path": str(report_path),
            "check_count": report["check_count"],
            "matched_count": report["matched_count"],
            "unmatched_count": report["unmatched_count"],
            "keyword_status": keyword_status,
            "reuse_search_concurrency": report.get("reuse_search_concurrency"),
            "generated_images": False,
            "updated_asset_library": False,
        }
        human = [
            f"Reuse check: {report_path}",
            f"Matched: {report['matched_count']}/{report['check_count']}",
            "Images: not generated",
            "Asset library: not updated",
        ]
        _emit_result(payload, as_json=as_json, human_lines=human)
    except Exception as e:
        logger.error("Plan reuse check failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)

@main.command("asset-ingest")
@click.option("--output-root", default="./output", type=click.Path(file_okay=False), help="Output root containing session_* dirs")
@click.option("--library-dir", default=None, type=click.Path(file_okay=False), help="Reusable material library directory")
@click.option("--keywords/--no-keywords", default=True, show_default=True, help="Use the configured LLM to build matching keywords while ingesting")
@click.option(
    "--keyword-batch-size",
    default=DEFAULT_KEYWORD_BATCH_SIZE,
    show_default=True,
    type=click.IntRange(1, 50),
    help="Assets per LLM keyword batch",
)
@_asset_library_vlm_review_option
@click.option("--env-file", default=".env", help=".env file path used by --keywords and LIBRARY_DIR")
@_llm_profile_option
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def asset_ingest(
    output_root: str,
    library_dir: str | None,
    keywords: bool,
    keyword_batch_size: int,
    vlm_review: bool,
    env_file: str,
    llm: str | None,
    as_json: bool,
):
    """Copy AI-generated images from output sessions into the reusable library."""
    try:
        from edupptx.materials.ai_image_asset_db import ingest_ai_image_asset_library_from_output

        config = _load_config(env_file, llm)
        target_library = Path(library_dir) if library_dir else config.library_dir

        keyword_client, keyword_status = _optional_keyword_client(config, enabled=keywords)
        vlm_client = None
        if vlm_review:
            if not config.vlm_api_key or not config.vlm_model:
                _emit_error(
                    "VLM_APIKEY/VLM_MODEL not configured",
                    as_json=as_json,
                    kind="MissingVlmConfig",
                )
            from edupptx.llm_client import create_vlm_client

            vlm_client = create_vlm_client(config)

        db, target, report = ingest_ai_image_asset_library_from_output(
            output_root,
            target_library,
            keyword_client=keyword_client,
            keyword_batch_size=keyword_batch_size,
            vlm_client=vlm_client,
            vlm_review=vlm_review,
        )
        report_library_dir = report.get("library_dir") or report.get("asset_root") or str(target_library)
        payload = {
            "ok": True,
            "match_index_path": str(target),
            "output_root": report["output_root"],
            "library_dir": report_library_dir,
            "session_count": report["session_count"],
            "processed_session_count": len(report["processed_sessions"]),
            "failed_session_count": len(report["failed_sessions"]),
            "asset_count": db.get("asset_count", 0),
            "warning_count": report["warning_count"],
            "keywords": keyword_status == "enabled",
            "keyword_status": keyword_status,
            "VLM_review": vlm_review,
        }
        human = [
            f"Asset library: {report_library_dir}",
            f"Split indexes: {target}",
            f"Sessions: {len(report['processed_sessions'])}/{report['session_count']}",
            f"Assets: {db.get('asset_count', 0)}",
        ]
        if keyword_status == "enabled":
            human.append("Keywords: LLM enriched")
        elif keyword_status == "missing_config":
            human.append("Keywords: skipped (GEN_APIKEY/GEN_MODEL not configured)")
        human.append(f"VLM review: {'enabled' if vlm_review else 'disabled'}")
        if report["failed_sessions"]:
            human.append(f"Failed sessions: {len(report['failed_sessions'])}")
        if report["warning_count"]:
            human.append(f"Warnings: {report['warning_count']}")
        _emit_result(payload, as_json=as_json, human_lines=human)
    except Exception as e:
        logger.error("Asset library ingest failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("embedding-build")
@click.argument("library_dir", type=click.Path(file_okay=False))
@click.option("--env-file", default=".env", help=".env file path used for embedding model config")
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def embedding_build(library_dir: str, env_file: str, as_json: bool):
    """Build embedding sidecar files for an existing image match index."""
    try:
        from edupptx.materials.ai_image_asset_db import (
            DEFAULT_EMBEDDING_INDEX_FILENAME,
            DEFAULT_EMBEDDING_META_FILENAME,
            STRICT_REUSE_INDEX_DIRNAME,
            read_ai_image_split_match_index,
            write_ai_image_embedding_index,
        )

        Config.from_env(env_file)
        root = Path(library_dir).expanduser().resolve()
        split_index = read_ai_image_split_match_index(root)
        if split_index is None:
            _emit_error(
                f"Split reuse indexes not found: {root / STRICT_REUSE_INDEX_DIRNAME}",
                as_json=as_json,
                kind="MatchIndexNotFound",
                path=str(root / STRICT_REUSE_INDEX_DIRNAME),
            )

        index, index_path = split_index
        report = write_ai_image_embedding_index(index, root)
        index["embedding_index"] = report

        payload = {
            "ok": bool(report.get("enabled")),
            "mode": "embedding-build",
            "library_dir": str(root),
            "split_index_dir": str(index_path),
            "embedding_index_path": str(root / DEFAULT_EMBEDDING_INDEX_FILENAME),
            "embedding_meta_path": str(root / DEFAULT_EMBEDDING_META_FILENAME),
            **report,
        }
        if not report.get("enabled"):
            _emit_error(
                f"Embedding index build failed: {report.get('reason') or 'unknown'}",
                as_json=as_json,
                kind="EmbeddingBuildFailed",
                **payload,
            )

        human = [
            f"Split indexes: {index_path}",
            f"Embedding index: {root / DEFAULT_EMBEDDING_INDEX_FILENAME}",
            f"Embedding meta: {root / DEFAULT_EMBEDDING_META_FILENAME}",
            f"Assets: {report.get('asset_count', 0)}",
            f"Model: {report.get('model', '')}",
        ]
        _emit_result(payload, as_json=as_json, human_lines=human)
    except click.ClickException:
        raise
    except Exception as e:
        logger.error("Embedding index build failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("strict-reuse-classify")
@click.argument("library_dir", type=click.Path(file_okay=False))
@click.option("--index-filename", default="ai_image_match_index.json", show_default=True, help="Match index filename")
@click.option("--dry-run", is_flag=True, help="Classify in memory without writing files")
@click.option("--write-debug/--no-write-debug", default=True, show_default=True, help="Write debug report and classification review queue")
@click.option("--split-dir", default="strict_reuse_indexes", show_default=True, type=click.Path(file_okay=False), help="Directory for per-group split indexes")
@click.option("--from-main-index", is_flag=True, help="Ignore existing split indexes and rebuild from the main match index")
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def strict_reuse_classify(
    library_dir: str,
    index_filename: str,
    dry_run: bool,
    write_debug: bool,
    split_dir: str | None,
    from_main_index: bool,
    as_json: bool,
):
    """Normalize material reuse groups for split-library retrieval."""
    try:
        from edupptx.materials.strict_reuse_classifier import classify_strict_reuse_library

        report, index_path = classify_strict_reuse_library(
            library_dir,
            index_filename=index_filename,
            dry_run=dry_run,
            write_debug=write_debug,
            split_dir=split_dir,
            prefer_split_index=not from_main_index,
        )
        payload = {
            "ok": True,
            "mode": "strict-reuse-classify",
            "dry_run": dry_run,
            **{key: value for key, value in report.items() if key != "review_items"},
        }
        group_counts = report.get("group_counts") or {}
        human = [
            f"Source: {report.get('source_index_path', index_path)}",
            f"Assets: {report.get('asset_count', 0)}",
            "Groups: "
            + ", ".join(f"{key}={group_counts.get(key, 0)}" for key in sorted(group_counts)),
            f"Classification review queue: {report.get('review_required_count', 0)}",
        ]
        if dry_run:
            human.append("Dry run: files unchanged")
        if report.get("debug_report_path"):
            human.append(f"Debug report: {report['debug_report_path']}")
        if report.get("review_queue_path"):
            human.append(f"Review queue: {report['review_queue_path']}")
        if report.get("split_indexes"):
            human.append(f"Split indexes: {report['split_indexes']['split_dir']}")
        _emit_result(payload, as_json=as_json, human_lines=human)
    except click.ClickException:
        raise
    except Exception as e:
        logger.error("Strict reuse classification failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("strict-reuse-export-check")
@click.argument("library_dir", type=click.Path(file_okay=False))
@click.option("--output-dir", default="strict_reuse_visual_check", show_default=True, type=click.Path(file_okay=False), help="Directory that receives 4-class material category image copies")
@click.option("--index-filename", default="ai_image_match_index.json", show_default=True, help="Match index filename")
@click.option("--clean/--no-clean", default=True, show_default=True, help="Clean command-owned material category folders before exporting")
@click.option("--force", is_flag=True, help="Allow overwriting an existing unrelated manifest in the output directory")
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def strict_reuse_export_check(
    library_dir: str,
    output_dir: str,
    index_filename: str,
    clean: bool,
    force: bool,
    as_json: bool,
):
    """Export reuse groups into 4-class material category folders for visual checking."""
    try:
        from edupptx.materials.strict_reuse_classifier import export_strict_reuse_visual_check

        manifest, target_dir = export_strict_reuse_visual_check(
            library_dir,
            output_dir,
            index_filename=index_filename,
            clean=clean,
            force=force,
        )
        payload = {
            "ok": True,
            "mode": "strict-reuse-export-check",
            "output_dir": str(target_dir),
            "asset_library_unchanged": True,
            **{key: value for key, value in manifest.items() if key != "assets"},
        }
        group_counts = manifest.get("group_counts") or {}
        non_empty_group_lines = [
            f"{group}: {count}"
            for group, count in sorted(group_counts.items())
            if count
        ]
        if not non_empty_group_lines:
            non_empty_group_lines = ["No assets in material category folders"]
        human = [
            f"Output: {target_dir}",
            "Groups: 7 material categories",
            *non_empty_group_lines,
            f"Copied: {manifest.get('copied_count', 0)}",
            f"Missing images: {manifest.get('missing_image_count', 0)}",
            f"Manifest: {manifest.get('manifest_path')}",
            "Asset library: unchanged",
        ]
        _emit_result(payload, as_json=as_json, human_lines=human)
    except click.ClickException:
        raise
    except Exception as e:
        logger.error("Strict reuse visual export failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("vlm-enrich")
@click.argument("library_dir", type=click.Path(file_okay=False))
@click.option("--asset-id", "asset_ids", multiple=True, help="Only enrich the given asset id; may be repeated")
@click.option("--force", is_flag=True, help="Re-run assets already present in the VLM review sidecar")
@click.option("--env-file", default=".env", help=".env file path used for VLM_* config")
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def vlm_enrich(
    library_dir: str,
    asset_ids: tuple[str, ...],
    force: bool,
    env_file: str,
    as_json: bool,
):
    """Run VLM verification/enrichment for the AI image asset library."""
    try:
        from edupptx.llm_client import create_vlm_client
        from edupptx.materials.ai_image_asset_db import (
            CONTENT_REUSE_GROUP,
            GENERAL_REUSE_GROUP,
            STRICT_REUSE_INDEX_DIRNAME,
            read_ai_image_split_match_index,
            write_ai_image_match_index,
        )
        from edupptx.materials.vlm_asset_enricher import enrich_assets_with_vlm

        config = Config.from_env(env_file)
        if not config.vlm_api_key or not config.vlm_model:
            _emit_error(
                "VLM_APIKEY/VLM_MODEL not configured",
                as_json=as_json,
                kind="MissingVlmConfig",
            )

        root = Path(library_dir).expanduser().resolve()
        split_index = read_ai_image_split_match_index(root)
        if split_index is None:
            _emit_error(
                f"Split reuse indexes not found: {root / STRICT_REUSE_INDEX_DIRNAME}",
                as_json=as_json,
                kind="MatchIndexNotFound",
                path=str(root / STRICT_REUSE_INDEX_DIRNAME),
            )

        db, index_path = split_index
        client = create_vlm_client(config)
        keyword_client, keyword_status = _optional_keyword_client(config, enabled=True)
        report: dict[str, Any] = {
            "processed_count": 0,
            "skipped_reviewed_count": 0,
            "missing_image_count": 0,
            "failed_count": 0,
            "group_reports": {},
        }
        requested = set(asset_ids or ())
        for group in (GENERAL_REUSE_GROUP, CONTENT_REUSE_GROUP):
            group_assets = [
                asset
                for asset in db.get("assets", [])
                if isinstance(asset, dict)
                and str(asset.get("strict_reuse_group") or GENERAL_REUSE_GROUP) == group
            ]
            group_db = {**db, "assets": group_assets, "asset_count": len(group_assets)}
            group_report = enrich_assets_with_vlm(
                group_db,
                client,
                skip_reviewed=not force,
                image_root=root,
                asset_ids=asset_ids or None,
                debug_dir=root / "debug" / group,
                review_index_path=root / "debug" / f"ai_image_vlm_review_{group}.json",
                keyword_client=keyword_client,
            )
            report["group_reports"][group] = group_report
            for key in ("processed_count", "skipped_reviewed_count", "missing_image_count", "failed_count"):
                report[key] += int(group_report.get(key) or 0)
            if requested:
                requested -= set(group_report.get("processed_asset_ids") or [])
        written = bool(report.get("processed_count"))
        if written:
            _index, index_path = write_ai_image_match_index(db, root)

        payload = {
            "ok": True,
            "mode": "vlm-enrich",
            "library_dir": str(root),
            "split_index_dir": str(index_path),
            "model": config.vlm_model,
            "force": force,
            "written": written,
            "keyword_status": keyword_status,
            **report,
        }
        human = [
            f"Split indexes: {index_path}",
            f"VLM processed: {report['processed_count']}",
            f"Skipped reviewed: {report['skipped_reviewed_count']}",
            f"Missing images: {report['missing_image_count']}",
            f"Failed: {report['failed_count']}",
        ]
        if keyword_status == "enabled":
            human.append("Keywords: LLM rebuilt for rewritten assets")
        elif keyword_status == "missing_config":
            human.append("Keywords: skipped (GEN_APIKEY/GEN_MODEL not configured)")
        if report.get("missing_asset_ids"):
            human.append(f"Missing asset ids: {', '.join(report['missing_asset_ids'])}")
        if not written:
            human.append("Match index unchanged")
        _emit_result(payload, as_json=as_json, human_lines=human)
    except click.ClickException:
        raise
    except Exception as e:
        logger.error("VLM enrichment failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


_STYLE_DESCRIPTIONS = {
    "edu_emerald": "翠绿主调，自然/生命科学/语文人文",
    "edu_academic": "深蓝学术，物理/数学/严谨理科",
    "edu_minimal": "极简灰白，通用/演示/职场汇报",
    "edu_tech": "科技深色，计算机/工程/前沿话题",
    "edu_warm": "暖橙色调，低龄/小学/活泼主题",
}


@main.group(invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出（含描述）")
@click.pass_context
def styles(ctx: click.Context, as_json: bool):
    """列出可用的风格模板，或转换调色板 JSON ↔ DESIGN.md。"""
    if ctx.invoked_subcommand is not None:
        return  # subcommand will run; don't list templates

    styles_dir = Path(__file__).parent / "design" / "style_templates"
    if not styles_dir.exists():
        if as_json:
            click.echo(json.dumps({"ok": True, "styles": []}, ensure_ascii=False))
        else:
            click.echo("No style templates found.")
        return
    items = []
    for f in sorted(styles_dir.glob("*.svg")):
        items.append({
            "name": f.stem,
            "description": _STYLE_DESCRIPTIONS.get(f.stem, ""),
        })
    if as_json:
        click.echo(json.dumps({"ok": True, "styles": items}, ensure_ascii=False))
    else:
        for item in items:
            desc = f" — {item['description']}" if item["description"] else ""
            click.echo(f"  {item['name']}{desc}")


@styles.command("convert")
@click.argument("name")
@click.option("--styles-dir", "styles_dir_opt", default=None, type=click.Path(),
              help="自定义 styles 目录 (默认: 项目根目录的 styles/)")
@click.option("--force", is_flag=True, help="覆盖已存在的 .md 文件")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出结果")
def styles_convert(name: str, styles_dir_opt: str | None, force: bool, as_json: bool):
    """将 styles/<name>.json 调色板转换为 styles/<name>.md 脚手架。

    \b
    生成的 .md 含 YAML frontmatter（与 JSON 完全等价）+ 8 段占位 prose；
    用户编辑 prose 后运行 `pytest tests/test_style_migration_regression.py` 验证等价。
    """
    try:
        from edupptx.style.design_md import (
            PROSE_HEADINGS,
            parse_design_md,
            serialize_style,
        )
        from edupptx.style_schema import load_style

        if styles_dir_opt is not None:
            base_dir = Path(styles_dir_opt)
        else:
            base_dir = Path(__file__).resolve().parent.parent / "styles"

        json_path = base_dir / f"{name}.json"
        md_path = base_dir / f"{name}.md"

        if not json_path.exists():
            _emit_error(
                f"未找到 {json_path}",
                as_json=as_json,
                kind="StyleNotFound",
                path=str(json_path),
            )
        if md_path.exists() and not force:
            _emit_error(
                f"{md_path} 已存在；使用 --force 覆盖（注意会丢失手写 prose）",
                as_json=as_json,
                kind="MdExists",
                path=str(md_path),
            )

        schema = load_style(json_path)
        # Roundtrip via Layer 3a serializer to ensure YAML matches parser expectations.
        scaffold_text = serialize_style(
            schema,
            prose_sections={h: f"TODO: 描述 {h}" for h in PROSE_HEADINGS},
        )
        # Sanity-check: re-parse to guarantee the scaffold is valid Layer 3a input.
        parse_design_md(scaffold_text)

        md_path.write_text(scaffold_text, encoding="utf-8")
        next_steps = [
            f"已写入 {md_path}",
            f"下一步：编辑 {md_path} 中的 8 段 prose",
            "然后运行：uv run pytest tests/test_style_migration_regression.py -v",
        ]
        payload = {
            "ok": True,
            "name": name,
            "json_path": str(json_path),
            "md_path": str(md_path),
            "next_steps": next_steps,
        }
        _emit_result(payload, as_json=as_json, human_lines=next_steps)
    except click.ClickException:
        raise
    except Exception as e:
        logger.error("Convert failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


if __name__ == "__main__":
    main()
