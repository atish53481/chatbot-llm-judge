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
