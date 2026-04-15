"""Parse documents (PDF, DOCX, Markdown, TXT) into plain text."""

from __future__ import annotations

from pathlib import Path

_SUPPORTED = {".pdf", ".docx", ".md", ".txt"}


def parse_document(file_path: str | Path) -> str:
    """Parse a document file and return plain text content."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return _parse_pdf(path)
    elif suffix == ".docx":
        return _parse_docx(path)
    elif suffix in (".md", ".txt"):
        return path.read_text(encoding="utf-8")
    else:
        raise ValueError(
            f"Unsupported format '{suffix}'. Supported: {', '.join(sorted(_SUPPORTED))}"
        )


def _parse_pdf(path: Path) -> str:
    import fitz  # pymupdf

    doc = fitz.open(str(path))
    pages = []
    for page in doc:
        text = page.get_text()
        if text.strip():
            pages.append(text.strip())
    doc.close()
    return "\n\n".join(pages)


def _parse_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)
