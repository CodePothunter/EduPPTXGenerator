"""Session management — structured output directory for a single generation run."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class Session:
    """Manages the output directory for a single PPT generation session."""

    def __init__(self, base_dir: Path):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir = Path(base_dir) / f"session_{ts}"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "materials").mkdir(exist_ok=True)
        (self.dir / "slides").mkdir(exist_ok=True)
        self.thinking_file = self.dir / "thinking.jsonl"
        self.output_path = self.dir / "output.pptx"

    def log_step(self, step_type: str, content: str) -> None:
        """Append a thinking step to thinking.jsonl."""
        entry = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "type": step_type,
            "content": content,
        }
        with open(self.thinking_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def save_plan(self, plan: dict) -> None:
        """Save the presentation plan as plan.json."""
        (self.dir / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_slide_state(self, index: int, slide_type: str, state: dict) -> None:
        """Save per-slide render state."""
        filename = f"slide_{index:02d}_{slide_type}.json"
        (self.dir / "slides" / filename).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
