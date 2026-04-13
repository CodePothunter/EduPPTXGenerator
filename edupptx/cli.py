"""CLI entry point for edupptx."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from loguru import logger

from edupptx.agent import PPTXAgent
from edupptx.config import Config
from edupptx.material_library import MaterialLibrary


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(verbose: bool):
    """EduPPTX - AI Agent 驱动的教育演示文稿生成器。"""
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
        level="DEBUG" if verbose else "INFO",
    )


@main.command()
@click.argument("topic")
@click.option("--requirements", "-r", default="", help="Additional requirements")
@click.option("--output", "-o", default="./output", type=click.Path(), help="Output directory")
@click.option("--palette", "-p", default=None, help="Color palette (emerald/blue/violet/amber/rose/slate)")
@click.option("--env-file", default=".env", help="Path to .env file")
def gen(topic: str, requirements: str, output: str, palette: str | None, env_file: str):
    """Generate an educational presentation from a topic.

    Examples:

        edupptx gen "勾股定理"

        edupptx gen "光合作用" -r "适合高中生" -p blue -o output/
    """
    try:
        config = Config.from_env(env_file)
        config.output_dir = Path(output)
        if palette:
            requirements += f"\npalette: {palette}"

        agent = PPTXAgent(config)
        # Note: session is created inside agent.run(), so file logger is added by agent
        session_dir = agent.run(topic, requirements)

        click.echo(f"✓ Output: {session_dir / 'output.pptx'}")
        click.echo(f"✓ Thinking log: {session_dir / 'thinking.jsonl'}")
    except Exception as e:
        logger.error("Generation failed: {}", e)
        sys.exit(1)


@main.group()
def library():
    """Manage the material library."""
    pass


@library.command("list")
@click.option("--type", "mat_type", default=None, help="Filter by type (background/diagram/illustration)")
@click.option("--env-file", default=".env", help="Path to .env file")
def library_list(mat_type: str | None, env_file: str):
    """List all materials in the library."""
    config = Config.from_env(env_file)
    lib = MaterialLibrary(config.library_dir)
    entries = lib.list_all(type=mat_type)
    if not entries:
        click.echo("Library is empty.")
        return
    for entry in entries:
        click.echo(f"  [{entry.type}] {entry.id}: {entry.description} (tags: {', '.join(entry.tags)})")


@library.command("search")
@click.option("--tags", required=True, help="Comma-separated tags to search for")
@click.option("--type", "mat_type", default=None, help="Filter by type")
@click.option("--palette", default=None, help="Boost results matching this palette")
@click.option("--env-file", default=".env", help="Path to .env file")
def library_search(tags: str, mat_type: str | None, palette: str | None, env_file: str):
    """Search materials by tags."""
    config = Config.from_env(env_file)
    lib = MaterialLibrary(config.library_dir)
    results = lib.search(tags=tags.split(","), type=mat_type, palette=palette)
    if not results:
        click.echo("No matches found.")
        return
    for entry in results:
        click.echo(f"  [{entry.type}] {entry.id}: {entry.description}")


@library.command("stats")
@click.option("--env-file", default=".env", help="Path to .env file")
def library_stats(env_file: str):
    """Show library statistics."""
    config = Config.from_env(env_file)
    lib = MaterialLibrary(config.library_dir)
    summary = lib.summary()
    click.echo(f"Total materials: {summary['total']}")
    for type_name, count in summary["by_type"].items():
        click.echo(f"  {type_name}: {count}")


@main.command()
def palettes():
    """List available style themes."""
    from edupptx.style_schema import load_style
    styles_dir = Path(__file__).parent.parent / "styles"
    if not styles_dir.exists():
        click.echo("No styles directory found.")
        return
    for f in sorted(styles_dir.glob("*.json")):
        schema = load_style(f)
        accent = schema.global_tokens.palette.get("accent", "?")
        bg = schema.global_tokens.palette.get("bg", "?")
        click.echo(f"  {f.stem:10s}  accent={accent}  bg={bg}  — {schema.meta.description}")


@main.command()
def icons():
    """List available icon names."""
    from edupptx.icons import list_icons
    names = list_icons()
    click.echo(f"Available icons ({len(names)}):")
    for i in range(0, len(names), 8):
        row = ", ".join(names[i : i + 8])
        click.echo(f"  {row}")


if __name__ == "__main__":
    main()
