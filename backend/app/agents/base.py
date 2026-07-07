import math
from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Perform analysis and return agent-specific insights."""
        pass

def _compute_rsi(closes, period: int = 14) -> float | None:
    """Wilder's RSI from a list of closes. Returns None if too few points."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _sma(values, n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


class TechnicalAgent(BaseAgent):
    """Compute the trade signal for a symbol using its tournament-winning strategy.

    Loads per-symbol tuned parameters AND the winning strategy from
    `backend/app/learning/tuned_params.json` (written by the strategy
    tournament). The directional signal is produced by the SAME strategy
    function the backtest used — guaranteeing backtest/live parity. RSI/SMA are
    still computed for the explainability panel regardless of which strategy won.
    Falls back to the default RSI+SMA rule for any symbol not yet trained.
    """
    DEFAULTS = {
        "strategy": "rsi_sma",
        "rsi_period": 14,
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "sma_period": 20,
    }

    def __init__(self):
        super().__init__("TechnicalAnalysis")
        self._tuned_cache = None  # lazy load to avoid import-time IO

    def _params_for(self, symbol: str) -> Dict[str, Any]:
        # Lazy-load tuned params (the file may not exist on first boot)
        if self._tuned_cache is None:
            try:
                from ..learning.tune import load_tuned_params
                payload = load_tuned_params()
                self._tuned_cache = payload.get("tuned_params") or {}
            except Exception:
                self._tuned_cache = {}
        tuned = self._tuned_cache.get((symbol or "").upper())
        if tuned:
            return {**self.DEFAULTS, **tuned}
        return dict(self.DEFAULTS)

    def reload_tuned(self):
        """Called by the /learning/run endpoint after a fresh tune writes new params."""
        self._tuned_cache = None

    @staticmethod
    def _build_params(p: Dict[str, Any]):
        """Build a StrategyParams from a (possibly partial/legacy) dict, ignoring stray keys."""
        from ..learning.strategies import StrategyParams
        valid = {f.name for f in __import__("dataclasses").fields(StrategyParams)}
        return StrategyParams(**{k: v for k, v in p.items() if k in valid})

    @staticmethod
    def _to_bars(series: list):
        """Turn the intraday series dicts into Bar objects for the strategy functions."""
        from ..learning.bar import Bar
        bars = []
        for b in series:
            c = b.get("c")
            if c is None:
                continue
            try:
                c = float(c)
                bars.append(Bar(
                    t=int(b.get("t") or 0),
                    o=float(b["o"]) if b.get("o") is not None else c,
                    h=float(b["h"]) if b.get("h") is not None else c,
                    l=float(b["l"]) if b.get("l") is not None else c,
                    c=c,
                    v=float(b["v"]) if b.get("v") is not None else 0.0,
                ))
            except (TypeError, ValueError):
                continue
        return bars

    async def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        series = (data.get("intraday") or {}).get("series") or []
        closes = [float(b.get("c")) for b in series if b.get("c") is not None]
        last = float(data.get("current_price") or (closes[-1] if closes else 0))
        symbol = data.get("symbol") or data.get("Symbol") or ""
        p = self._params_for(symbol)
        strat_key = p.get("strategy", "rsi_sma")

        params_tuned = bool(symbol and symbol.upper() in (self._tuned_cache or {}))

        if not closes:
            return {
                "signal": "neutral",
                "strategy": strat_key,
                "indicators": {"rsi": None, "sma20": None, "trend": "no-data"},
                "confidence": 0.4,
                "explanation": "No intraday history available — falling back to neutral.",
                "params_source": "tuned" if params_tuned else "default",
            }

        rsi_period = int(p["rsi_period"])
        sma_period = int(p["sma_period"])
        rsi_os = float(p["rsi_oversold"])
        rsi_ob = float(p["rsi_overbought"])

        rsi = _compute_rsi(closes, rsi_period)
        sma_val = _sma(closes, sma_period) or _sma(closes, max(5, len(closes) // 2))
        sma_distance_pct = ((last - sma_val) / sma_val * 100) if sma_val else 0.0

        if sma_val is None:
            trend = "indeterminate"
        elif sma_distance_pct > 0.5:
            trend = "uptrend"
        elif sma_distance_pct < -0.5:
            trend = "downtrend"
        else:
            trend = "sideways"

        # ---- Directional signal from the winning strategy (backtest/live parity) ----
        from ..learning.strategies import get_strategy
        strat = get_strategy(strat_key)
        sp = self._build_params(p)
        bars = self._to_bars(series)
        signal = strat.signal_fn(bars, sp) if bars else "neutral"

        # Confidence: base on a directional signal, then let RSI confirm or temper it.
        if signal == "neutral":
            confidence = 0.5
        else:
            confidence = 0.62
            if rsi is not None:
                if signal == "bullish" and rsi <= rsi_os:
                    confidence = min(0.85, 0.65 + (rsi_os - rsi) / 60)   # oversold confirms longs
                elif signal == "bearish" and rsi >= rsi_ob:
                    confidence = min(0.85, 0.65 + (rsi - rsi_ob) / 60)   # overbought confirms shorts
                elif (signal == "bullish" and rsi >= rsi_ob) or (signal == "bearish" and rsi <= rsi_os):
                    confidence = 0.55   # RSI disagrees with the strategy — temper conviction

        explanation_bits = [f"{strat.label}: {signal}"]
        if rsi is not None:
            explanation_bits.append(f"RSI({rsi_period})={rsi}")
        if sma_val is not None:
            explanation_bits.append(f"{trend} {sma_distance_pct:+.1f}% vs SMA{sma_period}")

        return {
            "signal": signal,
            "strategy": strat_key,
            "strategy_label": strat.label,
            "indicators": {
                "rsi": rsi,
                "sma20": round(sma_val, 2) if sma_val else None,
                "sma_distance_pct": round(sma_distance_pct, 2),
                "trend": trend,
                "bars_analysed": len(closes),
            },
            "confidence": round(confidence, 2),
            "explanation": " · ".join(explanation_bits) or "no signal",
            "params_source": "tuned" if params_tuned else "default",
            "params": p,
        }


# Singleton — exposes reload_tuned() so /learning/run can hot-swap params
technical_agent_singleton = TechnicalAgent()

from ..services.news_service import news_service

# Weighted financial-news lexicon — bigger terms move sentiment more. Continuous
# scoring (not 3 buckets) so impact actually varies with the headlines.
_NEWS_POS = {
    "surge": 2.0, "surges": 2.0, "soar": 2.5, "soars": 2.5, "jump": 1.8, "jumps": 1.8,
    "rally": 2.0, "rallies": 2.0, "gain": 1.3, "gains": 1.3, "rise": 1.0, "rises": 1.0,
    "beat": 2.0, "beats": 2.0, "record": 1.8, "profit": 1.5, "profits": 1.5, "growth": 1.5,
    "upgrade": 2.0, "upgraded": 2.0, "outperform": 2.0, "buy": 1.0, "bullish": 2.0,
    "strong": 1.3, "dividend": 1.2, "expansion": 1.3, "wins": 1.5, "win": 1.2,
    "approval": 1.5, "approved": 1.5, "high": 0.8, "boost": 1.5, "rebound": 1.8,
}
_NEWS_NEG = {
    "plunge": 2.5, "plunges": 2.5, "crash": 3.0, "slump": 2.0, "tumble": 2.0, "tumbles": 2.0,
    "fall": 1.3, "falls": 1.3, "drop": 1.3, "drops": 1.3, "decline": 1.5, "declines": 1.5,
    "miss": 2.0, "misses": 2.0, "loss": 1.5, "losses": 1.5, "fraud": 3.0, "scam": 3.0,
    "downgrade": 2.0, "downgraded": 2.0, "underperform": 2.0, "sell": 1.0, "bearish": 2.0,
    "weak": 1.5, "cut": 1.5, "cuts": 1.5, "probe": 2.0, "investigation": 2.0, "warning": 2.0,
    "warns": 2.0, "lawsuit": 2.0, "default": 2.5, "low": 0.8, "halt": 1.8, "resign": 1.8,
}


def _score_headlines(titles) -> tuple:
    """Net weighted sentiment over headlines → (impact_score 0..1, pos_hits, neg_hits)."""
    import re
    pos = neg = 0.0
    for t in titles:
        for w in re.findall(r"[a-z]+", (t or "").lower()):
            pos += _NEWS_POS.get(w, 0.0)
            neg += _NEWS_NEG.get(w, 0.0)
    net = pos - neg
    # squash net into 0..1 around 0.5; ~3 net points ≈ a full swing
    score = 0.5 + (math.tanh(net / 3.0) / 2.0)
    return max(0.0, min(1.0, score)), pos, neg


class NewsAgent(BaseAgent):
    def __init__(self):
        super().__init__("NewsIntelligence")

    async def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        symbol = data.get("symbol", "market")
        news_items = await news_service.fetch_news(symbol)

        if news_items:
            titles = [n.get("title", "") for n in news_items]
            score, pos, neg = _score_headlines(titles)
        else:
            score, pos, neg = 0.5, 0.0, 0.0

        return {
            "sentiment": "positive" if score > 0.58 else ("negative" if score < 0.42 else "neutral"),
            "impact_score": round(score, 3),
            "news_count": len(news_items),
            "pos_signal": round(pos, 1),
            "neg_signal": round(neg, 1),
            "top_headline": news_items[0]["title"] if news_items else "No recent news",
        }

def _atr_from_series(series, period: int = 14):
    """Wilder ATR (absolute price) from an OHLC series; None if too short."""
    bars = [b for b in (series or []) if b.get("c") is not None]
    if len(bars) < period + 1:
        return None
    trs = []
    for i, b in enumerate(bars):
        c = float(b["c"]); h = float(b.get("h") or c); l = float(b.get("l") or c)
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


# Market regime is market-wide, so cache it briefly instead of re-deriving per symbol.
import time as _time
_regime_cache: dict = {}
_REGIME_TTL = 180.0  # seconds


async def _market_regime(benchmark: str):
    """Derive risk-on/off from a benchmark index's price vs its 20-day SMA.

    Replaces the old hardcoded 'expansionary' constant with a real read of the
    tape, so the macro nudge actually varies with the market. Returns
    (regime, commentary). Cached per benchmark for _REGIME_TTL.
    """
    now = _time.monotonic()
    cached = _regime_cache.get(benchmark)
    if cached and now - cached[0] < _REGIME_TTL:
        return cached[1]
    from ..services.market_data import market_data_service
    closes = []
    try:
        data = await market_data_service.get_intraday(benchmark, range_="3mo", interval="1d")
        closes = [b["c"] for b in (data.get("series") or []) if b.get("c") is not None]
    except Exception:
        closes = []
    res = ("neutral", f"{benchmark} trend unavailable")
    if len(closes) >= 20:
        sma = sum(closes[-20:]) / 20
        last = closes[-1]
        dist = (last - sma) / sma * 100 if sma else 0.0
        if dist > 0.5:
            res = ("expansionary", f"{benchmark} {dist:+.1f}% above 20D SMA — risk-on")
        elif dist < -0.5:
            res = ("contractionary", f"{benchmark} {dist:+.1f}% below 20D SMA — risk-off")
        else:
            res = ("neutral", f"{benchmark} flat vs 20D SMA ({dist:+.1f}%)")
    _regime_cache[benchmark] = (now, res)
    return res


class MacroAgent(BaseAgent):
    def __init__(self):
        super().__init__("MacroEconomics")

    async def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # Pick the benchmark for the symbol's market, then read its real trend.
        benchmark = "NIFTY" if (data.get("currency") == "INR") else "SPX"
        regime, commentary = await _market_regime(benchmark)
        macro_risk = {"expansionary": "low", "contractionary": "elevated"}.get(regime, "moderate")
        return {
            "market_regime": regime,
            "macro_risk": macro_risk,
            "benchmark": benchmark,
            "commentary": commentary,
        }


class RiskAgent(BaseAgent):
    def __init__(self):
        super().__init__("RiskManager")

    async def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        price = float(data.get("current_price") or 0)
        atr = _atr_from_series((data.get("intraday") or {}).get("series") or [], 14)
        stop_dist = max(atr * 1.5, price * 0.005) if (atr and atr > 0) else price * 0.02
        return {
            "max_loss": round(stop_dist, 2),          # per-share risk at the suggested stop
            "atr": round(atr, 2) if atr else None,
            "risk_allowed": price > 0,
            "stop_loss_suggested": round(max(0.0, price - stop_dist), 2),
        }
