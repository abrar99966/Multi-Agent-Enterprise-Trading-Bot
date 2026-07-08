"""Seed paper trading recommendations into the live DB for performance tracking.

Creates a user if needed, then inserts realistic graded trade signals so
the /performance endpoint shows hit rate and win probability.
"""
import sys, asyncio, random
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.stdout.reconfigure(encoding='utf-8')

from app.db.session import async_session
from app.models.database import TradeRecommendation, TradeSide, MarketType, User, TradeStatus
from sqlalchemy import select, text

# Realistic price scenarios for Indian + US stocks
SCENARIOS = [
    # (symbol, market, side, entry, after_1h, after_24h)
    ("RELIANCE", "equity", "buy",  2865.50, 2878.30, 2910.50),
    ("RELIANCE", "equity", "buy",  2900.00, 2912.00, 2935.20),
    ("RELIANCE", "equity", "sell", 2950.00, 2935.00, 2888.40),
    ("RELIANCE", "equity", "buy",  2850.00, 2845.00, 2830.00),
    ("TCS",      "equity", "buy",  3540.00, 3558.00, 3600.00),
    ("TCS",      "equity", "sell", 3620.00, 3598.00, 3555.00),
    ("TCS",      "equity", "buy",  3500.00, 3488.00, 3470.00),
    ("TCS",      "equity", "buy",  3480.00, 3510.00, 3545.00),
    ("INFY",     "equity", "buy",  1520.00, 1535.00, 1560.00),
    ("INFY",     "equity", "buy",  1480.00, 1492.00, 1510.00),
    ("INFY",     "equity", "sell", 1560.00, 1545.00, 1520.00),
    ("INFY",     "equity", "buy",  1500.00, 1498.00, 1485.00),
    ("HDFCBANK", "equity", "buy",  1680.00, 1695.00, 1720.00),
    ("HDFCBANK", "equity", "buy",  1650.00, 1670.00, 1690.00),
    ("HDFCBANK", "equity", "sell", 1730.00, 1715.00, 1690.00),
    ("HDFCBANK", "equity", "buy",  1700.00, 1695.00, 1680.00),
    ("NIFTY",    "f_o",    "buy",  24200.00, 24300.00, 24500.00),
    ("NIFTY",    "f_o",    "sell", 24500.00, 24400.00, 24200.00),
    ("NIFTY",    "f_o",    "buy",  24000.00, 24050.00, 24150.00),
    ("NIFTY",    "f_o",    "buy",  24100.00, 24080.00, 24000.00),
    ("AAPL",     "equity", "buy",  195.00,  197.50,  201.00),
    ("AAPL",     "equity", "sell", 200.00,  197.00,  193.00),
    ("MSFT",     "equity", "buy",  420.00,  425.00,  435.00),
    ("MSFT",     "equity", "buy",  430.00,  428.00,  422.00),
    ("NVDA",     "equity", "buy",  130.00,  133.00,  140.00),
    ("NVDA",     "equity", "buy",  135.00,  137.50,  142.00),
    ("NVDA",     "equity", "sell", 145.00,  140.00,  132.00),
    ("NVDA",     "equity", "buy",  140.00,  138.00,  135.00),
    ("BANKNIFTY","f_o",    "buy",  52000.0, 52200.0, 52500.0),
    ("BANKNIFTY","f_o",    "sell", 52500.0, 52200.0, 51800.0),
]

MARKET_MAP = {"equity": MarketType.EQUITY, "f_o": MarketType.F_O}
NEUTRAL_THRESHOLD = 0.1


async def run():
    now = datetime.utcnow()
    random.seed(42)

    async with async_session() as db:
        # Ensure a user exists
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                username="trader",
                email="trader@etb.local",
                hashed_password="paper_only_no_real_auth",
            )
            db.add(user)
            await db.flush()
            print(f"  Created user: {user.username} (id={user.id})")
        user_id = user.id

        total = wins_1h = wins_24h = losses_1h = 0

        for i, (symbol, market, side, entry, after_1h, after_24h) in enumerate(SCENARIOS):
            created = now - timedelta(
                days=random.uniform(0.1, 7),
                hours=random.uniform(0, 8),
            )

            move_1h = (after_1h - entry) / entry * 100
            move_24h = (after_24h - entry) / entry * 100

            if abs(move_1h) < NEUTRAL_THRESHOLD:
                correct_1h = False
            elif side == "buy":
                correct_1h = move_1h > 0
            else:
                correct_1h = move_1h < 0

            if abs(move_24h) < NEUTRAL_THRESHOLD:
                correct_24h = False
            elif side == "buy":
                correct_24h = move_24h > 0
            else:
                correct_24h = move_24h < 0

            multiplier = 1.02 if side == "buy" else 0.98
            sl_mult = 0.98 if side == "buy" else 1.02
            confidence = min(0.95, 0.5 + abs(move_1h) * 0.12 + random.uniform(-0.05, 0.1))

            rec = TradeRecommendation(
                user_id=user_id,
                symbol=symbol,
                market=MARKET_MAP[market],
                side=TradeSide.BUY if side == "buy" else TradeSide.SELL,
                entry_price=entry,
                target_price=round(entry * multiplier, 2),
                stop_loss=round(entry * sl_mult, 2),
                quantity=10,
                confidence_score=round(confidence, 3),
                risk_reward_ratio=round(abs(multiplier - 1) / abs(sl_mult - 1), 2),
                reasoning=f"Momentum crossover signal on {symbol} ({market}). "
                          f"{'Bullish' if side == 'buy' else 'Bearish'} setup with "
                          f"{abs(move_1h):.1f}% expected move.",
                agent_outputs={
                    "source": "paper_trading_simulation",
                    "rationale": {
                        "strategy": "momentum_crossover",
                        "regime": "trending",
                        "signal_strength": round(abs(move_1h), 2),
                    }
                },
                status=TradeStatus.EXECUTED,
                created_at=created,
                expires_at=created + timedelta(hours=24),
                # Pre-grade
                graded_at=now,
                price_after_1h=round(after_1h, 2),
                actual_move_pct_1h=round(move_1h, 3),
                signal_correct_1h=correct_1h,
                price_after_24h=round(after_24h, 2),
                actual_move_pct_24h=round(move_24h, 3),
                signal_correct_24h=correct_24h,
            )

            total += 1
            if correct_1h:
                wins_1h += 1
            else:
                losses_1h += 1
            if correct_24h:
                wins_24h += 1

            db.add(rec)

        await db.commit()

    graded_1h = wins_1h + losses_1h
    hit_1h = wins_1h / graded_1h * 100 if graded_1h > 0 else 0
    graded_24h = total  # all have 24h data
    hit_24h = wins_24h / graded_24h * 100 if graded_24h > 0 else 0

    print("=" * 70)
    print("PAPER TRADING SIGNALS -> LIVE DATABASE")
    print("=" * 70)
    print(f"  Total recommendations:  {total}")
    print(f"  Graded (1h):           {graded_1h}")
    print(f"  Correct (1h):          {wins_1h}")
    print(f"  Wrong (1h):            {losses_1h}")
    print(f"  HIT RATE (1h):         {hit_1h:.1f}%")
    print(f"  Correct (24h):         {wins_24h}")
    print(f"  HIT RATE (24h):        {hit_24h:.1f}%")
    print()
    print("  Now check these endpoints:")
    print("  - http://127.0.0.1:8000/api/v1/performance/stats?days=7")
    print("  - http://127.0.0.1:8000/dash")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run())
