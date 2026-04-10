"""Thin agent — content planning + per-slide material decisions + parallel execution."""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from edupptx.backgrounds import generate_background
from edupptx.config import Config
from edupptx.content_planner import ContentPlanner
from edupptx.design_system import DesignTokens, get_design_tokens
from edupptx.diagram_gen import generate_diagram
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

素材库当前状态: {library_summary}
"""


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
            renderer.render_slide(slide, bg)
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

                # Apply background decision
                bg_style = decision.get("bg_style", "diagonal_gradient")
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

                session.log_step(
                    "material_decision",
                    f"Slide {idx+1}: bg={bg_style}, diagram={diagram.get('type') if diagram else 'none'}",
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

            bg_path = generate_background(design, style)
            self.library.add(
                bg_path, "background", tags, plan.palette, "programmatic",
                f"Slide {i}: {slide.title}",
            )
            dest = session.dir / "materials" / bg_path.name
            shutil.copy2(bg_path, dest)
            return ("bg", i), bg_path

        def _gen_diagram(i: int, mat: ContentMaterial) -> tuple[tuple[str, int], Path] | None:
            if not mat.diagram_type or not mat.diagram_data:
                return None
            try:
                img = generate_diagram(mat.diagram_type, mat.diagram_data, design)
                import tempfile
                path = Path(tempfile.mktemp(suffix=".png"))
                img.save(path, "PNG")
                self.library.add(
                    path, "diagram", mat.tags, plan.palette, "programmatic",
                    f"Slide {i}: {mat.diagram_type}",
                    resolution=img.size,
                )
                dest = session.dir / "materials" / path.name
                shutil.copy2(path, dest)
                return ("mat", i), path
            except Exception as e:
                logger.warning("Diagram generation failed for slide {}: {}", i, e)
                return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = []
            for i, slide in enumerate(plan.slides):
                futures.append(pool.submit(_gen_bg, i, slide))
                if slide.content_materials:
                    for mat in slide.content_materials:
                        if mat.action == "generate_diagram":
                            futures.append(pool.submit(_gen_diagram, i, mat))

            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    key, path = result
                    results[key] = path

        return results
