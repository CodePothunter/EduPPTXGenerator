"""CLI entry point for edupptx V2."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
from loguru import logger

from edupptx.agent import PPTXAgent
from edupptx.config import Config

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
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出结果（agent 友好）")
@click.option("--qa", is_flag=True, help="生成完成后运行视觉 QA，把摘要附加到结果")
def gen(topic: str, requirements: str, file_path: str | None, research: bool,
        style: str, review: bool, debug: bool, web_search: bool, output: str,
        env_file: str, as_json: bool, qa: bool):
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
        config = Config.from_env(env_file)
        config.output_dir = Path(output)
        config.web_search = web_search

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
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出结果")
def render(plan_path: str, style: str, debug: bool, env_file: str, as_json: bool):
    """从策划稿 JSON 渲染 SVG + PPTX。

    \b
    示例：
      edupptx render output/session_xxx/plan.json
      edupptx render plan.json --style edu_tech --debug
    """
    try:
        config = Config.from_env(env_file)
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
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def images(topic: str, requirements: str, file_path: str | None, research: bool,
           style: str, web_search: bool, output: str, env_file: str, as_json: bool):
    """Run planning and image/material phases, then stop after asset-library update."""
    try:
        config = Config.from_env(env_file)
        config.output_dir = Path(output)
        config.web_search = web_search
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
        db_path = Path(config.library_dir) / "ai_image_asset_db.json"
        payload = {
            "ok": True,
            "mode": "images",
            "session_dir": str(session_dir),
            "plan_path": str(plan_path),
            "materials_dir": str(materials_dir),
            "image_count": image_count,
            "asset_db_path": str(db_path),
        }
        human = [
            f"Materials: {materials_dir}",
            f"Images: {image_count}",
            f"Asset DB: {db_path}",
        ]
        _emit_result(payload, as_json=as_json, human_lines=human)
    except Exception as e:
        logger.error("Image/material generation failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("images-from-plan")
@click.argument("plan_path", type=click.Path(exists=True))
@click.option("--env-file", default=".env", help=".env file path")
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def images_from_plan(plan_path: str, env_file: str, as_json: bool):
    """Run image/material phases from an existing plan and stop after asset-library update."""
    try:
        config = Config.from_env(env_file)
        agent = PPTXAgent(config)
        session_dir = asyncio.run(agent.run_images_from_plan(Path(plan_path)))
        materials_dir = session_dir / "materials"
        image_suffixes = {".png", ".jpg", ".jpeg", ".webp"}
        image_count = (
            sum(1 for path in materials_dir.iterdir() if path.suffix.lower() in image_suffixes)
            if materials_dir.exists()
            else 0
        )
        db_path = Path(config.library_dir) / "ai_image_asset_db.json"
        payload = {
            "ok": True,
            "mode": "images-from-plan",
            "session_dir": str(session_dir),
            "plan_path": str(Path(plan_path)),
            "materials_dir": str(materials_dir),
            "image_count": image_count,
            "asset_db_path": str(db_path),
        }
        human = [
            f"Materials: {materials_dir}",
            f"Images: {image_count}",
            f"Asset DB: {db_path}",
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
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出结果")
def plan(topic: str, requirements: str, file_path: str | None, research: bool,
         output: str, env_file: str, as_json: bool):
    """只生成策划稿，不渲染。

    \b
    示例：
      edupptx plan "量子计算"
      edupptx plan "人工智能" --research
    """
    try:
        config = Config.from_env(env_file)
        config.output_dir = Path(output)

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
        _emit_result(payload, as_json=as_json, human_lines=[f"策划稿: {plan_path}"])
    except Exception as e:
        logger.error("Planning failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("asset-db")
@click.option("--output-root", default="./output", type=click.Path(file_okay=False), help="Output root containing session_* dirs")
@click.option("--db-path", default=None, type=click.Path(dir_okay=False), help="Where to write the asset DB JSON")
@click.option("--keywords/--no-keywords", default=True, show_default=True, help="Use the configured LLM to build offline matching keywords")
@click.option("--keyword-batch-size", default=12, show_default=True, type=click.IntRange(1, 50), help="Assets per LLM keyword batch")
@click.option("--env-file", default=".env", help=".env file path used by --keywords")
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def asset_db(
    output_root: str,
    db_path: str | None,
    keywords: bool,
    keyword_batch_size: int,
    env_file: str,
    as_json: bool,
):
    """Build the offline AI-generated image asset database from output sessions."""
    try:
        from edupptx.materials.ai_image_asset_db import DEFAULT_MATCH_INDEX_FILENAME, write_ai_image_asset_db

        config = Config.from_env(env_file)
        keyword_client, keyword_status = _optional_keyword_client(config, enabled=keywords)

        db, target = write_ai_image_asset_db(
            output_root,
            db_path,
            keyword_client=keyword_client,
            keyword_batch_size=keyword_batch_size,
        )
        payload = {
            "ok": True,
            "db_path": str(target),
            "match_index_path": str(target.with_name(DEFAULT_MATCH_INDEX_FILENAME)),
            "output_root": db["output_root"],
            "asset_count": db["asset_count"],
            "warning_count": len(db.get("warnings", [])),
            "keywords": keyword_status == "enabled",
            "keyword_status": keyword_status,
        }
        human = [
            f"Asset DB: {target}",
            f"Match index: {target.with_name(DEFAULT_MATCH_INDEX_FILENAME)}",
            f"Assets: {db['asset_count']}",
        ]
        if keyword_status == "enabled":
            human.append("Keywords: LLM enriched")
        elif keyword_status == "missing_config":
            human.append("Keywords: skipped (GEN_APIKEY/GEN_MODEL not configured)")
        if db.get("warnings"):
            human.append(f"Warnings: {len(db['warnings'])}")
        _emit_result(payload, as_json=as_json, human_lines=human)
    except Exception as e:
        logger.error("Asset DB build failed: {}", e)
        _emit_error(str(e), as_json=as_json, kind=type(e).__name__)


@main.command("asset-ingest")
@click.option("--output-root", default="./output", type=click.Path(file_okay=False), help="Output root containing session_* dirs")
@click.option("--library-dir", default=None, type=click.Path(file_okay=False), help="Reusable material library directory")
@click.option("--keywords/--no-keywords", default=True, show_default=True, help="Use the configured LLM to build matching keywords while ingesting")
@click.option("--keyword-batch-size", default=3, show_default=True, type=click.IntRange(1, 50), help="Assets per LLM keyword batch")
@click.option("--env-file", default=".env", help=".env file path used by --keywords and LIBRARY_DIR")
@click.option("--json", "as_json", is_flag=True, help="Emit command result as JSON")
def asset_ingest(
    output_root: str,
    library_dir: str | None,
    keywords: bool,
    keyword_batch_size: int,
    env_file: str,
    as_json: bool,
):
    """Copy AI-generated images from output sessions into the reusable library."""
    try:
        from edupptx.materials.ai_image_asset_db import (
            DEFAULT_MATCH_INDEX_FILENAME,
            ingest_ai_image_asset_library_from_output,
        )

        config = Config.from_env(env_file)
        target_library = Path(library_dir) if library_dir else config.library_dir

        keyword_client, keyword_status = _optional_keyword_client(config, enabled=keywords)

        db, target, report = ingest_ai_image_asset_library_from_output(
            output_root,
            target_library,
            keyword_client=keyword_client,
            keyword_batch_size=keyword_batch_size,
        )
        payload = {
            "ok": True,
            "db_path": str(target),
            "match_index_path": str(target.with_name(DEFAULT_MATCH_INDEX_FILENAME)),
            "output_root": report["output_root"],
            "library_dir": report["library_dir"],
            "session_count": report["session_count"],
            "processed_session_count": len(report["processed_sessions"]),
            "failed_session_count": len(report["failed_sessions"]),
            "asset_count": db.get("asset_count", 0),
            "warning_count": report["warning_count"],
            "keywords": keyword_status == "enabled",
            "keyword_status": keyword_status,
        }
        human = [
            f"Asset library: {report['library_dir']}",
            f"Asset DB: {target}",
            f"Match index: {target.with_name(DEFAULT_MATCH_INDEX_FILENAME)}",
            f"Sessions: {len(report['processed_sessions'])}/{report['session_count']}",
            f"Assets: {db.get('asset_count', 0)}",
        ]
        if keyword_status == "enabled":
            human.append("Keywords: LLM enriched")
        elif keyword_status == "missing_config":
            human.append("Keywords: skipped (GEN_APIKEY/GEN_MODEL not configured)")
        if report["failed_sessions"]:
            human.append(f"Failed sessions: {len(report['failed_sessions'])}")
        if report["warning_count"]:
            human.append(f"Warnings: {report['warning_count']}")
        _emit_result(payload, as_json=as_json, human_lines=human)
    except Exception as e:
        logger.error("Asset library ingest failed: {}", e)
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
