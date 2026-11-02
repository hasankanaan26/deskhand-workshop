"""The HTTP front door, offline. The lifespan does not run (no worker thread)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module
import store


@pytest.fixture()
def client(seeded, fake_model):
    return TestClient(app_module.app)


def test_cards_and_clock(client):
    cards = client.get("/cases").json()["cases"]
    assert [c["case_id"] for c in cards] == ["C-1001", "C-1002", "C-1003", "C-1004"]
    assert cards[0]["day_label"] == "D-2"
    clock = client.get("/clock").json()
    assert clock["day_label"] == "D-2" and clock["queue"] == "sqlite" and clock["worker_seen_s_ago"] is None


def test_advance_evaluate_and_timeline(client):
    r = client.post("/clock/advance", json={"days": 5})
    assert r.status_code == 200 and r.json()["day_label"] == "D+3"
    r = client.post("/cases/C-1001/evaluate")
    assert r.status_code == 200 and r.json()["decision"].startswith("wait")
    detail = client.get("/cases/C-1001").json()
    assert detail["case"]["stage_history"] == {"soft_reminder": "skipped", "first_notice": "sent"}
    assert [e["kind"] for e in detail["events"]] == ["policy.applied", "stage.skipped", "draft.checked", "reminder.sent"]
    assert len(detail["messages"]) == 1
    assert client.post("/clock/advance", json={"days": -1}).status_code == 422


def test_payment_inbound_and_the_people_doors(client):
    assert client.post("/cases/C-1001/payments", json={"amount_usd": 120}).json()["decision"].startswith("wait")
    assert store.get_case("C-1001").balance_usd == 120.0
    r = client.post("/inbound", json={"customer_email": "accounts@globex.example", "channel": "email",
                                      "text": "We never received item 3, not paying until this is sorted."})
    assert r.status_code == 200 and r.json()["intent"]["intent"] == "dispute"
    assert client.post("/inbound", json={"customer_email": "x@y", "text": "hi"}).status_code == 404
    assert client.post("/cases/C-1001/resolve", json={"credit_usd": 10}).status_code == 409
    r = client.post("/cases/C-1002/resolve", json={"credit_usd": 60, "note": "item 3", "by": "test"})
    assert r.status_code == 200 and store.get_case("C-1002").balance_usd == 120.0
    assert client.post("/cases/C-1001/approve", json={"approved": True}).status_code == 409
    assert client.post("/cases/C-1001/resume", json={}).status_code == 409
    assert client.post("/cases/C-9999/evaluate").status_code == 404


def test_tick_feed_and_policies(client):
    r = client.post("/tick").json()
    assert sorted(r["woke"]) == ["C-1001", "C-1002", "C-1003", "C-1004"]
    feed = client.get("/feed").json()
    assert feed["events"] and feed["cursor"] == feed["events"][-1]["id"]
    assert client.get("/feed", params={"after": feed["cursor"]}).json()["events"] == []
    p = client.get("/policies").json()
    assert set(p["policies"]) == {"nimbus-retail", "nimbus-business", "nimbus-subscriptions"}
    assert {(v["tenant"], v["version"]) for v in p["versions_applied"]} == {("nimbus-retail", 4), ("nimbus-business", 2)}
