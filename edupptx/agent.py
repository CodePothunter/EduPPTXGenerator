"""V2 Agent: 5-phase SVG pipeline orchestrator."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

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


def _attach_ai_image_captions(
    routed_image_needs_by_page: dict[int, list[Any]],
    client: Any | None,
) -> int:
    """Attach missing reusable captions to routed AI image needs."""
    records: list[dict[str, str]] = []
    targets: list[Any] = []
    for routed_image_needs in routed_image_needs_by_page.values():
        for need in routed_image_needs:
            if getattr(need, "source", "") != "ai_generate":
                continue
            existing = str(getattr(need, "caption", "") or "").strip()
            if existing:
                continue
            query = str(getattr(need, "query", "") or "").strip()
            if not query:
                continue
            records.append({"query": query})
            targets.append(need)

    if not records:
        return 0

    summarized = records
    if client is not None:
        try:
            from edupptx.materials.caption_rules import summarize_records

            summarized = summarize_records(
                records,
                client,
                query_field="query",
                caption_field="caption",
                batch_size=50,
            )
        except Exception as exc:
            logger.warning("AI image caption summarization skipped: {}", str(exc)[:160])

    for need, source, item in zip(targets, records, summarized):
        caption = str(item.get("caption") or "").strip() or source["query"]
        need.caption = caption
    return len(targets)


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
        stop_after_asset_library: bool = False,
    ) -> Path:
        """Run the full pipeline. Returns session directory."""
        return asyncio.run(self._run_async(
            topic, requirements, file_path, research, style, review, debug, stop_after_asset_library,
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
        stop_after_asset_library: bool,
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
        draft = self._route_ai_image_prompts(draft)
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
        ai_image_reuse_search_context = self._new_ai_image_reuse_search_context()
        bg_path = await self._phase2_background(
            draft,
            session,
            reuse_search_context=ai_image_reuse_search_context,
        )

        # ── Phase 2b: Materials (skipped in debug mode) ─────
        if not debug:
            session.log_step("materials", "Fetching materials")
            all_assets = await self._phase2_materials(
                draft,
                session,
                reuse_search_context=ai_image_reuse_search_context,
            )
            # Attach background to all assets
            if bg_path:
                for assets in all_assets.values():
                    assets.background_path = bg_path
            logger.info("Materials ready for {} pages", len(all_assets))
            if stop_after_asset_library:
                session.log_step("asset_library", "Queueing reusable AI image asset library update")
                self._phase2c_asset_library(
                    session,
                    reuse_search_context=ai_image_reuse_search_context,
                )
                session.log_step("images_done", "Stopped after queueing reusable AI image asset library update")
                logger.info("Stopped after planning/image/material library queue phase: {}", session.dir)
                return session.dir
            logger.info("AI image asset library update deferred until PPTX output")
        else:
            logger.info("Debug mode: skipping material fetch")
            all_assets = {
                p.page_number: SlideAssets(
                    page_number=p.page_number,
                    background_path=bg_path,
                )
                for p in draft.pages
            }

        self._persist_reuse_query_cache(session, ai_image_reuse_search_context)

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
        if not debug:
            session.log_step("asset_library", "Starting reusable AI image asset library background update")
            self._phase2c_asset_library(
                session,
                reuse_search_context=ai_image_reuse_search_context,
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
        draft = self._route_ai_image_prompts(draft)

        session = Session.from_existing(plan_path.parent)
        session_dir = session.dir
        session.save_plan(draft.model_dump())

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

        ai_image_reuse_search_context = self._new_ai_image_reuse_search_context()
        bg_path = await self._phase2_background(
            draft,
            session,
            reuse_search_context=ai_image_reuse_search_context,
        )

        if not debug:
            all_assets = await self._phase2_materials(
                draft,
                session,
                reuse_search_context=ai_image_reuse_search_context,
            )
            if bg_path:
                for assets in all_assets.values():
                    assets.background_path = bg_path
            logger.info("AI image asset library update deferred until PPTX output")
        else:
            all_assets = {
                p.page_number: SlideAssets(page_number=p.page_number, background_path=bg_path)
                for p in draft.pages
            }

        self._persist_reuse_query_cache(session, ai_image_reuse_search_context)

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
        if not debug:
            session.log_step("asset_library", "Starting reusable AI image asset library background update")
            self._phase2c_asset_library(
                session,
                reuse_search_context=ai_image_reuse_search_context,
            )
        logger.info("Rendered {} slides from plan", len(svg_paths))
        return session_dir

    async def run_images_from_plan(self, plan_path: Path) -> Path:
        """Resume from a saved plan and stop after image/material library phases."""
        import json

        with open(plan_path, encoding="utf-8") as f:
            data = json.load(f)
        draft = PlanningDraft.model_validate(data)
        draft = self._ensure_template_state(draft)
        draft = self._route_ai_image_prompts(draft)

        session = Session.from_existing(plan_path.parent)
        session.save_plan(draft.model_dump())
        session.log_step("background", "Generating unified background")
        ai_image_reuse_search_context = self._new_ai_image_reuse_search_context()
        bg_path = await self._phase2_background(
            draft,
            session,
            reuse_search_context=ai_image_reuse_search_context,
        )

        session.log_step("materials", "Fetching materials")
        all_assets = await self._phase2_materials(
            draft,
            session,
            reuse_search_context=ai_image_reuse_search_context,
        )
        if bg_path:
            for assets in all_assets.values():
                assets.background_path = bg_path
        logger.info("Image/material phase ready for {} pages", len(all_assets))

        self._persist_reuse_query_cache(session, ai_image_reuse_search_context)

        session.log_step("asset_library", "Queueing reusable AI image asset library update")
        self._phase2c_asset_library(
            session,
            reuse_search_context=ai_image_reuse_search_context,
        )
        session.log_step("images_done", "Stopped after queueing reusable AI image asset library update")
        logger.info("Stopped after image/material library queue phase: {}", session.dir)
        return session.dir

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

    def _normalize_image_aspect_ratios(self, draft: PlanningDraft) -> None:
        """Normalize planned image ratios before prompt routing and material generation."""

        from edupptx.planning.image_aspect_ratio_normalizer import normalize_draft_image_aspect_ratios

        changes = normalize_draft_image_aspect_ratios(draft)
        for change in changes:
            logger.warning(
                "Normalized image aspect ratio: page={}, slot={}, role={}, {} -> {}",
                change.page_number,
                change.slot_key,
                change.role,
                change.original_ratio,
                change.normalized_ratio,
            )

    def _route_ai_image_prompts(self, draft: PlanningDraft) -> PlanningDraft:
        """Attach routed generation prompts while preserving semantic image queries."""

        from edupptx.materials.image_prompt_router import build_routed_image_needs

        self._coerce_unavailable_search_sources(draft)
        self._normalize_image_aspect_ratios(draft)
        for page in draft.pages:
            if page.material_needs.images:
                page.material_needs.images = build_routed_image_needs(draft, page)
        return draft

    def _search_images_enabled(self) -> bool:
        return bool(self.config.pixabay_api_key or self.config.unsplash_access_key)

    def _coerce_unavailable_search_sources(self, draft: PlanningDraft) -> None:
        """Use reusable/generated images when no search provider is configured."""

        if self._search_images_enabled():
            return
        for page in draft.pages:
            for need in page.material_needs.images or []:
                if need.source == "search":
                    need.source = "ai_generate"

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

    def _new_ai_image_reuse_search_context(self):
        try:
            from edupptx.materials.ai_image_asset_db import ReuseSearchContext

            return ReuseSearchContext()
        except Exception:
            return None

    def _persist_reuse_query_cache(self, session: Session, ctx) -> None:
        """Snapshot the per-target keyword + embedding caches to disk for replay."""
        if not bool(getattr(self.config, "debug_artifacts", False)):
            return
        if ctx is None:
            return
        try:
            from edupptx.materials.reuse_query_cache import save_reuse_query_cache

            save_reuse_query_cache(
                session.dir,
                target_keyword_cache=getattr(ctx, "target_keyword_cache", None),
                query_embedding_cache=getattr(ctx, "query_embedding_cache", None),
            )
        except Exception as exc:
            logger.warning("Reuse query cache save skipped: {}", str(exc)[:160])

    async def _phase2_background(
        self,
        draft: PlanningDraft,
        session: Session,
        *,
        reuse_search_context=None,
    ):
        from edupptx.materials.background_generator import build_background_content_prompt, generate_background

        prompt = build_background_content_prompt(draft.visual)
        keyword_client = self._build_llm_client()
        reuse_context = self._ai_image_reuse_context(draft)
        match = self._find_reusable_ai_image(
            asset_kind="background",
            prompt=prompt,
            background_route=self._background_reuse_route(draft),
            theme=reuse_context["theme"],
            grade=reuse_context["grade"],
            subject=reuse_context["subject"],
            aspect_ratio="16:9",
            keyword_client=keyword_client,
            debug_path=(
                session.dir / "materials" / "ai_image_reuse_debug.json"
                if bool(getattr(self.config, "debug_artifacts", False))
                else None
            ),
            debug_context={
                "asset_kind": "background",
                "page_number": None,
                "slot_key": "background",
            },
            reuse_search_context=reuse_search_context,
        )
        self._persist_reuse_query_cache(session, reuse_search_context)
        if match:
            dest = session.dir / "materials" / "background.png"
            self._copy_reusable_ai_image(match, dest, session)
            logger.info(
                "Reused background image asset: {} score={}",
                match["asset"].get("asset_id"),
                match.get("keyword_score"),
            )
            return dest

        return await generate_background(draft.visual, self.config, session)

    async def _phase2_materials(
        self,
        draft: PlanningDraft,
        session: Session,
        *,
        reuse_search_context=None,
    ) -> dict[int, SlideAssets]:
        from edupptx.materials.ai_image_asset_db import (
            ReuseSearchContext,
            _build_reuse_target_asset,
            _finalize_reuse_candidate_collection,
            _prewarm_reuse_target_keywords,
        )
        from edupptx.materials.image_provider import fetch_images
        from edupptx.materials.image_prompt_router import build_routed_image_needs

        materials_dir = session.dir / "materials"
        materials_dir.mkdir(exist_ok=True)
        keyword_client = self._build_llm_client()
        # R5 near-miss VLM verify: optional. We only construct the VLM
        # client when the config has VLM credentials so deployments without
        # one degrade gracefully (R5 simply does not fire).
        vlm_client = None
        if getattr(self.config, "vlm_api_key", None) and getattr(self.config, "vlm_model", None):
            try:
                from edupptx.llm_client import create_vlm_client
                vlm_client = create_vlm_client(self.config)
            except Exception as exc:
                logger.warning("AI image reuse VLM client init skipped: {}", str(exc)[:160])
        reuse_context = self._ai_image_reuse_context(draft)
        if reuse_search_context is None:
            reuse_search_context = ReuseSearchContext()
        reuse_session_state: dict[str, Any] = {
            "strict_asset_use_counts": {},
            "strict_asset_used_by": {},
        }
        # R5 budget state: shared across parallel policy calls so the
        # per-session VLM cap is enforced globally. Kept separate from
        # reuse_session_state because the parallel policy phase passes
        # reuse_session_state=None to suppress occupancy races.
        near_miss_vlm_state: dict[str, Any] = {}
        page_limit = max(1, int(getattr(self.config, "materials_concurrency", 3) or 3))
        page_semaphore = asyncio.Semaphore(page_limit)
        debug_path = (
            session.dir / "materials" / "ai_image_reuse_debug.json"
            if bool(getattr(self.config, "debug_artifacts", False))
            else None
        )
        assets_by_page = {
            page.page_number: SlideAssets(page_number=page.page_number)
            for page in draft.pages
        }
        pending_fetch_by_page: dict[int, list[tuple[str, Any]]] = {
            page.page_number: []
            for page in draft.pages
        }
        routed_image_needs_by_page: dict[int, list[Any]] = {}
        for page in draft.pages:
            routed = build_routed_image_needs(draft, page)
            routed_image_needs_by_page[page.page_number] = routed
            page.material_needs.images = routed
        if _attach_ai_image_captions(routed_image_needs_by_page, keyword_client):
            session.save_plan(draft.model_dump())
        reuse_specs: list[dict[str, Any]] = []

        for page in draft.pages:
            if page.material_needs.images:
                routed_image_needs = routed_image_needs_by_page.get(page.page_number, [])
                for slot_key, need in iter_image_slot_keys(routed_image_needs):
                    if need.source != "ai_generate":
                        pending_fetch_by_page[page.page_number].append((slot_key, need))
                        continue
                    debug_context = {
                        "asset_kind": "page_image",
                        "page_number": page.page_number,
                        "slot_key": slot_key,
                        "aspect_ratio": need.aspect_ratio,
                    }
                    target = _build_reuse_target_asset(
                        asset_kind="page_image",
                        prompt=need.query,
                        prompt_route=need.prompt_route,
                        background_route=None,
                        theme=reuse_context["theme"],
                        grade=reuse_context["grade"],
                        subject=reuse_context["subject"],
                        page_title=page.title,
                        page_type=page.page_type,
                        role=need.role,
                        aspect_ratio=need.aspect_ratio,
                        caption=getattr(need, "caption", ""),
                    )
                    reuse_specs.append(
                        {
                            "page": page,
                            "page_number": page.page_number,
                            "slot_key": slot_key,
                            "need": need,
                            "target": target,
                            "debug_context": debug_context,
                        }
                    )

            if page.material_needs.icons:
                from edupptx.materials.icons import get_icon_svg

                for icon_name in page.material_needs.icons:
                    try:
                        assets_by_page[page.page_number].icon_svgs[icon_name] = get_icon_svg(icon_name)
                    except Exception:
                        logger.warning("Icon not found: {}", icon_name)

        try:
            _prewarm_reuse_target_keywords(
                [spec["target"] for spec in reuse_specs],
                keyword_client,
                reuse_search_context.target_keyword_cache,
                on_batch_cached=lambda _batch_count, _total_count: self._persist_reuse_query_cache(
                    session,
                    reuse_search_context,
                ),
            )
            self._persist_reuse_query_cache(session, reuse_search_context)
        except Exception as exc:
            logger.warning("AI image generation reuse target keyword prewarm skipped: {}", str(exc)[:160])

        total_reuse_checks = len(reuse_specs)

        def collect_reuse_candidates(spec: dict[str, Any], ordinal: int):
            need = spec["need"]
            logger.info(
                "AI image generation reuse check {}/{} candidate search start: page={}, slot={}, role={}, "
                "aspect={}, query={}",
                ordinal,
                total_reuse_checks,
                spec["page_number"],
                spec["slot_key"],
                getattr(need, "role", "") or "unknown",
                getattr(need, "aspect_ratio", "") or "unknown",
                str(getattr(need, "query", ""))[:120],
            )
            collection = self._find_reusable_ai_image(
                asset_kind="page_image",
                prompt=need.query,
                prompt_route=need.prompt_route,
                theme=reuse_context["theme"],
                grade=reuse_context["grade"],
                subject=reuse_context["subject"],
                page_title=spec["page"].title,
                page_type=spec["page"].page_type,
                role=need.role,
                aspect_ratio=need.aspect_ratio,
                caption=getattr(need, "caption", ""),
                keyword_client=None,
                debug_path=None,
                debug_context=spec["debug_context"],
                reuse_session_state=None,
                reuse_search_context=reuse_search_context,
                collect_candidates_only=True,
            )
            if isinstance(collection, dict) and collection.get("_reuse_candidate_collection"):
                candidate_count = len(collection.get("candidates") or [])
            elif isinstance(collection, dict) and collection.get("asset"):
                candidate_count = 1
            else:
                candidate_count = 0
            logger.info(
                "AI image generation reuse check {}/{} candidate search done: page={}, slot={}, candidates={}",
                ordinal,
                total_reuse_checks,
                spec["page_number"],
                spec["slot_key"],
                candidate_count,
            )
            return collection

        collected: list[Any | None] = [None] * len(reuse_specs)
        if reuse_specs:
            max_workers = min(page_limit, len(reuse_specs))
            logger.info(
                "AI image generation reuse candidate searches parallel start: checks={}, workers={}",
                len(reuse_specs),
                max_workers,
            )
            loop = asyncio.get_running_loop()
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                tasks = [
                    loop.run_in_executor(
                        executor,
                        functools.partial(collect_reuse_candidates, spec, index + 1),
                    )
                    for index, spec in enumerate(reuse_specs)
                ]
                collected = list(await asyncio.gather(*tasks))
            self._persist_reuse_query_cache(session, reuse_search_context)
            logger.info(
                "AI image generation reuse candidate searches parallel done: checks={}",
                len(reuse_specs),
            )

        # P1: parallelise the policy + LLM-review stage. Each check is
        # I/O bound on LLM round-trips, so a ThreadPoolExecutor is the
        # right fit. We deliberately run policy with reuse_session_state
        # set to None — the occupancy / strict-reuse limit decisions are
        # then "disabled" inside _strict_reuse_occupancy_status, which
        # turns them into a re-checkable advisory. The materialise loop
        # below stays sequential and re-applies occupancy on accept,
        # so the strict-reuse-per-session invariant is preserved.
        #
        # Default workers=4 keeps total concurrency conservative:
        #   - prewarm stage above already runs up to 4 parallel batches;
        #   - upstream LLM APIs (e.g. 豆包 Seed-2.0) have a typical QPS
        #     ceiling around 10-20 req/s, so 4 here leaves headroom for
        #     overlap with prewarm and the candidate-search stage.
        # Operators can opt into higher concurrency via the env var when
        # they know the deployment can absorb it.
        policy_max_workers_env = os.environ.get("EDUPPTX_REUSE_POLICY_WORKERS")
        try:
            policy_max_workers = int(policy_max_workers_env) if policy_max_workers_env else 4
        except ValueError:
            policy_max_workers = 4
        policy_max_workers = max(1, min(policy_max_workers, len(reuse_specs) or 1))

        def _run_policy(index: int):
            spec = reuse_specs[index]
            collection = collected[index]
            if isinstance(collection, dict) and collection.get("asset") and not collection.get("_reuse_candidate_collection"):
                return collection
            try:
                return _finalize_reuse_candidate_collection(
                    collection,
                    debug_path=debug_path,
                    keyword_client=keyword_client,
                    reuse_session_state=None,  # see comment above
                    llm_review_enabled=True,
                    reuse_debug_mode="full",
                    vlm_client=vlm_client,
                    near_miss_vlm_state=near_miss_vlm_state,
                    constraint_embedding_cache=getattr(reuse_search_context, "query_embedding_cache", None),
                )
            except Exception as exc:
                logger.warning(
                    "AI image generation reuse policy skipped for page {} {}: {}",
                    spec["page_number"],
                    spec["slot_key"],
                    str(exc)[:160],
                )
                return None

        if reuse_specs:
            logger.info(
                "AI image generation reuse policy parallel start: checks={}, workers={}",
                len(reuse_specs),
                policy_max_workers,
            )
            policy_loop = asyncio.get_running_loop()
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=policy_max_workers) as policy_executor:
                policy_tasks = [
                    policy_loop.run_in_executor(policy_executor, functools.partial(_run_policy, idx))
                    for idx in range(len(reuse_specs))
                ]
                policy_results = list(await asyncio.gather(*policy_tasks))
            self._persist_reuse_query_cache(session, reuse_search_context)
            logger.info(
                "AI image generation reuse policy parallel done: checks={}",
                len(reuse_specs),
            )
        else:
            policy_results = []

        # Sequential materialise + occupancy re-check pass. We must keep
        # this serial: ``mark_reused_ai_image_asset_in_session`` mutates
        # ``reuse_session_state`` and the strict-reuse-per-session limit
        # depends on observing each accept before the next candidate is
        # considered.
        from edupptx.materials.ai_image_asset_db import _strict_reuse_occupancy_status

        for index, spec in enumerate(reuse_specs):
            current_check = index + 1
            logger.info(
                "AI image generation reuse check {}/{} policy start: page={}, slot={}",
                current_check,
                total_reuse_checks,
                spec["page_number"],
                spec["slot_key"],
            )
            match = policy_results[index]
            if match:
                # Re-apply occupancy now that we know the cumulative
                # session state. Strict assets that hit their per-session
                # limit downgrade to "no reuse"; everything else proceeds.
                occupancy = _strict_reuse_occupancy_status(match, reuse_session_state)
                if occupancy.get("decision") == "skip_strict_asset_reuse_limit":
                    logger.info(
                        "AI image generation reuse check {}/{} occupancy reject: page={}, slot={}, asset_id={}",
                        current_check,
                        total_reuse_checks,
                        spec["page_number"],
                        spec["slot_key"],
                        (match.get("asset") or {}).get("asset_id"),
                    )
                    match["strict_reuse_occupancy"] = occupancy
                    match = None
            if match:
                suffix = Path(str(match.get("candidate_image_path") or "")).suffix.lower() or ".img"
                dest = materials_dir / f"page_{spec['page_number']:02d}_{spec['slot_key']}{suffix}"
                try:
                    self._copy_reusable_ai_image(
                        match,
                        dest,
                        session,
                        reuse_session_state=reuse_session_state,
                        reuse_context={
                            "asset_kind": "page_image",
                            "page_number": spec["page_number"],
                            "slot_key": spec["slot_key"],
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "AI image generation reuse materialize skipped for page {} {}: {}",
                        spec["page_number"],
                        spec["slot_key"],
                        str(exc)[:160],
                    )
                    pending_fetch_by_page[spec["page_number"]].append((spec["slot_key"], spec["need"]))
                    continue
                assets_by_page[spec["page_number"]].image_paths[spec["slot_key"]] = dest
                logger.info(
                    "AI image generation reuse check {}/{} done: page={}, slot={}, matched=True, "
                    "asset_id={}, score={}",
                    current_check,
                    total_reuse_checks,
                    spec["page_number"],
                    spec["slot_key"],
                    match["asset"].get("asset_id"),
                    match.get("keyword_score"),
                )
                self._persist_reuse_query_cache(session, reuse_search_context)
                continue
            pending_fetch_by_page[spec["page_number"]].append((spec["slot_key"], spec["need"]))
            logger.info(
                "AI image generation reuse check {}/{} done: page={}, slot={}, matched=False",
                current_check,
                total_reuse_checks,
                spec["page_number"],
                spec["slot_key"],
            )
            self._persist_reuse_query_cache(session, reuse_search_context)

        async def fetch_page_pending(page: PagePlan) -> None:
            pending = pending_fetch_by_page.get(page.page_number) or []
            if not pending:
                return
            async with page_semaphore:
                fetched = await fetch_images([need for _slot_key, need in pending], self.config)
                for (slot_key, _need), result in zip(pending, fetched):
                    if result and result.local_path and result.local_path.exists():
                        suffix = result.local_path.suffix.lower() or ".img"
                        dest = materials_dir / f"page_{page.page_number:02d}_{slot_key}{suffix}"
                        shutil.copy2(result.local_path, dest)
                        assets_by_page[page.page_number].image_paths[slot_key] = dest

        await asyncio.gather(*(fetch_page_pending(page) for page in draft.pages))

        # Post-pipeline observability: write the logical-need summary and
        # append any coverage-gap events to the cross-session log. Failures
        # here MUST NOT abort generation, so we trap broadly.
        try:
            from edupptx.materials.reuse_observability import (
                DEFAULT_COVERAGE_LOG_FILENAME,
                append_coverage_gap_events,
                write_reuse_logical_summary,
            )
            summary = write_reuse_logical_summary(debug_path)
            if summary is not None:
                logger.info(
                    "AI image reuse logical summary: checks={}, matched={}, match_rate={}",
                    summary.get("logical_check_count"),
                    summary.get("matched_count"),
                    summary.get("match_rate"),
                )
            # The coverage log lives at the repo root by default so it
            # accumulates across sessions; users can override with the env
            # variable EDUPPTX_REUSE_COVERAGE_LOG.
            import os as _os
            coverage_log_path = _os.environ.get("EDUPPTX_REUSE_COVERAGE_LOG")
            if coverage_log_path:
                coverage_log = Path(coverage_log_path)
            else:
                coverage_log = Path.cwd() / DEFAULT_COVERAGE_LOG_FILENAME
            appended = append_coverage_gap_events(debug_path, log_path=coverage_log)
            if appended:
                logger.info(
                    "AI image reuse coverage gap events appended: count={}, log={}",
                    appended,
                    coverage_log,
                )
        except Exception as exc:
            logger.warning(
                "AI image reuse observability skipped: {}",
                str(exc)[:200],
            )

        return assets_by_page

    def _find_reusable_ai_image(
        self,
        *,
        asset_kind: str,
        prompt: str,
        theme: str,
        grade: str,
        subject: str,
        aspect_ratio: str,
        keyword_client,
        page_title: str = "",
        page_type: str = "",
        role: str = "",
        prompt_route: dict[str, Any] | None = None,
        background_route: dict[str, Any] | None = None,
        caption: str = "",
        debug_path: Path | None = None,
        debug_context: dict[str, Any] | None = None,
        reuse_session_state: dict[str, Any] | None = None,
        llm_review_enabled: bool = True,
        reuse_search_context=None,
        collect_candidates_only: bool = False,
    ):
        try:
            from edupptx.materials.ai_image_asset_db import find_reusable_ai_image_asset

            return find_reusable_ai_image_asset(
                library_dir=self.config.reuse_library_dirs or (self.config.library_dir,),
                asset_kind=asset_kind,
                prompt=prompt,
                prompt_route=prompt_route,
                background_route=background_route,
                theme=theme,
                grade=grade,
                subject=subject,
                page_title=page_title,
                page_type=page_type,
                role=role,
                aspect_ratio=aspect_ratio,
                caption=caption,
                keyword_client=keyword_client,
                debug_path=debug_path,
                debug_context=debug_context,
                reuse_session_state=reuse_session_state,
                llm_review_enabled=llm_review_enabled,
                reuse_search_context=reuse_search_context,
                _collect_candidates_only=collect_candidates_only,
            )
        except Exception as exc:
            logger.warning("AI image reuse lookup skipped: {}", str(exc)[:160])
            return None

    @staticmethod
    def _ai_image_reuse_context(draft: PlanningDraft) -> dict[str, str]:
        return {
            "theme": draft.meta.topic,
            "grade": getattr(draft.meta, "grade", ""),
            "subject": getattr(draft.meta, "subject", ""),
        }

    @staticmethod
    def _background_reuse_route(draft: PlanningDraft) -> dict[str, str]:
        visual = draft.visual
        routing = draft.style_routing
        values = {
            "template_family": getattr(routing, "template_family", ""),
            "style_name": getattr(routing, "style_name", ""),
            "palette_id": getattr(routing, "palette_id", ""),
            "primary_color": getattr(visual, "primary_color", ""),
            "secondary_color": getattr(visual, "secondary_color", ""),
            "accent_color": getattr(visual, "accent_color", ""),
            "card_bg_color": getattr(visual, "card_bg_color", ""),
            "secondary_bg_color": getattr(visual, "secondary_bg_color", ""),
            "background_color_bias": getattr(visual, "background_color_bias", ""),
        }
        return {key: str(value).strip() for key, value in values.items() if str(value or "").strip()}

    @staticmethod
    def _copy_reusable_ai_image(
        match,
        dest: Path,
        session: Session,
        *,
        reuse_session_state: dict[str, Any] | None = None,
        reuse_context: dict[str, Any] | None = None,
    ) -> None:
        from edupptx.materials.ai_image_asset_db import (
            mark_reused_ai_image_asset_in_session,
            materialize_reused_ai_image_asset,
        )

        dest.parent.mkdir(parents=True, exist_ok=True)
        materialize_reused_ai_image_asset(
            session_dir=session.dir,
            session_image_path=dest,
            match=match,
        )
        mark_reused_ai_image_asset_in_session(match, reuse_session_state, reuse_context)

    def _phase2c_asset_library(
        self,
        session: Session,
        *,
        reuse_search_context=None,
    ) -> None:
        """Ingest this session's newly generated AI images into the reusable library."""

        if not bool(getattr(self.config, "asset_library_ingest_enabled", True)):
            logger.info("AI image asset library update skipped by configuration")
            return
        job_id = self._enqueue_asset_library_update_job(session, reuse_search_context)
        if job_id is None:
            logger.info("AI image asset library background update skipped: no generated assets to ingest")
            return
        self._launch_asset_library_update_worker(session)

    def _asset_ingest_job_db_path(self) -> Path:
        configured = getattr(self.config, "asset_ingest_job_db", None)
        if configured:
            return Path(configured).expanduser().resolve()
        from edupptx.materials.asset_ingest_job_store import default_asset_ingest_job_db_path

        return default_asset_ingest_job_db_path(self.config.library_dir)

    def _enqueue_asset_library_update_job(self, session: Session, reuse_search_context=None) -> str | None:
        """Create a lightweight SQLite ingest job from this session's generated assets."""

        try:
            from edupptx.materials.ai_image_asset_db import build_ai_image_asset_db
            from edupptx.materials.asset_ingest_job_store import AssetIngestJobStore

            target_keyword_cache = getattr(reuse_search_context, "target_keyword_cache", None)
            session_db = build_ai_image_asset_db(
                session.dir,
                target_keyword_cache=target_keyword_cache if isinstance(target_keyword_cache, dict) else None,
            )
            raw_assets = session_db.get("assets")
            assets = [asset for asset in raw_assets if isinstance(asset, dict)] if isinstance(raw_assets, list) else []
            if not assets:
                return None
            store = AssetIngestJobStore(self._asset_ingest_job_db_path())
            job_id = f"asset_ingest_{session.dir.name}"
            store.enqueue(
                job_id=job_id,
                session_dir=session.dir,
                library_dir=self.config.library_dir,
                assets=assets,
                vlm_review=bool(getattr(self.config, "asset_library_vlm_review", False)),
                debug_artifacts=bool(getattr(self.config, "debug_artifacts", False)),
                extra_payload={
                    "warnings": session_db.get("warnings") or [],
                    "asset_count": len(assets),
                },
            )
            logger.info(
                "AI image asset library ingest job queued: job_id={}, db={}, assets={}",
                job_id,
                self._asset_ingest_job_db_path(),
                len(assets),
            )
            return job_id
        except Exception as exc:
            logger.warning("AI image asset library ingest job enqueue skipped: {}", str(exc)[:160])
            return None

    def _launch_asset_library_update_worker(self, session: Session) -> None:
        """Start asset-library ingest in a detached Python process."""

        log_path = session.dir / "asset_library_ingest.log"
        job_path = session.dir / "asset_library_ingest_job.json"
        command = [
            sys.executable,
            "-m",
            "edupptx.asset_ingest_worker",
            "--job-db",
            str(self._asset_ingest_job_db_path()),
            "--env-file",
            str(getattr(self.config, "env_file", Path(".env"))),
            "--log-file",
            str(log_path),
            "--job-file",
            str(job_path),
        ]
        if bool(getattr(self.config, "asset_library_vlm_review", False)):
            command.append("--vlm-review")
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        creationflags = 0
        popen_kwargs: dict[str, Any] = {
            "cwd": str(Path.cwd()),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(command, **popen_kwargs)
            job = {
                "status": "started",
                "pid": process.pid,
                "mode": "background",
                "session_dir": str(session.dir),
                "library_dir": str(self.config.library_dir),
                "log_path": str(log_path),
                "command": command,
            }
            job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(
                "AI image asset library background update started: pid={}, log={}",
                process.pid,
                log_path,
            )
        except Exception as exc:
            logger.warning("AI image asset library background update failed to start: {}", str(exc)[:160])

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
