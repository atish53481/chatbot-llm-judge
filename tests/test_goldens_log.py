import json

import pytest

from backend.datasets import goldens as g

SHIPPED = [
    {"id": "s1", "theme": "shop", "question": "q1", "expected_answer": "a1", "context": [], "categories": []},
    {"id": "s2", "theme": "shop", "question": "q2", "expected_answer": "a2", "context": [], "categories": []},
    {"id": "s3", "theme": "generic", "question": "q3", "expected_answer": "a3", "context": [], "categories": []},
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    live, defaults = tmp_path / "goldens.json", tmp_path / "goldens.default.json"
    live.write_text(json.dumps(SHIPPED), encoding="utf-8")
    defaults.write_text(json.dumps(SHIPPED), encoding="utf-8")
    monkeypatch.setattr(g, "GOLDENS_PATH", str(live))
    monkeypatch.setattr(g, "DEFAULT_GOLDENS_PATH", str(defaults))
    return tmp_path


def _log(store):
    path = store / "goldens_changes.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_each_write_logs_what_changed_and_who_changed_it(store):
    added = g.add_golden("shop", "new?", "yes")
    g.update_golden(added["id"], "shop", "new?", "yes, really")
    g.delete_golden("s1")
    add, update, delete = _log(store)
    assert add["action"] == "add_golden" and add["added"] == [added["id"]]
    assert update["action"] == "update_golden" and update["changed"] == [added["id"]]
    assert delete["action"] == "delete_golden" and delete["removed"] == ["s1"]
    assert delete["themes"]["shop"] == {"before": 3, "after": 2}
    assert all(entry["time"].endswith("Z") for entry in (add, update, delete))


def test_reset_logs_the_rows_it_brings_back(store):
    g.delete_golden("s1")
    g.delete_golden("s2")
    g.reset_to_defaults()
    reset = _log(store)[-1]
    assert reset["action"] == "reset_to_defaults"
    assert sorted(reset["added"]) == ["s1", "s2"] and reset["removed"] == []


def test_a_write_that_changes_nothing_is_not_logged(store):
    g.reset_to_defaults()
    assert _log(store) == []


def test_shipped_health_counts_missing_rows_per_theme(store):
    g.delete_golden("s1")
    g.add_golden("my_bot", "q", "a")
    health = {h["theme"]: h for h in g.shipped_health()}
    assert health["shop"] == {"theme": "shop", "shipped": 2, "present": 1, "missing": 1}
    assert health["generic"] == {"theme": "generic", "shipped": 1, "present": 1, "missing": 0}
    assert "my_bot" not in health
