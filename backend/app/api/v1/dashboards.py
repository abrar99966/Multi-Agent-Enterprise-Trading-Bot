"""Layer 6 dashboard API (docs/LAYER6_DASHBOARDS.md, phase 6.0).

Read-side projections over the event journal, TCA store, and model artifacts,
plus ONE action: launching a paper backtest (synthetic or real bars) as a
background job. Backtests only write new journal/TCA files -- they cannot
touch positions, brokers, or limits. The sanctioned trading write surfaces
(approval decisions, kill switches) arrive with the live session host.

Free-tools constraint: FastAPI + SQLite + the journal on disk. Nothing paid.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.audit.chain import verify_journal
from app.core.config import get_settings
from app.dashboards import projections

router = APIRouter()

_ARTIFACT_PATH = Path("backend/app/models/artifacts/gbdt-v1.json")
_TCA_DB_PATH = Path("data/tca/bt.db")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

# Background backtest jobs: name -> state dict. In-process registry; jobs are
# short (seconds) and journal files are the durable record.
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _journal_dir() -> Path:
    return Path(get_settings().journal_dir)


def _resolve_journal(name: str) -> Path:
    """Resolve a journal by file name, refusing path traversal."""
    if "/" in name or "\\" in name or not name.endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="invalid journal name")
    path = (_journal_dir() / name).resolve()
    if path.parent != _journal_dir().resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"journal {name!r} not found")
    return path


def _load(name: str):
    path = _resolve_journal(name)  # raises 400/404 itself
    try:
        return projections.load_events(path)
    except Exception as exc:  # chain violation, decode error
        raise HTTPException(status_code=422, detail=f"journal unreadable: {exc}")


# ------------------------------------------------------------- backtest jobs


class BacktestRequest(BaseModel):
    """Launch a paper backtest from the UI. Writes data/journal/<name>.jsonl
    and data/tca/<name>.db; never touches live state."""

    name: str = Field(description="output name, e.g. 'real-jun11'")
    symbols: str = Field(default="RELIANCE,TCS", description="comma-separated")
    source: str = Field(default="synthetic", pattern="^(synthetic|real)$")
    strategy: str = Field(default="model", pattern="^(model|momentum)$")
    # synthetic source
    n_bars: int = Field(default=500, ge=100, le=10_000)
    seed: int = 7
    # real source (durable bar store)
    interval: str = "day"
    last_n: int = Field(default=500, ge=50, le=20_000)
    overwrite: bool = False


def _run_backtest_job(req: BacktestRequest, journal: Path, tca_db: Path) -> None:
    from app.engine.runner import PaperSession
    from app.tca.store import SqliteTcaStore

    job = _JOBS[req.name]
    try:
        symbols = [s.strip().upper() for s in req.symbols.split(",") if s.strip()]
        factory = None
        strategy_desc = "momentum-v0"
        if req.strategy == "model" and _ARTIFACT_PATH.is_file():
            from app.engine.inference import InferenceService
            from app.strategy.model_strategy import ModelStrategy

            inference = InferenceService.from_path(_ARTIFACT_PATH)
            factory = lambda bus, clock: ModelStrategy(bus, clock, inference)  # noqa: E731
            strategy_desc = inference.model_id
        elif req.strategy == "model":
            strategy_desc = "momentum-v0 (no model artifact; fell back)"

        bars = None
        if req.source == "real":
            from app.marketdata.bridge import load_store_bars

            bars = load_store_bars(symbols, interval=req.interval, last_n=req.last_n)

        session = PaperSession(
            symbols,
            n_bars=req.n_bars,
            seed=req.seed,
            journal_path=journal,
            strategy_factory=factory,
            bars=bars,
        )
        summary = session.run()
        with SqliteTcaStore(tca_db) as store:
            store.insert(session.tca.results())
        chain = verify_journal(journal)
        job.update(
            state="done",
            strategy=strategy_desc,
            chain_ok=chain.ok,
            summary=summary,
        )
    except Exception as exc:
        job.update(state="error", error=f"{type(exc).__name__}: {exc}")


@router.post("/dash/backtest")
async def start_backtest(req: BacktestRequest) -> Dict[str, Any]:
    """Run a backtest in the background; poll GET /dash/backtest/jobs."""
    if not _NAME_RE.match(req.name):
        raise HTTPException(status_code=400, detail="name must be [a-z0-9_-], start alphanumeric")
    journal = _journal_dir() / f"{req.name}.jsonl"
    tca_db = _TCA_DB_PATH.parent / f"{req.name}.db"
    with _JOBS_LOCK:
        running = _JOBS.get(req.name, {}).get("state") == "running"
        if running:
            raise HTTPException(status_code=409, detail=f"job {req.name!r} already running")
        if journal.exists() and not req.overwrite:
            raise HTTPException(
                status_code=409,
                detail=f"journal {journal.name!r} exists; set overwrite=true to replace",
            )
        for path in (journal, journal.with_suffix(".jsonl.head"), tca_db):
            if path.exists():
                path.unlink()
        _JOBS[req.name] = {
            "name": req.name,
            "state": "running",
            "source": req.source,
            "symbols": req.symbols,
        }
    threading.Thread(
        target=_run_backtest_job, args=(req, journal, tca_db), daemon=True
    ).start()
    return {"name": req.name, "state": "running"}


@router.get("/dash/backtest/jobs")
async def backtest_jobs() -> Dict[str, Any]:
    with _JOBS_LOCK:
        return {"jobs": list(_JOBS.values())}


# ------------------------------------------------------------- app links


@router.get("/dash/links")
async def links() -> Dict[str, Any]:
    """Companion-app links for the sidebar. Probes whether the classic (Part-1)
    frontend is actually reachable so the UI can show live vs. not-running.
    Configure via ETB_LEGACY_UI_URL / ETB_LEGACY_UI_BROKERS_PATH."""
    import httpx

    s = get_settings()
    base = s.legacy_ui_url.rstrip("/")
    # Probe via 127.0.0.1: on Windows, "localhost" can resolve to IPv6 ::1
    # first while dev servers bind IPv4 only -> false "offline". Dev servers
    # can also take >1.5s on a cold compile, hence the longer timeout.
    probe_url = base.replace("//localhost", "//127.0.0.1")
    reachable = False
    try:
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
            resp = await client.get(probe_url)
            reachable = resp.status_code < 500
    except Exception:
        reachable = False
    return {
        "classic_app": {
            "url": base,
            "brokers_url": base + s.legacy_ui_brokers_path,
            "reachable": reachable,
        },
        "api_docs": "/docs",
    }


# ------------------------------------------------------------- bar-store data


@router.get("/dash/symbols")
async def symbols(interval: str = "day") -> Dict[str, Any]:
    """What real history is available to backtest on: every stored symbol with
    bar count and freshness, plus store-wide aggregates. Powers the symbol
    picker and the 'data through' indicators."""
    from app.learning import bar_store

    rows = bar_store.coverage_by_symbol(interval)
    last_t = max((r["last_t"] for r in rows), default=None)
    stalest = sorted(rows, key=lambda r: r["last_t"])[:10]
    return {
        "interval": interval,
        "n_symbols": len(rows),
        "total_bars": sum(r["bars"] for r in rows),
        "freshest_t": last_t,
        "stalest": stalest,
        "symbols": rows,
    }


# ------------------------------------------------------------- journal index


@router.get("/dash/journals")
async def list_journals() -> Dict[str, Any]:
    """All journals with hash-chain verification status."""
    directory = _journal_dir()
    out = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.jsonl")):
            report = verify_journal(path)
            out.append(
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "records": report.records,
                    "chain_ok": report.ok,
                    "chain_reason": report.reason,
                }
            )
    return {"journal_dir": str(directory), "journals": out}


# ------------------------------------------------------------- dashboards


@router.get("/dash/journal/{name}/trading")
async def trading(name: str) -> Dict[str, Any]:
    return projections.trading_view(_load(name))


@router.get("/dash/journal/{name}/risk")
async def risk(name: str) -> Dict[str, Any]:
    return projections.risk_view(_load(name))


@router.get("/dash/journal/{name}/ai")
async def ai(name: str) -> Dict[str, Any]:
    return projections.ai_view(_load(name))


@router.get("/dash/journal/{name}/platform")
async def platform(name: str) -> Dict[str, Any]:
    view = projections.platform_view(_load(name))
    report = verify_journal(_resolve_journal(name))
    view["chain_ok"] = report.ok
    view["chain_records"] = report.records
    return view


@router.get("/dash/journal/{name}/events")
async def events(
    name: str,
    stream: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=2000),
) -> Dict[str, Any]:
    return projections.events_page(_load(name), stream=stream, offset=offset, limit=limit)


# ------------------------------------------------------------- incident replay


@router.post("/dash/journal/{name}/replay")
async def replay(name: str, use_model: bool = False) -> Dict[str, Any]:
    """Incident replay: re-run the journaled bars through fresh components and
    diff the regenerated intent/fill streams against what the journal recorded.
    A clean session diffs to zero -- any divergence is surfaced with the first
    differing event. Read-only: the replay bus has no journal and no broker."""
    from app.engine.runner import PaperSession

    path = _resolve_journal(name)
    factory = None
    if use_model:
        from app.engine.inference import InferenceService
        from app.strategy.model_strategy import ModelStrategy

        if not _ARTIFACT_PATH.is_file():
            raise HTTPException(status_code=404, detail="model artifact not found")
        inference = InferenceService.from_path(_ARTIFACT_PATH)
        factory = lambda bus, clock: ModelStrategy(bus, clock, inference)  # noqa: E731

    original = projections.load_events(path)
    session = PaperSession.replay_from_journal(path, strategy_factory=factory)

    def _stream_payloads(evts, stream):
        return [e.payload for e in evts if e.stream == stream]

    divergence = None
    from app.core.events import Streams

    for stream in ("signal.intents", "exec.fills"):
        orig = _stream_payloads(original, stream)
        repl = [e.payload for e in session.bus.events if e.stream == stream]
        if orig != repl:
            first = next(
                (i for i, (a, b) in enumerate(zip(orig, repl)) if a != b),
                min(len(orig), len(repl)),
            )
            divergence = {
                "stream": stream,
                "journaled": len(orig),
                "replayed": len(repl),
                "first_diff_index": first,
                "journaled_event": orig[first] if first < len(orig) else None,
                "replayed_event": repl[first] if first < len(repl) else None,
            }
            break

    bars = len([e for e in original if e.stream == Streams.MD_BARS])
    return {
        "journal": name,
        "strategy": "model" if use_model else "momentum",
        "bars_replayed": bars,
        "match": divergence is None,
        "divergence": divergence,
        "replay_summary": session.summary,
    }


# ------------------------------------------------------------- TCA + model


@router.get("/dash/tca")
async def tca(
    limit: int = Query(500, ge=1, le=10_000),
    db: Optional[str] = Query(None, description="store name under data/tca, e.g. 'real'"),
) -> Dict[str, Any]:
    """Per-fill TCA rows + notional-weighted aggregates from the SQLite store."""
    path = _TCA_DB_PATH
    if db is not None:
        if "/" in db or "\\" in db or "." in db:
            raise HTTPException(status_code=400, detail="invalid tca db name")
        path = _TCA_DB_PATH.parent / f"{db}.db"
    if not path.is_file():
        return {"db": str(path), "rows": [], "aggregates": {"n_fills": 0}}
    from app.tca.store import SqliteTcaStore

    with SqliteTcaStore(path) as store:
        rows = store.all()
    rows = rows[-limit:]
    total_notional = sum(r["notional"] for r in rows) or 1.0

    def wavg(col: str) -> float:
        return round(sum(r[col] * r["notional"] for r in rows) / total_notional, 3)

    aggregates = (
        {
            "n_fills": len(rows),
            "delay_bps": wavg("delay_bps"),
            "execution_bps": wavg("execution_bps"),
            "fees_bps": wavg("fees_bps"),
            "total_is_bps": wavg("total_is_bps"),
            "total_is_cost": round(sum(r["total_is_cost"] for r in rows), 2),
        }
        if rows
        else {"n_fills": 0}
    )
    by_strategy: Dict[str, Dict[str, float]] = {}
    for r in rows:
        agg = by_strategy.setdefault(
            r["strategy_id"], {"n": 0, "notional": 0.0, "is_cost": 0.0}
        )
        agg["n"] += 1
        agg["notional"] += r["notional"]
        agg["is_cost"] += r["total_is_cost"]
    for row in rows:  # markouts arrive as JSON strings from the store
        if isinstance(row.get("markouts_bps"), str):
            row["markouts_bps"] = json.loads(row["markouts_bps"])
    return {
        "db": str(path),
        "aggregates": aggregates,
        "by_strategy": by_strategy,
        "rows": rows,
    }


@router.get("/dash/model")
async def model() -> Dict[str, Any]:
    """Signed fast-path artifact metadata (no booster body)."""
    if not _ARTIFACT_PATH.is_file():
        return {"present": False, "path": str(_ARTIFACT_PATH)}
    from app.learning.artifact import load_artifact, model_id

    artifact = load_artifact(_ARTIFACT_PATH)
    return {
        "present": True,
        "model_id": model_id(artifact),
        "schema": artifact.get("schema"),
        "horizon": artifact.get("horizon"),
        "enter_threshold": artifact.get("enter_threshold"),
        "exit_threshold": artifact.get("exit_threshold"),
        "n_features": len(artifact.get("feature_names", [])),
        "feature_names": artifact.get("feature_names", []),
        "training": artifact.get("training", {}),
    }


@router.get("/dash/llm")
async def llm() -> Dict[str, Any]:
    """Slow-path analyst configuration (key masked)."""
    from app.core.config import llm_config

    cfg = llm_config()
    return {
        "provider": cfg.provider,
        "model": cfg.model or "(provider default)",
        "base_url": cfg.base_url or "(preset)",
        "api_key_set": bool(cfg.api_key),
        "timeout_s": cfg.timeout_s,
    }
