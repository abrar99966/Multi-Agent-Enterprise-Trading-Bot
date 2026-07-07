from typing import Dict, Any, List, Optional

from .base import NewsAgent, MacroAgent, RiskAgent, technical_agent_singleton
from ..models.database import TradeSide, MarketType
from ..services.risk_engine import risk_engine
from ..learning.horizons import expected_move


def _atr_from_series(series: List[dict], period: int = 14) -> Optional[float]:
    """Absolute-price ATR from an intraday/daily OHLC series (Wilder). None if too short.

    Drives volatility-adaptive stop/target so levels scale with each symbol's real
    movement instead of a flat percentage on every card.
    """
    bars = [b for b in (series or []) if b.get("c") is not None]
    if len(bars) < period + 1:
        return None
    trs: List[float] = []
    for i, b in enumerate(bars):
        c = float(b["c"])
        h = float(b.get("h") or c)
        l = float(b.get("l") or c)
        if i == 0:
            trs.append(max(0.0, h - l))
        else:
            pc = float(bars[i - 1]["c"])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _daily_atr(symbol: str, period: int = 14) -> Optional[float]:
    """ATR from stored DAILY bars — real volatility, not the tiny 5m-intraday ATR.

    Served from the durable bar store (offline, instant) so stop/target widths
    reflect how much the symbol actually moves day to day. None if not stored.
    """
    try:
        from ..learning import bar_store
        bars = bar_store.get_bars((symbol or "").upper(), "day")
        if len(bars) < period + 1:
            return None
        series = [{"h": b.h, "l": b.l, "c": b.c} for b in bars[-(period * 3):]]
        return _atr_from_series(series, period)
    except Exception:
        return None


def _trust_level(best: Optional[dict], params_source: str) -> str:
    """Bucket how much weight to put on a signal, based on its backtest track record."""
    if params_source != "tuned" or not best:
        return "untested"
    n = best.get("n_trades", 0) or 0
    sh = best.get("sharpe", 0.0) or 0.0
    wr = best.get("win_rate", 0.0) or 0.0
    dd = best.get("max_drawdown_pct", 99.0) or 99.0
    if n >= 20 and sh >= 1.0 and wr >= 0.55 and dd <= 15:
        return "high"
    if n >= 10 and sh > 0 and wr >= 0.50:
        return "moderate"
    return "low"


def _build_trust(symbol: str, technical: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the 'why can I trust this' block from the symbol's backtest history."""
    from ..learning.tune import load_symbol_report
    strat = technical.get("strategy", "rsi_sma")
    strat_label = technical.get("strategy_label", strat)
    source = technical.get("params_source", "default")

    report = load_symbol_report(symbol) or {}
    best = report.get("best")
    baseline = report.get("baseline") or {}
    level = _trust_level(best, source)

    backtest = None
    if best:
        backtest = {
            "strategy": report.get("best_strategy", strat),
            "win_rate": best.get("win_rate"),
            "sharpe": best.get("sharpe"),
            "total_return_pct": best.get("total_return_pct"),
            "max_drawdown_pct": best.get("max_drawdown_pct"),
            "n_trades": best.get("n_trades"),
            "interval": report.get("interval"),
            "lookback_days": report.get("lookback_days"),
            "trained_at": report.get("trained_at"),
            "baseline_win_rate": baseline.get("win_rate"),
            "improvement_pp": report.get("improvement_pp"),
            "validation": best.get("validation"),
            "train_win_rate": best.get("train_win_rate"),
        }

    caveats: List[str] = ["Backtest results are simulated — NOT a guarantee of live performance."]
    if level == "untested":
        caveats.append(f"{symbol} hasn't been backtested yet — train it on the Learning page to earn a track record.")
    if best and (best.get("n_trades") or 0) < 10:
        caveats.append(f"Only {best.get('n_trades')} backtested trades — small sample; treat with caution.")
    if best and (best.get("max_drawdown_pct") or 0) > 20:
        caveats.append(f"High historical drawdown ({best.get('max_drawdown_pct')}%) — size conservatively.")

    return {
        "strategy": strat, "strategy_label": strat_label,
        "params_source": source, "level": level,
        "backtest": backtest, "caveats": caveats,
    }


def _build_why(technical, news, macro, risk) -> List[Dict[str, Any]]:
    """Plain-language factors that drove the recommendation, technical first."""
    headline = news.get("top_headline")
    return [
        {"factor": "Technical", "primary": True,
         "detail": technical.get("explanation") or technical.get("signal", "—")},
        {"factor": "News", "primary": False,
         "detail": f"{news.get('sentiment', 'neutral')} sentiment across {news.get('news_count', 0)} items"
                   + (f" — top: {headline}" if headline and headline != "No recent news" else "")},
        {"factor": "Macro", "primary": False,
         "detail": f"{macro.get('market_regime', '—')} regime · {macro.get('commentary', '')}".strip(" ·")},
        {"factor": "Risk", "primary": False,
         "detail": f"Stop suggested near {risk.get('stop_loss_suggested')}, max loss {risk.get('max_loss')}"},
    ]


def _trust_from_metrics(symbol: str, strat_key: str, strat_label: str,
                        m: Dict[str, Any], horizon: str) -> Dict[str, Any]:
    """Trust block from a horizon's on-the-fly backtest metrics (parallel to _build_trust)."""
    level = _trust_level(m, "tuned")
    backtest = {
        "strategy": strat_key,
        "win_rate": m.get("win_rate"), "sharpe": m.get("sharpe"),
        "total_return_pct": m.get("total_return_pct"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "n_trades": m.get("n_trades"),
        "interval": None, "lookback_days": None, "horizon": horizon,
    }
    caveats = ["Backtest results are simulated — NOT a guarantee of live performance."]
    n = m.get("n_trades") or 0
    if n < 10:
        caveats.append(f"Only {n} backtested trades on the {horizon} horizon — small sample; treat with caution.")
    if (m.get("max_drawdown_pct") or 0) > 20:
        caveats.append(f"High historical drawdown ({m.get('max_drawdown_pct')}%) on this horizon — size conservatively.")
    return {"strategy": strat_key, "strategy_label": strat_label, "params_source": "tuned",
            "level": level, "backtest": backtest, "caveats": caveats, "horizon": horizon}


class DecisionAgent:
    def __init__(self):
        # Use the shared TechnicalAgent so /learning/run can reload tuned params
        # without recreating any agent instance.
        self.agents = [
            technical_agent_singleton,
            NewsAgent(),
            MacroAgent(),
            RiskAgent(),
        ]

    async def generate_recommendation(
        self, symbol: str, market_data: Dict[str, Any],
        intraday: Dict[str, Any] | None = None,
        horizon: Optional[str] = None,
        horizon_signal: Optional[Dict[str, Any]] = None,
        capital: Optional[float] = None,
        calibration: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Produce a recommendation. When `horizon` (+ its precomputed `horizon_signal`)
        is supplied, direction/levels/track-record come from a backtest on that
        horizon's own timeframe; otherwise the short-term technical path is used."""
        ctx = {**market_data, "symbol": symbol, "intraday": intraday or {},
               "currency": market_data.get("currency")}

        agent_results: Dict[str, Any] = {}
        for agent in self.agents:
            agent_results[agent.name] = await agent.analyze(ctx)

        technical = agent_results["TechnicalAnalysis"]
        news = agent_results["NewsIntelligence"]
        macro = agent_results["MacroEconomics"]
        risk = agent_results["RiskManager"]

        entry_price = float(market_data["current_price"])
        hs = horizon_signal if (horizon and horizon_signal and horizon_signal.get("ok")) else None
        hs_failed_reason = (horizon_signal.get("reason")
                            if (horizon and horizon_signal and not horizon_signal.get("ok")) else None)

        # ---- Direction + horizon-matched track record ----
        if hs:
            signal = hs["signal"]
            strat_key, strat_label = hs["strategy"], hs["strategy_label"]
            metrics = hs["metrics"]
            trust = _trust_from_metrics(symbol, strat_key, strat_label, metrics, hs["horizon"])
            win_anchor = metrics.get("win_rate")
            bt = trust["backtest"]
            bt["validation"] = hs.get("validation")
            bt["train_win_rate"] = hs.get("train_win_rate")
        else:
            signal = technical.get("signal", "neutral")
            trust = _build_trust(symbol, technical)
            strat_label = trust["strategy_label"]
            bt = trust.get("backtest") or {}
            win_anchor = bt.get("win_rate")

        # neutral ⇒ HOLD (don't fabricate a directional trade). `side` is only the
        # storage bias; the UI keys off `action`.
        action = "buy" if signal == "bullish" else ("sell" if signal == "bearish" else "hold")
        side = TradeSide.SELL if signal == "bearish" else TradeSide.BUY

        # Edge gate: only emit a directional call when the chosen strategy has a
        # PROVEN positive out-of-sample edge (enough trades · positive expectancy ·
        # positive net return). No edge ⇒ HOLD. This is the biggest accuracy lever —
        # it stops recommending symbols whose own backtest loses money.
        no_edge = False
        if hs and not hs.get("edge_ok") and action in ("buy", "sell"):
            action, no_edge = "hold", True

        # ---- Confidence: technical + news, nudged by macro regime + backtest edge ----
        tech_conf = float(technical.get("confidence", 0.5))
        news_score = float(news.get("impact_score", 0.5))
        macro_bias = {"expansionary": 1.05, "contractionary": 0.95}.get(macro.get("market_regime"), 1.0)
        confidence = ((tech_conf * 0.6) + (news_score * 0.4)) * macro_bias
        edge_mult = {"high": 1.18, "moderate": 1.07, "low": 0.95, "untested": 0.88}.get(trust.get("level"), 0.9)
        confidence *= edge_mult
        if win_anchor is not None:
            # Anchor toward the (horizon-matched) historical win rate — weighted more
            # heavily for explicit horizons, since that's the metric the user cares about.
            blend = 0.4 if hs else 0.3
            confidence = (1 - blend) * confidence + blend * float(win_anchor)
        if signal == "neutral":
            confidence *= 0.7
        confidence = max(0.05, min(0.95, confidence))

        # ---- Self-learning policy (RL): bend conviction by what THIS market state
        # (horizon · regime · signal · sentiment) has historically done. No-op until
        # the state has enough graded outcomes; bounded so it can't dominate. ----
        rl_state, rl_mult = None, 1.0
        try:
            from ai.rl.q_learning_agent import rl_learning_agent
            rl_state = rl_learning_agent.state_of(horizon, macro.get("market_regime"), signal, news.get("sentiment"))
            rl_mult = rl_learning_agent.multiplier(rl_state)
            confidence = max(0.05, min(0.95, confidence * rl_mult))
        except Exception:
            rl_state, rl_mult = None, 1.0

        # Calibrate: map this raw confidence to the realized hit rate of past calls
        # at this confidence level (shrunk toward identity until data accumulates).
        # The calibrated number drives BOTH display and position sizing — honest odds.
        raw_confidence = confidence
        if calibration:
            from ..services.calibration import calibrate
            calibrated = calibrate(confidence, calibration)
            if calibrated is not None:
                confidence = max(0.05, min(0.95, float(calibrated)))

        # ---- Levels: horizon expected-move (ATR×√hold) or short-term ATR band ----
        if hs:
            move = expected_move(hs.get("atr"), hs.get("hold_bars", 21), entry_price) or entry_price * 0.08
            target_dist = move
            stop_dist = move * 0.5                      # 2:1 reward:risk over the horizon
            atr, atr_source = hs.get("atr"), f"{hs['interval']} ATR×√{hs['hold_bars']}"
            expiry_days = hs.get("expiry_days")
        else:
            atr = _daily_atr(symbol, period=14)
            atr_source = "daily"
            if not atr:
                atr = _atr_from_series((intraday or {}).get("series") or [], period=14)
                atr_source = "intraday" if atr else "none"
            stop_dist = min(max(atr * 1.5, entry_price * 0.01), entry_price * 0.10) if (atr and atr > 0) else entry_price * 0.02
            target_dist = stop_dist * 2.0
            expiry_days = None

        if side == TradeSide.SELL:
            stop_loss, target_price = entry_price + stop_dist, entry_price - target_dist
        else:
            stop_loss, target_price = entry_price - stop_dist, entry_price + target_dist

        sizing = risk_engine.calculate_position_size(entry_price, stop_loss, confidence, total_capital=capital)
        reward = abs(target_price - entry_price)
        risk_distance = max(0.0001, abs(entry_price - stop_loss))
        rr_ratio = round(reward / risk_distance, 2)

        why = _build_why(technical, news, macro, risk)

        # ---- Track-record sentence (works for both horizon + short-term blocks) ----
        if bt and bt.get("win_rate") is not None:
            tf = (f"{bt['horizon']} horizon" if bt.get("horizon")
                  else f"{bt.get('lookback_days')}d {bt.get('interval')}")
            val = bt.get("validation")
            val_tag = f", {val}" if val else ""
            track = (f" Track record (backtest, {tf}{val_tag}): {bt['win_rate']*100:.0f}% win over "
                     f"{bt['n_trades']} trades, Sharpe {bt['sharpe']}, maxDD {bt['max_drawdown_pct']}%.")
        else:
            track = " No backtest track record yet — ingest/train this symbol to earn metrics."

        horizon_label = hs["horizon"] if hs else (horizon or None)
        fallback_note = (f" [{horizon}: {hs_failed_reason} — showing short-term view]"
                         if hs_failed_reason else "")
        edge_note = (" No proven OOS edge here (strategy loses/break-even on held-out data) → HOLD, don't trade."
                     if no_edge else "")
        reasoning = (
            f"{('['+horizon_label+'] ') if (horizon_label and hs) else ''}"
            f"{strat_label} → {action.upper()}.{fallback_note}{edge_note} "
            f"Why — {technical.get('explanation', signal)}; "
            f"news {news.get('sentiment', 'neutral')} ({news.get('news_count', 0)}); "
            f"macro {macro.get('market_regime', '—')}."
            f"{track} Trust: {trust['level']}. "
            f"Qty {sizing['quantity']} @ {confidence*100:.0f}% conviction. "
            f"Backtest ≠ live — approve only if you agree."
        )[:1999]

        agent_results["rationale"] = {
            "why": why, "trust": trust,
            "action": action, "horizon": horizon_label,
            "raw_confidence": round(raw_confidence, 2),
            "calibrated": bool(calibration and (calibration.get("samples") or 0) > 0),
            "rl_state": rl_state, "rl_multiplier": round(rl_mult, 3),
            "edge_ok": (hs.get("edge_ok") if hs else None),
            "no_edge_hold": no_edge,
            "expectancy_pct": (metrics.get("expectancy_pct") if hs else None),
            "profit_factor": (metrics.get("profit_factor") if hs else None),
            "levels": {
                "atr": round(atr, 2) if atr else None,
                "atr_source": atr_source,
                "stop_pct": round(stop_dist / entry_price * 100, 2) if entry_price else None,
                "target_pct": round(target_dist / entry_price * 100, 2) if entry_price else None,
                "hold_bars": hs.get("hold_bars") if hs else None,
            },
        }
        if hs:
            agent_results["HorizonEngine"] = {
                "ok": True, "horizon": hs["horizon"], "interval": hs["interval"],
                "strategy": strat_key, "signal": signal, "metrics": metrics,
                "validation": hs.get("validation"), "train_win_rate": hs.get("train_win_rate"),
                "leaderboard": hs.get("leaderboard"),
            }
        elif hs_failed_reason:
            agent_results["HorizonEngine"] = {"ok": False, "horizon": horizon, "reason": hs_failed_reason}

        return {
            "symbol": symbol,
            "market": market_data.get("market_type", MarketType.EQUITY),
            "side": side,
            "entry_price": round(entry_price, 2),
            "target_price": round(target_price, 2),
            "stop_loss": round(stop_loss, 2),
            "quantity": int(sizing["quantity"]),
            "confidence_score": round(confidence, 2),
            "risk_reward_ratio": rr_ratio,
            "reasoning": reasoning,
            "agent_outputs": agent_results,
            "action": action,
            "horizon": horizon_label,
            "expiry_days": expiry_days,
        }


decision_agent = DecisionAgent()
