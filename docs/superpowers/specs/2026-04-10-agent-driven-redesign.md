# EduPPTX v0.2.0 — Agent-Driven Redesign

## Summary

Redesign EduPPTX from a rigid 4-phase pipeline into a ReAct tool-use agent. The agent
reasons step-by-step, decides what materials to generate vs reuse, and renders slides
incrementally. All intermediate state is observable via a structured session directory.

Five changes:
1. **Agent architecture** — ReAct loop replaces the fixed pipeline
2. **Material library** — persistent, searchable asset library with AI + programmatic generation
3. **Session & artifacts** — structured output directory with thinking.jsonl
4. **Diagram generation** — new Pillow-based diagram renderer (flowchart, timeline, etc.)
5. **Logging** — migrate from stdlib logging to loguru

## Architecture

```
User: "做一个勾股定理的PPT"
         │
         ▼
┌─────────────────────────────────────────────────────┐
│  PPTXAgent (ReAct loop)                             │
│                                                     │
│  while not done:                                    │
│    response = llm.chat(messages, tools=TOOL_BELT)   │
│    session.log_thinking(response)                   │
│    for tool_call in response.tool_calls:            │
│      result = execute_tool(tool_call)               │
│      session.log_tool(tool_call, result)            │
│      messages.append(tool_result(...))              │
│                                                     │
│  TOOL BELT:                                         │
│  ┌─────────────┐ ┌──────────────────┐               │
│  │ plan_slides  │ │ search_library   │               │
│  ├─────────────┤ ├──────────────────┤               │
│  │ gen_bg      │ │ gen_illustration │               │
│  ├─────────────┤ ├──────────────────┤               │
│  │ gen_diagram │ │ render_slide     │               │
│  ├─────────────┤ ├──────────────────┤               │
│  │ assemble    │ │ list_icons       │               │
│  ├─────────────┤ ├──────────────────┤               │
│  │ get_palettes│ │                  │               │
│  └─────────────┘ └──────────────────┘               │
└─────────────────────────────────────────────────────┘
         │
         ▼
output/session_YYYYMMDD_HHMMSS/
├── thinking.jsonl
├── plan.json
├── materials/
├── slides/
└── output.pptx
```

## Module Design

### New files

#### `edupptx/agent.py` — Agent core

```python
class PPTXAgent:
    def __init__(self, config: Config, session_dir: Path):
        self.config = config
        self.session = Session(session_dir)
        self.library = MaterialLibrary(config.library_dir)
        self.llm = LLMClient(config)  # extended with tool-use
        self.renderer = PresentationRenderer(...)  # lazy init after palette known
        self.tools = register_tools(self)

    def run(self, topic: str, requirements: str = "") -> Path:
        """Main ReAct loop. Returns path to session directory."""
        messages = [
            {"role": "system", "content": agent_system_prompt(self.library, self.tools)},
            {"role": "user", "content": f"Topic: {topic}\nRequirements: {requirements}"},
        ]

        while True:
            response = self.llm.chat_with_tools(messages, tools=self.tool_schemas)
            self.session.log_thinking(response)

            if not response.tool_calls:
                break  # agent is done

            for tool_call in response.tool_calls:
                result = self._execute_tool(tool_call)
                self.session.log_tool(tool_call, result)
                messages.append(make_tool_result(tool_call.id, result))

        return self.session.dir
```

Key behaviors:
- Agent system prompt includes: available tools, library contents summary, palette options,
  icon catalog, slide type reference. The agent has full context to make decisions.
- Each tool returns structured JSON. The agent sees the result and decides what to do next.
- Loop terminates when the agent responds without tool calls (natural end).
- Max iterations safety: 50 tool calls. If exceeded, assemble whatever is rendered so far.

#### `edupptx/tools.py` — Tool definitions + implementations

Each tool is a function + JSON schema for OpenAI tool_use format.

**Tool: `plan_slides`**
- Input: `{topic: str, requirements: str, palette: str}`
- Behavior: Calls LLM with the existing content planning prompt (from `prompts/content.py`).
  Returns structured slide plan.
- Output: `{slides: [{type, title, subtitle, cards, formula, footer, notes}], palette: str}`
- Side effect: Saves to `session/plan.json`

**Tool: `search_library`**
- Input: `{tags: list[str], type: str | null, palette: str | null}`
- Behavior: Searches MaterialLibrary by tag overlap. Scores by tag match + palette bonus.
- Output: `{matches: [{id, type, tags, palette, description, path}]}`
- No side effects.

**Tool: `generate_background`**
- Input: `{style: "diagonal_gradient" | "radial_gradient" | "geometric_circles" | "geometric_triangles", palette: str, tags: list[str]}`
- Behavior: Uses existing Pillow generation code from backgrounds.py.
  Saves to library. Copies to session materials/.
- Output: `{material_id: str, path: str}`

**Tool: `generate_illustration`**
- Input: `{description: str, style: "flat" | "realistic" | "sketch" | "watercolor", palette: str, tags: list[str]}`
- Behavior: Calls AI image model via ImageClient. Downloads result.
  Saves to library. Copies to session materials/.
- Output: `{material_id: str, path: str}`
- Requires: `config.image_api_key` and `config.image_model`. If not configured, returns error
  telling agent to use programmatic alternatives.

**Tool: `generate_diagram`**
- Input: `{diagram_type: "flowchart" | "timeline" | "comparison" | "hierarchy" | "cycle", data: dict, palette: str, tags: list[str]}`
- Behavior: Uses diagram_gen.py to render diagram with Pillow.
  Saves to library. Copies to session materials/.
- Output: `{material_id: str, path: str}`
- Data format per type:
  - flowchart: `{nodes: [{id, label}], edges: [{from, to, label?}], direction: "TB" | "LR"}`
  - timeline: `{events: [{year, label, description?}]}`
  - comparison: `{columns: [{header, items: [str]}]}`
  - hierarchy: `{root: {label, children: [{label, children: [...]}]}}`
  - cycle: `{steps: [{label, description?}]}`

**Tool: `render_slide`**
- Input: `{index: int, slide_type: str, title: str, subtitle: str | null, cards: [{icon, title, body}], formula: str | null, footer: str | null, notes: str | null, background_material_id: str | null, content_materials: [{material_id: str, position: "left" | "right" | "center" | "full"}] | null}`
- Behavior: Uses existing layout_engine.py + renderer.py to render one slide.
  If background_material_id provided, uses that material as background.
  If content_materials provided, embeds illustrations/diagrams into the slide.
  Material positioning: "full" replaces the cards area entirely (full-bleed image with title),
  "left"/"right" splits the content area 50/50 (image + cards), "center" places the image
  between title and cards. The layout_engine handles these as new layout variants.
  Saves slide state to session/slides/.
- Output: `{slide_index: int, status: "rendered"}`

**Tool: `assemble_pptx`**
- Input: `{filename: str}`
- Behavior: Calls renderer.save(). Copies to session/output.pptx.
- Output: `{output_path: str, slide_count: int, file_size_kb: int}`

**Tool: `list_icons`**
- Input: `{}`
- Behavior: Returns available Lucide icon names from icons.py.
- Output: `{icons: [str], count: int}`

**Tool: `get_palettes`**
- Input: `{}`
- Behavior: Returns all palette names with color details from design_system.py.
- Output: `{palettes: [{name, accent, bg_overlay, text_primary, ...}]}`

#### `edupptx/session.py` — Session management

```python
class Session:
    def __init__(self, base_dir: Path):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir = base_dir / f"session_{ts}"
        self.dir.mkdir(parents=True)
        (self.dir / "materials").mkdir()
        (self.dir / "slides").mkdir()
        self.thinking_file = self.dir / "thinking.jsonl"
        self.output_path = self.dir / "output.pptx"

    def log_thinking(self, response) -> None:
        """Extract assistant message content, append to thinking.jsonl."""
        entry = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "type": "thinking",
            "content": response.content,
        }
        with open(self.thinking_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_tool(self, tool_call, result) -> None:
        """Log tool call and result as two entries in thinking.jsonl."""
        call_entry = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "type": "tool_call",
            "tool": tool_call.function.name,
            "args": json.loads(tool_call.function.arguments),
        }
        result_entry = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "type": "tool_result",
            "tool": tool_call.function.name,
            "result": result,
        }
        with open(self.thinking_file, "a") as f:
            f.write(json.dumps(call_entry, ensure_ascii=False) + "\n")
            f.write(json.dumps(result_entry, ensure_ascii=False) + "\n")

    def save_plan(self, plan: dict) -> None:
        (self.dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))

    def save_slide_state(self, index: int, slide_type: str, state: dict) -> None:
        filename = f"slide_{index:02d}_{slide_type}.json"
        (self.dir / "slides" / filename).write_text(
            json.dumps(state, ensure_ascii=False, indent=2)
        )
```

#### `edupptx/material_library.py` — Persistent material library

```python
@dataclass
class MaterialEntry:
    id: str
    type: str  # "background" | "illustration" | "diagram"
    tags: list[str]
    palette: str
    source: str  # "programmatic" | "ai_generated" | "user_uploaded"
    description: str
    resolution: tuple[int, int]
    path: str  # relative to library root
    created_at: str

class MaterialLibrary:
    def __init__(self, library_dir: Path):
        self.dir = library_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self._entries: list[MaterialEntry] = self._load_index()

    def search(
        self,
        tags: list[str],
        type: str | None = None,
        palette: str | None = None,
    ) -> list[MaterialEntry]:
        """Search by tag overlap, optionally filter by type/palette.
        Returns matches sorted by relevance score (tag overlap + palette bonus)."""
        results = []
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
        subdir = self.dir / f"{type}s"  # backgrounds/, illustrations/, diagrams/
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
        return entry

    def get(self, material_id: str) -> MaterialEntry | None:
        return next((e for e in self._entries if e.id == material_id), None)

    def list_all(self, type: str | None = None) -> list[MaterialEntry]:
        if type:
            return [e for e in self._entries if e.type == type]
        return list(self._entries)

    def summary(self) -> dict:
        """Summary for agent system prompt: counts by type."""
        counts = {}
        for e in self._entries:
            counts[e.type] = counts.get(e.type, 0) + 1
        return {"total": len(self._entries), "by_type": counts}
```

#### `edupptx/diagram_gen.py` — Programmatic diagram generation

Five diagram types, all rendered with Pillow using DesignTokens colors.

```python
def generate_flowchart(data: dict, tokens: DesignTokens, size: tuple[int, int] = (1200, 800)) -> Image:
    """Render flowchart: boxes connected by arrows.
    data: {nodes: [{id, label}], edges: [{from, to, label?}], direction: "TB"|"LR"}
    """

def generate_timeline(data: dict, tokens: DesignTokens, size: tuple[int, int] = (1400, 400)) -> Image:
    """Render horizontal timeline with event markers.
    data: {events: [{year, label, description?}]}
    """

def generate_comparison(data: dict, tokens: DesignTokens, size: tuple[int, int] = (1200, 800)) -> Image:
    """Render side-by-side comparison columns.
    data: {columns: [{header, items: [str]}]}
    """

def generate_hierarchy(data: dict, tokens: DesignTokens, size: tuple[int, int] = (1200, 800)) -> Image:
    """Render tree hierarchy.
    data: {root: {label, children: [{label, children: [...]}]}}
    """

def generate_cycle(data: dict, tokens: DesignTokens, size: tuple[int, int] = (800, 800)) -> Image:
    """Render circular flow diagram.
    data: {steps: [{label, description?}]}
    """
```

Each function returns a Pillow Image. The tool wrapper saves it to PNG + registers in library.

Styling:
- Boxes: rounded rectangles filled with `tokens.accent_light`, border in `tokens.accent`
- Text: `tokens.text_primary` for labels, Noto Sans SC font
- Arrows/lines: `tokens.accent` color, 3px width
- Background: transparent (PNG) so it composites on slide backgrounds

#### `edupptx/prompts/agent.py` — Agent system prompt

The agent's system prompt tells it:
- What tools are available and when to use each
- Current library contents summary (so it knows what's reusable)
- Available palettes and icons
- Slide type reference (from existing content.py)
- Guidelines: prefer reusing materials, generate diagrams for structured content,
  use AI illustrations for conceptual/artistic content, use programmatic backgrounds
- Output expectations: render all slides, then assemble

### Modified files

#### `edupptx/llm_client.py` — Add tool-use support

Extend `LLMClient` with a `chat_with_tools` method:
```python
def chat_with_tools(self, messages: list[dict], tools: list[dict]) -> ChatCompletion:
    """Chat completion with tool-use. Returns response with possible tool_calls."""
    return self.client.chat.completions.create(
        model=self.model,
        messages=messages,
        tools=tools,
        timeout=180,
        extra_body={"thinking": {"type": "disabled"}},
    )
```

#### `edupptx/config.py` — New config fields

Add:
- `library_dir: Path` — material library location (default: `./materials_library`)
- `output_dir: Path` — session output base directory (default: `./output`)

#### `edupptx/models.py` — New models

Add `MaterialEntry` Pydantic model (for serialization in index.json).

#### `edupptx/backgrounds.py` — Refactor

Extract the 4 Pillow generation functions into standalone functions (they become the
backend for `generate_background` tool). Remove BackgroundManager class — its
responsibilities are split between MaterialLibrary (caching/search) and the tool
(generation).

#### `edupptx/cli.py` — Agent-driven flow + loguru

```python
import sys
from loguru import logger

@click.group()
@click.option("--verbose", "-v", is_flag=True)
def main(verbose: bool):
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
        level="DEBUG" if verbose else "INFO",
    )

@main.command()
@click.argument("topic")
@click.option("-r", "--requirements", default="")
@click.option("-o", "--output", default="./output", type=click.Path())
@click.option("-p", "--palette", default=None)
@click.option("--env-file", default=".env")
def gen(topic, requirements, output, palette, env_file):
    config = Config.from_env(env_file)
    config.output_dir = Path(output)
    if palette:
        requirements += f"\npalette: {palette}"

    agent = PPTXAgent(config, config.output_dir)
    # Add file logger to session dir
    logger.add(
        agent.session.dir / "edupptx.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="DEBUG",
    )

    session_dir = agent.run(topic, requirements)

    # Print summary
    logger.info(f"Output: {session_dir / 'output.pptx'}")
    logger.info(f"Thinking log: {session_dir / 'thinking.jsonl'}")

@main.group()
def library():
    pass

@library.command("list")
def library_list():
    config = Config.from_env()
    lib = MaterialLibrary(config.library_dir)
    for entry in lib.list_all():
        logger.info(f"[{entry.type}] {entry.id}: {entry.description} ({entry.tags})")

@library.command("search")
@click.option("--tags", required=True, help="Comma-separated tags")
@click.option("--type", default=None)
def library_search(tags, type):
    config = Config.from_env()
    lib = MaterialLibrary(config.library_dir)
    results = lib.search(tags=tags.split(","), type=type)
    for entry in results:
        logger.info(f"[{entry.type}] {entry.id}: {entry.description}")

@library.command("stats")
def library_stats():
    config = Config.from_env()
    lib = MaterialLibrary(config.library_dir)
    summary = lib.summary()
    logger.info(f"Total materials: {summary['total']}")
    for type_name, count in summary["by_type"].items():
        logger.info(f"  {type_name}: {count}")
```

#### `edupptx/__init__.py` — New exports

```python
from edupptx.agent import PPTXAgent
from edupptx.material_library import MaterialLibrary

def run_agent(topic: str, requirements: str = "", **kwargs) -> Path:
    """Main API entry point. Returns session directory path."""
    config = Config.from_env(kwargs.get("env_file", ".env"))
    agent = PPTXAgent(config, config.output_dir)
    return agent.run(topic, requirements)

# Backward compat (thin wrapper)
def generate(topic: str, requirements: str = "", **kwargs) -> Path:
    return run_agent(topic, requirements, **kwargs)
```

### Deleted files

- `edupptx/generator.py` — replaced by `agent.py`

### Migration

- `backgrounds_cache/` contents are migrated into `materials_library/backgrounds/` on first run.
  The migration is automatic: if `backgrounds_cache/index.json` exists and
  `materials_library/` does not, copy and convert entries.

## Dependencies

Add to pyproject.toml:
- `loguru>=0.7.0` — structured logging

Remove: nothing. All existing deps are still used.

## Testing Strategy

### Existing tests
- `test_models.py` — keep as-is, add MaterialEntry tests
- `test_layout_engine.py` — keep as-is
- `test_renderer.py` — keep as-is

### New tests
- `test_material_library.py` — search, add, get, migration from backgrounds_cache
- `test_session.py` — thinking.jsonl writing, slide state, directory structure
- `test_diagram_gen.py` — each diagram type produces valid PNG, correct dimensions
- `test_tools.py` — each tool function returns expected schema
- `test_agent.py` — mock LLM responses, verify agent loop terminates, correct tool dispatch

### Integration test
- `test_integration.py` — run agent with mocked LLM, verify full session directory output

## README Changes

Rewrite README.md to:
1. Lead with "AI Agent that generates educational PPTs" (not "pipeline")
2. Show the agent thinking process as a feature
3. Update architecture diagram to show agent loop
4. Add material library section
5. Add session output structure
6. Update CLI reference with library commands
7. Keep: slide types, palettes, icons, development sections

## Error Handling

- LLM call failures: retry once, then log error and continue with partial output
- Material generation failures: log warning, agent sees error and adapts (uses alternative)
- Max iterations (50 tool calls): force assemble with whatever is rendered
- Missing image API config: generate_illustration tool returns error, agent falls back to
  programmatic alternatives
- Invalid tool arguments: return structured error, agent can retry with corrected args

## Non-Goals (explicitly out of scope)

- Real-time streaming of agent events (directory artifacts are sufficient)
- Multi-agent architecture (single agent with tools)
- Custom slide template editor
- Web UI or API server
- Slide preview/thumbnail generation
