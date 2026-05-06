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
    PagePlan,
    PlanningDraft,
    SlideAssets,
    iter_image_slot_keys,
)
from edupptx.session import Session


def _uses_structured_table(page: PagePlan | None) -> bool:
    if page is None:
        return False
    return page.layout_hint == "comparison"


def _needs_llm_review(page: PagePlan | None, warnings: list[str]) -> bool:
    """Check if warnings are serious enough to warrant LLM review."""
    if not warnings:
        return False

    if _uses_structured_table(page):
        severe_patterns = (
            "SVG parse error",
            "Recovered by wrapping",
        )
        return any(
            w.startswith(pattern)
            for w in warnings
            for pattern in severe_patterns
        )
    # Minor warnings that validator already auto-fixed — skip review
    minor_patterns = (
        "Wrapped long text",
        "Fixed text overlap",
        "Expanded card height",
        "Adjusted image to fit card bounds",
        "PPT 对 SVG clipPath",
        "Trimmed subtitle",
        "Replaced unsafe font",
        "Clamped",
        "viewBox fixed",
        "Fixed <circle>",
    )
    for w in warnings:
        if not any(w.startswith(p) for p in minor_patterns):
            return True  # Has at least one non-minor warning
    return False  # All warnings are minor auto-fixes


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
        session.log_step("planning_stage1", "Generating stage-1 content outline without template constraints")
        draft = self._phase1_outline_planning(ctx)
        logger.info("Stage-1 outline: {} pages", len(draft.pages))

        session.log_step("template_routing", "Selecting template family and palette from outline")
        routing, manifest, palette = self._phase1a_template_routing(draft)
        draft.style_routing = routing
        logger.info(
            "Template routing: style_name={}, template_family={}, palette_id={}, resolved_by={}",
            routing.style_name,
            routing.template_family,
            routing.palette_id,
            routing.resolved_by,
        )

        session.log_step("page_variant_assignment", "Matching template variants to outline pages")
        draft = self._phase1b_page_variant_assignment(draft, manifest)

        session.log_step("planning_stage2", "Refining outline with matched template references")
        draft = self._phase1c_planning_refinement(draft, manifest)
        draft.style_routing = routing
        logger.info("Stage-2 refined draft: {} pages before reveal expansion", len(draft.pages))

        session.log_step("reveal_expansion", "Expanding pseudo-animation reveal pages")
        draft = self._phase1d_finalize_reveals(draft)
        draft.style_routing = routing

        session.log_step("page_variant_assignment", "Re-assigning template variants after refinement")
        draft = self._phase1b_page_variant_assignment(draft, manifest)

        # ── Phase 1e: Visual Planning ───────────────────────
        session.log_step("visual_planning", "Generating visual plan with palette reference")
        draft.visual = self._phase1e_visual_planning(
            draft,
            palette_hint=palette,
            template_label=manifest.label,
        )

        # ── Phase 1e+: optional DESIGN.md artifact (gated by env) ──
        design_md_str = self._phase1e_design_md(
            draft,
            palette_hint=palette,
            template_label=manifest.label,
        )
        if design_md_str:
            (session.dir / "DESIGN.md").write_text(design_md_str, encoding="utf-8")
            logger.info("DESIGN.md written: {}", session.dir / "DESIGN.md")
            # v3.2: lint immediately so broken-ref / contrast errors surface here.
            # On failure, drop design_md so Phase 3 falls back to the legacy path
            # rather than aborting the whole run.
            try:
                from edupptx.style.design_md import parse_design_md
                from edupptx.style_resolver import resolve_style
                resolve_style(parse_design_md(design_md_str))
                logger.info("DESIGN.md lint passed")
            except Exception as exc:
                logger.warning(
                    "DESIGN.md lint failed, Phase 3 will skip DESIGN.md injection: {}",
                    str(exc)[:160],
                )
                design_md_str = None

        session.log_step("template_alignment", "Aligning plan to selected template contracts")
        draft = self._phase1f_template_alignment(draft, manifest)
        session.save_plan(draft.model_dump())
        self._save_design_spec(draft, session)
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
            session.log_step("asset_library", "Updating reusable AI image asset library")
            self._phase2c_asset_library(session)
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
        slides = await self._phase3_design(
            draft,
            all_assets,
            draft.style_routing.style_name or style,
            session,
            debug=debug,
            design_md=design_md_str,
        )
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
        speaker_notes = [
            page.notes for page in sorted(draft.pages, key=lambda p: p.page_number)
        ]
        self._phase5_output(
            svg_paths, session, bg_path=bg_path, speaker_notes=speaker_notes,
        )
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
        draft = self._ensure_template_state(draft)

        session = Session.from_existing(plan_path.parent)
        session_dir = session.dir

        # v3.2: pick up an existing DESIGN.md so user edits flow into Phase 3.
        # Lint-fail downgrades to legacy path rather than aborting the render.
        design_md_str: str | None = None
        design_md_path = plan_path.parent / "DESIGN.md"
        if design_md_path.exists():
            design_md_str = design_md_path.read_text(encoding="utf-8")
            try:
                from edupptx.style.design_md import parse_design_md
                from edupptx.style_resolver import resolve_style
                resolve_style(parse_design_md(design_md_str))
                logger.info("DESIGN.md found and lint-passed: {}", design_md_path)
            except Exception as exc:
                logger.warning(
                    "DESIGN.md at {} failed lint, Phase 3 will skip injection: {}",
                    design_md_path, str(exc)[:160],
                )
                design_md_str = None

        bg_path = await self._phase2_background(draft.visual, session)

        if not debug:
            all_assets = await self._phase2_materials(draft, session)
            if bg_path:
                for assets in all_assets.values():
                    assets.background_path = bg_path
            session.log_step("asset_library", "Updating reusable AI image asset library")
            self._phase2c_asset_library(session)
        else:
            all_assets = {
                p.page_number: SlideAssets(page_number=p.page_number, background_path=bg_path)
                for p in draft.pages
            }

        slides = await self._phase3_design(
            draft,
            all_assets,
            draft.style_routing.style_name or style,
            session,
            debug=debug,
            design_md=design_md_str,
        )
        svg_paths = self._phase4_postprocess(slides, session, all_assets, draft=draft, do_review=True)
        speaker_notes = [
            page.notes for page in sorted(draft.pages, key=lambda p: p.page_number)
        ]
        self._phase5_output(
            svg_paths, session, bg_path=bg_path, speaker_notes=speaker_notes,
        )
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

    def _build_llm_client(self):
        from edupptx.llm_client import create_llm_client

        if not self.config.llm_api_key or not self.config.llm_model:
            return None
        try:
            return create_llm_client(self.config, web_search=False)
        except Exception as exc:
            logger.warning("LLM client unavailable: {}", str(exc)[:120])
            return None

    def _phase1a_template_routing(self, draft: PlanningDraft):
        from edupptx.design.template_router import resolve_style_routing

        client = self._build_llm_client()
        return resolve_style_routing(draft, client=client)

    def _phase1_outline_planning(self, ctx: InputContext) -> PlanningDraft:
        from edupptx.planning.content_planner import generate_planning_outline

        return generate_planning_outline(ctx, self.config)

    def _phase1b_page_variant_assignment(self, draft: PlanningDraft, manifest) -> PlanningDraft:
        from edupptx.design.template_router import assign_page_template_variants

        client = self._build_llm_client()
        return assign_page_template_variants(draft, manifest, client=client)

    def _phase1c_planning_refinement(self, draft: PlanningDraft, manifest) -> PlanningDraft:
        from edupptx.planning.content_planner import refine_planning_draft

        return refine_planning_draft(draft, manifest, self.config)

    def _phase1d_finalize_reveals(self, draft: PlanningDraft) -> PlanningDraft:
        from edupptx.planning.content_planner import finalize_reveal_pages

        return finalize_reveal_pages(draft)

    def _phase1e_visual_planning(self, draft: PlanningDraft, palette_hint=None, template_label: str = ""):
        from edupptx.planning.visual_planner import generate_visual_plan
        return generate_visual_plan(
            draft,
            self.config,
            palette_hint=palette_hint,
            template_label=template_label,
        )

    def _phase1e_design_md(
        self,
        draft: PlanningDraft,
        palette_hint=None,
        template_label: str = "",
    ) -> str | None:
        """Generate a DESIGN.md artifact for this session. Returns None on full failure
        or when the env opt-in flag is unset. Strictly additive — never blocks Phase 2/3.
        """
        import os
        if os.environ.get("EDUPPTX_VISUAL_PLANNER_FORMAT", "json") != "design_md":
            return None
        try:
            from edupptx.planning.visual_planner import generate_design_md
            return generate_design_md(
                draft,
                self.config,
                palette_hint=palette_hint,
                template_label=template_label,
            )
        except Exception as exc:
            logger.warning(
                "Failed to generate DESIGN.md, falling back to JSON-only flow: {}",
                str(exc)[:120],
            )
            return None

    def _phase1f_template_alignment(self, draft: PlanningDraft, manifest) -> PlanningDraft:
        from edupptx.design.template_router import align_draft_to_template

        return align_draft_to_template(draft, manifest)

    def _ensure_template_state(self, draft: PlanningDraft) -> PlanningDraft:
        from edupptx.design.template_router import (
            assign_page_template_variants,
            load_style_manifest,
            resolve_style_routing,
            resolve_palette_preset,
            align_draft_to_template,
        )

        client = self._build_llm_client()
        manifest = None
        palette = None
        if draft.style_routing.template_family:
            manifest = load_style_manifest(draft.style_routing.template_family)
            if manifest is not None:
                palette = resolve_palette_preset(
                    manifest,
                    routing_text=self._collect_template_routing_text(draft),
                    preferred_palette_id=draft.style_routing.palette_id,
                )

        if manifest is None:
            routing, manifest, palette = resolve_style_routing(draft, client=client)
            draft.style_routing = routing

        assign_page_template_variants(draft, manifest, client=client)
        align_draft_to_template(draft, manifest)
        return draft

    def _collect_template_routing_text(self, draft: PlanningDraft) -> str:
        from edupptx.design.template_router import collect_template_routing_text

        return collect_template_routing_text(draft)

    def _save_design_spec(self, draft: PlanningDraft, session: Session) -> None:
        """Save human-readable design specification for audit and debugging."""
        v = draft.visual
        m = draft.meta
        r = draft.style_routing
        lines = [
            "# Design Specification",
            "",
            f"**主题**: {m.topic}",
            f"**受众**: {m.audience}",
            f"**目的**: {m.purpose}",
            f"**风格方向**: {m.style_direction}",
            f"**总页数**: {m.total_pages}",
            "",
            "## 配色方案",
            "",
            "| 角色 | 色值 |",
            "|------|------|",
            f"| 主色 (primary) | `{v.primary_color}` |",
            f"| 辅色 (secondary) | `{v.secondary_color}` |",
            f"| 强调色 (accent) | `{v.accent_color}` |",
            f"| 卡片背景 | `{v.card_bg_color}` |",
            f"| 次背景 | `{v.secondary_bg_color}` |",
            f"| 正文色 | `{v.text_color}` |",
            f"| 标题色 | `{v.heading_color}` |",
            "",
            "## 内容密度",
            "",
            f"模式: **{v.content_density}**",
            "",
            "## 页面规划",
            "",
            "| # | 类型 | 标题 | 布局 |",
            "|---|------|------|------|",
        ]
        for p in draft.pages:
            lines.append(f"| {p.page_number} | {p.page_type} | {p.title} | {p.layout_hint} |")

        spec_path = session.dir / "design_spec.md"
        spec_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Design spec saved: {}", spec_path)

    async def _phase2_background(self, visual, session: Session):
        from edupptx.materials.background_generator import generate_background
        return await generate_background(visual, self.config, session)

    async def _phase2_materials(
        self, draft: PlanningDraft, session: Session,
    ) -> dict[int, SlideAssets]:
        from edupptx.materials.image_provider import fetch_images
        from edupptx.materials.image_prompt_router import build_routed_image_needs

        all_assets: dict[int, SlideAssets] = {}
        materials_dir = session.dir / "materials"
        materials_dir.mkdir(exist_ok=True)

        for page in draft.pages:
            assets = SlideAssets(page_number=page.page_number)

            # Fetch images if needed
            if page.material_needs.images:
                routed_image_needs = build_routed_image_needs(draft, page)
                fetched = await fetch_images(routed_image_needs, self.config)
                for (slot_key, _need), result in zip(iter_image_slot_keys(routed_image_needs), fetched):
                    if result and result.local_path and result.local_path.exists():
                        suffix = result.local_path.suffix.lower() or ".img"
                        dest = materials_dir / f"page_{page.page_number:02d}_{slot_key}{suffix}"
                        shutil.copy2(result.local_path, dest)
                        assets.image_paths[slot_key] = dest

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

    def _phase2c_asset_library(self, session: Session) -> None:
        """Ingest this session's newly generated AI images into the reusable library."""

        try:
            from edupptx.materials.ai_image_asset_db import update_ai_image_asset_library

            keyword_client = self._build_llm_client()
            db, target = update_ai_image_asset_library(
                session.dir,
                self.config.library_dir,
                keyword_client=keyword_client,
            )
            logger.info(
                "AI image asset library updated: {} ({} assets)",
                target,
                db.get("asset_count", 0),
            )
            if db.get("warnings"):
                logger.warning("AI image asset library warnings: {}", len(db["warnings"]))
        except Exception as exc:
            logger.warning("AI image asset library update skipped: {}", str(exc)[:160])

    async def _phase3_design(
        self, draft: PlanningDraft,
        all_assets: dict[int, SlideAssets],
        style: str,
        session: Session,
        debug: bool = False,
        design_md: str | None = None,
    ) -> list[GeneratedSlide]:
        from edupptx.design.svg_generator import generate_slide_svgs

        slides_raw_dir = session.dir / "slides_raw"
        slides_raw_dir.mkdir(exist_ok=True)

        def _save_raw_slide(slide: GeneratedSlide) -> None:
            raw_path = slides_raw_dir / f"slide_{slide.page_number:02d}.svg"
            raw_path.write_text(slide.svg_content, encoding="utf-8")
            logger.info("Slide {} raw SVG saved: {}", slide.page_number, raw_path)

        return await generate_slide_svgs(
            draft,
            all_assets,
            style,
            self.config,
            debug=debug,
            on_slide=_save_raw_slide,
            design_md=design_md,
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
        slides_raw_dir = session.dir / "slides_raw"
        slides_raw_dir.mkdir(exist_ok=True)

        # Build page lookup for review
        page_lookup: dict[int, "PagePlan"] = {}
        if draft:
            page_lookup = {p.page_number: p for p in draft.pages}

        def _process_one(slide: GeneratedSlide) -> tuple[int, Path]:
            # Save raw LLM output for debugging
            raw_path = slides_raw_dir / f"slide_{slide.page_number:02d}.svg"
            raw_path.write_text(slide.svg_content, encoding="utf-8")

            # Step 1: Validate and fix
            page = page_lookup.get(slide.page_number)
            fixed_svg, warnings = validate_and_fix(slide.svg_content, page=page)
            for w in warnings:
                logger.warning("Slide {}: {}", slide.page_number, w)

            # Step 2: LLM Review (only if meaningful warnings exist)
            if do_review and draft and page is not None and _needs_llm_review(page, warnings):
                from edupptx.postprocess.svg_reviewer import review_and_fix_svg
                fixed_svg = review_and_fix_svg(
                    fixed_svg, warnings,
                    page,
                    draft.visual,
                    self.config,
                )
            elif do_review and warnings:
                logger.debug("Slide {}: skipping LLM review (only minor auto-fixes)", slide.page_number)

            # Step 3: Sanitize for PPT
            clean_svg = sanitize_for_ppt(fixed_svg)

            # Step 3.3: Render LaTeX formulas
            from edupptx.postprocess.latex_renderer import render_latex_formulas
            _text_color = draft.visual.text_color if draft else "#1E293B"
            clean_svg, formula_count = render_latex_formulas(clean_svg, text_color=_text_color)
            if formula_count:
                logger.info("Slide {}: rendered {} formula(s)", slide.page_number, formula_count)

            # Step 3.5: Embed icon placeholders
            from edupptx.postprocess.icon_embedder import embed_icon_placeholders
            _icon_color = draft.visual.primary_color if draft else "#333"
            clean_svg, icon_count = embed_icon_placeholders(clean_svg, icon_color=_icon_color)
            if icon_count:
                logger.info("Slide {}: embedded {} icon(s)", slide.page_number, icon_count)

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
        """Replace per-slot __IMAGE_XXX__ placeholders with base64 data URIs."""
        import base64
        import io
        for slot_key, path in assets.image_paths.items():
            placeholder = f"__IMAGE_{slot_key.upper()}__"
            if placeholder not in svg_content:
                continue
            try:
                from PIL import Image
                with Image.open(path) as img:
                    # Compress for SVG embedding: max 800px wide, JPEG quality 70
                    if img.width > 800:
                        ratio = 800 / img.width
                        img = img.resize((800, int(img.height * ratio)), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    img.convert("RGB").save(buf, "JPEG", quality=70, optimize=True)
                    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    data_uri = f"data:image/jpeg;base64,{b64}"
                    svg_content = svg_content.replace(placeholder, data_uri)
                    logger.debug("Injected image for {} ({}KB)", slot_key, len(b64) // 1024)
            except Exception as e:
                logger.warning("Failed to inject image {}: {}", slot_key, e)
        return svg_content

    def _phase5_output(
        self, svg_paths: list[Path], session: Session,
        bg_path: Path | None = None,
        speaker_notes: list[str] | None = None,
    ) -> None:
        from edupptx.output.pptx_assembler import assemble_pptx
        assemble_pptx(
            svg_paths, session.output_path,
            bg_path=bg_path,
            speaker_notes=speaker_notes,
        )
