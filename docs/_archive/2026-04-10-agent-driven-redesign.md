# EduPPTX v0.2.0 Agent-Driven Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform EduPPTX from a rigid 4-phase pipeline into a thin-agent architecture with enriched LLM planning, a persistent material library, diagram generation, session artifacts, and loguru logging.

**Architecture:** One enriched LLM call plans slides AND material decisions (backgrounds, diagrams, illustrations). A deterministic executor generates materials in parallel via ThreadPoolExecutor, then renders slides sequentially. All intermediate state goes to a session output directory.

**Tech Stack:** Python 3.10+, python-pptx, Pillow, OpenAI SDK (tool-use), Pydantic v2, loguru, Click

---

## File Structure

```
edupptx/
├── __init__.py             # MODIFY — new exports (PPTXAgent, run_agent)
├── agent.py                # CREATE — thin agent (replaces generator.py)
├── session.py              # CREATE — session directory + thinking.jsonl
├── material_library.py     # CREATE — persistent material library
├── diagram_gen.py          # CREATE — 5 Pillow diagram generators
├── backgrounds.py          # MODIFY — extract standalone functions, remove class
├── models.py               # MODIFY — add MaterialEntry, BackgroundAction, ContentMaterial
├── llm_client.py           # MODIFY — replace stdlib logging with loguru
├── config.py               # MODIFY — add library_dir, output_dir
├── cli.py                  # MODIFY — agent flow, loguru, library commands
├── content_planner.py      # MODIFY — replace stdlib logging with loguru
├── design_system.py        # NO CHANGE
├── layout_engine.py        # MODIFY — add material-positioned layouts
├── renderer.py             # MODIFY — support per-slide rendering with materials, loguru
├── icons.py                # MODIFY — replace stdlib logging with loguru
├── generator.py            # DELETE (after agent.py is complete)
└── prompts/
    ├── __init__.py          # NO CHANGE
    ├── content.py           # NO CHANGE (existing planning prompt kept as-is)
    └── agent.py             # CREATE — enriched agent system prompt

tests/
├── conftest.py             # MODIFY — add library + session fixtures
├── test_models.py          # MODIFY — add MaterialEntry/BackgroundAction tests
├── test_material_library.py # CREATE
├── test_session.py         # CREATE
├── test_diagram_gen.py     # CREATE
├── test_agent.py           # CREATE
├── test_layout_engine.py   # MODIFY — add material layout tests
└── test_renderer.py        # NO CHANGE
```

---

## Task 1: Add loguru + new config fields

**Files:**
- Modify: `pyproject.toml`
- Modify: `edupptx/config.py`

- [ ] **Step 1: Add loguru dependency**

```bash
cd /home/wxy/projects/EduPPTXGenerator && uv add loguru
```

- [ ] **Step 2: Add library_dir and output_dir to Config**

In `edupptx/config.py`, add two new fields and env var loading:

```python
@dataclass
class Config:
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = _DEFAULT_BASE_URL
    image_api_key: str = ""
    image_model: str = ""
    image_base_url: str = _DEFAULT_BASE_URL
    cache_dir: Path = field(default_factory=lambda: Path("./backgrounds_cache"))
    library_dir: Path = field(default_factory=lambda: Path("./materials_library"))
    output_dir: Path = field(default_factory=lambda: Path("./output"))

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> Config:
        load_dotenv(env_path or ".env")
        return cls(
            llm_api_key=os.getenv("GEN_APIKEY", ""),
            llm_model=os.getenv("GEN_MODEL", "").split("#")[0].strip(),
            llm_base_url=os.getenv("API_BASE_URL", _DEFAULT_BASE_URL),
            image_api_key=os.getenv("VISION_GEN_APIKEY", ""),
            image_model=os.getenv("VISION_GEN_MODEL", "").split("#")[0].strip(),
            image_base_url=os.getenv("API_BASE_URL", _DEFAULT_BASE_URL),
            cache_dir=Path(os.getenv("CACHE_DIR", "./backgrounds_cache")),
            library_dir=Path(os.getenv("LIBRARY_DIR", "./materials_library")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "./output")),
        )
```

- [ ] **Step 3: Replace stdlib logging with loguru across all modules**

In every file that has `import logging` and `log = logging.getLogger(__name__)`, replace with:

```python
from loguru import logger
```

Then replace all `log.info(...)` with `logger.info(...)`, `log.warning(...)` with `logger.warning(...)`, etc.

Files to update: `llm_client.py`, `content_planner.py`, `backgrounds.py`, `renderer.py`, `icons.py`.

Do NOT touch `cli.py` yet (that's Task 9).

- [ ] **Step 4: Run existing tests**

```bash
uv run pytest tests/ -v
```

Expected: All existing tests still pass (loguru is a drop-in for the log calls).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock edupptx/config.py edupptx/llm_client.py edupptx/content_planner.py edupptx/backgrounds.py edupptx/renderer.py edupptx/icons.py
git commit -m "🔧【重构】：添加 loguru 依赖，迁移所有模块到 loguru 日志，扩展 Config 字段"
```

---

## Task 2: Extended models (MaterialEntry, BackgroundAction, ContentMaterial)

**Files:**
- Modify: `edupptx/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write failing tests for new models**

Add to `tests/test_models.py`:

```python
from edupptx.models import (
    BackgroundAction,
    ContentMaterial,
    MaterialEntry,
    SlideContent,
)


def test_material_entry_creation():
    entry = MaterialEntry(
        id="mat_0001",
        type="background",
        tags=["math", "geometry"],
        palette="emerald",
        source="programmatic",
        description="Diagonal gradient background",
        resolution=(1920, 1080),
        path="backgrounds/mat_0001_bg.jpg",
        created_at="2026-04-10T14:30:00",
    )
    assert entry.id == "mat_0001"
    assert entry.type == "background"
    assert entry.tags == ["math", "geometry"]


def test_material_entry_serialization():
    entry = MaterialEntry(
        id="mat_0002",
        type="diagram",
        tags=["biology"],
        palette="emerald",
        source="programmatic",
        description="Flowchart",
        resolution=(1200, 800),
        path="diagrams/mat_0002_flow.png",
        created_at="2026-04-10T14:30:00",
    )
    data = entry.model_dump()
    restored = MaterialEntry.model_validate(data)
    assert restored.id == entry.id
    assert restored.resolution == (1200, 800)


def test_background_action_generate():
    action = BackgroundAction(action="generate", style="diagonal_gradient", tags=["math"])
    assert action.action == "generate"
    assert action.material_id is None


def test_background_action_reuse():
    action = BackgroundAction(action="reuse", material_id="mat_0001")
    assert action.action == "reuse"
    assert action.style is None


def test_content_material_diagram():
    mat = ContentMaterial(
        action="generate_diagram",
        position="center",
        diagram_type="flowchart",
        diagram_data={"nodes": [{"id": "1", "label": "Start"}], "edges": [], "direction": "TB"},
    )
    assert mat.action == "generate_diagram"
    assert mat.diagram_type == "flowchart"


def test_slide_content_with_materials():
    slide = SlideContent(
        type="content",
        title="Test",
        notes="Notes",
        bg_action=BackgroundAction(action="generate", style="radial_gradient", tags=["test"]),
        content_materials=[
            ContentMaterial(action="generate_diagram", position="center", diagram_type="timeline",
                           diagram_data={"events": [{"year": "2020", "label": "Event"}]}),
        ],
    )
    assert slide.bg_action is not None
    assert len(slide.content_materials) == 1


def test_slide_content_backward_compat():
    """SlideContent without new fields still works (backward compatible)."""
    slide = SlideContent(type="cover", title="Test", notes="Notes")
    assert slide.bg_action is None
    assert slide.content_materials is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_models.py -v -k "material or background_action or content_material or backward_compat"
```

Expected: FAIL — `MaterialEntry`, `BackgroundAction`, `ContentMaterial` not defined.

- [ ] **Step 3: Implement new models**

Add to `edupptx/models.py`:

```python
class MaterialEntry(BaseModel):
    """A material asset in the library (background, illustration, or diagram)."""

    id: str
    type: Literal["background", "illustration", "diagram"]
    tags: list[str] = Field(default_factory=list)
    palette: str = ""
    source: Literal["programmatic", "ai_generated", "user_uploaded"] = "programmatic"
    description: str = ""
    resolution: tuple[int, int] = (1920, 1080)
    path: str = Field(description="Relative path within library directory")
    created_at: str = ""


class BackgroundAction(BaseModel):
    """LLM-specified background decision for a slide."""

    action: Literal["generate", "reuse"]
    style: str | None = Field(default=None, description="Pillow style for generate action")
    material_id: str | None = Field(default=None, description="Library ID for reuse action")
    tags: list[str] = Field(default_factory=list)


class ContentMaterial(BaseModel):
    """LLM-specified content material decision for a slide."""

    action: Literal["generate_diagram", "generate_illustration", "reuse"]
    position: Literal["full", "left", "right", "center"] = "center"
    material_id: str | None = None
    diagram_type: str | None = Field(default=None, description="flowchart|timeline|comparison|hierarchy|cycle")
    diagram_data: dict | None = None
    illustration_description: str | None = None
    illustration_style: str | None = None
    tags: list[str] = Field(default_factory=list)
```

Extend `SlideContent` — add two optional fields at the end:

```python
class SlideContent(BaseModel):
    # ... existing fields ...
    bg_action: BackgroundAction | None = None
    content_materials: list[ContentMaterial] | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_models.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add edupptx/models.py tests/test_models.py
git commit -m "✨【功能】：添加 MaterialEntry/BackgroundAction/ContentMaterial 模型"
```

---

## Task 3: Session system

**Files:**
- Create: `edupptx/session.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_session.py`:

```python
import json
from pathlib import Path

from edupptx.session import Session


def test_session_creates_directory_structure(tmp_path):
    session = Session(tmp_path)
    assert session.dir.exists()
    assert (session.dir / "materials").is_dir()
    assert (session.dir / "slides").is_dir()
    assert session.thinking_file.name == "thinking.jsonl"
    assert session.output_path.name == "output.pptx"


def test_session_dir_name_has_timestamp(tmp_path):
    session = Session(tmp_path)
    assert session.dir.name.startswith("session_")
    # Format: session_YYYYMMDD_HHMMSS
    assert len(session.dir.name) == len("session_20260410_143022")


def test_log_step(tmp_path):
    session = Session(tmp_path)
    session.log_step("planning", "Planning 12 slides about 勾股定理")

    lines = session.thinking_file.read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["type"] == "planning"
    assert "勾股定理" in entry["content"]
    assert "ts" in entry


def test_log_step_appends(tmp_path):
    session = Session(tmp_path)
    session.log_step("planning", "Step 1")
    session.log_step("material", "Generating background")

    lines = session.thinking_file.read_text().strip().split("\n")
    assert len(lines) == 2


def test_save_plan(tmp_path):
    session = Session(tmp_path)
    plan = {"topic": "勾股定理", "slides": []}
    session.save_plan(plan)

    saved = json.loads((session.dir / "plan.json").read_text())
    assert saved["topic"] == "勾股定理"


def test_save_slide_state(tmp_path):
    session = Session(tmp_path)
    session.save_slide_state(0, "cover", {"title": "封面", "cards": []})
    session.save_slide_state(1, "content", {"title": "内容", "cards": []})

    files = list((session.dir / "slides").glob("*.json"))
    assert len(files) == 2
    assert (session.dir / "slides" / "slide_00_cover.json").exists()
    assert (session.dir / "slides" / "slide_01_content.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_session.py -v
```

Expected: FAIL — `edupptx.session` module not found.

- [ ] **Step 3: Implement Session**

Create `edupptx/session.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_session.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add edupptx/session.py tests/test_session.py
git commit -m "✨【功能】：添加 Session 会话管理模块"
```

---

## Task 4: Material library

**Files:**
- Create: `edupptx/material_library.py`
- Create: `tests/test_material_library.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_material_library.py`:

```python
import json
from pathlib import Path

import pytest

from edupptx.material_library import MaterialLibrary
from edupptx.models import MaterialEntry


@pytest.fixture
def library(tmp_path):
    return MaterialLibrary(tmp_path / "library")


@pytest.fixture
def sample_image(tmp_path):
    """Create a tiny valid image file for testing."""
    from PIL import Image
    img = Image.new("RGB", (100, 100), (255, 0, 0))
    path = tmp_path / "test_bg.jpg"
    img.save(path)
    return path


def test_empty_library(library):
    assert library.list_all() == []
    assert library.summary() == {"total": 0, "by_type": {}}


def test_add_material(library, sample_image):
    entry = library.add(
        source_path=sample_image,
        type="background",
        tags=["math", "geometry"],
        palette="emerald",
        source="programmatic",
        description="Test background",
    )
    assert entry.id == "mat_0000"
    assert entry.type == "background"
    assert (library.dir / entry.path).exists()


def test_add_increments_id(library, sample_image):
    e1 = library.add(sample_image, "background", ["a"], "emerald", "programmatic", "First")
    e2 = library.add(sample_image, "diagram", ["b"], "blue", "programmatic", "Second")
    assert e1.id == "mat_0000"
    assert e2.id == "mat_0001"


def test_search_by_tags(library, sample_image):
    library.add(sample_image, "background", ["math", "geometry"], "emerald", "programmatic", "Math bg")
    library.add(sample_image, "background", ["biology", "cell"], "emerald", "programmatic", "Bio bg")

    results = library.search(tags=["math"])
    assert len(results) == 1
    assert results[0].description == "Math bg"


def test_search_palette_bonus(library, sample_image):
    library.add(sample_image, "background", ["math"], "blue", "programmatic", "Blue math")
    library.add(sample_image, "background", ["math"], "emerald", "programmatic", "Emerald math")

    results = library.search(tags=["math"], palette="emerald")
    assert results[0].description == "Emerald math"  # palette bonus puts it first


def test_search_by_type(library, sample_image):
    library.add(sample_image, "background", ["math"], "emerald", "programmatic", "BG")
    library.add(sample_image, "diagram", ["math"], "emerald", "programmatic", "Diagram")

    results = library.search(tags=["math"], type="diagram")
    assert len(results) == 1
    assert results[0].type == "diagram"


def test_search_no_match(library, sample_image):
    library.add(sample_image, "background", ["math"], "emerald", "programmatic", "Math bg")
    results = library.search(tags=["biology"])
    assert len(results) == 0


def test_get_by_id(library, sample_image):
    entry = library.add(sample_image, "background", ["math"], "emerald", "programmatic", "Test")
    found = library.get(entry.id)
    assert found is not None
    assert found.id == entry.id


def test_get_missing(library):
    assert library.get("nonexistent") is None


def test_list_all_filtered(library, sample_image):
    library.add(sample_image, "background", ["a"], "emerald", "programmatic", "BG")
    library.add(sample_image, "diagram", ["b"], "emerald", "programmatic", "Diagram")

    bgs = library.list_all(type="background")
    assert len(bgs) == 1
    assert bgs[0].type == "background"


def test_index_persists(tmp_path, sample_image):
    lib_dir = tmp_path / "library"
    lib1 = MaterialLibrary(lib_dir)
    lib1.add(sample_image, "background", ["math"], "emerald", "programmatic", "Persisted")

    lib2 = MaterialLibrary(lib_dir)
    assert len(lib2.list_all()) == 1
    assert lib2.list_all()[0].description == "Persisted"


def test_summary(library, sample_image):
    library.add(sample_image, "background", ["a"], "emerald", "programmatic", "BG1")
    library.add(sample_image, "background", ["b"], "emerald", "programmatic", "BG2")
    library.add(sample_image, "diagram", ["c"], "emerald", "programmatic", "D1")

    s = library.summary()
    assert s["total"] == 3
    assert s["by_type"]["background"] == 2
    assert s["by_type"]["diagram"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_material_library.py -v
```

Expected: FAIL — `edupptx.material_library` not found.

- [ ] **Step 3: Implement MaterialLibrary**

Create `edupptx/material_library.py`:

```python
"""Persistent material library — searchable asset store for backgrounds, diagrams, illustrations."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger

from edupptx.models import MaterialEntry


class MaterialLibrary:
    """Manages a persistent library of visual materials."""

    def __init__(self, library_dir: Path):
        self.dir = Path(library_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self._entries: list[MaterialEntry] = self._load_index()

    def _load_index(self) -> list[MaterialEntry]:
        if not self.index_path.exists():
            return []
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        return [MaterialEntry.model_validate(e) for e in raw]

    def _save_index(self) -> None:
        data = [e.model_dump() for e in self._entries]
        self.index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def search(
        self,
        tags: list[str],
        type: str | None = None,
        palette: str | None = None,
    ) -> list[MaterialEntry]:
        """Search by tag overlap with optional type/palette filtering."""
        results: list[tuple[int, MaterialEntry]] = []
        for entry in self._entries:
            if type and entry.type != type:
                continue
            tag_score = len(set(tags) & set(entry.tags))
            if tag_score == 0:
                continue
            palette_bonus = 2 if palette and entry.palette == palette else 0
            results.append((tag_score + palette_bonus, entry))
        results.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in results]

    def add(
        self,
        source_path: Path,
        type: str,
        tags: list[str],
        palette: str,
        source: str,
        description: str,
        resolution: tuple[int, int] = (1920, 1080),
    ) -> MaterialEntry:
        """Copy file into library, register in index, return entry."""
        mat_id = f"mat_{len(self._entries):04d}"
        subdir = self.dir / f"{type}s"
        subdir.mkdir(exist_ok=True)
        dest = subdir / f"{mat_id}_{source_path.name}"
        shutil.copy2(source_path, dest)

        entry = MaterialEntry(
            id=mat_id,
            type=type,
            tags=tags,
            palette=palette,
            source=source,
            description=description,
            resolution=resolution,
            path=str(dest.relative_to(self.dir)),
            created_at=datetime.now().isoformat(),
        )
        self._entries.append(entry)
        self._save_index()
        logger.debug("Added material {} to library: {}", mat_id, description)
        return entry

    def get(self, material_id: str) -> MaterialEntry | None:
        """Get material by ID."""
        return next((e for e in self._entries if e.id == material_id), None)

    def list_all(self, type: str | None = None) -> list[MaterialEntry]:
        """List all materials, optionally filtered by type."""
        if type:
            return [e for e in self._entries if e.type == type]
        return list(self._entries)

    def summary(self) -> dict:
        """Summary for agent system prompt."""
        counts: dict[str, int] = {}
        for e in self._entries:
            counts[e.type] = counts.get(e.type, 0) + 1
        return {"total": len(self._entries), "by_type": counts}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_material_library.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add edupptx/material_library.py tests/test_material_library.py
git commit -m "✨【功能】：添加 MaterialLibrary 持久化素材库"
```

---

## Task 5: Diagram generation

**Files:**
- Create: `edupptx/diagram_gen.py`
- Create: `tests/test_diagram_gen.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_diagram_gen.py`:

```python
import pytest
from PIL import Image

from edupptx.design_system import get_design_tokens
from edupptx.diagram_gen import (
    generate_comparison,
    generate_cycle,
    generate_flowchart,
    generate_hierarchy,
    generate_timeline,
)


@pytest.fixture
def tokens():
    return get_design_tokens("emerald")


class TestFlowchart:
    def test_basic(self, tokens):
        data = {
            "nodes": [{"id": "1", "label": "开始"}, {"id": "2", "label": "结束"}],
            "edges": [{"from": "1", "to": "2"}],
            "direction": "TB",
        }
        img = generate_flowchart(data, tokens)
        assert isinstance(img, Image.Image)
        assert img.size == (1200, 800)

    def test_empty_nodes_returns_placeholder(self, tokens):
        data = {"nodes": [], "edges": [], "direction": "TB"}
        img = generate_flowchart(data, tokens)
        assert isinstance(img, Image.Image)  # placeholder, not crash

    def test_custom_size(self, tokens):
        data = {
            "nodes": [{"id": "1", "label": "A"}],
            "edges": [],
            "direction": "LR",
        }
        img = generate_flowchart(data, tokens, size=(800, 600))
        assert img.size == (800, 600)


class TestTimeline:
    def test_basic(self, tokens):
        data = {"events": [
            {"year": "2020", "label": "Event A"},
            {"year": "2021", "label": "Event B"},
            {"year": "2022", "label": "Event C"},
        ]}
        img = generate_timeline(data, tokens)
        assert isinstance(img, Image.Image)
        assert img.size == (1400, 400)

    def test_empty_events(self, tokens):
        data = {"events": []}
        img = generate_timeline(data, tokens)
        assert isinstance(img, Image.Image)


class TestComparison:
    def test_basic(self, tokens):
        data = {"columns": [
            {"header": "优点", "items": ["快速", "简单"]},
            {"header": "缺点", "items": ["成本高"]},
        ]}
        img = generate_comparison(data, tokens)
        assert isinstance(img, Image.Image)
        assert img.size == (1200, 800)


class TestHierarchy:
    def test_basic(self, tokens):
        data = {"root": {
            "label": "Root",
            "children": [
                {"label": "Child A", "children": []},
                {"label": "Child B", "children": [
                    {"label": "Grandchild", "children": []}
                ]},
            ],
        }}
        img = generate_hierarchy(data, tokens)
        assert isinstance(img, Image.Image)

    def test_single_node(self, tokens):
        data = {"root": {"label": "Alone", "children": []}}
        img = generate_hierarchy(data, tokens)
        assert isinstance(img, Image.Image)


class TestCycle:
    def test_basic(self, tokens):
        data = {"steps": [
            {"label": "Step 1"},
            {"label": "Step 2"},
            {"label": "Step 3"},
            {"label": "Step 4"},
        ]}
        img = generate_cycle(data, tokens)
        assert isinstance(img, Image.Image)
        assert img.size == (800, 800)

    def test_empty_steps(self, tokens):
        data = {"steps": []}
        img = generate_cycle(data, tokens)
        assert isinstance(img, Image.Image)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_diagram_gen.py -v
```

Expected: FAIL — `edupptx.diagram_gen` not found.

- [ ] **Step 3: Implement diagram generators**

Create `edupptx/diagram_gen.py`. This is a substantial file. Implement all 5 diagram types using Pillow. Each function validates input, returns a placeholder image for empty/invalid data, and uses DesignTokens for colors.

Key implementation notes:
- Use `ImageFont.truetype("NotoSansSC-Regular.otf", size)` for text, falling back to `ImageFont.load_default()` if the font is not found.
- All generators return `Image.Image` (RGBA mode for transparency).
- Empty data returns a placeholder image with centered text "No data".
- Helper function `_hex_to_rgb()` is shared (same as in backgrounds.py).
- Helper function `_draw_rounded_rect()` for consistent box drawing.
- Helper function `_draw_arrow()` for flowchart edges.

The implementation should be 200-300 lines covering all 5 diagram types. Each type arranges elements spatially using the provided size dimensions, draws boxes/circles/lines with DesignTokens colors.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_diagram_gen.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add edupptx/diagram_gen.py tests/test_diagram_gen.py
git commit -m "✨【功能】：添加 5 种图表程序化生成器（流程图/时间线/对比/层级/循环）"
```

---

## Task 6: Refactor backgrounds.py

**Files:**
- Modify: `edupptx/backgrounds.py`

- [ ] **Step 1: Refactor BackgroundManager into standalone functions**

Keep the 4 Pillow generation functions as module-level functions. Remove the `BackgroundManager` class. Keep `_hex_to_rgb()`, `_blend()`, and the 4 style generators.

New API:

```python
def generate_background(
    design: DesignTokens,
    style: str = "diagonal_gradient",
    output_dir: Path | None = None,
) -> Path:
    """Generate a single background image. Returns path to saved file."""
```

Keep `generate_ai_background()` as a standalone function too.

The key change: no more class, no more internal caching/index. The MaterialLibrary handles that now.

- [ ] **Step 2: Run existing tests**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass. (No existing tests directly test BackgroundManager internals. `test_renderer.py` may need checking.)

- [ ] **Step 3: Commit**

```bash
git add edupptx/backgrounds.py
git commit -m "🔧【重构】：backgrounds.py 提取独立函数，移除 BackgroundManager 类"
```

---

## Task 7: Layout engine extensions for content materials

**Files:**
- Modify: `edupptx/layout_engine.py`
- Modify: `tests/test_layout_engine.py`

- [ ] **Step 1: Write failing tests for new material-positioned layouts**

Add to `tests/test_layout_engine.py`:

```python
from edupptx.layout_engine import get_layout


def test_content_layout_with_full_material():
    layout = get_layout("content", 0, material_position="full")
    assert layout.material_slot is not None
    assert layout.material_slot.width > 0
    assert len(layout.cards) == 0  # full replaces cards


def test_content_layout_with_left_material():
    layout = get_layout("content", 2, material_position="left")
    assert layout.material_slot is not None
    # Material on left, cards on right
    assert layout.material_slot.x < layout.cards[0].x


def test_content_layout_with_center_material():
    layout = get_layout("content", 2, material_position="center")
    assert layout.material_slot is not None
    # Material between title and cards
    assert layout.material_slot.y > layout.title.y
    assert layout.material_slot.y < layout.cards[0].y
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_layout_engine.py -v -k "material"
```

Expected: FAIL — `get_layout()` doesn't accept `material_position` parameter, `SlotLayout` has no `material_slot`.

- [ ] **Step 3: Implement material positioning**

Add `material_slot: SlotPosition | None = None` to `SlotLayout` dataclass.

Modify `get_layout()` to accept optional `material_position: str | None = None`. When set:
- `"full"`: material_slot fills the content area (no cards). Width = content area width, height = content area height.
- `"left"`: material_slot takes left 45% of content area. Cards squeezed to right 50%.
- `"right"`: mirror of left.
- `"center"`: material_slot placed between title and cards, ~40% of content area height. Cards shifted down.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_layout_engine.py -v
```

Expected: ALL PASS (including old tests).

- [ ] **Step 5: Commit**

```bash
git add edupptx/layout_engine.py tests/test_layout_engine.py
git commit -m "✨【功能】：layout_engine 支持素材定位（full/left/right/center）"
```

---

## Task 8: Enriched agent prompt

**Files:**
- Create: `edupptx/prompts/agent.py`

- [ ] **Step 1: Create the enriched agent system prompt**

Create `edupptx/prompts/agent.py`:

```python
"""Enriched system prompt for the thin-agent content planner."""

from edupptx.prompts.content import ICON_CATALOG, SYSTEM_PROMPT as BASE_SYSTEM_PROMPT

DIAGRAM_TYPES_REFERENCE = """
## 可用图表类型

当幻灯片内容适合用图表表达时，在 content_materials 中指定图表生成指令。

| 类型 | 用途 | data 格式 |
|------|------|----------|
| flowchart | 流程/步骤 | {"nodes": [{"id": "1", "label": "步骤1"}], "edges": [{"from": "1", "to": "2"}], "direction": "TB"} |
| timeline | 时间线/历史 | {"events": [{"year": "2020", "label": "事件A", "description": "描述"}]} |
| comparison | 对比/优劣 | {"columns": [{"header": "优点", "items": ["快速", "简单"]}]} |
| hierarchy | 层级/分类 | {"root": {"label": "根", "children": [{"label": "子节点", "children": []}]}} |
| cycle | 循环/流转 | {"steps": [{"label": "步骤1", "description": "描述"}]} |
"""

MATERIAL_INSTRUCTIONS = """
## 素材决策指南

每个 slide 可以包含：
1. **bg_action** — 背景图决策：
   - `{"action": "generate", "style": "diagonal_gradient|radial_gradient|geometric_circles|geometric_triangles", "tags": ["主题标签"]}`
   - `{"action": "reuse", "material_id": "mat_xxxx"}` — 复用素材库中已有的素材

2. **content_materials** — 内容素材（图表/插图）：
   - 图表生成：`{"action": "generate_diagram", "position": "center|full|left|right", "diagram_type": "flowchart|timeline|comparison|hierarchy|cycle", "diagram_data": {...}, "tags": [...]}`
   - AI插图生成：`{"action": "generate_illustration", "position": "center|full|left|right", "illustration_description": "描述", "illustration_style": "flat|realistic|sketch|watercolor", "tags": [...]}`
   - 复用素材：`{"action": "reuse", "position": "center", "material_id": "mat_xxxx"}`

### position 说明
- `"full"`: 素材占满内容区域，替换卡片（此时 cards 应为空）
- `"left"` / `"right"`: 素材占一半，卡片占另一半
- `"center"`: 素材在标题和卡片之间

### 何时使用素材
- **流程/步骤类内容** → flowchart
- **历史/时间线** → timeline
- **对比/优劣** → comparison
- **分类/组织结构** → hierarchy
- **循环过程** → cycle
- **抽象概念需要可视化** → AI 插图
- **背景图** → 每页都需要，优先复用库中已有的

### 素材库当前状态
{library_summary}
"""


def build_agent_system_prompt(library_summary: str) -> str:
    """Build the enriched system prompt with library context."""
    return (
        BASE_SYSTEM_PROMPT
        + "\n\n"
        + MATERIAL_INSTRUCTIONS.format(library_summary=library_summary)
        + "\n\n"
        + DIAGRAM_TYPES_REFERENCE
    )


def build_agent_user_message(topic: str, requirements: str = "") -> str:
    """Build the user message for the enriched planning call."""
    parts = [f"请为以下教学主题设计完整的演示文稿方案：\n\n主题：{topic}"]
    if requirements:
        parts.append(f"\n附加要求：{requirements}")
    parts.append("\n请在每个 slide 中包含 bg_action 和 content_materials 决策。")
    return "\n".join(parts)
```

- [ ] **Step 2: Commit**

```bash
git add edupptx/prompts/agent.py
git commit -m "✨【功能】：添加 enriched agent 系统提示词（素材决策+图表类型参考）"
```

---

## Task 9: Agent core (thin agent)

**Files:**
- Create: `edupptx/agent.py`
- Create: `tests/test_agent.py`
- Modify: `edupptx/__init__.py`
- Delete: `edupptx/generator.py`

- [ ] **Step 1: Write failing tests for agent**

Create `tests/test_agent.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from edupptx.agent import PPTXAgent
from edupptx.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="http://localhost:8080/v1",
        library_dir=tmp_path / "library",
        output_dir=tmp_path / "output",
    )


def _mock_llm_response():
    """Return a minimal enriched plan as if the LLM returned it."""
    return {
        "topic": "测试主题",
        "palette": "emerald",
        "language": "zh",
        "slides": [
            {
                "type": "cover",
                "title": "测试封面",
                "subtitle": "副标题",
                "cards": [
                    {"icon": "book", "title": "卡片1", "body": "内容1"},
                    {"icon": "star", "title": "卡片2", "body": "内容2"},
                    {"icon": "target", "title": "卡片3", "body": "内容3"},
                ],
                "formula": None,
                "footer": None,
                "notes": "测试备注",
                "bg_action": {"action": "generate", "style": "diagonal_gradient", "tags": ["test"]},
                "content_materials": None,
            },
            {
                "type": "closing",
                "title": "结束",
                "subtitle": "感谢",
                "cards": [],
                "formula": None,
                "footer": None,
                "notes": "结束备注",
                "bg_action": {"action": "generate", "style": "radial_gradient", "tags": ["test"]},
                "content_materials": None,
            },
        ],
    }


@patch("edupptx.agent.LLMClient")
def test_agent_creates_session_dir(mock_llm_cls, config):
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _mock_llm_response()
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(config)
    result = agent.run("测试主题")

    assert result.exists()
    assert (result / "plan.json").exists()
    assert (result / "thinking.jsonl").exists()
    assert (result / "output.pptx").exists()


@patch("edupptx.agent.LLMClient")
def test_agent_saves_plan_json(mock_llm_cls, config):
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _mock_llm_response()
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(config)
    result = agent.run("测试主题")

    plan = json.loads((result / "plan.json").read_text())
    assert plan["topic"] == "测试主题"
    assert len(plan["slides"]) == 2


@patch("edupptx.agent.LLMClient")
def test_agent_writes_thinking_log(mock_llm_cls, config):
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _mock_llm_response()
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(config)
    result = agent.run("测试主题")

    lines = (result / "thinking.jsonl").read_text().strip().split("\n")
    assert len(lines) >= 1  # at least the planning step
    first = json.loads(lines[0])
    assert "type" in first
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_agent.py -v
```

Expected: FAIL — `edupptx.agent` not found.

- [ ] **Step 3: Implement PPTXAgent**

Create `edupptx/agent.py`:

```python
"""Thin agent — enriched LLM planning + deterministic execution."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from edupptx.backgrounds import generate_background
from edupptx.config import Config
from edupptx.design_system import DesignTokens, get_design_tokens
from edupptx.diagram_gen import generate_diagram
from edupptx.llm_client import LLMClient, ImageClient
from edupptx.material_library import MaterialLibrary
from edupptx.models import ContentMaterial, PresentationPlan, SlideContent
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

        # Step 1: Enriched planning
        session.log_step("planning", f"Planning slides for: {topic}")
        plan = self._plan(topic, requirements)
        session.save_plan(plan.model_dump())
        logger.info("Plan: {} slides, palette={}", len(plan.slides), plan.palette)

        # Step 2: Design tokens
        design = get_design_tokens(plan.palette)

        # Step 3: Execute material actions (parallel)
        session.log_step("materials", f"Generating materials for {len(plan.slides)} slides")
        backgrounds = self._execute_materials(plan, design, session)

        # Step 4: Render slides
        session.log_step("rendering", f"Rendering {len(plan.slides)} slides")
        renderer = PresentationRenderer(design)
        for i, slide in enumerate(plan.slides):
            bg = backgrounds.get(i)
            renderer.render_slide(slide, bg)
            session.save_slide_state(i, slide.type, slide.model_dump())

        # Step 5: Assemble
        renderer.save(session.output_path)
        session.log_step("done", f"Saved to {session.output_path}")
        logger.info("Done! {} slides, output: {}", len(plan.slides), session.output_path)

        return session.dir

    def _plan(self, topic: str, requirements: str) -> PresentationPlan:
        """Make the enriched planning LLM call."""
        library_summary = f"素材库: {self.library.summary()}"
        system = build_agent_system_prompt(library_summary)
        user = build_agent_user_message(topic, requirements)

        raw = self.llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        return PresentationPlan.model_validate(raw)

    def _execute_materials(
        self, plan: PresentationPlan, design: DesignTokens, session: Session
    ) -> dict[int, Path]:
        """Execute all material actions in parallel. Returns {slide_index: bg_path}."""
        backgrounds: dict[int, Path] = {}

        def _process_bg(i: int, slide: SlideContent) -> tuple[int, Path]:
            if slide.bg_action and slide.bg_action.action == "reuse" and slide.bg_action.material_id:
                entry = self.library.get(slide.bg_action.material_id)
                if entry:
                    return i, self.library.dir / entry.path

            style = "diagonal_gradient"
            if slide.bg_action and slide.bg_action.style:
                style = slide.bg_action.style
            tags = slide.bg_action.tags if slide.bg_action else []

            bg_path = generate_background(design, style)
            entry = self.library.add(
                bg_path, "background", tags, plan.palette, "programmatic",
                f"Background for slide {i}: {slide.title}",
            )
            # Copy to session materials
            dest = session.dir / "materials" / bg_path.name
            shutil.copy2(bg_path, dest)
            return i, bg_path

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_process_bg, i, s) for i, s in enumerate(plan.slides)]
            for future in as_completed(futures):
                idx, path = future.result()
                backgrounds[idx] = path

        # TODO: Handle content_materials (diagrams, illustrations) — Task 10
        return backgrounds
```

- [ ] **Step 4: Update renderer.py to support per-slide rendering**

The existing `PresentationRenderer.render()` takes the full plan + backgrounds list. We need a `render_slide()` method that handles a single slide with an optional background.

Add to `edupptx/renderer.py`:

```python
def render_slide(self, content: SlideContent, bg_path: Path | None = None) -> None:
    """Render a single slide into the presentation."""
    if bg_path is None:
        # Generate a fallback solid color background
        bg_path = self._make_fallback_bg()
    self._render_slide(content, bg_path)
```

- [ ] **Step 5: Update `__init__.py`**

Replace `edupptx/__init__.py`:

```python
"""EduPPTX - AI-powered educational presentation generator."""

from edupptx.agent import PPTXAgent
from edupptx.config import Config

__version__ = "0.2.0"


def run_agent(topic: str, requirements: str = "", **kwargs) -> "Path":
    """Main API entry point. Returns session directory path."""
    from pathlib import Path
    config = Config.from_env(kwargs.get("env_file", ".env"))
    agent = PPTXAgent(config)
    return agent.run(topic, requirements)


# Backward compat
def generate(topic: str, requirements: str = "", **kwargs) -> "Path":
    return run_agent(topic, requirements, **kwargs)


__all__ = ["PPTXAgent", "run_agent", "generate"]
```

- [ ] **Step 6: Delete generator.py**

```bash
git rm edupptx/generator.py
```

- [ ] **Step 7: Run ALL tests**

```bash
uv run pytest tests/ -v
```

Expected: ALL PASS.

- [ ] **Step 8: Commit**

```bash
git add edupptx/agent.py edupptx/__init__.py edupptx/renderer.py tests/test_agent.py
git rm edupptx/generator.py
git commit -m "✨【功能】：添加 PPTXAgent 薄代理核心，替换 generator.py"
```

---

## Task 10: CLI refactor + library commands

**Files:**
- Modify: `edupptx/cli.py`

- [ ] **Step 1: Rewrite cli.py with agent flow + loguru + library commands**

Replace `edupptx/cli.py` with agent-driven flow:
- `gen` command creates `PPTXAgent` and calls `run()`
- `-o` flag is now output directory (not file), defaults to `./output`
- Add `library` command group with `list`, `search`, `stats` subcommands
- Remove `logging.basicConfig()`, use loguru `logger.remove()` + `logger.add()`
- Keep existing `palettes` and `icons` commands

- [ ] **Step 2: Test CLI manually**

```bash
uv run edupptx --help
uv run edupptx gen --help
uv run edupptx library --help
uv run edupptx palettes
uv run edupptx icons
```

Expected: All help texts display correctly. No import errors.

- [ ] **Step 3: Commit**

```bash
git add edupptx/cli.py
git commit -m "🔧【重构】：CLI 改用 agent 驱动 + loguru 日志 + library 子命令"
```

---

## Task 11: Update conftest.py and fix test imports

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Update conftest with new fixtures**

Add library and session fixtures to `tests/conftest.py`:

```python
from edupptx.material_library import MaterialLibrary
from edupptx.session import Session


@pytest.fixture
def library(tmp_path):
    return MaterialLibrary(tmp_path / "test_library")


@pytest.fixture
def session(tmp_path):
    return Session(tmp_path / "test_output")
```

- [ ] **Step 2: Run all tests**

```bash
uv run pytest tests/ -v
```

Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "🔧【重构】：更新 conftest.py 添加 library/session fixtures"
```

---

## Task 12: README rewrite

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rewrite README.md**

Rewrite to:
1. Lead with "AI Agent that generates educational PPTs"
2. Show the agent thinking process (thinking.jsonl) as a feature
3. Update architecture diagram to show thin-agent flow
4. Add material library section
5. Add session output structure section
6. Update CLI reference with library commands
7. Update library usage section with `PPTXAgent` and `run_agent()`
8. Keep: slide types, palettes, icons, development sections

- [ ] **Step 2: Update CLAUDE.md architecture diagram**

Update the architecture section to match the new thin-agent flow.

- [ ] **Step 3: Update .gitignore**

Add `materials_library/` and `output/` to `.gitignore`.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md .gitignore
git commit -m "📝【文档】：README/CLAUDE.md 更新为 agent 驱动架构"
```

---

## Task 13: Final integration test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/test_integration.py`:

```python
"""Integration test: run the full agent with mocked LLM, verify output structure."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from edupptx.agent import PPTXAgent
from edupptx.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="http://localhost:8080/v1",
        library_dir=tmp_path / "library",
        output_dir=tmp_path / "output",
    )


def _full_plan():
    return {
        "topic": "勾股定理",
        "palette": "emerald",
        "language": "zh",
        "slides": [
            {
                "type": "cover",
                "title": "探索勾股定理",
                "subtitle": "数学之美",
                "cards": [
                    {"icon": "triangle", "title": "定义", "body": "直角三角形三边关系"},
                    {"icon": "calculator", "title": "计算", "body": "a² + b² = c²"},
                    {"icon": "lightbulb", "title": "应用", "body": "测量与工程"},
                ],
                "formula": "a² + b² = c²",
                "footer": None,
                "notes": "今天我们来学习勾股定理",
                "bg_action": {"action": "generate", "style": "diagonal_gradient", "tags": ["math"]},
                "content_materials": None,
            },
            {
                "type": "content",
                "title": "定理内容",
                "subtitle": None,
                "cards": [
                    {"icon": "book", "title": "条件", "body": "直角三角形"},
                    {"icon": "check-circle", "title": "结论", "body": "两直角边平方和等于斜边平方"},
                ],
                "formula": None,
                "footer": "勾股定理是几何学的基石",
                "notes": "让我们深入了解定理的内容",
                "bg_action": {"action": "generate", "style": "radial_gradient", "tags": ["math"]},
                "content_materials": None,
            },
            {
                "type": "closing",
                "title": "谢谢",
                "subtitle": "期待下次课",
                "cards": [],
                "formula": None,
                "footer": None,
                "notes": "本节课结束",
                "bg_action": {"action": "generate", "style": "geometric_circles", "tags": ["math"]},
                "content_materials": None,
            },
        ],
    }


@patch("edupptx.agent.LLMClient")
def test_full_agent_run(mock_llm_cls, config):
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _full_plan()
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(config)
    session_dir = agent.run("勾股定理")

    # Session directory structure
    assert session_dir.exists()
    assert (session_dir / "output.pptx").exists()
    assert (session_dir / "plan.json").exists()
    assert (session_dir / "thinking.jsonl").exists()
    assert (session_dir / "materials").is_dir()
    assert (session_dir / "slides").is_dir()

    # Plan JSON is valid
    plan = json.loads((session_dir / "plan.json").read_text())
    assert plan["topic"] == "勾股定理"
    assert len(plan["slides"]) == 3

    # Thinking log has entries
    lines = (session_dir / "thinking.jsonl").read_text().strip().split("\n")
    assert len(lines) >= 3  # planning, materials, rendering, done

    # Per-slide state files
    slide_files = list((session_dir / "slides").glob("*.json"))
    assert len(slide_files) == 3

    # PPTX is valid (non-empty file)
    pptx = session_dir / "output.pptx"
    assert pptx.stat().st_size > 0

    # Library got populated
    assert len(agent.library.list_all()) >= 3  # at least 3 backgrounds
```

- [ ] **Step 2: Run integration test**

```bash
uv run pytest tests/test_integration.py -v
```

Expected: PASS.

- [ ] **Step 3: Run ALL tests**

```bash
uv run pytest tests/ -v
```

Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "✅【测试】：添加完整 agent 集成测试"
```
