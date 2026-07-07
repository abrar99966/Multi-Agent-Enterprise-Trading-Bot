"""Dashboard projections -- pure folds of the event journal.

Layer 6 principle (docs/LAYER6_DASHBOARDS.md): every dashboard is a projection
of the journal + derived stores. These functions read a hash-chain-verified
journal and produce the JSON payloads the dashboards render. Pure and
deterministic: same journal -> same view; no clock, no I/O beyond the read.

The journal already contains everything needed -- including the
oms.positions snapshots with realized PnL -- so no trading logic is
re-implemented here; the views are literal event folds.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.bus.journal import JournalReader
from app.core.events import Event, Streams


def load_events(journal_path: Path, verify: bool = True) -> List[Event]:
    return list(JournalReader(Path(journal_path)).iter_events(verify=verify))


def _by_stream(events: List[Event]) -> Dict[str, List[Event]]:
    grouped: Dict[str, List[Event]] = defaultdict(list)
    for e in events:
        grouped[e.stream].append(e)
    return grouped


def trading_view(events: List[Event], tape_limit: int = 50) -> Dict[str, Any]:
    """Positions, PnL curve, and the intent->order->fill tape."""
    grouped = _by_stream(events)
    last_prices: Dict[str, float] = {}
    for e in grouped.get(Streams.MD_BARS, []):
        last_prices[e.payload["symbol"]] = e.payload["close"]

    # Final position + realized PnL per symbol = last snapshot per symbol.
    last_snap: Dict[str, dict] = {}
    equity_curve: List[Dict[str, float]] = []
    realized_by_symbol: Dict[str, float] = {}
    for e in grouped.get(Streams.OMS_POSITIONS, []):
        p = e.payload
        last_snap[p["symbol"]] = p
        realized_by_symbol[p["symbol"]] = p["realized_pnl"]
        equity_curve.append(
            {"ts": e.ts_event, "realized_pnl": sum(realized_by_symbol.values())}
        )

    positions = []
    unrealized_total = 0.0
    for symbol, snap in sorted(last_snap.items()):
        last = last_prices.get(symbol)
        unrealized = (
            (last - snap["avg_price"]) * snap["qty"]
            if last is not None and snap["qty"] != 0.0
            else 0.0
        )
        unrealized_total += unrealized
        positions.append(
            {
                "symbol": symbol,
                "qty": snap["qty"],
                "avg_price": snap["avg_price"],
                "last_price": last,
                "realized_pnl": round(snap["realized_pnl"], 2),
                "unrealized_pnl": round(unrealized, 2),
            }
        )

    tape = [
        {
            "ts": e.ts_event,
            "stream": e.stream,
            "type": e.type,
            "summary": _tape_line(e),
        }
        for e in events
        if e.stream
        in (Streams.SIGNAL_INTENTS, Streams.EXEC_ORDERS, Streams.EXEC_FILLS)
    ][-tape_limit:]

    realized_total = sum(realized_by_symbol.values())
    bar_events = grouped.get(Streams.MD_BARS, [])
    return {
        "positions": positions,
        "realized_pnl_total": round(realized_total, 2),
        "unrealized_pnl_total": round(unrealized_total, 2),
        "equity_pnl_total": round(realized_total + unrealized_total, 2),
        "equity_curve": equity_curve,
        # When the market data in this session ENDS -- the freshness of every
        # number above. Stale data is the #1 silent backtest trap.
        "data_through": max((e.ts_event for e in bar_events), default=None),
        "tape": tape,
        "counts": {
            "bars": len(grouped.get(Streams.MD_BARS, [])),
            "intents": len(grouped.get(Streams.SIGNAL_INTENTS, [])),
            "orders": len(grouped.get(Streams.EXEC_ORDERS, [])),
            "fills": len(grouped.get(Streams.EXEC_FILLS, [])),
        },
    }


def _tape_line(e: Event) -> str:
    p = e.payload
    if e.stream == Streams.SIGNAL_INTENTS:
        return f"{p['side']} {p['qty']:g} {p['symbol']} ({p.get('reason', '')})"
    if e.stream == Streams.EXEC_ORDERS:
        return f"{p['side']} {p['qty']:g} {p['symbol']} [{p['order_type']}]"
    return f"{p['side']} {p['qty']:g} {p['symbol']} @ {p['price']}"


def risk_view(events: List[Event]) -> Dict[str, Any]:
    """Verdicts, rejections by reason, tiers, approvals, param changes, kills."""
    grouped = _by_stream(events)
    approved = rejected = 0
    tier_counts: Counter = Counter()
    reject_reasons: Counter = Counter()
    check_failures: Counter = Counter()
    for e in grouped.get(Streams.RISK_VERDICTS, []):
        v = e.payload
        if v["approved"]:
            approved += 1
            tier_counts[v["tier"]] += 1
        else:
            rejected += 1
            reason = (v.get("reject_reason") or "unknown").split(":")[0]
            reject_reasons[reason] += 1
        for check in v.get("checks", []):
            if not check["passed"]:
                check_failures[check["name"]] += 1

    param_changes = [
        {
            "ts": e.ts_event,
            "parameter": e.payload["parameter"],
            "old": e.payload["old_value"],
            "new": e.payload["new_value"],
            "source": e.payload["source"],
            "ttl_s": e.payload.get("ttl_s"),
            "rationale": e.payload.get("rationale", ""),
        }
        for e in grouped.get(Streams.CTL_PARAMS, [])
    ]
    kills = [
        {"ts": e.ts_event, **e.payload} for e in grouped.get(Streams.CTL_KILL, [])
    ]
    return {
        "approved": approved,
        "rejected": rejected,
        "tier_counts": dict(tier_counts),
        "reject_reasons": dict(reject_reasons),
        "check_failures": dict(check_failures),
        "approval_requests": len(grouped.get(Streams.CTL_APPROVAL_REQUESTS, [])),
        "approval_decisions": len(grouped.get(Streams.CTL_APPROVAL_DECISIONS, [])),
        "param_changes": param_changes,
        "kill_switches": kills,
    }


def ai_view(events: List[Event], limit: int = 100) -> Dict[str, Any]:
    """Per-decision explainability: model ids, probabilities, attributions,
    and slow-path proposals with evidence."""
    grouped = _by_stream(events)
    decisions = []
    model_ids: Counter = Counter()
    for e in grouped.get(Streams.SIGNAL_INTENTS, [])[-limit:]:
        p = e.payload
        attributions = dict(p.get("attributions", {}))
        prob = attributions.pop("prob", None)
        top = sorted(attributions.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
        model_ids[p.get("model_id", "?")] += 1
        decisions.append(
            {
                "ts": e.ts_event,
                "symbol": p["symbol"],
                "side": p["side"],
                "strategy_id": p["strategy_id"],
                "model_id": p.get("model_id"),
                "reason": p.get("reason", ""),
                "prob": prob,
                "top_attributions": top,
            }
        )
    proposals = [
        {
            "ts": e.ts_event,
            "parameter": e.payload["parameter"],
            "proposed_value": e.payload["proposed_value"],
            "source": e.payload["source"],
            "rationale": e.payload.get("rationale", ""),
            "evidence": e.payload.get("evidence", []),
        }
        for e in grouped.get(Streams.CTL_PARAM_PROPOSALS, [])
    ]
    return {
        "decisions": decisions,
        "model_ids": dict(model_ids),
        "proposals": proposals,
    }


def platform_view(events: List[Event]) -> Dict[str, Any]:
    """Stream rates and journal health."""
    if not events:
        return {"streams": {}, "n_events": 0}
    per_stream: Counter = Counter(e.stream for e in events)
    ts_first = min(e.ts_recorded for e in events)
    ts_last = max(e.ts_recorded for e in events)
    span_s = max((ts_last - ts_first) / 1e9, 1e-9)
    return {
        "n_events": len(events),
        "ts_first": ts_first,
        "ts_last": ts_last,
        "span_seconds": round(span_s, 1),
        "events_per_second": round(len(events) / span_s, 2),
        "streams": dict(sorted(per_stream.items())),
    }


def events_page(
    events: List[Event],
    stream: Optional[str] = None,
    offset: int = 0,
    limit: int = 200,
) -> Dict[str, Any]:
    """Paged raw events for the inspector / replay scrubber."""
    selected = [e for e in events if stream is None or e.stream == stream]
    page = selected[offset : offset + limit]
    return {
        "total": len(selected),
        "offset": offset,
        "limit": limit,
        "events": [
            {
                "stream": e.stream,
                "seq": e.seq,
                "ts_event": e.ts_event,
                "ts_recorded": e.ts_recorded,
                "type": e.type,
                "payload": e.payload,
            }
            for e in page
        ],
    }
