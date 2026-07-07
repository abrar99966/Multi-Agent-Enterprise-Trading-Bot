"""Hot-path latency profiler with HDR-histogram-style bucketing.

Instruments every stage of the tick-to-order pipeline and tracks p50/p99/p999
latencies in nanosecond precision. This is the prerequisite for the "port to
Rust + Aeron" decision: you can't optimise what you can't measure.

Stages instrumented (TARGET_ARCHITECTURE §12 latency budget):
    1. FEED_DECODE     — venue → normalised bar/tick
    2. FEATURE_UPDATE  — bar → feature fabric computation
    3. INFERENCE        — features → model score
    4. RISK_CHECK      — intent → risk gateway verdict
    5. ORDER_ENCODE    — verdict → order dispatch
    6. TOTAL           — end-to-end tick → order

Each stage maintains a sorted-insertable list of latency samples. For
production use at scale, replace with HdrHistogram (or the Rust
hdr_histogram crate). The Python version here is accurate for
proof-of-concept profiling at retail tick rates (< 10k/s).

Thread-safe via per-stage locks (critical for the feed handler which may
be on a separate thread).

Usage::

    profiler = LatencyProfiler()
    with profiler.measure(Stage.FEATURE_UPDATE):
        features = fabric.update(bar)

    # Or explicit start/stop:
    token = profiler.start(Stage.INFERENCE)
    result = model.score(features)
    profiler.stop(token)

    print(profiler.report())
"""
from __future__ import annotations

import bisect
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Generator, List, Optional


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

class Stage(str, Enum):
    FEED_DECODE    = "feed_decode"
    FEATURE_UPDATE = "feature_update"
    INFERENCE      = "inference"
    RISK_CHECK     = "risk_check"
    ORDER_ENCODE   = "order_encode"
    TOTAL          = "total"


# Latency targets from TARGET_ARCHITECTURE §12 (retail tier, in microseconds)
RETAIL_TARGETS_US: Dict[Stage, float] = {
    Stage.FEED_DECODE:    500.0,     # 0.5 ms
    Stage.FEATURE_UPDATE: 1000.0,    # 1 ms
    Stage.INFERENCE:      2000.0,    # 2 ms
    Stage.RISK_CHECK:     1000.0,    # 1 ms
    Stage.ORDER_ENCODE:   100.0,     # 0.1 ms
    Stage.TOTAL:          5000.0,    # 5 ms p50
}

# Future DMA/colo targets (microseconds)
DMA_TARGETS_US: Dict[Stage, float] = {
    Stage.FEED_DECODE:    20.0,
    Stage.FEATURE_UPDATE: 50.0,
    Stage.INFERENCE:      80.0,
    Stage.RISK_CHECK:     50.0,
    Stage.ORDER_ENCODE:   20.0,
    Stage.TOTAL:          250.0,
}


# ---------------------------------------------------------------------------
# Measurement token
# ---------------------------------------------------------------------------

@dataclass
class _Token:
    stage: Stage
    start_ns: int
    thread_id: int


# ---------------------------------------------------------------------------
# Per-stage histogram
# ---------------------------------------------------------------------------

class _StageHistogram:
    """Sorted sample buffer for a single pipeline stage.

    Keeps the most recent ``max_samples`` latency measurements in sorted
    order for efficient percentile queries.
    """

    def __init__(self, max_samples: int = 10_000) -> None:
        self._lock = threading.Lock()
        self._samples: List[int] = []          # sorted ns values
        self._max = max_samples
        self._count: int = 0                   # total ever recorded
        self._sum_ns: int = 0
        self._min_ns: Optional[int] = None
        self._max_ns: Optional[int] = None
        # Rolling window for recent stats
        self._recent: deque[int] = deque(maxlen=1000)

    def record(self, latency_ns: int) -> None:
        with self._lock:
            self._count += 1
            self._sum_ns += latency_ns
            if self._min_ns is None or latency_ns < self._min_ns:
                self._min_ns = latency_ns
            if self._max_ns is None or latency_ns > self._max_ns:
                self._max_ns = latency_ns

            self._recent.append(latency_ns)

            if len(self._samples) >= self._max:
                # Evict oldest entry (approximation — keeps recent bias)
                self._samples.pop(0)
            bisect.insort(self._samples, latency_ns)

    def percentile(self, p: float) -> Optional[float]:
        """Return the p-th percentile in microseconds, or None."""
        with self._lock:
            n = len(self._samples)
            if n == 0:
                return None
            idx = int(p / 100.0 * (n - 1))
            return self._samples[idx] / 1000.0  # ns → µs

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            n = len(self._samples)
            if n == 0:
                return {
                    "count": 0, "p50_us": None, "p99_us": None,
                    "p999_us": None, "min_us": None, "max_us": None,
                    "mean_us": None,
                }
            return {
                "count": self._count,
                "p50_us": round(self._samples[n // 2] / 1000.0, 2),
                "p99_us": round(self._samples[int(0.99 * (n - 1))] / 1000.0, 2),
                "p999_us": round(self._samples[int(0.999 * (n - 1))] / 1000.0, 2),
                "min_us": round(self._min_ns / 1000.0, 2) if self._min_ns else None,
                "max_us": round(self._max_ns / 1000.0, 2) if self._max_ns else None,
                "mean_us": round(self._sum_ns / self._count / 1000.0, 2),
            }

    def recent_stats(self) -> Dict[str, Any]:
        """Stats over the most recent 1000 samples."""
        with self._lock:
            vals = sorted(self._recent)
            n = len(vals)
            if n == 0:
                return {"count": 0}
            return {
                "count": n,
                "p50_us": round(vals[n // 2] / 1000.0, 2),
                "p99_us": round(vals[int(0.99 * (n - 1))] / 1000.0, 2),
                "mean_us": round(sum(vals) / n / 1000.0, 2),
            }


# ---------------------------------------------------------------------------
# Profiler
# ---------------------------------------------------------------------------

class LatencyProfiler:
    """Instruments the tick-to-order pipeline.

    Use as a context manager or with explicit start/stop tokens.
    """

    def __init__(self, max_samples_per_stage: int = 10_000) -> None:
        self._histograms: Dict[Stage, _StageHistogram] = {
            stage: _StageHistogram(max_samples_per_stage)
            for stage in Stage
        }
        self._active_tokens: Dict[int, _Token] = {}  # token_id → token
        self._token_counter = 0
        self._lock = threading.Lock()
        self._breaches: List[Dict[str, Any]] = []     # SLA breaches

    # -- Measurement --------------------------------------------------------

    def start(self, stage: Stage) -> int:
        """Begin a measurement. Returns a token ID to pass to stop()."""
        with self._lock:
            self._token_counter += 1
            token_id = self._token_counter
        token = _Token(
            stage=stage,
            start_ns=time.perf_counter_ns(),
            thread_id=threading.get_ident(),
        )
        self._active_tokens[token_id] = token
        return token_id

    def stop(self, token_id: int) -> int:
        """End a measurement. Returns latency in nanoseconds."""
        end_ns = time.perf_counter_ns()
        token = self._active_tokens.pop(token_id, None)
        if token is None:
            return 0
        latency_ns = end_ns - token.start_ns
        self._histograms[token.stage].record(latency_ns)
        self._check_sla(token.stage, latency_ns)
        return latency_ns

    @contextmanager
    def measure(self, stage: Stage) -> Generator[None, None, None]:
        """Context manager for measuring a pipeline stage."""
        token_id = self.start(stage)
        try:
            yield
        finally:
            self.stop(token_id)

    def record_direct(self, stage: Stage, latency_ns: int) -> None:
        """Record a pre-measured latency directly."""
        self._histograms[stage].record(latency_ns)
        self._check_sla(stage, latency_ns)

    # -- SLA checking -------------------------------------------------------

    def _check_sla(self, stage: Stage, latency_ns: int) -> None:
        target = RETAIL_TARGETS_US.get(stage)
        if target is None:
            return
        latency_us = latency_ns / 1000.0
        if latency_us > target * 3:  # 3× target = breach
            self._breaches.append({
                "ts": time.time(),
                "stage": stage.value,
                "latency_us": round(latency_us, 2),
                "target_us": target,
                "multiple": round(latency_us / target, 2),
            })
            # Keep only last 1000 breaches
            if len(self._breaches) > 1000:
                self._breaches = self._breaches[-500:]

    # -- Reporting ----------------------------------------------------------

    def report(self) -> Dict[str, Any]:
        """Full profiling report across all stages."""
        stages = {}
        for stage in Stage:
            hist = self._histograms[stage]
            stats = hist.stats()
            target = RETAIL_TARGETS_US.get(stage)
            if stats["p50_us"] is not None and target:
                stats["target_us"] = target
                stats["meets_target"] = stats["p50_us"] <= target
                stats["headroom_pct"] = round(
                    (1 - stats["p50_us"] / target) * 100, 1
                )
            stages[stage.value] = stats
        return {
            "stages": stages,
            "total_breaches": len(self._breaches),
            "recent_breaches": self._breaches[-10:],
        }

    def stage_report(self, stage: Stage) -> Dict[str, Any]:
        """Detailed report for a single stage."""
        hist = self._histograms[stage]
        return {
            "stage": stage.value,
            "all_time": hist.stats(),
            "recent": hist.recent_stats(),
            "target_us": RETAIL_TARGETS_US.get(stage),
            "dma_target_us": DMA_TARGETS_US.get(stage),
        }

    def meets_phase5_target(self) -> Dict[str, Any]:
        """Check if internal p99 < 1ms (Phase 5 exit criterion)."""
        total = self._histograms[Stage.TOTAL].stats()
        p99_us = total.get("p99_us")
        return {
            "target": "internal p99 < 1ms (1000µs)",
            "current_p99_us": p99_us,
            "meets_target": p99_us is not None and p99_us < 1000.0,
            "gap_us": round(p99_us - 1000.0, 2) if p99_us else None,
        }

    def reset(self) -> None:
        """Clear all measurements."""
        for hist in self._histograms.values():
            with hist._lock:
                hist._samples.clear()
                hist._recent.clear()
                hist._count = 0
                hist._sum_ns = 0
                hist._min_ns = None
                hist._max_ns = None
        self._breaches.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_profiler: Optional[LatencyProfiler] = None


def get_profiler() -> LatencyProfiler:
    """Return or create the global profiler singleton."""
    global _profiler
    if _profiler is None:
        _profiler = LatencyProfiler()
    return _profiler
