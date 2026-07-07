import math
from typing import Dict, Any

class RiskEngine:
    def __init__(self, total_capital: float, max_risk_per_trade: float = 0.02):
        self.total_capital = total_capital
        self.max_risk_per_trade = max_risk_per_trade # 2% default

    def calculate_position_size(self, entry: float, stop_loss: float, confidence: float,
                                total_capital: float | None = None) -> Dict[str, Any]:
        """
        Uses Kelly Criterion and Risk-per-trade to determine optimal quantity.

        `total_capital` overrides the default when a real broker balance is known,
        so sizing reflects the user's actual account instead of a mock figure.
        """
        cap = total_capital if (total_capital and total_capital > 0) else self.total_capital
        risk_amount = cap * self.max_risk_per_trade
        per_share_risk = abs(entry - stop_loss)

        if per_share_risk == 0:
            return {"quantity": 0, "risk_amount": 0, "capital_basis": cap}

        # Basic position sizing based on risk amount
        base_quantity = risk_amount / per_share_risk

        # Adjust by confidence (Simple multiplier)
        adjusted_quantity = math.floor(base_quantity * confidence)

        # Kelly Criterion simplified: f* = (bp - q) / b
        win_rate = confidence
        loss_rate = 1 - win_rate
        win_loss_ratio = 2.0  # Assume 2:1 for Kelly calc

        kelly_f = (win_loss_ratio * win_rate - loss_rate) / win_loss_ratio
        kelly_f = max(0, min(kelly_f, 0.2))  # Cap Kelly at 20% of capital for safety

        kelly_quantity = math.floor((cap * kelly_f) / entry) if entry else 0

        # Final quantity is the more conservative of the two
        final_quantity = max(0, min(adjusted_quantity, kelly_quantity))

        return {
            "quantity": final_quantity,
            "risk_amount": final_quantity * per_share_risk,
            "capital_required": final_quantity * entry,
            "kelly_fraction": kelly_f,
            "capital_basis": cap,
        }

    def check_daily_limit(self, current_daily_loss: float, limit: float) -> bool:
        return current_daily_loss < limit

risk_engine = RiskEngine(total_capital=1000000) # Mock 10L capital
