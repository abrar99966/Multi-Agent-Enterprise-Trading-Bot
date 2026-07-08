"""Conversational endpoint â€” routes a user question into the right agent
and returns a structured response the frontend can render."""

import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.market_data import market_data_service, NSE_HINTS, INDEX_ALIAS
from app.services.news_service import news_service
from app.services.ai_service import ai_service

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
    if any(w in t for w in ["price", "quote", "trading at", "how much", "level", "current"]):
        return "quote"
    if any(w in t for w in ["news", "headline", "latest", "happening", "story"]):
        return "news"
    if any(w in t for w in ["buy", "sell", "recommend", "should i", "trade", "signal", "long", "short", "entry"]):
        return "recommend"
    if any(w in t for w in ["risk", "position size", "kelly", "stop loss", "stop-loss", "drawdown"]):
        return "risk"
    if any(w in t for w in ["macro", "fed", "rbi", "inflation", "rates", "gdp"]):
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
async def chat(req: ChatRequest):
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

    if intent == "risk":
        return ChatResponse(
            reply=(
                "Risk discipline: max 2% capital risk per trade, Kelly fraction capped at 20%, "
                "daily-loss circuit breaker, and stop-loss enforced on every position. "
                "Want me to size a specific trade?"
            ),
            intent="risk",
            suggestions=["Size a long on INFY", "Show portfolio drawdown"],
        )

    if intent == "macro":
        return ChatResponse(
            reply=(
                "Current macro read â€” Inflation 4.2%, policy rate 6.5%, GDP +7.1%. "
                "Regime: expansionary, stable rates favor growth and quality compounders. "
                "Watching: oil, USD/INR, and Fed dot plot."
            ),
            intent="macro",
            suggestions=["How does this affect banks?", "Best sectors right now?"],
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
