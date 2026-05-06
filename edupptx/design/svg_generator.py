"""Parallel SVG generation driven by template family + page template references."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable

from loguru import logger

from edupptx.config import Config
from edupptx.design.prompts import build_svg_system_prompt, build_svg_user_prompt
from edupptx.design.template_router import load_style_guide, resolve_template_family
from edupptx.llm_client import create_llm_client
from edupptx.models import GeneratedSlide, PlanningDraft, SlideAssets


def _load_style_guide_text(template_family: str, style_name: str, config: Config) -> str:
    """Load `style_guide.md` from the selected template folder, with legacy fallback."""

    content = load_style_guide(template_family)
    if content:
        return content

    legacy_template_path = config.styles_dir / f"{style_name}.svg"
    if not legacy_template_path.exists():
        logger.warning("Style guide not found for template_family={} or style_name={}", template_family, style_name)
        return ""

    content = legacy_template_path.read_text(encoding="utf-8")
    if len(content) > 8000:
        content = content[:8000] + "\n<!-- ... truncated ... -->"
    return content


def _extract_svg(response: str) -> str:
    """Extract SVG markup from one LLM response."""

    match = re.search(r"```svg\s*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    match = re.search(r"```xml\s*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    match = re.search(r"(<svg[\s\S]*?</svg>)", response)
    if match:
        return match.group(1).strip()

    logger.warning("Failed to extract SVG from model response; returning raw content")
    return response.strip()


def _failure_svg(page_number: int) -> str:
    return (
        '<svg viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">'
        f'<text x="640" y="360" text-anchor="middle" font-size="24" fill="#999">'
        f'Slide {page_number} generation failed'
        "</text></svg>"
    )


def _generate_one(
    client,
    system_prompt: str,
    page_plan,
    assets: SlideAssets,
    total_pages: int,
    reference_svg: str | None = None,
    template_family: str = "复用",
    debug: bool = False,
) -> GeneratedSlide:
    """Generate one SVG slide synchronously for thread-pool execution."""

    user_prompt = build_svg_user_prompt(
        page_plan,
        assets,
        total_pages,
        reference_svg=reference_svg,
        template_family=template_family,
        debug=debug,
    )
    logger.info("Generating slide {}/{} SVG", page_plan.page_number, total_pages)

    last_err = None
    for attempt in range(2):
        try:
            response = client.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=16384,
            )
            break
        except Exception as exc:
            last_err = exc
            if attempt == 0:
                logger.warning("Slide {} SVG generation failed, retrying: {}", page_plan.page_number, str(exc)[:80])
    else:
        raise last_err

    svg_content = _extract_svg(str(response))
    logger.info("Slide {} SVG generated, {} chars", page_plan.page_number, len(svg_content))
    return GeneratedSlide(page_number=page_plan.page_number, svg_content=svg_content)


async def generate_slide_svgs(
    draft: PlanningDraft,
    all_assets: dict[int, SlideAssets],
    style_name: str,
    config: Config,
    debug: bool = False,
    on_slide: Callable[[GeneratedSlide], None] | None = None,
    design_md: str | None = None,
) -> list[GeneratedSlide]:
    """Generate SVG slides, sequentially for reveal pages and in parallel otherwise."""

    client = create_llm_client(config, web_search=False)
    template_family = draft.style_routing.template_family or resolve_template_family(draft, client)
    style_guide = _load_style_guide_text(template_family, style_name, config)
    if not style_guide:
        logger.warning("No style guide loaded for template_family={} style_name={}", template_family, style_name)

    system_prompt = build_svg_system_prompt(
        style_guide,
        visual_plan=draft.visual,
        content_density=draft.visual.content_density,
        design_md=design_md,
    )

    logger.info(
        "Style routing: style_name={}, template_family={}, palette_id={}",
        draft.style_routing.style_name,
        template_family,
        draft.style_routing.palette_id,
    )

    total_pages = len(draft.pages)
    results: list[GeneratedSlide] = []
    has_reveal_pages = any(getattr(page, "reveal_from_page", None) for page in draft.pages)

    if has_reveal_pages:
        logger.info("Reveal pages detected; switching to sequential SVG generation")
        generated_svgs: dict[int, str] = {}
        for page in sorted(draft.pages, key=lambda item: item.page_number):
            page_assets = all_assets.get(page.page_number, SlideAssets(page_number=page.page_number))
            reference_svg = None
            if page.reveal_from_page is not None:
                reference_svg = generated_svgs.get(page.reveal_from_page)
                if reference_svg is None:
                    logger.warning(
                        "Slide {} references reveal_from_page={} but source SVG is unavailable",
                        page.page_number,
                        page.reveal_from_page,
                    )
            try:
                slide = _generate_one(
                    client,
                    system_prompt,
                    page,
                    page_assets,
                    total_pages,
                    reference_svg=reference_svg,
                    template_family=template_family,
                    debug=debug,
                )
                results.append(slide)
                generated_svgs[page.page_number] = slide.svg_content
                if on_slide is not None:
                    on_slide(slide)
            except Exception:
                logger.exception("Slide {} SVG generation failed", page.page_number)
                fallback_svg = _failure_svg(page.page_number)
                slide = GeneratedSlide(page_number=page.page_number, svg_content=fallback_svg)
                results.append(slide)
                generated_svgs[page.page_number] = fallback_svg
                if on_slide is not None:
                    on_slide(slide)
    else:
        max_workers = min(total_pages, config.llm_concurrency)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_page = {}
            for page in draft.pages:
                page_assets = all_assets.get(page.page_number, SlideAssets(page_number=page.page_number))
                future = executor.submit(
                    _generate_one,
                    client,
                    system_prompt,
                    page,
                    page_assets,
                    total_pages,
                    reference_svg=None,
                    template_family=template_family,
                    debug=debug,
                )
                future_to_page[future] = page.page_number

            for future in as_completed(future_to_page):
                page_num = future_to_page[future]
                try:
                    slide = future.result()
                    results.append(slide)
                    if on_slide is not None:
                        on_slide(slide)
                except Exception:
                    logger.exception("Slide {} SVG generation failed", page_num)
                    slide = GeneratedSlide(page_number=page_num, svg_content=_failure_svg(page_num))
                    results.append(slide)
                    if on_slide is not None:
                        on_slide(slide)

    results.sort(key=lambda slide: slide.page_number)
    logger.info("SVG generation completed: {} slides", len(results))
    return results
