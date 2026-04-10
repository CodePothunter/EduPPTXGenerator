"""Basic usage example for edupptx."""

from edupptx import generate

# Simplest usage — just a topic
path = generate("勾股定理")
print(f"Generated: {path}")

# With requirements and palette
path = generate(
    topic="光合作用",
    requirements="适合高中生，强调实验部分",
    palette="emerald",
    output_path="photosynthesis.pptx",
)
print(f"Generated: {path}")
