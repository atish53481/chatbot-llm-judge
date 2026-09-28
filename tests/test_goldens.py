import json
import os

import pytest

from backend.datasets import goldens as g


@pytest.fixture(autouse=True)
def temp_goldens_file(tmp_path, monkeypatch):
    path = tmp_path / "goldens.json"
    path.write_text(json.dumps([
        {
            "id": "g_0001",
            "theme": "general_support",
            "question": "What is your refund window?",
            "expected_answer": "Refunds within 7 business days.",
            "context": ["Refunds within 7 business days."],
            "categories": ["policy"],
        }
    ]))
    monkeypatch.setattr(g, "GOLDENS_PATH", str(path))
    return path


def test_load_all_goldens():
    rows = g.load_goldens()
    assert len(rows) == 1
    assert rows[0]["question"] == "What is your refund window?"


def test_load_filters_by_theme():
    assert len(g.load_goldens(theme="general_support")) == 1
    assert len(g.load_goldens(theme="nonexistent")) == 0


def test_add_golden_appends_and_persists(temp_goldens_file):
    new_row = g.add_golden(
        theme="general_support",
        question="How do I reset my password?",
        expected_answer="Visit example.com/reset.",
        context=["Visit example.com/reset."],
        categories=["account"],
    )
    assert new_row["id"]
    assert len(g.load_goldens()) == 2
    on_disk = json.loads(temp_goldens_file.read_text())
    assert len(on_disk) == 2


def test_delete_golden_removes_row(temp_goldens_file):
    assert g.delete_golden("g_0001") is True
    assert g.load_goldens() == []
    assert g.delete_golden("g_missing") is False


def test_update_golden_replaces_fields_and_keeps_id(temp_goldens_file):
    updated = g.update_golden(
        "g_0001",
        theme="billing",
        question="How long do refunds take?",
        expected_answer="7 business days.",
        context=["Refunds take 7 business days."],
        categories=["policy"],
    )
    assert updated["id"] == "g_0001"
    rows = g.load_goldens()
    assert len(rows) == 1
    assert rows[0]["theme"] == "billing"
    assert rows[0]["question"] == "How long do refunds take?"
    assert rows[0]["expected_answer"] == "7 business days."
    assert rows[0]["context"] == ["Refunds take 7 business days."]
    on_disk = json.loads(temp_goldens_file.read_text())
    assert on_disk[0]["question"] == "How long do refunds take?"


def test_update_missing_golden_returns_none(temp_goldens_file):
    assert g.update_golden("g_missing", theme="t", question="q", expected_answer="a") is None
    assert len(g.load_goldens()) == 1


def test_add_golden_leaves_no_temp_file(temp_goldens_file):
    g.add_golden(theme="general_support", question="q", expected_answer="a")
    assert not os.path.exists(str(temp_goldens_file) + ".tmp")


def test_failed_write_keeps_existing_goldens(temp_goldens_file, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(g.json, "dump", boom)
        with pytest.raises(OSError):
            g.add_golden(theme="general_support", question="q", expected_answer="a")

    assert [r["id"] for r in g.load_goldens()] == ["g_0001"]
    assert not os.path.exists(str(temp_goldens_file) + ".tmp")


def test_add_golden_defaults_source_to_manual(temp_goldens_file):
    row = g.add_golden(theme="t", question="q", expected_answer="a")
    assert row["source"] == "manual"
    assert row["source_document"] is None


def test_add_golden_records_synthesized_source(temp_goldens_file):
    row = g.add_golden(
        theme="t",
        question="q",
        expected_answer="a",
        source="synthesized",
        source_document="policy.pdf",
    )
    assert row["source"] == "synthesized"
    assert row["source_document"] == "policy.pdf"


def test_reset_restores_the_reference_set_and_keeps_generated_goldens(tmp_path, monkeypatch):
    # The panel resets on every load: manual edits to a shipped theme are undone,
    # but a document's generated goldens stay until the document is deleted.
    defaults = tmp_path / "goldens.default.json"
    defaults.write_text(json.dumps([
        {"id": "g_0001", "theme": "general_support", "question": "Shipped?", "expected_answer": "Yes.",
         "context": [], "categories": []},
    ]), encoding="utf-8")
    monkeypatch.setattr(g, "DEFAULT_GOLDENS_PATH", str(defaults))
    g.add_golden(theme="general_support", question="Manual edit?", expected_answer="Scratch.",
                 context=[], categories=[])
    g.add_golden(theme="general_support", question="From the PRD?", expected_answer="Scratch.",
                 context=["ctx"], categories=[], source="synthesized", source_document="prd.docx")
    g.reset_to_defaults()
    assert [row["question"] for row in g.load_goldens(theme="general_support")] == ["Shipped?", "From the PRD?"]


def test_delete_generated_for_document_leaves_other_documents_alone(temp_goldens_file):
    g.add_golden(theme="general_support", question="From faq?", expected_answer="A", context=[],
                 categories=[], source="synthesized", source_document="faq.txt", source_document_id=7)
    g.add_golden(theme="general_support", question="From other?", expected_answer="A", context=[],
                 categories=[], source="synthesized", source_document="other.txt", source_document_id=8)

    assert g.delete_generated_for("general_support", source_document_id=7) == 1
    assert {row["question"] for row in g.load_goldens(theme="general_support")} == {
        "What is your refund window?", "From other?",
    }


def test_delete_generated_for_document_matches_a_reupload_by_name(temp_goldens_file):
    # A re-upload is a new document row, so the file name is what ties them.
    g.add_golden(theme="general_support", question="Old set", expected_answer="A", context=[],
                 categories=[], source="synthesized", source_document="faq.txt", source_document_id=7)
    assert g.delete_generated_for("general_support", source_document="faq.txt") == 1
    assert [row["question"] for row in g.load_goldens(theme="general_support")] == ["What is your refund window?"]


def test_delete_generated_for_document_never_touches_hand_written_goldens(temp_goldens_file):
    assert g.delete_generated_for("general_support", source_document="faq.txt") == 0
    assert len(g.load_goldens(theme="general_support")) == 1


def test_add_golden_records_the_document_id(temp_goldens_file):
    row = g.add_golden(theme="general_support", question="q", expected_answer="a",
                       source="synthesized", source_document="faq.txt", source_document_id=12)
    assert row["source_document_id"] == 12
