"""EduPPTX - AI-powered educational presentation generator."""

from edupptx.agent import PPTXAgent
from edupptx.config import Config

__version__ = "0.2.0"


def run_agent(topic: str, requirements: str = "", **kwargs):
    """Main API entry point. Returns session directory path."""
    config = Config.from_env(kwargs.get("env_file", ".env"))
    agent = PPTXAgent(config)
    return agent.run(topic, requirements)


# Backward compat
def generate(topic: str, requirements: str = "", **kwargs):
    return run_agent(topic, requirements, **kwargs)


__all__ = ["PPTXAgent", "run_agent", "generate"]
