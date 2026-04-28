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


_STYLE_DESCRIPTIONS = {
    "edu_emerald": "翠绿主调，自然/生命科学/语文人文",
    "edu_academic": "深蓝学术，物理/数学/严谨理科",
    "edu_minimal": "极简灰白，通用/演示/职场汇报",
    "edu_tech": "科技深色，计算机/工程/前沿话题",
    "edu_warm": "暖橙色调，低龄/小学/活泼主题",
}


@main.command()
@click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出（含描述）")
def styles(as_json: bool):
    """列出可用的风格模板及其适用场景。"""
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


if __name__ == "__main__":
    main()
