"""CLI entry point for edupptx."""

from __future__ import annotations

import logging
import sys

import click

from edupptx.config import Config
from edupptx.generator import generate


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(verbose: bool):
    """EduPPTX - AI-powered educational presentation generator."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@main.command()
@click.argument("topic")
@click.option("--requirements", "-r", default="", help="Additional requirements")
@click.option("--output", "-o", default=None, help="Output file path")
@click.option("--palette", "-p", default=None, help="Color palette (emerald/blue/violet/amber/rose/slate)")
@click.option("--env-file", default=".env", help="Path to .env file")
def gen(topic: str, requirements: str, output: str | None, palette: str | None, env_file: str):
    """Generate an educational presentation from a topic.

    Examples:

        edupptx gen "勾股定理"

        edupptx gen "光合作用" -r "适合高中生" -p blue -o biology.pptx
    """
    try:
        path = generate(
            topic=topic,
            requirements=requirements,
            output_path=output,
            palette=palette,
            env_path=env_file,
        )
        click.echo(f"Generated: {path}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
def palettes():
    """List available color palettes."""
    from edupptx.design_system import PALETTES
    for name, tokens in PALETTES.items():
        click.echo(f"  {name:10s}  accent={tokens.accent}  overlay={tokens.bg_overlay}")


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
