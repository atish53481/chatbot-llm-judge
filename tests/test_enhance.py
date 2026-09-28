from pathlib import Path

import pytest

from backend.rag.enhance import (
    ENHANCE_PROMPT,
    enhance_document,
    enhance_text,
    extract_text,
    split_sections,
)


class FakeJudge:
    """Returns a canned rewrite per call, the way LocalModel.generate does: (text, cost)."""

    def __init__(self, replies=None):
        self.prompts = []
        self.replies = replies

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.replies is not None:
            return self.replies.pop(0), 0.0
        return f"ENHANCED[{len(self.prompts)}]", 0.0


def test_split_sections_keeps_short_text_whole():
    assert split_sections("one\n\ntwo", max_chars=100) == ["one\n\ntwo"]


def test_split_sections_breaks_long_text_on_paragraphs():
    paragraphs = [f"Paragraph {i} " + "x" * 40 for i in range(10)]
    sections = split_sections("\n\n".join(paragraphs), max_chars=120)
    assert all(len(s) <= 120 for s in sections)
    assert "\n\n".join(sections).replace("\n\n", "") == "".join(paragraphs)


def test_split_sections_cuts_a_single_huge_paragraph():
    sections = split_sections("y" * 250, max_chars=100)
    assert [len(s) for s in sections] == [100, 100, 50]


def test_enhance_text_rewrites_each_section_with_the_no_new_facts_prompt():
    judge = FakeJudge()
    out = enhance_text("a" * 30 + "\n\n" + "b" * 30, judge, max_chars=40)
    assert out == "ENHANCED[1]\n\nENHANCED[2]"
    assert len(judge.prompts) == 2
    assert "Do not add" in ENHANCE_PROMPT and "a" * 30 in judge.prompts[0]


def test_enhance_text_strips_code_fences_and_keeps_empty_replies_original():
    judge = FakeJudge(replies=["```markdown\n# Refunds\nWithin 7 days.\n```", "   "])
    out = enhance_text("first part\n\nsecond part", judge, max_chars=12)
    assert out == "# Refunds\nWithin 7 days.\n\nsecond part"


def test_extract_text_reads_plain_text_and_rejects_empty(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# Help\nRefunds in 7 days.", encoding="utf-8")
    assert extract_text(f) == "# Help\nRefunds in 7 days."
    empty = tmp_path / "empty.txt"
    empty.write_text("   ", encoding="utf-8")
    with pytest.raises(ValueError, match="no text"):
        extract_text(empty)


def test_enhance_document_writes_an_enhanced_copy_next_to_the_original(tmp_path):
    src = tmp_path / "12_new_21.txt"
    src.write_text('[{"question": "Refund window?", "expected_answer": "30 days."}]', encoding="utf-8")
    out = enhance_document(src, FakeJudge(replies=["Returns are accepted within 30 days."]))
    assert out == tmp_path / "12_new_21_enhanced.txt"
    assert out.read_text(encoding="utf-8") == "Returns are accepted within 30 days."
    assert src.read_text(encoding="utf-8").startswith("[{")
