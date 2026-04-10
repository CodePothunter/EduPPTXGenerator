"""EduPPTX - AI-powered educational presentation generator."""

__version__ = "0.2.0"

# Lazy imports to avoid circular dependency during v0.2.0 migration
# generator.py will be deleted once agent.py is ready

def generate(*args, **kwargs):
    from edupptx.generator import generate as _generate
    return _generate(*args, **kwargs)

__all__ = ["generate"]
