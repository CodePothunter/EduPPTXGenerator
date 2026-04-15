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
        debug: bool = False,
    ) -> Path:
        """Run the full pipeline. Returns session directory."""
        return asyncio.run(self._run_async(
            topic, requirements, file_path, research, style, review, debug,
        ))

    async def _run_async(
        self,
        topic: str,
        requirements: str,
        file_path: str | None,
        research: bool,
        style: str,
        review: bool,
        debug: bool,
    ) -> Path:
        session = Session(self.config.output_dir)
        logger.info("Session: {} (debug={})", session.dir, debug)

        # ── Phase 0: Input ──────────────────────────────────
        session.log_step("input", "Processing input")
        ctx = await self._phase0_input(topic, requirements, file_path, research)
        logger.info("Input ready: topic={}, has_doc={}, has_research={}",
                     ctx.topic, ctx.source_text is not None, ctx.research_summary is not None)

        # ── Phase 1a: Content Planning ──────────────────────
        session.log_step("planning", "Generating content plan")
        draft = self._phase1_planning(ctx)
        logger.info("Content plan: {} pages", len(draft.pages))

        # ── Phase 1b: Visual Planning ───────────────────────
        session.log_step("visual_planning", "Generating visual plan (colors, background)")
        draft.visual = self._phase1b_visual_planning(draft)
        session.save_plan(draft.model_dump())
        logger.info("Visual plan: primary={}, bg_prompt={}...",
                     draft.visual.primary_color, draft.visual.background_prompt[:40])

        if review:
            plan_path = session.dir / "plan.json"
            logger.info("Review mode: edit {} then run `edupptx render {}`", plan_path, plan_path)
            return session.dir

        # ── Phase 2: Background Generation ──────────────────
        session.log_step("background", "Generating unified background")
        bg_path = await self._phase2_background(draft.visual, session)

        # ── Phase 2b: Materials (skipped in debug mode) ─────
        if not debug:
            session.log_step("materials", "Fetching materials")
            all_assets = await self._phase2_materials(draft, session)
            # Attach background to all assets
            if bg_path:
                for assets in all_assets.values():
                    assets.background_path = bg_path
            logger.info("Materials ready for {} pages", len(all_assets))
        else:
            logger.info("Debug mode: skipping material fetch")
            all_assets = {
                p.page_number: SlideAssets(
                    page_number=p.page_number,
                    background_path=bg_path,
                )
                for p in draft.pages
            }

        # ── Phase 3: SVG Design ─────────────────────────────
        session.log_step("design", f"Generating SVG for {len(draft.pages)} pages")
        slides = await self._phase3_design(draft, all_assets, style, debug=debug)
        logger.info("Generated {} SVG slides", len(slides))

        # ── Phase 4: Validation + LLM Review ────────────────
        session.log_step("postprocess", "Validating + LLM reviewing SVGs")
        svg_paths = self._phase4_postprocess(
            slides, session, all_assets,
            draft=draft, do_review=True,
        )
        logger.info("Post-processed {} SVGs", len(svg_paths))

        # ── Phase 5: Output ─────────────────────────────────
        session.log_step("output", "Assembling PPTX")
        self._phase5_output(svg_paths, session)
        session.log_step("done", f"Saved {len(svg_paths)} slides to {session.output_path}")
        logger.info("Done! {} slides, output: {}", len(svg_paths), session.output_path)

        return session.dir

    async def run_from_plan(
        self, plan_path: Path, style: str = "edu_emerald", debug: bool = False,
    ) -> Path:
        """Resume from a saved planning draft (for `edupptx render`)."""
        import json
        with open(plan_path, encoding="utf-8") as f:
            data = json.load(f)
        draft = PlanningDraft.model_validate(data)

        session_dir = plan_path.parent
        session = Session.__new__(Session)
        session.dir = session_dir
        session.output_path = session_dir / "output.pptx"

        bg_path = await self._phase2_background(draft.visual, session)

        if not debug:
            all_assets = await self._phase2_materials(draft, session)
            if bg_path:
                for assets in all_assets.values():
                    assets.background_path = bg_path
        else:
            all_assets = {
                p.page_number: SlideAssets(page_number=p.page_number, background_path=bg_path)
                for p in draft.pages
            }

        slides = await self._phase3_design(draft, all_assets, style, debug=debug)
        svg_paths = self._phase4_postprocess(slides, session, all_assets, draft=draft, do_review=True)
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

    def _phase1b_visual_planning(self, draft: PlanningDraft):
        from edupptx.planning.visual_planner import generate_visual_plan
        return generate_visual_plan(draft, self.config)

    async def _phase2_background(self, visual, session: Session):
        from edupptx.materials.background_generator import generate_background
        return await generate_background(visual, self.config, session)

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
        debug: bool = False,
    ) -> list[GeneratedSlide]:
        from edupptx.design.svg_generator import generate_slide_svgs
        return await generate_slide_svgs(
            draft, all_assets, style, self.config, debug=debug,
        )

    def _phase4_postprocess(
        self, slides: list[GeneratedSlide], session: Session,
        all_assets: dict[int, SlideAssets] | None = None,
        draft: PlanningDraft | None = None,
        do_review: bool = False,
    ) -> list[Path]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from edupptx.postprocess.svg_sanitizer import sanitize_for_ppt
        from edupptx.postprocess.svg_validator import validate_and_fix

        slides_dir = session.dir / "slides"
        slides_dir.mkdir(exist_ok=True)

        # Build page lookup for review
        page_lookup: dict[int, "PagePlan"] = {}
        if draft:
            page_lookup = {p.page_number: p for p in draft.pages}

        def _process_one(slide: GeneratedSlide) -> tuple[int, Path]:
            # Step 1: Validate and fix
            fixed_svg, warnings = validate_and_fix(slide.svg_content)
            for w in warnings:
                logger.warning("Slide {}: {}", slide.page_number, w)

            # Step 2: LLM Review (parallel per slide)
            if do_review and draft and slide.page_number in page_lookup:
                from edupptx.postprocess.svg_reviewer import review_and_fix_svg
                fixed_svg = review_and_fix_svg(
                    fixed_svg, warnings,
                    page_lookup[slide.page_number],
                    draft.visual,
                    self.config,
                )

            # Step 3: Sanitize for PPT
            clean_svg = sanitize_for_ppt(fixed_svg)

            # Step 4: Inject real images
            if all_assets and slide.page_number in all_assets:
                clean_svg = self._inject_images(clean_svg, all_assets[slide.page_number])

            # Save
            path = slides_dir / f"slide_{slide.page_number:02d}.svg"
            path.write_text(clean_svg, encoding="utf-8")
            return slide.page_number, path

        # Parallel review: concurrency follows config
        max_workers = min(len(slides), self.config.llm_concurrency)
        results: dict[int, Path] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_process_one, slide): slide.page_number
                for slide in slides
            }
            for future in as_completed(futures):
                page_num = futures[future]
                try:
                    pn, path = future.result()
                    results[pn] = path
                except Exception:
                    logger.exception("Slide {} postprocess failed", page_num)

        # Return paths sorted by page number
        return [results[k] for k in sorted(results)]

    @staticmethod
    def _inject_images(svg_content: str, assets: SlideAssets) -> str:
        """Replace __IMAGE_XXX__ placeholders with base64 data URIs."""
        import base64
        import io
        for role, path in assets.image_paths.items():
            placeholder = f"__IMAGE_{role.upper()}__"
            if placeholder not in svg_content:
                continue
            try:
                from PIL import Image
                with Image.open(path) as img:
                    # Compress for SVG embedding: max 800px wide, JPEG quality 70
                    if img.width > 800:
                        ratio = 800 / img.width
                        img = img.resize((800, int(img.height * ratio)), Image.LANCZOS)
                    buf = io.BytesIO()
                    img.convert("RGB").save(buf, "JPEG", quality=70, optimize=True)
                    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    data_uri = f"data:image/jpeg;base64,{b64}"
                    svg_content = svg_content.replace(placeholder, data_uri)
                    logger.debug("Injected image for {} ({}KB)", role, len(b64) // 1024)
            except Exception as e:
                logger.warning("Failed to inject image {}: {}", role, e)
        return svg_content

    def _phase5_output(self, svg_paths: list[Path], session: Session) -> None:
        from edupptx.output.pptx_assembler import assemble_pptx
        assemble_pptx(svg_paths, session.output_path)
