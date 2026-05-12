"""
Tests for AI Agent Cost Monitor.
Uses TestClient (httpx) against a temp-file SQLite DB so that all
connections share the same database (avoids :memory: cross-connection issues).
"""

import os
import tempfile
import pytest

# ---------------------------------------------------------------------------
# We need to set COST_DB_PATH before the app module is imported, so we use a
# module-scoped temp file created here and cleaned up after the session.
# ---------------------------------------------------------------------------
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["COST_DB_PATH"] = _tmp.name

from fastapi.testclient import TestClient  # noqa: E402
from app import app, Base, engine  # noqa: E402

# Ensure tables exist (idempotent)
Base.metadata.create_all(bind=engine)

client = TestClient(app)


def teardown_module(_module):
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
GOOD_PAYLOAD = {
    "api": "anthropic",
    "model": "claude-sonnet-4-6",
    "input_tokens": 1000,
    "output_tokens": 500,
    "project_id": "proj-test",
    "agent_id": "agent-1",
    "request_id": "req-abc",
}

GOOD_PAYLOAD_WITH_COST = {**GOOD_PAYLOAD, "cost_usd": 0.012345}


# ---------------------------------------------------------------------------
# Ingest: happy path — cost auto-computed from pricing table
# ---------------------------------------------------------------------------
def test_ingest_auto_cost():
    resp = client.post("/ingest", json=GOOD_PAYLOAD)
    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    assert body["cost_usd"] > 0
    assert "timestamp" in body


# ---------------------------------------------------------------------------
# Ingest: happy path — explicit cost_usd overrides pricing table
# ---------------------------------------------------------------------------
def test_ingest_explicit_cost():
    resp = client.post("/ingest", json=GOOD_PAYLOAD_WITH_COST)
    assert resp.status_code == 201
    assert resp.json()["cost_usd"] == pytest.approx(0.012345)


# ---------------------------------------------------------------------------
# Ingest: invalid api value is rejected (422)
# ---------------------------------------------------------------------------
def test_ingest_invalid_api():
    bad = {**GOOD_PAYLOAD, "api": "fakeapi"}
    resp = client.post("/ingest", json=bad)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Ingest: negative tokens rejected (422)
# ---------------------------------------------------------------------------
def test_ingest_negative_tokens():
    bad = {**GOOD_PAYLOAD, "input_tokens": -1}
    resp = client.post("/ingest", json=bad)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Ingest: missing required field rejected (422)
# ---------------------------------------------------------------------------
def test_ingest_missing_project_id():
    bad = {k: v for k, v in GOOD_PAYLOAD.items() if k != "project_id"}
    resp = client.post("/ingest", json=bad)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Ingest: unknown model without cost_usd returns 422
# ---------------------------------------------------------------------------
def test_ingest_unknown_model_no_cost():
    bad = {**GOOD_PAYLOAD, "model": "totally-unknown-model-xyz"}
    resp = client.post("/ingest", json=bad)
    assert resp.status_code == 422
    assert "pricing table" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Summary: returns expected structure after ingestion
# ---------------------------------------------------------------------------
def test_summary_aggregation():
    for i in range(3):
        payload = {
            "api": "openai",
            "model": "gpt-4o",
            "input_tokens": 2000,
            "output_tokens": 1000,
            "cost_usd": 0.05 * (i + 1),
            "project_id": "proj-summary",
            "agent_id": f"agent-summ-{i}",
        }
        r = client.post("/ingest", json=payload)
        assert r.status_code == 201

    resp = client.get("/api/summary", params={"project_id": "proj-summary"})
    assert resp.status_code == 200
    body = resp.json()

    assert "today_cost_usd" in body
    assert "week_cost_usd" in body
    assert "total_cost_usd" in body
    assert "runaway_agents" in body
    assert "top_models" in body
    assert "hourly_burn" in body
    assert "top_agents" in body

    # Today cost == sum of what we ingested: 0.05 + 0.10 + 0.15 = 0.30
    assert body["today_cost_usd"] == pytest.approx(0.30, abs=1e-5)
    assert len(body["top_agents"]) >= 3
    assert body["top_models"][0]["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# Summary: empty DB returns zeros, not errors
# ---------------------------------------------------------------------------
def test_summary_empty():
    resp = client.get("/api/summary", params={"project_id": "nonexistent-project-xyz"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["today_cost_usd"] == 0.0
    assert body["runaway_agents"] == []


# ---------------------------------------------------------------------------
# Dashboard: GET / returns HTML with "Cost Monitor"
# ---------------------------------------------------------------------------
def test_dashboard_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Cost Monitor" in resp.text
