"""Layer 6 dashboards: projections + REST API against a real journaled session.

Generates a deterministic paper session, then asserts every dashboard view is
a faithful projection of it (numbers must match the session summary exactly --
the dashboards may not invent or lose anything)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.v1.dashboards as dash_module
from app.dashboards import projections
from app.engine.runner import PaperSession
from app.main import app
from app.tca.store import SqliteTcaStore


@pytest.fixture(scope="module")
def session_env(tmp_path_factory):
    """One journaled momentum session + TCA db, served by the API."""
    root = tmp_path_factory.mktemp("dash")
    journal_dir = root / "journal"
    journal_dir.mkdir()
    session = PaperSession(
        ["RELIANCE", "TCS"], n_bars=400, seed=7,
        journal_path=journal_dir / "session.jsonl",
    )
    summary = session.run()
    tca_db = root / "tca.db"
    with SqliteTcaStore(tca_db) as store:
        store.insert(session.tca.results())
    return {"journal_dir": journal_dir, "session": session, "summary": summary,
            "tca_db": tca_db}


@pytest.fixture()
def client(session_env, monkeypatch):
    monkeypatch.setenv("ETB_JOURNAL_DIR", str(session_env["journal_dir"]))
    monkeypatch.setattr(dash_module, "_TCA_DB_PATH", session_env["tca_db"])
    return TestClient(app)


# ------------------------------------------------------------- projections


def test_trading_projection_matches_session(session_env) -> None:
    events = projections.load_events(
        session_env["journal_dir"] / "session.jsonl"
    )
    view = projections.trading_view(events)
    s = session_env["summary"]
    assert view["counts"]["intents"] == s["intents"]
    assert view["counts"]["fills"] == s["fills"]
    assert abs(view["realized_pnl_total"] - round(s["realized_pnl_total"], 2)) < 0.01
    assert {p["symbol"] for p in view["positions"]} <= {"RELIANCE", "TCS"}
    assert view["equity_curve"], "equity curve from oms snapshots"


def test_risk_projection_matches_session(session_env) -> None:
    events = projections.load_events(session_env["journal_dir"] / "session.jsonl")
    view = projections.risk_view(events)
    s = session_env["summary"]
    assert view["approved"] == s["approved"]
    assert view["rejected"] == s["rejected"]
    assert view["approval_requests"] == s["approval_requests"]


# ------------------------------------------------------------- REST API


def test_journal_index_lists_with_chain_status(client) -> None:
    data = client.get("/api/v1/dash/journals").json()
    assert len(data["journals"]) == 1
    j = data["journals"][0]
    assert j["name"] == "session.jsonl" and j["chain_ok"] is True


def test_dashboard_endpoints_serve_projections(client, session_env) -> None:
    s = session_env["summary"]
    trading = client.get("/api/v1/dash/journal/session.jsonl/trading").json()
    assert trading["counts"]["fills"] == s["fills"]
    risk = client.get("/api/v1/dash/journal/session.jsonl/risk").json()
    assert risk["approved"] == s["approved"]
    ai = client.get("/api/v1/dash/journal/session.jsonl/ai").json()
    assert ai["decisions"]
    platform = client.get("/api/v1/dash/journal/session.jsonl/platform").json()
    assert platform["chain_ok"] is True
    assert platform["n_events"] == len(session_env["session"].bus.events)


def test_events_endpoint_pages_and_filters(client, session_env) -> None:
    page = client.get(
        "/api/v1/dash/journal/session.jsonl/events",
        params={"stream": "exec.fills", "limit": 5},
    ).json()
    assert page["total"] == session_env["summary"]["fills"]
    assert len(page["events"]) <= 5
    assert all(e["stream"] == "exec.fills" for e in page["events"])


def test_replay_endpoint_confirms_determinism(client, session_env) -> None:
    out = client.post("/api/v1/dash/journal/session.jsonl/replay").json()
    assert out["match"] is True, out.get("divergence")
    assert out["divergence"] is None
    assert out["replay_summary"]["fills"] == session_env["summary"]["fills"]


def test_tca_endpoint_aggregates(client, session_env) -> None:
    data = client.get("/api/v1/dash/tca").json()
    assert data["aggregates"]["n_fills"] == session_env["summary"]["fills"]
    assert data["rows"]
    assert isinstance(data["rows"][0]["markouts_bps"], dict)


def test_model_and_llm_endpoints(client) -> None:
    model = client.get("/api/v1/dash/model").json()
    if model["present"]:  # artifact exists when trained; both shapes valid
        assert model["model_id"].startswith("model-")
        assert model["n_features"] > 0
    llm = client.get("/api/v1/dash/llm").json()
    assert llm["provider"]
    assert "api_key" not in {k for k in llm if "key" in k and k != "api_key_set"}


def test_links_endpoint_probes_classic_app(client, monkeypatch) -> None:
    """Sidebar companion links: configurable URL + honest reachability flag
    (nothing listens at the test URL, so reachable must be False)."""
    monkeypatch.setenv("ETB_LEGACY_UI_URL", "http://127.0.0.1:59999")
    monkeypatch.setenv("ETB_LEGACY_UI_BROKERS_PATH", "/brokers")
    data = client.get("/api/v1/dash/links").json()
    c = data["classic_app"]
    assert c["url"] == "http://127.0.0.1:59999"
    assert c["brokers_url"] == "http://127.0.0.1:59999/brokers"
    assert c["reachable"] is False
    assert data["api_docs"] == "/docs"


def test_journal_name_traversal_rejected(client) -> None:
    assert client.get("/api/v1/dash/journal/..%2Fsecrets.jsonl/trading").status_code in (400, 404)
    assert client.get("/api/v1/dash/journal/nope.jsonl/trading").status_code == 404


def test_dash_ui_served(client) -> None:
    r = client.get("/dash")
    assert r.status_code == 200
    # User-language IA: task pages first, engineering demoted to Advanced.
    for marker in ("AI Trading Assistant", "Today", "Check a Strategy", "Advanced"):
        assert marker in r.text


def test_backtest_job_runs_and_appears_in_journals(client) -> None:
    import time

    body = {"name": "uitest", "symbols": "RELIANCE", "source": "synthetic",
            "strategy": "momentum", "n_bars": 150, "seed": 3, "overwrite": True}
    r = client.post("/api/v1/dash/backtest", json=body)
    assert r.status_code == 200 and r.json()["state"] == "running"
    job = None
    for _ in range(60):  # poll up to ~30s
        jobs = client.get("/api/v1/dash/backtest/jobs").json()["jobs"]
        job = next(j for j in jobs if j["name"] == "uitest")
        if job["state"] != "running":
            break
        time.sleep(0.5)
    assert job is not None and job["state"] == "done", job.get("error")
    assert job["chain_ok"] is True
    assert job["summary"]["bars"] == 150
    names = {j["name"] for j in client.get("/api/v1/dash/journals").json()["journals"]}
    assert "uitest.jsonl" in names


def test_symbols_endpoint_shape(client, monkeypatch, tmp_path) -> None:
    """Coverage catalog for the symbol picker: one-query GROUP BY, correct
    per-symbol freshness, store-wide aggregates."""
    from app.learning import bar_store
    from app.learning.bar import Bar as LegacyBar

    monkeypatch.setattr(bar_store, "DB_PATH", tmp_path / "store.db")
    monkeypatch.setattr(bar_store, "_init_done", False)
    bar_store.save_bars("RELIANCE", "day",
                        [LegacyBar(t=1_700_000_000 + i*86_400, o=1, h=2, l=1, c=1.5, v=10)
                         for i in range(5)])
    bar_store.save_bars("TCS", "day",
                        [LegacyBar(t=1_700_000_000, o=1, h=2, l=1, c=1.5, v=10)])
    data = client.get("/api/v1/dash/symbols?interval=day").json()
    assert data["n_symbols"] == 2 and data["total_bars"] == 6
    rel = next(s for s in data["symbols"] if s["symbol"] == "RELIANCE")
    assert rel["bars"] == 5 and rel["last_t"] == 1_700_000_000 + 4*86_400
    assert data["freshest_t"] == rel["last_t"]
    assert data["stalest"][0]["symbol"] == "TCS"  # oldest last_t first


def test_trading_view_reports_data_through(session_env) -> None:
    from app.dashboards import projections

    events = projections.load_events(session_env["journal_dir"] / "session.jsonl")
    view = projections.trading_view(events)
    bars = [e for e in events if e.stream == "md.bars"]
    assert view["data_through"] == max(e.ts_event for e in bars)


def test_backtest_rejects_bad_name_and_running_dup(client) -> None:
    bad = client.post("/api/v1/dash/backtest", json={"name": "../evil", "source": "synthetic"})
    assert bad.status_code == 400
    upper = client.post("/api/v1/dash/backtest", json={"name": "Evil"})
    assert upper.status_code == 400
