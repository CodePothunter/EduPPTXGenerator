"""Basic usage example for edupptx — using the simple API."""

from edupptx import run_agent

# Simplest usage — just a topic, returns session directory path
session_dir = run_agent("勾股定理")
print(f"Session: {session_dir}")
print(f"Output: {session_dir / 'output.pptx'}")

# With requirements
session_dir = run_agent(
    topic="光合作用",
    requirements="适合高中生，强调实验部分",
)
print(f"Session: {session_dir}")
