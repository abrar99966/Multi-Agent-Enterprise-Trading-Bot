"""AI-agent training pipeline.

Phase 1: rule-based backtest + grid-tune RSI/SMA params per symbol.
Phase 2 (later): XGBoost classifier on engineered features.
Phase 3 (only if 2 wins out-of-sample): RL or transformer.

Honest framing — backtest results are NOT a guarantee of live performance.
Slippage, fees, and look-ahead bias all eat into apparent edge. Treat tuned
params as a starting point, not a magic improvement.
"""
