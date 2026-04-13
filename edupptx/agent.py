"""V2 Agent: 5-phase SVG pipeline orchestrator."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from loguru import logger

from edupptx.config import Config
from edupptx.models import (
    GeneratedSlide,
    InputContext,
    PlanningDraft,
    SlideAssets,
)
from edupptx.session import Session


class PPTXAgent:
    """Orchestrates the 5-phase SVG pipeline."""

    def __init__(self, config: Config):
        self.config = config

    def run(
        self,
        topic: str,
        requirements: str = "",
        file_path: str | None = None,
        research: bool = False,
        style: str = "edu_emerald",
        review: bool = False,
    ) -> Path:
        """Run the full pipeline. Returns session directory."""
        return asyncio.run(self._run_async(
            topic, requirements, file_path, research, style, review,
        ))

    async def _run_async(
        self,
        topic: str,
        requirements: str,
        file_path: str | None,
        research: bool,
        style: str,
        review: bool,
    ) -> Path:
        session = Session(self.config.output_dir)
        logger.info("Session: {}", session.dir)

        # ── Phase 0: Input ──────────────────────────────────
        session.log_step("input", "Processing input")
        ctx = await self._phase0_input(topic, requirements, file_path, research)
        logger.info("Input ready: topic={}, has_doc={}, has_research={}",
                     ctx.topic, ctx.source_text is not None, ctx.research_summary is not None)

        # ── Phase 1: Planning ───────────────────────────────
        session.log_step("planning", "Generating planning draft")
        draft = self._phase1_planning(ctx)
        session.save_plan(draft.model_dump())
        logger.info("Planning draft: {} pages", len(draft.pages))

        if review:
            plan_path = session.dir / "plan.json"
            logger.info("Review mode: edit {} then run `edupptx render {}`", plan_path, plan_path)
            return session.dir

        # ── Phase 2: Materials ──────────────────────────────
        session.log_step("materials", "Fetching materials")
        all_assets = await self._phase2_materials(draft, session)
        logger.info("Materials ready for {} pages", len(all_assets))

        # ── Phase 3: SVG Design ─────────────────────────────
        session.log_step("design", f"Generating SVG for {len(draft.pages)} pages")
        slides = await self._phase3_design(draft, all_assets, style)
        logger.info("Generated {} SVG slides", len(slides))

        # ── Phase 4: Post-processing ────────────────────────
        session.log_step("postprocess", "Validating and fixing SVGs")
        svg_paths = self._phase4_postprocess(slides, session)
        logger.info("Post-processed {} SVGs", len(svg_paths))

        # ── Phase 5: Output ─────────────────────────────────
        session.log_step("output", "Assembling PPTX")
        self._phase5_output(svg_paths, session)
        session.log_step("done", f"Saved {len(svg_paths)} slides to {session.output_path}")
        logger.info("Done! {} slides, output: {}", len(svg_paths), session.output_path)

        return session.dir

    async def run_from_plan(self, plan_path: Path, style: str = "edu_emerald") -> Path:
        """Resume from a saved planning draft (for `edupptx render`)."""
        import json
        with open(plan_path, encoding="utf-8") as f:
            data = json.load(f)
        draft = PlanningDraft.model_validate(data)

        session_dir = plan_path.parent
        session = Session.__new__(Session)
        session.dir = session_dir
        session.output_path = session_dir / "output.pptx"

        all_assets = await self._phase2_materials(draft, session)
        slides = await self._phase3_design(draft, all_assets, style)
        svg_paths = self._phase4_postprocess(slides, session)
        self._phase5_output(svg_paths, session)
        logger.info("Rendered {} slides from plan", len(svg_paths))
        return session_dir

    # ── Phase implementations ───────────────────────────────

    async def _phase0_input(
        self, topic: str, requirements: str,
        file_path: str | None, research: bool,
    ) -> InputContext:
        source_text = None
        research_summary = None

        if file_path:
            from edupptx.input.document_parser import parse_document
            source_text = parse_document(file_path)

        if research and self.config.tavily_api_key:
            from edupptx.input.web_researcher import research_topic
            research_summary = await research_topic(
                topic, self.config.tavily_api_key,
            )

        return InputContext(
            topic=topic,
            source_text=source_text,
            research_summary=research_summary,
            requirements=requirements,
        )

    def _phase1_planning(self, ctx: InputContext) -> PlanningDraft:
        from edupptx.planning.content_planner import generate_planning_draft
        return generate_planning_draft(ctx, self.config)

    async def _phase2_materials(
        self, draft: PlanningDraft, session: Session,
    ) -> dict[int, SlideAssets]:
        from edupptx.materials.image_provider import fetch_images

        all_assets: dict[int, SlideAssets] = {}
        materials_dir = session.dir / "materials"
        materials_dir.mkdir(exist_ok=True)

        for page in draft.pages:
            assets = SlideAssets(page_number=page.page_number)

            # Fetch images if needed
            if page.material_needs.images:
                fetched = await fetch_images(page.material_needs.images, self.config)
                for role, result in fetched.items():
                    if result and result.local_path and result.local_path.exists():
                        dest = materials_dir / result.local_path.name
                        shutil.copy2(result.local_path, dest)
                        assets.image_paths[role] = dest

            # Collect icon SVGs
            if page.material_needs.icons:
                from edupptx.materials.icons import get_icon_svg
                for icon_name in page.material_needs.icons:
                    try:
                        svg_str = get_icon_svg(icon_name)
                        assets.icon_svgs[icon_name] = svg_str
                    except Exception:
                        logger.warning("Icon not found: {}", icon_name)

            all_assets[page.page_number] = assets

        return all_assets

    async def _phase3_design(
        self, draft: PlanningDraft,
        all_assets: dict[int, SlideAssets],
        style: str,
    ) -> list[GeneratedSlide]:
        from edupptx.design.svg_generator import generate_slide_svgs
        return await generate_slide_svgs(draft, all_assets, style, self.config)

    def _phase4_postprocess(
        self, slides: list[GeneratedSlide], session: Session,
    ) -> list[Path]:
        from edupptx.postprocess.svg_validator import validate_and_fix
        from edupptx.postprocess.svg_sanitizer import sanitize_for_ppt

        slides_dir = session.dir / "slides"
        slides_dir.mkdir(exist_ok=True)
        svg_paths: list[Path] = []

        for slide in sorted(slides, key=lambda s: s.page_number):
            # Validate and fix
            fixed_svg, warnings = validate_and_fix(slide.svg_content)
            for w in warnings:
                logger.warning("Slide {}: {}", slide.page_number, w)

            # Sanitize for PPT
            clean_svg = sanitize_for_ppt(fixed_svg)

            # Save
            path = slides_dir / f"slide_{slide.page_number:02d}.svg"
            path.write_text(clean_svg, encoding="utf-8")
            svg_paths.append(path)

        return svg_paths

    def _phase5_output(self, svg_paths: list[Path], session: Session) -> None:
        from edupptx.output.pptx_assembler import assemble_pptx
        assemble_pptx(svg_paths, session.output_path)
