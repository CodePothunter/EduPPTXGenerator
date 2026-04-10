"""Agent usage example — build a plan, modify it, then render."""

from edupptx.config import Config
from edupptx.content_planner import ContentPlanner
from edupptx.generator import generate_from_plan
from edupptx.llm_client import LLMClient

# Step 1: Generate a plan via LLM
config = Config.from_env()
llm = LLMClient(config)
planner = ContentPlanner(llm)

plan = planner.plan("勾股定理", "适合初中生")

# Step 2: Agent can inspect and modify the plan
print(f"Plan has {len(plan.slides)} slides:")
for i, slide in enumerate(plan.slides):
    print(f"  {i+1}. [{slide.type}] {slide.title}")

# Example: add a custom slide
from edupptx.models import SlideCard, SlideContent

plan.slides.insert(-1, SlideContent(
    type="content",
    title="趣味数学：勾股数",
    cards=[
        SlideCard(icon="sparkles", title="经典三元组", body="(3,4,5) (5,12,13) (8,15,17)"),
        SlideCard(icon="search", title="发现规律", body="尝试找出更多满足 a²+b²=c² 的整数组合"),
    ],
    notes="这是一个扩展探索环节，鼓励学生自主发现勾股数的规律。",
))

# Step 3: Render the modified plan
path = generate_from_plan(plan, output_path="agent_modified.pptx")
print(f"Generated: {path}")
