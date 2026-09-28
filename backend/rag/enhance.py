"""Rewrites an uploaded document into clean reference prose before golden
generation.

Raw uploads are often JSON exports, tables, bullet fragments or messy notes,
which DeepEval's Synthesizer turns into few (or no) goldens. The judge model
restates the same facts as plain, self-contained sentences under headings; it
is told never to add facts. Long documents are rewritten section by section.
"""
from __future__ import annotations

from pathlib import Path

MAX_SECTION_CHARS = 8000

ENHANCE_PROMPT = """You are preparing a reference document so that test questions and \
answers can be generated from it.

Rewrite the text below as clean reference prose:
- Use short headings (Markdown "#"/"##") for topics.
- State every fact as a complete, self-contained sentence (name the product, policy \
or item each time instead of "it" or "this").
- Turn JSON, tables, lists and notes into readable sentences.
- Keep every number, time frame, price, name and condition exactly as written.
- Do not add facts, examples, advice or assumptions that are not in the text. Do not \
summarise away details. If the text says nothing useful, return it unchanged.

Return only the rewritten document, with no introduction or closing remarks.

TEXT:
{text}
"""


def extract_text(path: Path) -> str:
    """Plain text of a PDF, DOCX or text/Markdown file."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        text = "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    elif suffix == ".docx":
        import docx2txt

        text = docx2txt.process(str(path)) or ""
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    text = text.strip()
    if not text:
        raise ValueError(f"{path.name} has no text to work with (a scanned PDF has none)")
    return text


def split_sections(text: str, max_chars: int = MAX_SECTION_CHARS) -> list[str]:
    """Groups paragraphs into sections of at most max_chars; a single longer
    paragraph is cut into max_chars pieces."""
    sections: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        pieces = [paragraph[i:i + max_chars] for i in range(0, len(paragraph), max_chars)] or [""]
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= max_chars:
                current = candidate
            else:
                sections.append(current)
                current = piece
    if current:
        sections.append(current)
    return sections


def _strip_fences(reply: str) -> str:
    lines = reply.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def enhance_text(text: str, judge, max_chars: int = MAX_SECTION_CHARS) -> str:
    """Rewrites text section by section; a section the model returns empty is kept as is."""
    rewritten = []
    for section in split_sections(text, max_chars):
        reply = judge.generate(ENHANCE_PROMPT.format(text=section))
        reply = reply[0] if isinstance(reply, tuple) else reply
        rewritten.append(_strip_fences(str(reply or "")) or section)
    return "\n\n".join(rewritten)


def enhance_document(path: Path, judge) -> Path:
    """Writes the enhanced text next to the original as <stem>_enhanced.txt and
    returns that path; the original upload is left untouched."""
    out = path.with_name(f"{path.stem}_enhanced.txt")
    out.write_text(enhance_text(extract_text(path), judge), encoding="utf-8")
    return out
