"""Thin agent — content planning + per-slide material decisions + parallel execution."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from edupptx.backgrounds import generate_background
from edupptx.config import Config
from edupptx.content_planner import ContentPlanner
from edupptx.design_system import DesignTokens, get_design_tokens
from edupptx.llm_client import LLMClient
from edupptx.material_library import MaterialLibrary
from edupptx.models import (
    BackgroundAction,
    ContentMaterial,
    PresentationPlan,
    SlideContent,
)
from edupptx.renderer import PresentationRenderer
from edupptx.session import Session

# Per-slide material decision prompt — small, focused
_MATERIAL_PROMPT = """你是一位教育演示文稿的素材规划师。给定一页幻灯片的内容，决定它需要什么素材。

## 背景图（必选）
选择一种程序生成风格: diagonal_gradient, radial_gradient, geometric_circles, geometric_triangles
**重要：不同页面应使用不同风格，避免所有页面都用同一种。**

## 图表（可选，仅在内容确实适合图表表达时才添加）
可用类型:
- flowchart: 流程/步骤 → data: {"nodes":[{"id":"1","label":"..."}],"edges":[{"from":"1","to":"2"}],"direction":"TB"}
- timeline: 时间线 → data: {"events":[{"year":"...","label":"..."}]}
- comparison: 对比 → data: {"columns":[{"header":"...","items":["..."]}]}
- hierarchy: 层级 → data: {"root":{"label":"...","children":[{"label":"...","children":[]}]}}
- cycle: 循环 → data: {"steps":[{"label":"..."}]}

## 插图（可选，仅在内容适合用图片辅助理解时才添加）
当页面描述具体概念、实验、场景或自然现象时，可以请求 AI 生成一张教育插图。
**重要：图表和插图二选一，一页不能同时有 diagram 和 illustration。**

## 输出格式（严格 JSON，不要 markdown 代码块）
{
  "bg_style": "diagonal_gradient",
  "diagram": null
}

如果需要图表:
{
  "bg_style": "radial_gradient",
  "diagram": {"type": "flowchart", "data": {...}}
}

如果需要插图（不能和图表同时使用）:
{
  "bg_style": "radial_gradient",
  "illustration": {
    "description": "A flat-style educational illustration showing...",
    "style": "educational_flat",
    "anchor": "center",
    "scale": 0.85
  }
}
style 可选: educational_flat, scientific_realistic, watercolor_soft
anchor: top（靠上）/ center（居中）/ bottom（靠下），根据画面内容决定
scale: 0.4-1.0，图片占区域比例。全图页用0.95，配文字页用0.7-0.85，小装饰用0.5

素材库当前状态: {library_summary}
"""

# Slide types that skip LLM material decision (use default bg only)
_SKIP_MATERIAL_TYPES = {"big_quote", "closing", "section"}

# Background styles for forced rotation
_BG_STYLES = ["diagonal_gradient", "radial_gradient", "geometric_circles", "geometric_triangles"]


class PPTXAgent:
    """Thin agent: content planning → per-slide material decisions → parallel execution."""

    def __init__(self, config: Config):
        self.config = config
        self.library = MaterialLibrary(config.library_dir)
        self.llm = LLMClient(config)

    def run(self, topic: str, requirements: str = "") -> Path:
        """Run the agent. Returns path to session directory."""
        session = Session(self.config.output_dir)
        logger.info("Session: {}", session.dir)

        # Phase 1: Content planning (1 LLM call, original prompt, small output)
        session.log_step("planning", f"Planning slides for: {topic}")
        planner = ContentPlanner(self.llm)
        plan = planner.plan(topic, requirements)
        session.save_plan(plan.model_dump())
        logger.info("Plan: {} slides, palette={}", len(plan.slides), plan.palette)

        # Phase 2: Design tokens
        design = get_design_tokens(plan.palette)

        # Phase 3: Per-slide material decisions (N small LLM calls, parallel)
        session.log_step("materials", f"Deciding materials for {len(plan.slides)} slides")
        self._decide_materials(plan, session)

        # Phase 4: Execute material actions (parallel, no LLM)
        session.log_step("executing", "Generating materials")
        slide_assets = self._execute_materials(plan, design, session)

        # Phase 5: Render slides (sequential)
        session.log_step("rendering", f"Rendering {len(plan.slides)} slides")
        renderer = PresentationRenderer(design)
        for i, slide in enumerate(plan.slides):
            bg = slide_assets.get(("bg", i))
            material = slide_assets.get(("mat", i))
            renderer.render_slide(slide, bg, material)
            session.save_slide_state(i, slide.type, slide.model_dump())
            logger.debug("Rendered slide {}/{}: {}", i + 1, len(plan.slides), slide.type)

        # Phase 6: Assemble
        renderer.save(session.output_path)
        session.log_step("done", f"Saved {len(plan.slides)} slides to {session.output_path}")
        logger.info("Done! {} slides, output: {}", len(plan.slides), session.output_path)

        return session.dir

    def _decide_materials(self, plan: PresentationPlan, session: Session) -> None:
        """Per-slide material decisions via small parallel LLM calls.

        Mutates plan.slides in-place, setting bg_action and content_materials.
        """
        library_summary = json.dumps(self.library.summary(), ensure_ascii=False)
        system = _MATERIAL_PROMPT.replace("{library_summary}", library_summary)

        def _decide_one(i: int, slide: SlideContent) -> tuple[int, dict]:
            # Skip LLM call for simple slide types
            if slide.type in _SKIP_MATERIAL_TYPES:
                return i, {"bg_style": _BG_STYLES[i % len(_BG_STYLES)]}

            user_msg = (
                f"幻灯片 {i+1}/{len(plan.slides)}\n"
                f"类型: {slide.type}\n"
                f"标题: {slide.title}\n"
            )
            if slide.subtitle:
                user_msg += f"副标题: {slide.subtitle}\n"
            if slide.cards:
                card_summary = ", ".join(c.title for c in slide.cards)
                user_msg += f"卡片: {card_summary}\n"
            if slide.formula:
                user_msg += f"公式: {slide.formula}\n"

            try:
                result = self.llm.chat_json(
                    [{"role": "system", "content": system},
                     {"role": "user", "content": user_msg}],
                    max_tokens=1024,
                )
                return i, result
            except Exception as e:
                logger.warning("Material decision failed for slide {}: {}", i, e)
                return i, {"bg_style": "diagonal_gradient", "diagram": None}

        # Run per-slide decisions in parallel
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_decide_one, i, s) for i, s in enumerate(plan.slides)]
            for future in as_completed(futures):
                idx, decision = future.result()
                slide = plan.slides[idx]

                # Apply background decision — force rotation instead of trusting LLM
                bg_style = _BG_STYLES[idx % len(_BG_STYLES)]
                slide.bg_action = BackgroundAction(
                    action="generate",
                    style=bg_style,
                    tags=[plan.topic],
                )

                # Apply diagram decision
                diagram = decision.get("diagram")
                if diagram and isinstance(diagram, dict) and diagram.get("type"):
                    slide.content_materials = [
                        ContentMaterial(
                            action="generate_diagram",
                            position="center",
                            diagram_type=diagram["type"],
                            diagram_data=diagram.get("data", {}),
                            tags=[plan.topic],
                        )
                    ]

                # Apply illustration decision (only if no diagram)
                illust_desc = None
                if not diagram:
                    illust = decision.get("illustration")
                    if illust and isinstance(illust, dict) and illust.get("description"):
                        illust_desc = illust["description"]
                        # Determine position from slide type
                        illust_position = "center"
                        if slide.type == "image_left":
                            illust_position = "left"
                        elif slide.type == "image_right":
                            illust_position = "right"
                        elif slide.type == "full_image":
                            illust_position = "full"

                        anchor = illust.get("anchor", "center")
                        if anchor not in ("top", "center", "bottom"):
                            anchor = "center"
                        scale = illust.get("scale", 0.85)
                        if not isinstance(scale, (int, float)):
                            scale = 0.85
                        scale = max(0.4, min(1.0, float(scale)))

                        slide.content_materials = [
                            ContentMaterial(
                                action="generate_illustration",
                                position=illust_position,
                                illustration_description=illust["description"],
                                illustration_style=illust.get("style", "educational_flat"),
                                image_anchor=anchor,
                                image_scale=scale,
                                tags=[plan.topic],
                            )
                        ]

                mat_info = (
                    f"diagram={diagram.get('type')}" if diagram and isinstance(diagram, dict) and diagram.get("type")
                    else f"illustration={illust_desc[:30]}..." if illust_desc
                    else "none"
                )
                session.log_step(
                    "material_decision",
                    f"Slide {idx+1}: bg={bg_style}, material={mat_info}",
                )

    def _execute_materials(
        self, plan: PresentationPlan, design: DesignTokens, session: Session,
    ) -> dict[tuple[str, int], Path]:
        """Execute all material actions in parallel (no LLM, pure generation)."""
        results: dict[tuple[str, int], Path] = {}

        def _gen_bg(i: int, slide: SlideContent) -> tuple[tuple[str, int], Path]:
            style = "diagonal_gradient"
            if slide.bg_action and slide.bg_action.style:
                style = slide.bg_action.style
            tags = slide.bg_action.tags if slide.bg_action else []

            # Try to reuse from library first
            cached = self.library.search(
                tags + [style], type="background", palette=plan.palette,
            )
            if cached:
                lib_path = self.library.dir / cached[0].path
                if lib_path.exists():
                    dest = session.dir / "materials" / lib_path.name
                    shutil.copy2(lib_path, dest)
                    logger.debug("Reused cached background for slide {}: {}", i, cached[0].id)
                    return ("bg", i), lib_path

            bg_path = generate_background(design, style, seed_extra=f"slide{i}")
            self.library.add(
                bg_path, "background", tags + [style], plan.palette, "programmatic",
                f"Slide {i}: {slide.title}",
            )
            dest = session.dir / "materials" / bg_path.name
            shutil.copy2(bg_path, dest)
            return ("bg", i), bg_path

        def _make_placeholder(desc: str, design: DesignTokens) -> Path:
            """Generate a placeholder illustration image."""
            from PIL import Image, ImageDraw, ImageFont
            img = Image.new("RGB", (1024, 768), tuple(int(design.accent_light.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)))
            draw = ImageDraw.Draw(img)
            text = desc[:80] + ("..." if len(desc) > 80 else "")
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            except (OSError, IOError):
                font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (1024 - tw) // 2
            y = (768 - th) // 2
            text_color = tuple(int(design.text_secondary.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
            draw.text((x, y), text, fill=text_color, font=font)
            path = Path(tempfile.mktemp(suffix=".png"))
            img.save(path, "PNG")
            return path

        # Recommended 2K pixel sizes sorted by aspect ratio (Seedream API)
        _RECOMMENDED_SIZES = [
            (9/16, "1600x2848"),   # 9:16
            (2/3,  "1664x2496"),   # 2:3
            (3/4,  "1728x2304"),   # 3:4
            (1/1,  "2048x2048"),   # 1:1
            (4/3,  "2304x1728"),   # 4:3
            (3/2,  "2496x1664"),   # 3:2
            (16/9, "2848x1600"),   # 16:9
            (21/9, "3136x1344"),   # 21:9
        ]

        def _pick_image_size(slide_type: str, position: str, card_count: int) -> str:
            """Pick the best 2K size by computing the actual material_slot aspect ratio."""
            from edupptx.layout_engine import get_layout
            layout = get_layout(slide_type, card_count, material_position=position)
            slot = layout.material_slot
            if not slot or slot.width <= 0 or slot.height <= 0:
                return "2848x1600"  # fallback 16:9
            slot_ratio = slot.width / slot.height
            # Find the closest recommended ratio
            best_size = _RECOMMENDED_SIZES[-1][1]
            best_diff = float("inf")
            for ratio, size in _RECOMMENDED_SIZES:
                diff = abs(ratio - slot_ratio)
                if diff < best_diff:
                    best_diff = diff
                    best_size = size
            return best_size

        def _gen_illustration(i: int, mat: ContentMaterial, slide: SlideContent) -> tuple[tuple[str, int], Path] | None:
            desc = mat.illustration_description or "educational illustration"
            style = mat.illustration_style or "educational_flat"
            desc_hash = hashlib.md5(desc.encode()).hexdigest()[:8]

            # Try to reuse from library — require desc_hash match to avoid
            # returning unrelated illustrations from previous runs
            cached = self.library.search(
                [desc_hash, style], type="illustration", palette=plan.palette,
            )
            if cached and desc_hash in cached[0].tags:
                lib_path = self.library.dir / cached[0].path
                if lib_path.exists():
                    dest = session.dir / "materials" / lib_path.name
                    shutil.copy2(lib_path, dest)
                    logger.debug("Reused cached illustration for slide {}: {}", i, cached[0].id)
                    return ("mat", i), lib_path

            # No API key → skip illustration entirely (no ugly placeholders)
            if not self.config.image_api_key:
                logger.debug("No image API key, skipping illustration for slide {}", i)
                return None

            style_prompts = {
                "educational_flat": "flat design, clean lines, vibrant colors, educational",
                "scientific_realistic": "scientific illustration, detailed, accurate, textbook style",
                "watercolor_soft": "soft watercolor style, gentle tones, artistic, educational",
            }
            style_suffix = style_prompts.get(style, style_prompts["educational_flat"])
            image_size = _pick_image_size(slide.type, mat.position, len(slide.cards))

            try:
                from edupptx.llm_client import ImageClient
                client = ImageClient(self.config)
                prompt = f"{desc}. Style: {style_suffix}. No text or labels in the image."
                urls = client.generate(prompt, size=image_size, n=1)
                if not urls:
                    logger.warning("Image API returned no URLs for slide {}", i)
                    return None
                import urllib.request
                path = Path(tempfile.mktemp(suffix=".png"))
                urllib.request.urlretrieve(urls[0], str(path))
            except Exception as e:
                logger.warning("Illustration generation failed for slide {}: {}", i, e)
                return None

            self.library.add(
                path, "illustration", mat.tags + [style, desc_hash], plan.palette,
                "ai_generated", f"Slide {i}: {desc[:50]}",
            )
            dest = session.dir / "materials" / path.name
            shutil.copy2(path, dest)
            return ("mat", i), path

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = []
            for i, slide in enumerate(plan.slides):
                futures.append(pool.submit(_gen_bg, i, slide))
                if slide.content_materials:
                    for mat in slide.content_materials:
                        if mat.action == "generate_illustration":
                            futures.append(pool.submit(_gen_illustration, i, mat, slide))

            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    key, path = result
                    results[key] = path

        return results
