"""Tests for the three integration modules:
  Phase A: OpenBB data adapter
  Phase B: TradingAgents-inspired personas
  Phase C: Paperclip-style agent governance
"""
import asyncio
import pytest
from unittest.mock import MagicMock

from tests.helpers import SyncTestBus
from app.core.clock import SimClock


def make_bus_clock():
    clock = SimClock()
    bus = SyncTestBus(clock)
    return bus, clock


# ═══════════════════════════════════════════════════════════════════════════
# Phase A: OpenBB Data Adapter
# ═══════════════════════════════════════════════════════════════════════════

class TestOpenBBAdapter:
    """Test the OpenBB adapter degrades gracefully (SDK not installed in CI)."""

    def test_import(self):
        from app.services.openbb_adapter import OpenBBDataAdapter, openbb_data
        assert openbb_data is not None
        assert isinstance(openbb_data, OpenBBDataAdapter)

    def test_cache_basic(self):
        from app.services.openbb_adapter import _TTLCache
        cache = _TTLCache(default_ttl=60)
        assert cache.get("missing") is None
        cache.set("key1", {"data": 42})
        assert cache.get("key1") == {"data": 42}
        cache.clear()
        assert cache.get("key1") is None

    def test_cache_expired(self):
        from app.services.openbb_adapter import _TTLCache
        cache = _TTLCache(default_ttl=0.0)  # instant expiry
        cache.set("key1", "value")
        assert cache.get("key1") is None  # already expired

    def test_graceful_degradation_historical(self):
        from app.services.openbb_adapter import OpenBBDataAdapter
        adapter = OpenBBDataAdapter()
        result = asyncio.run(adapter.historical_prices("AAPL"))
        assert isinstance(result, list)

    def test_graceful_degradation_profile(self):
        from app.services.openbb_adapter import OpenBBDataAdapter
        adapter = OpenBBDataAdapter()
        result = asyncio.run(adapter.company_profile("AAPL"))
        assert isinstance(result, dict)

    def test_graceful_degradation_news(self):
        from app.services.openbb_adapter import OpenBBDataAdapter
        adapter = OpenBBDataAdapter()
        result = asyncio.run(adapter.world_news())
        assert isinstance(result, list)

    def test_graceful_degradation_macro(self):
        from app.services.openbb_adapter import OpenBBDataAdapter
        adapter = OpenBBDataAdapter()
        result = asyncio.run(adapter.economic_indicators(["GDP"]))
        assert isinstance(result, list)

    def test_analyst_context_bundle(self):
        from app.services.openbb_adapter import OpenBBDataAdapter
        adapter = OpenBBDataAdapter()
        ctx = asyncio.run(adapter.analyst_context("AAPL", include_macro=True))
        assert ctx["symbol"] == "AAPL"
        for key in ["recent_news", "profile", "metrics", "macro_indicators"]:
            assert key in ctx


# ═══════════════════════════════════════════════════════════════════════════
# Phase B: TradingAgents-Inspired Personas
# ═══════════════════════════════════════════════════════════════════════════

class TestPersonas:
    """Test the specialized analyst personas."""

    def _stub_provider(self, response=None):
        from app.slowpath.providers import StubProvider
        return StubProvider(response=response or {
            "direction": "bearish", "severity": "medium",
            "confidence": 0.7, "affected": ["HDFCBANK"],
            "rationale": "Test signal.",
        })

    def test_fundamentals_analyst(self):
        bus, clock = make_bus_clock()
        from app.slowpath.personas import FundamentalsAnalyst
        analyst = FundamentalsAnalyst(bus, clock, self._stub_provider())
        result = analyst.assess("Q3 earnings miss", ["HDFCBANK"])
        assert result["direction"] == "bearish"
        assert result["severity"] == "medium"

    def test_sentiment_analyst(self):
        bus, clock = make_bus_clock()
        from app.slowpath.personas import SentimentAnalyst
        analyst = SentimentAnalyst(bus, clock, self._stub_provider())
        result = analyst.assess("Negative social media trend", ["INFY"])
        assert result["direction"] == "bearish"

    def test_technical_analyst(self):
        bus, clock = make_bus_clock()
        from app.slowpath.personas import TechnicalAnalyst
        analyst = TechnicalAnalyst(bus, clock, self._stub_provider())
        result = analyst.assess("Support broken at 1500", ["RELIANCE"])
        assert result["direction"] == "bearish"

    def test_macro_analyst(self):
        bus, clock = make_bus_clock()
        from app.slowpath.personas import MacroAnalyst
        analyst = MacroAnalyst(bus, clock, self._stub_provider())
        result = analyst.assess("RBI rate hike 50bps", ["NIFTY"])
        assert result["direction"] == "bearish"

    def test_persona_assess_and_propose_bearish(self):
        bus, clock = make_bus_clock()
        from app.slowpath.personas import FundamentalsAnalyst
        analyst = FundamentalsAnalyst(bus, clock, self._stub_provider())
        proposal = analyst.assess_and_propose("Earnings miss", ["HDFCBANK"])
        assert proposal is not None
        assert proposal.parameter == "risk.max_gross_exposure"
        # Bearish medium => tighten to 60% of baseline
        assert proposal.proposed_value == 2_000_000.0 * 0.6

    def test_persona_assess_and_propose_neutral(self):
        bus, clock = make_bus_clock()
        from app.slowpath.personas import SentimentAnalyst
        provider = self._stub_provider({
            "direction": "neutral", "severity": "low",
            "confidence": 0.3, "affected": [], "rationale": "Mixed signals.",
        })
        analyst = SentimentAnalyst(bus, clock, provider)
        proposal = analyst.assess_and_propose("Some headline", ["AAPL"])
        assert proposal is None  # neutral → no proposal

    def test_persona_source_tagging(self):
        bus, clock = make_bus_clock()
        from app.slowpath.personas import MacroAnalyst
        analyst = MacroAnalyst(bus, clock, self._stub_provider())
        assert "macro:" in analyst._source


# ═══════════════════════════════════════════════════════════════════════════
# Phase C: Paperclip-Style Agent Governance
# ═══════════════════════════════════════════════════════════════════════════

class TestGovernance:
    """Test the agent lifecycle governor."""

    def test_register_and_list(self):
        from app.slowpath.governance import AgentGovernor, AgentStatus
        gov = AgentGovernor()
        gov.register("fund-1", "fundamentals")
        gov.register("sent-1", "sentiment")
        assert len(gov.list_agents()) == 2
        assert len(gov.list_agents(status=AgentStatus.ACTIVE)) == 2

    def test_pause_resume(self):
        from app.slowpath.governance import AgentGovernor, AgentStatus
        gov = AgentGovernor()
        gov.register("a1", "test")
        assert gov.can_invoke("a1")
        gov.pause("a1", reason="testing")
        assert not gov.can_invoke("a1")
        agent = gov.get_agent("a1")
        assert agent.status == AgentStatus.PAUSED
        gov.resume("a1")
        assert gov.can_invoke("a1")
        assert agent.status == AgentStatus.ACTIVE

    def test_terminate(self):
        from app.slowpath.governance import AgentGovernor, AgentStatus
        gov = AgentGovernor()
        gov.register("a1", "test")
        gov.terminate("a1", reason="done")
        assert not gov.can_invoke("a1")
        agent = gov.get_agent("a1")
        assert agent.status == AgentStatus.TERMINATED
        # Cannot resume a terminated agent
        assert not gov.resume("a1")

    def test_record_invocation(self):
        from app.slowpath.governance import AgentGovernor
        gov = AgentGovernor()
        gov.register("a1", "test")
        gov.record_invocation("a1", latency_ms=150, tokens=500)
        gov.record_invocation("a1", latency_ms=250, tokens=300)
        agent = gov.get_agent("a1")
        assert agent.metrics.invocations == 2
        assert agent.metrics.estimated_token_spend == 800
        assert agent.metrics.avg_latency_ms == 200.0

    def test_auto_pause_on_errors(self):
        from app.slowpath.governance import AgentGovernor, AgentConfig, AgentStatus
        gov = AgentGovernor()
        gov.register("a1", "test", config=AgentConfig(max_errors_before_pause=3))
        gov.record_invocation("a1", error=True)
        gov.record_invocation("a1", error=True)
        assert gov.get_agent("a1").status == AgentStatus.ACTIVE
        gov.record_invocation("a1", error=True)  # 3rd error → auto-pause
        assert gov.get_agent("a1").status == AgentStatus.PAUSED

    def test_token_budget_exhaustion(self):
        from app.slowpath.governance import AgentGovernor, AgentConfig
        gov = AgentGovernor()
        gov.register("a1", "test", config=AgentConfig(max_token_budget=1000))
        gov.record_invocation("a1", tokens=999)
        assert gov.can_invoke("a1")
        gov.record_invocation("a1", tokens=1)
        # Budget exhausted → should be paused on next can_invoke
        assert not gov.can_invoke("a1")

    def test_dashboard_summary(self):
        from app.slowpath.governance import AgentGovernor
        gov = AgentGovernor()
        gov.register("a1", "fundamentals")
        gov.register("a2", "sentiment")
        gov.record_invocation("a1", latency_ms=100, tokens=200)
        gov.pause("a2")
        summary = gov.dashboard_summary()
        assert summary["total_agents"] == 2
        assert summary["active"] == 1
        assert summary["paused"] == 1
        assert summary["total_invocations"] == 1
        assert summary["total_token_spend"] == 200
        assert len(summary["agents"]) == 2

    def test_reset_metrics(self):
        from app.slowpath.governance import AgentGovernor
        gov = AgentGovernor()
        gov.register("a1", "test")
        gov.record_invocation("a1", tokens=500)
        assert gov.get_agent("a1").metrics.invocations == 1
        gov.reset_metrics("a1")
        assert gov.get_agent("a1").metrics.invocations == 0
        assert gov.get_agent("a1").metrics.estimated_token_spend == 0

    def test_duplicate_register(self):
        from app.slowpath.governance import AgentGovernor
        gov = AgentGovernor()
        a1 = gov.register("a1", "test")
        a1_dup = gov.register("a1", "test")
        assert a1 is a1_dup  # same object, not a new registration

    def test_status_change_callback(self):
        from app.slowpath.governance import AgentGovernor
        changes = []
        def on_change(agent_id, old, new):
            changes.append((agent_id, old, new))
        gov = AgentGovernor(on_status_change=on_change)
        gov.register("a1", "test")
        gov.pause("a1")
        gov.resume("a1")
        assert len(changes) == 2
        assert changes[0] == ("a1", "active", "paused")
        assert changes[1] == ("a1", "paused", "active")
