import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.datasets import goldens as goldens_store
from backend.targets.base import ChatReply

EXTENSION_ORIGIN = "chrome-extension://" + "a" * 32
_REPLY = ChatReply(reply="ok", model="m", mode="http")


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


BOT_CONFIG = {
    "url": "http://127.0.0.1:9/chat",
    "headers": {"Content-Type": "application/json"},
    "body_template": '{"message": "{{message}}"}',
    "response_path": "reply",
}


def _create_target(client, **overrides):
    config = {**BOT_CONFIG, **overrides.pop("config", {})}
    body = {"name": "Support bot", "type": "http", "config": config}
    body.update(overrides)
    r = client.post("/api/targets", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _fake_spec(seen_themes):
    metric = MagicMock()
    metric.score = 0.85
    metric.is_successful.return_value = True
    metric.reason = "fine"

    def cases(theme="general_support", **_kw):
        seen_themes.append(theme)
        return [{"id": "g1", "theme": theme, "question": "hi",
                 "expected_answer": "hello", "context": []}]

    return SimpleNamespace(
        key="answer_relevancy",
        title="Answer Relevancy",
        threshold=0.7,
        needs=(),
        cases=cases,
        build_metric=lambda judge, threshold=0.7: metric,
        build_case=lambda g, reply, retrieval=None: SimpleNamespace(input=g["question"], actual_output=reply),
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
    assert target["name"] == "Support bot"
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
    assert r.status_code == 400  # url missing
    assert len(client.get("/api/targets").json()) == before


def test_target_responses_mask_header_values(client):
    created = _create_target(
        client,
        name="OpenAI bot",
        type="http",
        config={"headers": {"Authorization": "Bearer sk-secret"}},
    )
    assert created["config"]["headers"] == {"Authorization": "***"}
    listing = client.get("/api/targets")
    assert "sk-secret" not in listing.text
    assert any(t["id"] == created["id"] for t in listing.json())


def test_update_golden_route(client):
    created = client.post("/api/goldens", json={
        "theme": "general_support",
        "question": "Does it ship internationally?",
        "expected_answer": "Yes, 10-14 business days.",
    }).json()
    r = client.put(f"/api/goldens/{created['id']}", json={
        "theme": "general_support",
        "question": "Does it ship worldwide?",
        "expected_answer": "Yes, 10-14 business days.",
        "context": ["Ships worldwide."],
        "categories": ["shipping"],
    })
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]
    assert r.json()["question"] == "Does it ship worldwide?"
    listed = client.get("/api/goldens", params={"theme": "general_support"}).json()
    assert [g["question"] for g in listed if g["id"] == created["id"]] == ["Does it ship worldwide?"]


def test_update_unknown_golden_is_404(client):
    r = client.put("/api/goldens/g_missing", json={
        "theme": "general_support", "question": "q", "expected_answer": "a"})
    assert r.status_code == 404


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
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": _fake_spec(seen)}), \
         patch.object(app_module.HttpTargetClient, "chat", return_value=_REPLY):
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


def test_judge_scores_one_answer_and_records_nothing(client, app_module):
    with patch.object(app_module, "build_judge", return_value=object()), \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": _fake_spec([])}):
        r = client.post("/api/judge", json={
            "metric_key": "answer_relevancy",
            "question": "What is your refund window?",
            "actual_output": "Refunds within 7 business days.",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pass"
    assert body["score"] == 0.85
    assert body["cases_run"] == 1
    assert body["rows"][0]["actual_output"] == "Refunds within 7 business days."
    # Ad-hoc judging must not pollute the golden-set trend history.
    history = client.get("/api/history", params={
        "target_id": 1, "metric_key": "answer_relevancy"}).json()
    assert history == []


def test_judge_takes_reference_data_from_the_golden_set(client, app_module):
    seen = {}
    spec = _fake_spec([])
    spec.needs = ("expected_answer",)
    spec.build_case = lambda g, reply, retrieval=None: seen.update(g) or SimpleNamespace()
    with patch.object(app_module, "build_judge", return_value=object()), \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": spec}):
        r = client.post("/api/judge", json={
            "metric_key": "answer_relevancy",
            "question": "What is your refund window?",
            "actual_output": "Refunds within 7 business days.",
            "theme": "general_support",
        })
    assert r.status_code == 200, r.text
    assert seen["expected_answer"]  # supplied by the golden row, not the request


def test_judge_reports_a_missing_reference_instead_of_scoring(client, app_module):
    spec = _fake_spec([])
    spec.needs = ("expected_answer",)
    with patch.object(app_module, "build_judge", return_value=object()), \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": spec}):
        r = client.post("/api/judge", json={
            "metric_key": "answer_relevancy",
            "question": "A question that is not in the golden set",
            "actual_output": "some answer",
            "theme": "general_support",
        })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "error"
    assert "expected_answer" in r.json()["error"]


def test_judge_404s_on_unknown_metric_and_503s_without_a_judge(client, monkeypatch):
    r = client.post("/api/judge", json={
        "metric_key": "no_such_metric", "question": "q", "actual_output": "a"})
    assert r.status_code == 404
    monkeypatch.delenv("JUDGE_API_KEY")
    r = client.post("/api/judge", json={
        "metric_key": "answer_relevancy", "question": "q", "actual_output": "a"})
    assert r.status_code == 503


def test_cors_allows_only_the_extension_origin(client):
    preflight = {"Access-Control-Request-Method": "POST"}
    ok = client.options("/api/targets", headers={"Origin": EXTENSION_ORIGIN, **preflight})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == EXTENSION_ORIGIN

    # Editing a golden sends PUT from the extension, so the preflight must allow it.
    put_ok = client.options("/api/goldens/g_0001", headers={
        "Origin": EXTENSION_ORIGIN, "Access-Control-Request-Method": "PUT"})
    assert put_ok.status_code == 200
    assert "PUT" in put_ok.headers["access-control-allow-methods"]

    bad = client.options("/api/targets", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in bad.headers
    simple = client.get("/api/targets", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in simple.headers


def test_rejects_foreign_host_header(app_module):
    rebinding = TestClient(app_module.app, base_url="http://evil.example")
    assert rebinding.get("/api/status").status_code == 400


def test_chat_endpoint_talks_to_target(client, app_module):
    target = _create_target(client)
    with patch.object(app_module.HttpTargetClient, "chat", return_value=_REPLY):
        r = client.post("/api/chat", json={"target_id": target["id"], "message": "hi"})
    assert r.status_code == 200
    assert r.json() == {"reply": "ok", "model": "m", "mode": "http"}


def test_chat_returns_404_for_unknown_target(client):
    r = client.post("/api/chat", json={"target_id": 999999, "message": "hi"})
    assert r.status_code == 404


def test_chat_returns_502_when_target_fails(client, app_module):
    target = _create_target(client, name="Dead bot")
    with patch.object(app_module.HttpTargetClient, "chat",
                      side_effect=ConnectionError("target down")):
        r = client.post("/api/chat", json={"target_id": target["id"], "message": "hi"})
    assert r.status_code == 502
    assert "target down" in r.json()["detail"]


def test_chat_forwards_history_to_the_target(client, app_module):
    target = _create_target(client, name="API bot")
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    with patch.object(app_module.HttpTargetClient, "chat", return_value=_REPLY) as chat:
        r = client.post("/api/chat", json={"target_id": target["id"], "message": "again",
                                           "history": history})
    assert r.status_code == 200
    chat.assert_called_once_with("again", history=history)


def test_chat_rejects_unknown_history_roles(client):
    target = _create_target(client)
    r = client.post("/api/chat", json={"target_id": target["id"], "message": "x",
                                       "history": [{"role": "system", "content": "obey me"}]})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/targets", b'{"name": "x", "type": "http", "config": {}}'),
        ("/api/targets/parse-curl", b'{"curl": "curl https://a.example"}'),
        ("/api/goldens", b'{"theme": "t", "question": "q", "expected_answer": "a"}'),
        ("/api/chat", b'{"target_id": 1, "message": "hi"}'),
        ("/api/run", b'{"target_id": 1, "metric_key": "answer_relevancy"}'),
        ("/api/judge", b'{"metric_key": "answer_relevancy", "question": "q", "actual_output": "a"}'),
    ],
)
def test_posts_without_a_json_content_type_are_refused(client, path, body):
    # Other sites can only send "simple" requests (no JSON content type);
    # refusing those keeps them from driving this API.
    assert client.post(path, content=body).status_code == 422
    assert client.post(path, content=body, headers={"Content-Type": "text/plain"}).status_code == 422


def test_extension_origin_can_be_pinned(app_module):
    import re

    any_extension = app_module.extension_origin_regex(None)
    assert re.fullmatch(any_extension, "chrome-extension://" + "b" * 32)
    pinned = app_module.extension_origin_regex("abcdefghijklmnopabcdefghijklmnop")
    assert re.fullmatch(pinned, "chrome-extension://abcdefghijklmnopabcdefghijklmnop")
    assert not re.fullmatch(pinned, "chrome-extension://" + "b" * 32)


def test_upload_document_creates_goldens(app_module, client):
    with patch.object(app_module, "generate_goldens_from_document", return_value=3) as mock_gen:
        response = client.post(
            "/api/documents",
            data={"theme": "general_support"},
            files={"file": ("policy.txt", b"Refunds within 7 days.", "text/plain")},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["goldens_created"] == 3
    assert body["status"] == "ready"
    assert body["filename"] == "policy.txt"
    mock_gen.assert_called_once()


def test_upload_document_rejects_unsupported_extension(app_module, client):
    response = client.post(
        "/api/documents",
        data={"theme": "general_support"},
        files={"file": ("slides.pptx", b"fake", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_upload_document_reports_generation_error(app_module, client):
    with patch.object(
        app_module, "generate_goldens_from_document", side_effect=RuntimeError("model unavailable")
    ):
        response = client.post(
            "/api/documents",
            data={"theme": "general_support"},
            files={"file": ("policy.txt", b"text", "text/plain")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "model unavailable" in body["error"]


def test_list_and_delete_documents(app_module, client):
    with patch.object(app_module, "generate_goldens_from_document", return_value=1):
        created = client.post(
            "/api/documents",
            data={"theme": "general_support"},
            files={"file": ("policy.txt", b"text", "text/plain")},
        ).json()
    listing = client.get("/api/documents?theme=general_support").json()
    assert any(d["id"] == created["id"] for d in listing)
    response = client.delete(f"/api/documents/{created['id']}")
    assert response.status_code == 200
    listing_after = client.get("/api/documents?theme=general_support").json()
    assert not any(d["id"] == created["id"] for d in listing_after)


SHOP_CURL = ("curl 'https://shop.example/api/chat' -H 'content-type: application/json' "
             "-b 'sid=1' --data-raw '{\"message\":\"Where is my order?\",\"conv\":\"c1\","
             "\"history\":[{\"role\":\"user\",\"content\":\"Where is my order?\"}]}'")


def _http_response(payload):
    import json as json_module
    resp = MagicMock(ok=True, status_code=200, headers={"Content-Type": "application/json"})
    resp.text = json_module.dumps(payload)
    resp.json.return_value = payload
    return resp


def test_parse_curl_detects_message_history_and_reply_path(client):
    answer = _http_response({"reply": "It ships tomorrow.", "mode": "live"})
    with patch("backend.targets.http_client.requests.request", return_value=answer) as sent:
        r = client.post("/api/targets/parse-curl", json={"curl": SHOP_CURL})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"] == "https://shop.example/api/chat"
    assert body["method"] == "POST"
    assert body["headers"]["Cookie"] == "sid=1"
    assert body["sample_message"] == "Where is my order?"
    assert body["message_marked"] is True
    import json as json_module
    assert json_module.loads(body["body_template"]) == {
        "message": "{{message}}", "conv": "c1", "history": []}
    assert body["response_path"] == "reply"
    assert body["reply_preview"] == "It ships tomorrow."
    assert body["probe_error"] is None
    # The probe asks the captured question, so the site sees a normal request.
    assert b"Where is my order?" in sent.call_args.kwargs["data"]


def test_parse_curl_still_fills_fields_when_probe_fails(client):
    with patch("backend.targets.http_client.requests.request", side_effect=ConnectionError("down")):
        r = client.post("/api/targets/parse-curl", json={"curl": SHOP_CURL})
    body = r.json()
    assert body["message_marked"] is True
    assert body["response_path"] == ""
    assert "down" in body["probe_error"]


def test_parse_curl_rejects_garbage(client):
    r = client.post("/api/targets/parse-curl", json={"curl": "wget https://a.example"})
    assert r.status_code == 400


def test_test_endpoint_reports_reply_or_error(client, app_module):
    with patch.object(app_module.HttpTargetClient, "chat", return_value=_REPLY):
        r = client.post("/api/targets/test", json={"config": BOT_CONFIG, "message": "hi"})
    assert r.json() == {"ok": True, "reply": "ok"}
    with patch.object(app_module.HttpTargetClient, "chat", side_effect=ValueError("HTTP 403")):
        r = client.post("/api/targets/test", json={"config": BOT_CONFIG})
    assert r.json()["ok"] is False and "HTTP 403" in r.json()["error"]
    r = client.post("/api/targets/test", json={"config": {"url": "http://a.example"}})
    assert r.status_code == 400  # no {{message}} placeholder


def test_edit_without_headers_keeps_the_stored_ones(client, app_module):
    target = _create_target(client, config={"headers": {"Cookie": "secret=1"}})
    config = {k: v for k, v in BOT_CONFIG.items() if k != "headers"}
    r = client.put(f"/api/targets/{target['id']}",
                   json={"name": "Renamed", "type": "http", "config": config})
    assert r.status_code == 200, r.text
    stored = app_module.storage.get_target(app_module._conn, target["id"])
    assert stored["config"]["headers"] == {"Cookie": "secret=1"}


def test_run_uses_the_requested_threshold(client, app_module):
    target = _create_target(client)
    spec = _fake_spec([])
    seen_thresholds = []
    spec.build_metric = lambda judge, threshold=0.7: seen_thresholds.append(threshold) or MagicMock(
        score=0.8, reason="", is_successful=lambda: 0.8 >= threshold)
    with patch.object(app_module, "build_judge", return_value=object()), \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": spec}), \
         patch.object(app_module.HttpTargetClient, "chat", return_value=_REPLY):
        r = client.post("/api/run", json={"target_id": target["id"],
                                          "metric_key": "answer_relevancy", "threshold": 0.9})
    assert seen_thresholds == [0.9]
    assert r.json()["threshold"] == 0.9
    assert r.json()["status"] == "fail"
    r = client.post("/api/run", json={"target_id": target["id"],
                                      "metric_key": "answer_relevancy", "threshold": 1.5})
    assert r.status_code == 422


def test_run_reports_progress_while_it_works(client, app_module):
    target = _create_target(client)
    seen = []

    def chat(message, history=None):
        seen.append(dict(app_module._progress["run-1"]))
        return _REPLY

    with patch.object(app_module, "build_judge", return_value=object()), \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": _fake_spec([])}), \
         patch.object(app_module.HttpTargetClient, "chat", side_effect=chat):
        r = client.post("/api/run", json={"target_id": target["id"],
                                          "metric_key": "answer_relevancy", "run_id": "run-1"})
    assert r.status_code == 200, r.text
    assert seen == [{"done": 0, "total": 1, "phase": "chat", "question": "hi"}]
    assert client.get("/api/run/progress", params={"run_id": "run-1"}).json() == {"active": False}


def test_cancel_stops_a_run_and_records_nothing(client, app_module):
    target = _create_target(client)
    spec = _fake_spec([])
    spec.cases = lambda theme="general_support", **_kw: [
        {"id": f"g{i}", "theme": theme, "question": f"q{i}", "expected_answer": "a", "context": []}
        for i in range(3)
    ]
    asked = []

    def chat(message, history=None):
        asked.append(message)
        # The user presses Stop while the first answer is on its way.
        assert client.post("/api/run/cancel", json={"run_id": "run-2"}).json() == {"cancelling": True}
        return _REPLY

    with patch.object(app_module, "build_judge", return_value=object()), \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": spec}), \
         patch.object(app_module.HttpTargetClient, "chat", side_effect=chat):
        r = client.post("/api/run", json={"target_id": target["id"],
                                          "metric_key": "answer_relevancy", "run_id": "run-2"})
    body = r.json()
    assert body["status"] == "cancelled"
    assert body["error"] == "stopped after 0 of 3 cases"
    assert asked == ["q0"]
    assert client.get("/api/history", params={
        "target_id": target["id"], "metric_key": "answer_relevancy"}).json() == []
    assert client.post("/api/run/cancel", json={"run_id": "run-2"}).json() == {"cancelling": False}
    app_module._cancelled.discard("run-2")


def test_stop_pressed_before_the_run_reports_still_stops_it(client, app_module):
    target = _create_target(client)
    assert client.post("/api/run/cancel", json={"run_id": "run-3"}).json() == {"cancelling": False}
    with patch.object(app_module, "build_judge", return_value=object()), \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": _fake_spec([])}), \
         patch.object(app_module.HttpTargetClient, "chat", return_value=_REPLY) as chat:
        r = client.post("/api/run", json={"target_id": target["id"],
                                          "metric_key": "answer_relevancy", "run_id": "run-3"})
    assert r.json()["status"] == "cancelled"
    chat.assert_not_called()
    assert "run-3" not in app_module._cancelled


def test_reset_restores_shipped_goldens_and_keeps_other_themes(client):
    shipped = client.get("/api/goldens", params={"theme": "general_support"}).json()
    assert len(shipped) == 10
    client.delete(f"/api/goldens/{shipped[0]['id']}")
    client.put(f"/api/goldens/{shipped[1]['id']}", json={
        "theme": "general_support", "question": "edited?", "expected_answer": "edited"})
    client.post("/api/goldens", json={"theme": "general_support", "question": "extra?",
                                      "expected_answer": "extra"})
    client.post("/api/goldens", json={"theme": "my_bot", "question": "mine?",
                                      "expected_answer": "kept"})

    r = client.post("/api/goldens/reset", json={})
    assert r.status_code == 200
    restored = client.get("/api/goldens", params={"theme": "general_support"}).json()
    assert restored == shipped
    assert [g["question"] for g in client.get("/api/goldens", params={"theme": "my_bot"}).json()] == ["mine?"]
    # Like every POST here, it needs a JSON body, so other sites cannot trigger it.
    assert client.post("/api/goldens/reset").status_code == 422


def test_judge_settings_saved_from_the_panel_override_env(client, app_module, monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "env-model")
    before = client.get("/api/judge/settings").json()
    assert before["key_source"] == "env" and before["model"] == "env-model"

    r = client.put("/api/judge/settings", json={
        "api_key": "sk-panel-secret-1234", "model": "panel-model",
        "base_url": "https://api.example.com/v1"})
    assert r.status_code == 200
    saved = r.json()
    assert saved == {"model": "panel-model", "base_url": "https://api.example.com/v1",
                     "has_key": True, "key_hint": "••••1234", "key_source": "panel"}
    assert "sk-panel-secret" not in client.get("/api/judge/settings").text
    assert client.get("/api/status").json()["judge"] == {"model": "panel-model", "up": True}

    # Runs use the saved values.
    with patch.object(app_module, "build_judge", return_value=object()) as build, \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": _fake_spec([])}), \
         patch.object(app_module.HttpTargetClient, "chat", return_value=_REPLY):
        target = _create_target(client)
        client.post("/api/run", json={"target_id": target["id"], "metric_key": "answer_relevancy"})
    build.assert_called_once_with(api_key="sk-panel-secret-1234", model="panel-model",
                                  base_url="https://api.example.com/v1")

    # An empty key keeps the saved one; empty model / URL fall back to .env.
    kept = client.put("/api/judge/settings", json={"api_key": "", "model": "", "base_url": ""}).json()
    assert kept["key_source"] == "panel" and kept["model"] == "env-model"
    cleared = client.put("/api/judge/settings", json={"clear_key": True}).json()
    assert cleared["key_source"] == "env"
    assert client.put("/api/judge/settings", json={"base_url": "ftp://x"}).status_code == 400


def test_judge_settings_test_reports_the_reply_or_the_error(client, app_module):
    judge = MagicMock()
    judge.generate.return_value = ('{"ok": true}', 0.0)
    with patch.object(app_module, "build_judge", return_value=judge) as build:
        r = client.post("/api/judge/settings/test", json={"api_key": "sk-try", "model": "m"})
    assert r.json() == {"ok": True, "model": "m", "reply": '{"ok": true}'}
    assert build.call_args.kwargs["api_key"] == "sk-try"
    judge.generate.side_effect = RuntimeError("401 invalid key")
    with patch.object(app_module, "build_judge", return_value=judge):
        r = client.post("/api/judge/settings/test", json={})
    assert r.json()["ok"] is False and "401 invalid key" in r.json()["error"]


def test_conversation_scenarios_crud_and_reset(client):
    shipped = client.get("/api/conversations", params={"theme": "general_support"}).json()
    assert len(shipped) == 3

    created = client.post("/api/conversations", json={
        "theme": "general_support", "name": "Gift card", "expected_outcome": "Explains balance",
        "user_turns": ["I have a gift card.", " ", "What's the balance on code ABC?"]}).json()
    assert created["user_turns"] == ["I have a gift card.", "What's the balance on code ABC?"]
    edited = client.put(f"/api/conversations/{created['id']}", json={
        "theme": "general_support", "name": "Gift card v2", "user_turns": ["Hi"]}).json()
    assert edited["name"] == "Gift card v2"
    assert client.delete(f"/api/conversations/{shipped[0]['id']}").status_code == 200
    assert client.delete(f"/api/conversations/{shipped[0]['id']}").status_code == 404
    assert client.post("/api/conversations", json={
        "theme": "t", "name": "x", "user_turns": [" "]}).status_code == 400
    client.post("/api/conversations", json={"theme": "my_bot", "name": "mine", "user_turns": ["hey"]})

    assert client.post("/api/conversations/reset", json={}).json() == {"restored": 5}
    assert client.get("/api/conversations", params={"theme": "general_support"}).json() == shipped
    assert [c["name"] for c in client.get("/api/conversations", params={"theme": "my_bot"}).json()] == ["mine"]
    # The panel-load reset restores scenarios along with the goldens.
    client.delete(f"/api/conversations/{shipped[1]['id']}")
    assert client.post("/api/goldens/reset", json={}).json()["conversations_restored"] == 5
    assert len(client.get("/api/conversations", params={"theme": "general_support"}).json()) == 3


def test_metrics_include_card_copy_and_cases_available(client):
    target = _create_target(client, config={"theme": "no_such_theme", "persona": "Shop bot"})
    rows = {m["key"]: m for m in client.get(f"/api/metrics?target_id={target['id']}").json()}
    assert len(rows) == 24
    assert rows["answer_relevancy"]["cases_available"] == 0
    assert rows["prompt_injection"]["cases_available"] == 2
    assert rows["prompt_injection"]["dataset"] == "security_probes"
    assert rows["jailbreak"]["ui_category"] == "security"
    assert rows["bias"]["scale_hint"].startswith("0.00")
    assert rows["answer_relevancy"]["question"].endswith("?")


def test_metrics_without_target_still_list_cases(client):
    rows = {m["key"]: m for m in client.get("/api/metrics").json()}
    assert rows["answer_relevancy"]["cases_available"] > 0


def test_metrics_for_unknown_target_is_404(client):
    assert client.get("/api/metrics?target_id=999999").status_code == 404


def test_target_persona_round_trips(client):
    target = _create_target(client, config={"persona": "Shop bot"})
    listed = next(t for t in client.get("/api/targets").json() if t["id"] == target["id"])
    assert listed["config"]["persona"] == "Shop bot"


def test_run_rejects_zero_limit(client):
    target = _create_target(client)
    r = client.post("/api/run", json={"target_id": target["id"], "metric_key": "answer_relevancy", "limit": 0})
    assert r.status_code == 422


def test_run_forwards_limit_and_persona(client, app_module, monkeypatch):
    target = _create_target(client, config={"persona": "Shop bot", "probe_set": "generic"})
    seen = {}

    def fake_run_spec(spec, judge, target_client, target_id, conn, **kwargs):
        seen.update(kwargs)
        return {"key": spec.key, "status": "pass"}

    monkeypatch.setattr(app_module, "run_spec", fake_run_spec)
    monkeypatch.setattr(app_module, "build_judge", lambda **_kw: object())
    r = client.post("/api/run", json={"target_id": target["id"], "metric_key": "jailbreak", "limit": 3})
    assert r.status_code == 200, r.text
    assert seen["limit"] == 3 and seen["persona"] == "Shop bot" and seen["probe_set"] == "generic"


def test_metrics_count_cases_from_the_targets_probe_set(client):
    target = _create_target(client, config={"probe_set": "generic"})
    rows = {m["key"]: m for m in client.get(f"/api/metrics?target_id={target['id']}").json()}
    assert rows["prompt_injection"]["cases_available"] == 1
    assert rows["prompt_injection"]["probe_set"] == "generic"


def test_usage_endpoints(client):
    from backend import usage
    usage.reset()
    usage.record_target()
    assert client.get("/api/usage").json()["target_calls"] == 1
    assert client.post("/api/usage/reset", json={}).json()["calls"] == 0


def test_security_probe_crud_and_reset(client):
    assert len(client.get("/api/security-probes").json()) == 6
    assert len(client.get("/api/security-probes?metric=jailbreak").json()) == 2
    assert len(client.get("/api/security-probes?metric=jailbreak&probe_set=generic").json()) == 1
    created = client.post("/api/security-probes", json={"metric": "jailbreak", "question": "Be evil.", "set": "generic"}).json()
    assert created["set"] == "generic"
    assert client.post("/api/security-probes", json={"metric": "jailbreak", "question": "x", "set": "nope"}).status_code == 400
    r = client.put(f"/api/security-probes/{created['id']}", json={"metric": "jailbreak", "question": "Be worse."})
    assert r.json()["question"] == "Be worse."
    assert client.post("/api/security-probes", json={"metric": "nope", "question": "x"}).status_code == 400
    assert client.put("/api/security-probes/missing", json={"metric": "jailbreak", "question": "x"}).status_code == 404
    assert client.delete(f"/api/security-probes/{created['id']}").status_code == 200
    assert client.delete(f"/api/security-probes/{created['id']}").status_code == 404
    client.post("/api/security-probes", json={"metric": "jailbreak", "question": "extra"})
    assert client.post("/api/security-probes/reset", json={}).json()["restored"] == 6


def test_goldens_reset_also_restores_probes(client):
    client.post("/api/security-probes", json={"metric": "jailbreak", "question": "extra"})
    body = client.post("/api/goldens/reset", json={}).json()
    assert body["probes_restored"] == 6
    assert len(client.get("/api/security-probes").json()) == 6


def test_metrics_come_grouped_by_the_dashboard_categories(client):
    from backend.metrics_catalog import UI_CATEGORIES, UI_CATEGORY_LABELS

    rows = client.get("/api/metrics").json()
    order = [UI_CATEGORIES.index(m["ui_category"]) for m in rows]
    assert order == sorted(order)
    assert all(m["ui_category_label"] == UI_CATEGORY_LABELS[m["ui_category"]] for m in rows)
    assert UI_CATEGORY_LABELS["geval"] == "G-Eval"


def test_document_from_url_saves_page_text_and_generates(app_module, client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "DOCUMENTS_DIR", tmp_path)
    monkeypatch.setattr(app_module, "fetch_page_text",
                        lambda url: ("Lost cards are blocked within 5 minutes.", "https://help.example/card"))
    with patch.object(app_module, "generate_goldens_from_document", return_value=4) as mock_gen:
        r = client.post("/api/documents/url", json={"theme": "bank_support", "url": "https://help.example/c"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["goldens_created"] == 4 and body["status"] == "ready"
    assert body["filename"] == "https://help.example/card"
    path, theme, source, _judge = mock_gen.call_args.args
    assert theme == "bank_support" and source == "https://help.example/card"
    saved = Path(path)
    assert saved.parent == tmp_path / "bank_support" and saved.name.endswith("_help.example.txt")
    assert saved.read_text(encoding="utf-8") == "Lost cards are blocked within 5 minutes."


def test_document_from_url_reports_unusable_pages(app_module, client, monkeypatch):
    def refuse(url):
        raise ValueError("the page has almost no readable text")

    monkeypatch.setattr(app_module, "fetch_page_text", refuse)
    r = client.post("/api/documents/url", json={"theme": "bank_support", "url": "https://help.example/spa"})
    assert r.status_code == 400
    assert "readable text" in r.json()["detail"]


def test_document_from_url_rejects_unsafe_theme(client):
    r = client.post("/api/documents/url", json={"theme": "../evil", "url": "https://help.example"})
    assert r.status_code == 422


def test_target_keeps_its_limits_and_rejects_bad_ones(client):
    created = _create_target(client, config={
        "max_message_length": 2000, "send_delay": 1.5, "check_replies": False})
    stored = client.get("/api/targets").json()
    config = next(t for t in stored if t["id"] == created["id"])["config"]
    assert (config["max_message_length"], config["send_delay"], config["check_replies"]) == (2000, 1.5, False)
    r = client.post("/api/targets", json={"name": "x", "type": "http",
                                          "config": {**BOT_CONFIG, "max_message_length": -5}})
    assert r.status_code == 400 and "max_message_length" in r.text
