"""Thin agent -- enriched LLM planning + deterministic execution."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from edupptx.backgrounds import generate_background
from edupptx.config import Config
from edupptx.design_system import DesignTokens, get_design_tokens
from edupptx.diagram_gen import generate_diagram
from edupptx.llm_client import LLMClient
from edupptx.material_library import MaterialLibrary
from edupptx.models import PresentationPlan, SlideContent
from edupptx.prompts.agent import build_agent_system_prompt, build_agent_user_message
from edupptx.renderer import PresentationRenderer
from edupptx.session import Session


class PPTXAgent:
    """Thin agent: 1 enriched LLM call + deterministic material/render execution."""

    def __init__(self, config: Config):
        self.config = config
        self.library = MaterialLibrary(config.library_dir)
        self.llm = LLMClient(config)

    def run(self, topic: str, requirements: str = "") -> Path:
        """Run the agent. Returns path to session directory."""
        session = Session(self.config.output_dir)
        logger.info("Session: {}", session.dir)

        # Step 1: Enriched planning (1 LLM call)
        session.log_step("planning", f"Planning slides for: {topic}")
        plan = self._plan(topic, requirements)
        session.save_plan(plan.model_dump())
        logger.info("Plan: {} slides, palette={}", len(plan.slides), plan.palette)

        # Step 2: Design tokens
        design = get_design_tokens(plan.palette)

        # Step 3: Execute material actions (parallel)
        session.log_step("materials", f"Generating materials for {len(plan.slides)} slides")
        slide_materials = self._execute_materials(plan, design, session)

        # Step 4: Render slides (sequential)
        session.log_step("rendering", f"Rendering {len(plan.slides)} slides")
        renderer = PresentationRenderer(design)
        for i, slide in enumerate(plan.slides):
            bg = slide_materials.get(("bg", i))
            renderer.render_slide(slide, bg)
            session.save_slide_state(i, slide.type, slide.model_dump())
            logger.debug("Rendered slide {}/{}: {}", i + 1, len(plan.slides), slide.type)

        # Step 5: Assemble
        renderer.save(session.output_path)
        session.log_step("done", f"Saved {len(plan.slides)} slides to {session.output_path}")
        logger.info("Done! {} slides, output: {}", len(plan.slides), session.output_path)

        return session.dir

    def _plan(self, topic: str, requirements: str) -> PresentationPlan:
        """Make the enriched planning LLM call."""
        library_summary = str(self.library.summary())
        system = build_agent_system_prompt(library_summary)
        user = build_agent_user_message(topic, requirements)

        raw = self.llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        return PresentationPlan.model_validate(raw)

    def _execute_materials(
        self, plan: PresentationPlan, design: DesignTokens, session: Session
    ) -> dict[tuple[str, int], Path]:
        """Execute all material actions in parallel. Returns {("bg"|"mat", slide_idx): path}."""
        results: dict[tuple[str, int], Path] = {}

        def _process_bg(i: int, slide: SlideContent) -> tuple[tuple[str, int], Path]:
            # Try reuse from library
            if slide.bg_action and slide.bg_action.action == "reuse" and slide.bg_action.material_id:
                entry = self.library.get(slide.bg_action.material_id)
                if entry:
                    path = self.library.dir / entry.path
                    if path.exists():
                        return ("bg", i), path

            # Generate new background
            style = "diagonal_gradient"
            if slide.bg_action and slide.bg_action.style:
                style = slide.bg_action.style
            tags = slide.bg_action.tags if slide.bg_action else []

            bg_path = generate_background(design, style)
            # Register in library
            self.library.add(
                bg_path, "background", tags, plan.palette, "programmatic",
                f"Background for slide {i}: {slide.title}",
            )
            # Copy to session
            dest = session.dir / "materials" / bg_path.name
            shutil.copy2(bg_path, dest)
            return ("bg", i), bg_path

        def _process_content_material(i: int, mat_idx: int, mat) -> tuple[tuple[str, int], Path] | None:
            if mat.action == "generate_diagram" and mat.diagram_type and mat.diagram_data:
                img = generate_diagram(mat.diagram_type, mat.diagram_data, design)
                # Save to temp file
                import tempfile
                path = Path(tempfile.mktemp(suffix=".png"))
                img.save(path, "PNG")
                # Register in library
                self.library.add(
                    path, "diagram", mat.tags, plan.palette, "programmatic",
                    f"Diagram for slide {i}: {mat.diagram_type}",
                    resolution=img.size,
                )
                dest = session.dir / "materials" / path.name
                shutil.copy2(path, dest)
                return ("mat", i), path

            if mat.action == "reuse" and mat.material_id:
                entry = self.library.get(mat.material_id)
                if entry:
                    path = self.library.dir / entry.path
                    if path.exists():
                        return ("mat", i), path

            # generate_illustration requires image API -- skip if not configured
            if mat.action == "generate_illustration":
                logger.warning("AI illustration generation not yet implemented, skipping")
                return None

            return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = []
            for i, slide in enumerate(plan.slides):
                futures.append(pool.submit(_process_bg, i, slide))
                if slide.content_materials:
                    for j, mat in enumerate(slide.content_materials):
                        futures.append(pool.submit(_process_content_material, i, j, mat))

            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    key, path = result
                    results[key] = path

        return results
