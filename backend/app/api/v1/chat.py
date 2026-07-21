"""Conversational endpoint â€” routes a user question into the right agent
and returns a structured response the frontend can render."""

import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import risk_limits
from app.services.macro_data import macro_data
from app.services.market_data import market_data_service, NSE_HINTS, INDEX_ALIAS
from app.services.news_service import news_service
from app.services.ai_service import ai_service
from app.slowpath.macro_regime import classify_macro_regime

router = APIRouter()


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[ChatMessage]] = None


class ChatResponse(BaseModel):
    reply: str
    intent: str
    data: Optional[dict] = None
    suggestions: List[str] = []


# Build a lookup of all known tickers so we can pick them out of a sentence.
_KNOWN_SYMBOLS = (
    set(NSE_HINTS)
    | set(INDEX_ALIAS.keys())
    | {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD", "NFLX", "JPM", "BAC"}
)


def _extract_symbol(text: str) -> Optional[str]:
    """Pick the first known ticker out of free-form text."""
    tokens = re.findall(r"\b[A-Z][A-Z0-9\-]{1,9}\b", text.upper())
    for tok in tokens:
        if tok in _KNOWN_SYMBOLS:
            return tok
    # fall back: a $TICKER mention
    m = re.search(r"\$([A-Z]{2,6})", text.upper())
    if m:
        return m.group(1)
    return None


def _classify(text: str) -> str:
    t = text.lower()
    # Specific intents first — these phrases also contain generic keywords
    # ("trade", "strategy") that would otherwise mis-route to recommend/general.
    if ("reject" in t or "declined" in t or "blocked" in t) and any(
        w in t for w in ["trade", "order", "why", "rejected"]
    ):
        return "why_reject"
    if any(w in t for w in ["optimize", "optimise", "tune", "improve my strategy", "best strategy"]):
        return "optimize"
    if "backtest" in t or "back-test" in t or "back test" in t:
        return "backtest"
    if "drawdown" in t or "under water" in t or "underwater" in t:
        return "risk"
    if any(w in t for w in ["price", "quote", "trading at", "how much", "level", "current"]):
        return "quote"
    if any(w in t for w in ["news", "headline", "latest", "happening", "story"]):
        return "news"
    if any(w in t for w in ["buy", "sell", "recommend", "should i", "trade", "signal", "long", "short", "entry"]):
        return "recommend"
    if any(w in t for w in ["risk", "position size", "kelly", "stop loss", "stop-loss", "limit", "kill switch", "exposure"]):
        return "risk"
    if any(w in t for w in ["macro", "fed", "rbi", "inflation", "rates", "gdp", "regime", "vix", "yield"]):
        return "macro"
    if any(w in t for w in ["hello", "hi ", "hey", "good morning", "good evening"]):
        return "greet"
    if any(w in t for w in ["analyze", "assessment", "analyst", "persona", "deep dive", "in-depth", "fundamental", "sentiment", "technical analysis"]):
        return "analyze"
    if any(w in t for w in ["who are you", "what can you do", "help", "capabilities"]):
        return "help"
    return "general"


def _fmt_price(q: dict) -> str:
    sign = "+" if q["change"] >= 0 else ""
    arrow = "â†‘" if q["change"] >= 0 else "â†“"
    return (
        f"{q['name']} ({q['symbol']}) is trading at {q['currency']} {q['current_price']:,} "
        f"{arrow} {sign}{q['change']} ({sign}{q['change_pct']}%) today. "
        f"Session range: {q.get('low', 'â€”')}â€“{q.get('high', 'â€”')}."
    )


@router.post("/", response_model=ChatResponse)
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    msg = req.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Empty message")

    intent = _classify(msg)
    symbol = _extract_symbol(msg)

    suggestions = [
        "What's RELIANCE trading at right now?",
        "Should I buy INFY?",
        "Show me NIFTY news",
        "What's the macro picture?",
    ]

    if intent == "greet":
        return ChatResponse(
            reply=(
                f"Good {('morning' if datetime.utcnow().hour < 12 else 'afternoon')}. "
                "I'm your AI trading desk â€” Technical, News, Macro, and Risk agents on standby. "
                "Ask me about a ticker, request a trade recommendation, or check the macro picture."
            ),
            intent=intent,
            suggestions=suggestions,
        )

    if intent == "help":
        return ChatResponse(
            reply=(
                "I coordinate four specialist agents: Technical (price action, RSI, MACD), "
                "News (sentiment + headlines), Macro (rates, inflation, regimes), and Risk "
                "(position sizing, stop-loss, drawdown). Try: \"Analyze HDFCBANK\" or "
                "\"What's the risk-reward on a TCS long?\""
            ),
            intent=intent,
            suggestions=suggestions,
        )

    if intent == "quote":
        if not symbol:
            return ChatResponse(
                reply="Which symbol would you like a quote on? Try RELIANCE, INFY, AAPL, or NIFTY.",
                intent=intent,
                suggestions=suggestions,
            )
        try:
            q = await market_data_service.get_quote(symbol)
            return ChatResponse(reply=_fmt_price(q), intent="quote", data={"quote": q}, suggestions=[
                f"Should I buy {symbol}?",
                f"Show news on {symbol}",
                f"Generate a trade signal for {symbol}",
            ])
        except Exception as exc:
            return ChatResponse(reply=f"I couldn't pull a quote for {symbol}: {exc}", intent="error")

    if intent == "news":
        if not symbol:
            return ChatResponse(reply="Tell me a ticker and I'll pull the latest stories.", intent=intent)
        items = await news_service.fetch_news(symbol)
        if not items:
            return ChatResponse(reply=f"No fresh stories on {symbol} right now.", intent="news")
        bullets = "\n".join(f"â€¢ {n['title']} â€” {n['source']}" for n in items[:5])
        return ChatResponse(
            reply=f"Latest on {symbol}:\n{bullets}",
            intent="news",
            data={"news": items},
            suggestions=[f"Sentiment on {symbol}?", f"Recommend a trade for {symbol}"],
        )

    if intent == "recommend":
        if not symbol:
            return ChatResponse(
                reply="Which symbol should I analyze? I'll run all four agents on it.",
                intent=intent,
                suggestions=suggestions,
            )
        try:
            rec = await ai_service.get_trade_recommendation(symbol)
            side = rec["side"].value if hasattr(rec["side"], "value") else rec["side"]
            return ChatResponse(
                reply=(
                    f"Recommendation for {rec['symbol']}: **{side.upper()}** "
                    f"at {rec['entry_price']}, target {rec['target_price']}, "
                    f"stop {rec['stop_loss']} (R:R {rec['risk_reward_ratio']}). "
                    f"Confidence {int(rec['confidence_score'] * 100)}%. "
                    f"Reasoning â€” {rec['reasoning']}"
                ),
                intent="recommend",
                data={"recommendation": {**rec, "side": side, "market": (rec["market"].value if hasattr(rec["market"], "value") else rec["market"])}},
                suggestions=[
                    f"What's the risk if {rec['symbol']} hits stop-loss?",
                    f"Show {rec['symbol']} chart",
                    "Show me other ideas",
                ],
            )
        except Exception as exc:
            return ChatResponse(reply=f"Couldn't generate a recommendation: {exc}", intent="error")

    if intent == "analyze":
        if not symbol:
            return ChatResponse(
                reply="Which symbol should I run the analyst personas on? Try: \"Analyze RELIANCE\" or \"Deep dive INFY\".",
                intent=intent,
                suggestions=["Analyze RELIANCE", "Deep dive HDFCBANK", "Sentiment on INFY"],
            )
        try:
            from app.slowpath.orchestrator import slowpath
            result = await slowpath.analyze(
                symbol=symbol,
                headline=msg,
                include_openbb=True,
            )
            # Format the multi-agent response
            lines = [f"**Multi-Agent Analysis for {symbol}** ({result.get('provider', 'stub')}:{result.get('model', '')})", ""]
            for persona, data in result.get("assessments", {}).items():
                if data.get("status") == "ok":
                    a = data["assessment"]
                    direction = a.get("direction", "?")
                    arrow = "\u2191" if direction == "bullish" else ("\u2193" if direction == "bearish" else "\u2194")
                    lines.append(
                        f"{arrow} **{persona.title()}**: {direction.upper()} "
                        f"(severity: {a.get('severity', '?')}, confidence: {int(float(a.get('confidence', 0))*100)}%)\n"
                        f"   {a.get('rationale', '')}"
                    )
                elif data.get("status") == "blocked":
                    lines.append(f"\u23F8 **{persona.title()}**: Paused ({data.get('reason', '')})")
                else:
                    lines.append(f"\u26A0 **{persona.title()}**: {data.get('error', 'unavailable')}")

            proposals = result.get("proposals", [])
            if proposals:
                lines.append("")
                lines.append("**Risk Proposals:**")
                for p in proposals:
                    auto = "auto-applies" if p.get("auto_applies") else "needs approval"
                    lines.append(
                        f"  \u2022 {p['persona'].title()}: {p['direction']} "
                        f"\u2192 {p['parameter']} = {p['proposed_value']:,.0f} ({auto})"
                    )

            reply = "\n".join(lines)
            return ChatResponse(
                reply=reply,
                intent="analyze",
                data={"analysis": result},
                suggestions=[
                    f"Quote {symbol}",
                    f"Recommend a trade for {symbol}",
                    f"Show agents dashboard",
                ],
            )
        except Exception as exc:
            return ChatResponse(reply=f"Analysis failed: {exc}", intent="error")

    if intent == "why_reject":
        # We have no order id here, so explain the boundary honestly and point at
        # where the actual verdict lives, rather than inventing a reason.
        lim = await risk_limits.get_limits(db)
        gates = []
        if lim.get("kill_switch"):
            gates.append("the kill switch is currently ENGAGED (all orders blocked)")
        if lim.get("today_remaining_trades", 1) <= 0:
            gates.append(f"today's trade budget is spent ({lim.get('today_trade_count')}/{lim.get('daily_max_trades')})")
        buf = lim.get("today_remaining_loss_buffer_inr")
        if buf is not None and buf <= 0:
            gates.append("the daily loss cap has been hit")
        active = (" Right now: " + "; ".join(gates) + ".") if gates else ""
        return ChatResponse(
            reply=(
                "Every order passes the risk gateway before a broker sees it. A rejection "
                "means one pre-trade check failed: per-trade notional cap "
                f"(₹{lim.get('per_trade_max_inr'):,.0f}), daily loss cap "
                f"(₹{lim.get('daily_max_loss_inr'):,.0f}), daily trade count "
                f"({lim.get('daily_max_trades')}), or the kill switch."
                f"{active} Open the specific order in the Orders module to see its exact "
                "verdict and which check failed."
            ),
            intent="why_reject",
            data={"limits": lim},
            suggestions=["Show my risk limits", "What's the kill switch state?"],
        )

    if intent == "optimize":
        return ChatResponse(
            reply=(
                "Strategy optimisation runs as a walk-forward tournament, not a chat action. "
                "Open the Learning module and start a training run over a symbol universe: it "
                "grid-searches each strategy's parameters with purged cross-validation and picks "
                "the champion per symbol. The Strategies module then lists the arms and their "
                "grid sizes. For the level-based PDH/PDL strategy, use scripts/backtest_pdh_pdl.py "
                "— its exits are previous-day levels the tournament's global stop can't model."
            ),
            intent="optimize",
            suggestions=["List my strategies", "Start a training run"],
        )

    if intent == "backtest":
        return ChatResponse(
            reply=(
                "Backtests run over stored bars, not from chat. Two paths: the tournament backtest "
                "(Learning module → train) grades every strategy per symbol with no look-ahead; and "
                "for a faithful path-dependent run of the PDH/PDL sweep-reversal, "
                "scripts/backtest_pdh_pdl.py --real replays real stored bars with level-based "
                "entries and intrabar SL/TP. Tell me a symbol and I can generate a fresh "
                "recommendation instead."
            ),
            intent="backtest",
            suggestions=["Recommend a trade for RELIANCE", "List my strategies"],
        )

    if intent == "risk":
        lim = await risk_limits.get_limits(db)
        pnl = lim.get("today_realized_pnl_inr", 0.0) or 0.0
        loss_cap = lim.get("daily_max_loss_inr", 0.0) or 0.0
        buf = lim.get("today_remaining_loss_buffer_inr")
        used_pct = (max(0.0, -pnl) / loss_cap * 100) if loss_cap else 0.0
        state = "ENGAGED — orders halted" if lim.get("kill_switch") else "off"
        return ChatResponse(
            reply=(
                f"Live risk state: realised P&L today ₹{pnl:,.0f}; daily loss cap "
                f"₹{loss_cap:,.0f} ({used_pct:.0f}% consumed"
                + (f", ₹{buf:,.0f} buffer left" if buf is not None else "")
                + f"). Per-trade cap ₹{lim.get('per_trade_max_inr'):,.0f}; trades today "
                f"{lim.get('today_trade_count')}/{lim.get('daily_max_trades')}. Kill switch: {state}. "
                "Every order is gated pre-trade; these caps are hard limits, not guidance."
            ),
            intent="risk",
            data={"limits": lim},
            suggestions=["Why was my order rejected?", "What's the macro picture?"],
        )

    if intent == "macro":
        # Real macro read from the same adapter the regime analyst uses — no
        # fabricated inflation/GDP figures.
        try:
            point = await macro_data.latest_yield_curve()
            vix = await macro_data.latest_value("VIXCLS")  # None without a FRED key
        except Exception:
            point, vix = None, None
        spread = point.spread_10y_2y if point else None
        regime = classify_macro_regime(spread, vix)
        if point is None and vix is None:
            reply = ("Macro data is unavailable right now (the US Treasury feed did not respond "
                     "and no FRED key is set for VIX).")
        else:
            bits = []
            if spread is not None:
                bits.append(f"US 10Y-2Y spread {spread:+.2f}%"
                            + (" (inverted — a stress precursor)" if point and point.inverted else ""))
            if vix is not None:
                bits.append(f"VIX {vix:.1f}")
            label = (regime or "calm").upper()
            reply = (f"Macro regime: {label}. " + "; ".join(bits) + ". "
                     "This is a tighten-only signal — on stress it cuts gross exposure, it never "
                     "loosens limits. (US Treasury needs no key; VIX requires a FRED key.)")
        return ChatResponse(
            reply=reply,
            intent="macro",
            data={"macro": {"spread_10y_2y": spread, "vix": vix, "regime": regime,
                            "inverted": bool(point and point.inverted)}},
            suggestions=["How does this affect my risk?", "What's my current exposure?"],
        )

    # General fallback
    return ChatResponse(
        reply=(
            "I can pull live quotes, headlines, generate trade recommendations, and explain "
            "the macro picture. Try: \"Quote RELIANCE\", \"News on INFY\", or \"Recommend a trade for HDFCBANK\"."
        ),
        intent="general",
        suggestions=suggestions,
    )
