"""Agent usage example — run the PPTXAgent to generate a presentation."""

from edupptx.agent import PPTXAgent
from edupptx.config import Config

# Step 1: Configure and create agent
config = Config.from_env()
agent = PPTXAgent(config)

# Step 2: Run the agent — plans, generates materials, renders
session_dir = agent.run("勾股定理", requirements="适合初中生")

print(f"Session: {session_dir}")
print(f"Output: {session_dir / 'output.pptx'}")
