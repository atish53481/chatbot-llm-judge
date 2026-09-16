import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.datasets import goldens as goldens_store

EXTENSION_ORIGIN = "chrome-extension://" + "a" * 32


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    # The app opens its database at import time, so the env var must be set
    # before the first import; later tests in the session reuse that database.
    monkeypatch.setenv("JUDGE_DB_PATH", str(tmp_path / "app_test.db"))
    monkeypatch.setenv("JUDGE_API_KEY", "fake-key")
    # API tests must never rewrite the tracked goldens.json.
    goldens_copy = tmp_path / "goldens.json"
    shutil.copy(goldens_store.GOLDENS_PATH, goldens_copy)
    monkeypatch.setattr(goldens_store, "GOLDENS_PATH", str(goldens_copy))
    from backend.dashboard import app as module
    return module


@pytest.fixture
def client(app_module):
    return TestClient(app_module.app, base_url="http://127.0.0.1")


def _create_target(client, **overrides):
    body = {"name": "Sample (mock)", "type": "mock", "config": {}}
    body.update(overrides)
    r = client.post("/api/targets", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _fake_spec(seen_themes):
    metric = MagicMock()
    metric.score = 0.85
    metric.is_successful.return_value = True
    metric.reason = "fine"

    def cases(theme="general_support"):
        seen_themes.append(theme)
        return [{"id": "g1", "theme": theme, "question": "hi",
                 "expected_answer": "hello", "context": []}]

    return SimpleNamespace(
        key="answer_relevancy",
        threshold=0.7,
        cases=cases,
        build_metric=lambda judge: metric,
        build_case=lambda g, reply: SimpleNamespace(input=g["question"], actual_output=reply),
    )


def test_status_reports_judge_configured(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["judge"]["up"] is True


def test_status_reports_judge_missing(client, monkeypatch):
    monkeypatch.delenv("JUDGE_API_KEY")
    assert client.get("/api/status").json()["judge"]["up"] is False


def test_create_list_delete_target(client):
    target = _create_target(client)
    assert target["name"] == "Sample (mock)"
    assert any(t["id"] == target["id"] for t in client.get("/api/targets").json())

    assert client.delete(f"/api/targets/{target['id']}").status_code == 200
    assert not any(t["id"] == target["id"] for t in client.get("/api/targets").json())


def test_delete_unknown_target_returns_404(client):
    assert client.delete("/api/targets/999999").status_code == 404


def test_create_target_rejects_unknown_type_and_bad_http_config(client):
    before = len(client.get("/api/targets").json())
    r = client.post("/api/targets", json={"name": "x", "type": "carrier-pigeon", "config": {}})
    assert r.status_code == 400
    r = client.post("/api/targets", json={"name": "x", "type": "http", "config": {}})
    assert r.status_code == 400  # base_url missing
    assert len(client.get("/api/targets").json()) == before


def test_target_responses_mask_header_values(client):
    created = _create_target(
        client,
        name="OpenAI bot",
        type="http",
        config={"base_url": "http://127.0.0.1:9",
                "headers": {"Authorization": "Bearer sk-secret"}},
    )
    assert created["config"]["headers"] == {"Authorization": "***"}
    listing = client.get("/api/targets")
    assert "sk-secret" not in listing.text
    assert any(t["id"] == created["id"] for t in listing.json())


def test_openai_preset_expands_config_and_hides_key(client):
    created = _create_target(
        client,
        name="GPT bot",
        type="http",
        preset="openai_compatible",
        config={"base_url": "https://api.example.com", "api_key": "sk-live-123",
                "model": "gpt-4o-mini", "theme": "billing"},
    )
    config = created["config"]
    assert config["request_format"] == "openai_messages"
    assert config["chat_path"] == "/v1/chat/completions"
    assert config["model"] == "gpt-4o-mini"
    assert config["theme"] == "billing"
    assert config["headers"] == {"Authorization": "***"}
    assert "api_key" not in config
    assert "sk-live-123" not in client.get("/api/targets").text


def test_preset_rejects_unknown_name_wrong_type_or_missing_key(client):
    r = client.post("/api/targets", json={"name": "x", "type": "http", "preset": "nope",
                                          "config": {"base_url": "https://a", "api_key": "k"}})
    assert r.status_code == 400
    r = client.post("/api/targets", json={"name": "x", "type": "mock",
                                          "preset": "openai_compatible", "config": {}})
    assert r.status_code == 400
    r = client.post("/api/targets", json={"name": "x", "type": "http",
                                          "preset": "openai_compatible",
                                          "config": {"base_url": "https://a"}})
    assert r.status_code == 400  # api_key missing


def test_goldens_crud(client):
    r = client.post("/api/goldens", json={
        "theme": "general_support",
        "question": "Does it ship internationally?",
        "expected_answer": "Yes, 10-14 business days.",
        "context": ["Yes, 10-14 business days."],
        "categories": ["shipping"],
    })
    assert r.status_code == 200
    golden_id = r.json()["id"]
    assert any(g["id"] == golden_id for g in client.get("/api/goldens").json())
    themed = client.get("/api/goldens", params={"theme": "general_support"}).json()
    assert themed and all(g["theme"] == "general_support" for g in themed)

    assert client.delete(f"/api/goldens/{golden_id}").status_code == 200
    assert client.delete(f"/api/goldens/{golden_id}").status_code == 404


def test_create_golden_rejects_empty_question(client):
    r = client.post("/api/goldens",
                    json={"theme": "general_support", "question": "", "expected_answer": "a"})
    assert r.status_code == 422


def test_metrics_endpoint_lists_catalog(client):
    keys = {m["key"] for m in client.get("/api/metrics").json()}
    assert {"answer_relevancy", "faithfulness", "hallucination", "bias",
            "toxicity", "pii_leakage", "correctness"} <= keys


def test_run_uses_target_theme_and_records_one_history_row(client, app_module):
    target = _create_target(client, name="Billing bot", config={"theme": "billing"})
    seen = []
    with patch.object(app_module, "build_judge", return_value=object()), \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": _fake_spec(seen)}):
        r = client.post("/api/run",
                        json={"target_id": target["id"], "metric_key": "answer_relevancy"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pass"
    assert body["theme"] == "billing"
    assert seen == ["billing"]

    latest = client.get("/api/runs/latest", params={"target_id": target["id"]}).json()
    assert [row["metric_key"] for row in latest] == ["answer_relevancy"]
    history = client.get("/api/history", params={
        "target_id": target["id"], "metric_key": "answer_relevancy"}).json()
    assert len(history) == 1
    assert history[0]["score"] == 0.85


def test_run_returns_404_for_unknown_target_or_metric(client):
    target = _create_target(client)
    r = client.post("/api/run", json={"target_id": 999999, "metric_key": "answer_relevancy"})
    assert r.status_code == 404
    r = client.post("/api/run", json={"target_id": target["id"], "metric_key": "no_such_metric"})
    assert r.status_code == 404


def test_run_returns_503_when_judge_not_configured(client, monkeypatch):
    target = _create_target(client)
    monkeypatch.delenv("JUDGE_API_KEY")
    r = client.post("/api/run", json={"target_id": target["id"], "metric_key": "answer_relevancy"})
    assert r.status_code == 503
    assert "JUDGE_API_KEY" in r.json()["detail"]


def test_relay_round_trip(client, app_module):
    session = {"session_id": "shop.example"}
    assert client.get("/api/relay/next", params=session).json() == {"id": None, "question": None}
    question_id = app_module._relay_queue.ask("shop.example", "What is your refund window?")
    assert client.get("/api/relay/next", params=session).json() == {
        "id": question_id,
        "question": "What is your refund window?",
    }
    r = client.post("/api/relay", json={"session_id": "shop.example",
                                         "question_id": question_id,
                                         "text": "7 business days"})
    assert r.status_code == 200
    assert app_module._relay_queue.pop("shop.example", question_id, timeout=1) == "7 business days"


def test_cors_allows_only_the_extension_origin(client):
    preflight = {"Access-Control-Request-Method": "POST"}
    ok = client.options("/api/targets", headers={"Origin": EXTENSION_ORIGIN, **preflight})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == EXTENSION_ORIGIN

    bad = client.options("/api/targets", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in bad.headers
    simple = client.get("/api/targets", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in simple.headers


def test_rejects_foreign_host_header(app_module):
    rebinding = TestClient(app_module.app, base_url="http://evil.example")
    assert rebinding.get("/api/status").status_code == 400
