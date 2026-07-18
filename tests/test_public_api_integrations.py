"""Offline tests for the public-API enrichment adapters (macro / OpenFIGI /
Finnhub).

All three adapters are slow-path / product-surface only and MUST:
  * never hit the network in these tests (pure parsers + disabled-by-default),
  * degrade gracefully (empty result, never raise) when no key is configured,
  * only ever TIGHTEN risk (the macro analyst), never loosen or emit an order.

No pytest-asyncio: async paths are driven with asyncio.run, matching
tests/test_integration_modules.py.
"""
from __future__ import annotations

import asyncio

from app.core.clock import SimClock
from app.core.events import ParameterChangeProposal, Streams
from app.services import finnhub_provider, macro_data, openfigi_symbols
from app.services.macro_data import (
    YieldCurvePoint,
    parse_fred_json,
    parse_treasury_xml,
)
from app.services.finnhub_provider import parse_quote, sentiment_score
from app.services.openfigi_symbols import parse_mapping_response
from app.slowpath.macro_regime import MacroRegimeAnalyst, classify_macro_regime
from tests.helpers import SyncTestBus

_T = 1_750_000_000_000_000_000

# --- Treasury yield-curve XML fixture (namespaced, two business days) --------
_TREASURY_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
  <entry><content><m:properties>
    <d:NEW_DATE>2026-07-15T00:00:00</d:NEW_DATE>
    <d:BC_2YEAR>4.20</d:BC_2YEAR>
    <d:BC_10YEAR>4.05</d:BC_10YEAR>
  </m:properties></content></entry>
  <entry><content><m:properties>
    <d:NEW_DATE>2026-07-16T00:00:00</d:NEW_DATE>
    <d:BC_2YEAR>4.60</d:BC_2YEAR>
    <d:BC_10YEAR>4.00</d:BC_10YEAR>
  </m:properties></content></entry>
</feed>"""


# --- macro_data pure parsers -------------------------------------------------

def test_parse_treasury_xml_extracts_sorted_points() -> None:
    points = parse_treasury_xml(_TREASURY_XML)
    assert [p.date for p in points] == [
        "2026-07-15T00:00:00", "2026-07-16T00:00:00"
    ]
    latest = points[-1]
    assert latest.y2 == 4.60 and latest.y10 == 4.00
    # 4.00 - 4.60 = -0.60 -> inverted (a stress precursor).
    assert round(latest.spread_10y_2y, 2) == -0.60
    assert latest.inverted is True


def test_parse_treasury_xml_bad_input_is_empty() -> None:
    assert parse_treasury_xml("not xml <<<") == []
    assert parse_treasury_xml("") == []


def test_parse_fred_json_drops_missing() -> None:
    payload = {"observations": [
        {"date": "2026-07-16", "value": "31.5"},
        {"date": "2026-07-15", "value": "."},   # FRED missing marker -> dropped
        {"date": "2026-07-14", "value": "30.0"},
    ]}
    obs = parse_fred_json(payload)
    assert [o["value"] for o in obs] == [31.5, 30.0]


def test_fred_disabled_without_key_makes_no_network_call(monkeypatch) -> None:
    # Force a blank key (env var overrides .env) -> disabled -> empty, no network.
    monkeypatch.setenv("ETB_FRED_API_KEY", "")
    adapter = macro_data.MacroDataAdapter()
    assert adapter.fred_enabled is False
    assert asyncio.run(adapter.fred_series("VIXCLS")) == []
    assert asyncio.run(adapter.latest_value("VIXCLS")) is None


# --- macro regime classification (pure) --------------------------------------

def test_classify_macro_calm_is_none() -> None:
    assert classify_macro_regime(spread_10y_2y=0.5, vix=15.0) is None
    assert classify_macro_regime(spread_10y_2y=None, vix=None) is None


def test_classify_macro_inversion_is_stress() -> None:
    assert classify_macro_regime(spread_10y_2y=-0.1, vix=12.0) == "stress"


def test_classify_macro_deep_inversion_or_high_vix_is_crisis() -> None:
    assert classify_macro_regime(spread_10y_2y=-0.7, vix=12.0) == "crisis"
    assert classify_macro_regime(spread_10y_2y=0.5, vix=45.0) == "crisis"


# --- MacroRegimeAnalyst: off-replay, TIGHTEN-only ----------------------------

class _FakeMacro:
    """Stand-in MacroDataAdapter with async methods, no network."""

    def __init__(self, point, vix) -> None:
        self._point, self._vix = point, vix

    async def latest_yield_curve(self):
        return self._point

    async def latest_value(self, series_id: str):
        return self._vix


def _run_analyst(point, vix, baseline=2_000_000.0):
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    analyst = MacroRegimeAnalyst(
        bus, clock, adapter=_FakeMacro(point, vix), baseline_gross=baseline
    )
    proposal = asyncio.run(analyst.poll_and_propose())
    proposals = [
        ParameterChangeProposal.model_validate(e.payload)
        for e in bus.stream(Streams.CTL_PARAM_PROPOSALS)
    ]
    return analyst, proposal, proposals


def test_macro_analyst_tightens_on_stress() -> None:
    inverted = YieldCurvePoint(date="2026-07-16T00:00:00", y2=4.60, y10=4.00)
    analyst, proposal, proposals = _run_analyst(inverted, vix=35.0)
    assert analyst.macro_regime in ("stress", "crisis")
    assert proposal is not None
    assert len(proposals) == 1
    p = proposals[0]
    assert p.parameter == "risk.max_gross_exposure"
    # Tightening only: proposed value is strictly BELOW baseline (fail-safe).
    assert p.proposed_value < 2_000_000.0
    assert any("macro_regime=" in ev for ev in p.evidence)


def test_macro_analyst_calm_emits_nothing() -> None:
    normal = YieldCurvePoint(date="2026-07-16T00:00:00", y2=4.00, y10=4.40)
    analyst, proposal, proposals = _run_analyst(normal, vix=14.0)
    assert analyst.macro_regime is None
    assert proposal is None
    assert proposals == []


def test_macro_analyst_swallows_adapter_error() -> None:
    class _Boom:
        async def latest_yield_curve(self):
            raise RuntimeError("macro outage")

        async def latest_value(self, series_id: str):
            raise RuntimeError("macro outage")

    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    analyst = MacroRegimeAnalyst(bus, clock, adapter=_Boom())
    # A macro outage changes nothing and never raises into the caller.
    assert asyncio.run(analyst.poll_and_propose()) is None
    assert analyst.errors == 1
    assert bus.stream(Streams.CTL_PARAM_PROPOSALS) == []


# --- OpenFIGI parsing --------------------------------------------------------

def test_parse_mapping_response_hit() -> None:
    item = {"data": [{
        "figi": "BBG000B9XRY4", "name": "APPLE INC", "ticker": "AAPL",
        "exchCode": "US", "securityType": "Common Stock", "marketSecDes": "Equity",
    }]}
    ref = parse_mapping_response(item)
    assert ref is not None
    assert ref.figi == "BBG000B9XRY4" and ref.ticker == "AAPL"
    assert ref.market_sector == "Equity"


def test_parse_mapping_response_miss() -> None:
    assert parse_mapping_response({"error": "No identifier found."}) is None
    assert parse_mapping_response({"data": []}) is None
    assert parse_mapping_response({"data": [{"name": "x"}]}) is None  # no figi
    assert parse_mapping_response("nope") is None  # type: ignore[arg-type]


def test_openfigi_cache_clear_runs() -> None:
    openfigi_symbols.clear_cache()  # no-op smoke: must not raise


# --- Finnhub parsing + disabled-by-default -----------------------------------

def test_parse_quote_valid_and_invalid() -> None:
    good = parse_quote({"c": 195.3, "h": 196.0, "l": 193.1, "o": 194.0,
                        "pc": 193.5, "t": 1_752_600_000})
    assert good is not None and good["price"] == 195.3
    assert parse_quote({"c": 0}) is None          # unknown symbol -> 0 -> miss
    assert parse_quote({"c": "bad"}) is None
    assert parse_quote({}) is None


def test_sentiment_score_bounds() -> None:
    assert sentiment_score([]) == 0.0
    assert sentiment_score([{"sentiment": 0.5}, {"sentiment": -0.5}]) == 0.0
    # Out-of-range values are clamped to [-1, 1] before averaging.
    assert sentiment_score([{"sentiment": 5.0}]) == 1.0
    assert sentiment_score([{"nope": 1}]) == 0.0  # no sentiment field -> ignored


# --- MacroRegimeService: auto-publish to a live bus + controller -------------

def _service_with(point, vix, ttl_s=3600):
    from app.engine.macro_regime_service import MacroRegimeService
    svc = MacroRegimeService(poll_interval_s=1, analyst_ttl_s=ttl_s)
    svc.analyst._data = _FakeMacro(point, vix)  # inject: no network in tests
    return svc


def test_macro_service_stress_tightens_effective_limit() -> None:
    inverted = YieldCurvePoint(date="2026-07-16T00:00:00", y2=4.60, y10=4.00)
    svc = _service_with(inverted, vix=35.0)
    status = asyncio.run(svc.poll_once())
    assert status["proposals_published"] == 1
    assert status["macro_regime"] in ("stress", "crisis")
    gross = status["limits"]["risk.max_gross_exposure"]
    # The controller applied the tightening: effective is below baseline.
    assert gross["effective"] < gross["baseline"]
    assert gross["tightened"] is True


def test_macro_service_calm_leaves_baseline() -> None:
    normal = YieldCurvePoint(date="2026-07-16T00:00:00", y2=4.00, y10=4.40)
    svc = _service_with(normal, vix=14.0)
    status = asyncio.run(svc.poll_once())
    assert status["proposals_published"] == 0
    assert status["macro_regime"] is None
    gross = status["limits"]["risk.max_gross_exposure"]
    assert gross["effective"] == gross["baseline"]
    assert gross["tightened"] is False


def test_macro_service_ttl_reverts_to_baseline() -> None:
    # A stress poll applies a tightening; a later CALM poll's heartbeat drives
    # the controller's TTL expiry, reverting the override to the baseline.
    class _Mutable:
        def __init__(self):
            self.point = YieldCurvePoint(date="d", y2=4.60, y10=4.00)  # inverted
            self.vix = 35.0

        async def latest_yield_curve(self):
            return self.point

        async def latest_value(self, series_id):
            return self.vix

    from app.engine.macro_regime_service import MacroRegimeService
    svc = MacroRegimeService(poll_interval_s=1, analyst_ttl_s=0)
    fake = _Mutable()
    svc.analyst._data = fake

    s1 = asyncio.run(svc.poll_once())              # stress -> tighten
    assert s1["limits"]["risk.max_gross_exposure"]["tightened"] is True

    fake.point = YieldCurvePoint(date="d", y2=4.00, y10=4.40)  # curve normalizes
    fake.vix = 14.0                                             # vol calms
    s2 = asyncio.run(svc.poll_once())              # calm -> ttl expiry reverts
    gross = s2["limits"]["risk.max_gross_exposure"]
    assert gross["effective"] == gross["baseline"]
    assert gross["tightened"] is False


def test_macro_service_simulate_tightens_then_restores_source() -> None:
    from app.engine.macro_regime_service import MacroRegimeService
    svc = MacroRegimeService(poll_interval_s=1, analyst_ttl_s=3600)
    live_source = svc.analyst._data
    # Synthetic crisis: deep inversion + high VIX.
    status = asyncio.run(svc.simulate_poll(spread=-0.6, vix=45.0))
    assert status["simulated"] == {"spread_10y_2y": -0.6, "vix": 45.0}
    assert status["macro_regime"] == "crisis"
    gross = status["limits"]["risk.max_gross_exposure"]
    assert gross["effective"] < gross["baseline"]  # genuine tightening applied
    # The live data source is restored (not left pointing at the sim).
    assert svc.analyst._data is live_source


def test_finnhub_disabled_without_key(monkeypatch) -> None:
    # Force a blank key (env var overrides .env) -> disabled -> empty, no network.
    monkeypatch.setenv("ETB_FINNHUB_API_KEY", "")
    fh = finnhub_provider.FinnhubProvider()
    assert fh.enabled is False
    assert asyncio.run(fh.quote("AAPL")) is None
    assert asyncio.run(fh.company_news("AAPL", "2026-07-01", "2026-07-16")) == []
    assert asyncio.run(
        fh.news_sentiment("AAPL", "2026-07-01", "2026-07-16")
    ) == 0.0
