"""CLI entry point for edupptx V2."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from loguru import logger

from edupptx.agent import PPTXAgent
from edupptx.config import Config


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(verbose: bool):
    """EduPPTX - AI 驱动的教育演示文稿生成器 (V2 SVG Pipeline)"""
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
        level="DEBUG" if verbose else "INFO",
    )


@main.command()
@click.argument("topic")
@click.option("--requirements", "-r", default="", help="附加要求")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="输入文档 (PDF/Word/MD/TXT)")
@click.option("--research", is_flag=True, help="启用联网搜索充实内容")
@click.option("--style", "-s", default="edu_emerald", help="风格模板名称")
@click.option("--review", is_flag=True, help="策划稿生成后暂停，供审核编辑")
@click.option("--output", "-o", default="./output", type=click.Path(), help="输出目录")
@click.option("--env-file", default=".env", help=".env 文件路径")
def gen(topic: str, requirements: str, file_path: str | None, research: bool,
        style: str, review: bool, output: str, env_file: str):
    """从主题生成教育演示文稿。

    示例：
        edupptx gen "勾股定理"
        edupptx gen "光合作用" -r "适合高中生" --style edu_academic
        edupptx gen --file report.pdf "基于报告做汇报" --research
        edupptx gen "年度总结" --review
    """
    try:
        config = Config.from_env(env_file)
        config.output_dir = Path(output)

        agent = PPTXAgent(config)
        session_dir = agent.run(
            topic, requirements,
            file_path=file_path,
            research=research,
            style=style,
            review=review,
        )

        if review:
            click.echo(f"策划稿已生成: {session_dir / 'plan.json'}")
            click.echo(f"审核编辑后运行: edupptx render {session_dir / 'plan.json'}")
        else:
            click.echo(f"输出: {session_dir / 'output.pptx'}")
            click.echo(f"SVG: {session_dir / 'slides/'}")

    except Exception as e:
        logger.error("Generation failed: {}", e)
        raise click.ClickException(str(e))


@main.command()
@click.argument("plan_path", type=click.Path(exists=True))
@click.option("--style", "-s", default="edu_emerald", help="风格模板名称")
@click.option("--env-file", default=".env", help=".env 文件路径")
def render(plan_path: str, style: str, env_file: str):
    """从策划稿 JSON 渲染 SVG + PPTX。

    示例：
        edupptx render output/session_xxx/plan.json
        edupptx render plan.json --style edu_tech
    """
    try:
        config = Config.from_env(env_file)
        agent = PPTXAgent(config)
        session_dir = asyncio.run(agent.run_from_plan(Path(plan_path), style))
        click.echo(f"输出: {session_dir / 'output.pptx'}")
    except Exception as e:
        logger.error("Render failed: {}", e)
        raise click.ClickException(str(e))


@main.command()
@click.argument("topic")
@click.option("--requirements", "-r", default="", help="附加要求")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="输入文档")
@click.option("--research", is_flag=True, help="启用联网搜索")
@click.option("--output", "-o", default="./output", type=click.Path(), help="输出目录")
@click.option("--env-file", default=".env", help=".env 文件路径")
def plan(topic: str, requirements: str, file_path: str | None, research: bool,
         output: str, env_file: str):
    """只生成策划稿，不渲染。

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
        click.echo(f"策划稿: {session_dir / 'plan.json'}")
    except Exception as e:
        logger.error("Planning failed: {}", e)
        raise click.ClickException(str(e))


@main.command()
def styles():
    """列出可用的风格模板。"""
    styles_dir = Path(__file__).parent / "design" / "style_templates"
    if not styles_dir.exists():
        click.echo("No style templates found.")
        return
    for f in sorted(styles_dir.glob("*.svg")):
        click.echo(f"  {f.stem}")


if __name__ == "__main__":
    main()
