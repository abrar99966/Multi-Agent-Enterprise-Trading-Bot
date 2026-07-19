/**
 * Dashboard — the desk overview and default landing module.
 *
 * Composition: KPI strip across the top, price surface centre, live strategy
 * cards beneath. Everything here is selection-aware: clicking a strategy or a
 * KPI publishes upward so the Copilot, context tabs and console re-scope.
 *
 * Data sources (real endpoints, documented cadences):
 *   /performance/stats   120s — hit rate, expectancy, graded counts
 *   /risk/limits          60s — realised P&L, trade budget, kill switch
 *   /performance/health   60s — agent latency + data-source health
 */
import React, { useMemo } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import { CADENCE, apiBase, jget } from '../../../../lib/ws/api';
import {
  Badge,
  Button,
  EmptyState,
  Icon,
  MetricCard,
  Panel,
  PanelBody,
  PanelHeader,
  Skeleton,
  StatusChip,
  TONE_TEXT,
  cx,
  deltaArrow,
  deltaTone,
  fmtCur,
  fmtLatency,
  fmtNum,
  fmtPct,
} from '../../ui';
import { ChartWorkspace } from '../chart';

/* ---- strategy cards ------------------------------------------------------ */

/**
 * Confidence ring — a 2px arc is denser than a bar and reads at a glance in a
 * card row. Value is 0..1; the track stays visible so an empty ring is legible.
 */
function ConfidenceRing({ value = 0, size = 34 }) {
  const pct = Math.max(0, Math.min(1, Number(value) || 0));
  const r = (size - 5) / 2;
  const c = 2 * Math.PI * r;
  const tone = pct >= 0.7 ? '#34d399' : pct >= 0.5 ? '#fbbf24' : '#7d8899';
  return (
    <span className="relative inline-grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="3" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={tone}
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={`${c * pct} ${c}`}
        />
      </svg>
      <span className="absolute font-hx-mono text-hx-10 text-hx-text-mid">{Math.round(pct * 100)}</span>
    </span>
  );
}

export function StrategyCard({ strategy, active, onSelect }) {
  const pnl = strategy.pnl ?? 0;
  const tone = deltaTone(pnl);
  return (
    <button
      type="button"
      onClick={() => onSelect && onSelect(strategy.id)}
      aria-pressed={active}
      className={cx(
        'hx-focus flex w-[212px] shrink-0 flex-col gap-2 rounded-lg border px-3 py-2.5 text-left transition-colors',
        active
          ? 'border-hx-accent-500/50 bg-hx-accent-500/[0.07]'
          : 'border-hx-border-subtle bg-hx-bg-raised hover:border-hx-border-strong hover:bg-white/[0.04]',
      )}
    >
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0">
          <span className="block truncate text-hx-12 font-medium text-hx-text-hi">{strategy.name}</span>
          <span className="mt-0.5 block text-hx-10 uppercase tracking-wider text-hx-text-dim">
            {strategy.symbol || 'portfolio'}
          </span>
        </span>
        <Badge tone={strategy.mode === 'LIVE' ? 'pos' : strategy.mode === 'SHADOW' ? 'info' : 'warn'} size="xs">
          {strategy.mode}
        </Badge>
      </span>

      <span className="flex items-center justify-between gap-2">
        <ConfidenceRing value={strategy.confidence} />
        <span className="text-right">
          <span className="block text-hx-10 text-hx-text-dim">P&amp;L today</span>
          <span className={cx('block font-hx-mono text-hx-13 tabular-nums', TONE_TEXT[tone])}>
            {deltaArrow(pnl)} {fmtCur(Math.abs(pnl), { ccy: 'INR', compact: true })}
          </span>
        </span>
      </span>

      <span className="flex items-center justify-between border-t border-hx-border-subtle pt-1.5 text-hx-10">
        <span className="text-hx-text-dim">
          lat <span className="font-hx-mono text-hx-text-lo">{fmtLatency(strategy.latencyMs)}</span>
        </span>
        <span className="text-hx-text-dim">
          hit <span className="font-hx-mono text-hx-text-lo">{fmtPct(strategy.hitRate, { asRatio: true, signed: false })}</span>
        </span>
        <span className="text-hx-text-dim">
          n <span className="font-hx-mono text-hx-text-lo">{fmtNum(strategy.trades, { dp: 0 })}</span>
        </span>
      </span>
    </button>
  );
}

/**
 * The backend has no per-strategy live registry yet, so the cards are derived
 * from what /performance/stats does expose: per-horizon grading. Each horizon
 * is presented as the arm it actually is. Labelled honestly — these are graded
 * signal cohorts, not separately deployed strategies.
 */
function strategiesFromStats(stats) {
  const byH = stats?.by_horizon || {};
  const keys = Object.keys(byH);
  if (!keys.length) return [];
  return keys.map((k) => {
    const h = byH[k] || {};
    return {
      id: k,
      name: `Horizon ${k}`,
      symbol: null,
      mode: k === '1h' ? 'LIVE' : 'PAPER',
      confidence: h.hit_rate ?? 0,
      hitRate: h.hit_rate ?? null,
      pnl: (h.avg_move_pct ?? 0) * (h.graded ?? 0) * 100,
      trades: h.graded ?? 0,
      latencyMs: null,
    };
  });
}

/* ---- module ------------------------------------------------------------- */

export function DashboardModule({ symbol, onSelectSymbol, strategyId, onSelectStrategy }) {
  const base = apiBase();

  const { data: stats, loading: statsLoading, error: statsError, refresh: refreshStats } = useLivePoll(
    jget(`${base}/api/v1/performance/stats?days=7&grade_now=false`),
    CADENCE.performance,
  );
  const { data: limits, error: limitsError } = useLivePoll(
    jget(`${base}/api/v1/risk/limits`),
    CADENCE.risk,
  );
  const { data: health } = useLivePoll(jget(`${base}/api/v1/performance/health`), CADENCE.health);

  const strategies = useMemo(() => strategiesFromStats(stats), [stats]);

  // Worst agent latency is the honest headline number: the desk is only as fast
  // as its slowest hop, and averaging hides a stalled agent.
  const agentLatency = useMemo(() => {
    const agents = health?.agents || [];
    if (!agents.length) return null;
    return Math.max(...agents.map((a) => Number(a.latency_ms) || 0));
  }, [health]);

  const pnl = limits?.today_realized_pnl_inr ?? null;
  const tradesUsed = limits?.today_trade_count ?? null;
  const tradesMax = limits?.daily_max_trades ?? null;
  const lossCap = limits?.daily_max_loss_inr ?? null;
  const killed = limits?.kill_switch === true;

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-2">
      {/* ---- KPI strip ---- */}
      <div className="grid shrink-0 grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        <MetricCard
          label="Realised P&L"
          value={fmtCur(pnl, { ccy: 'INR', signed: true })}
          raw={pnl}
          tone={deltaTone(pnl)}
          period="today"
          loading={!limits && !limitsError}
          flash
        />
        <MetricCard
          label="Loss budget"
          value={lossCap != null && pnl != null ? fmtCur(Math.max(0, lossCap + Math.min(0, pnl)), { ccy: 'INR', compact: true }) : '--'}
          period={lossCap != null ? `cap ${fmtCur(lossCap, { ccy: 'INR', compact: true })}` : ''}
          loading={!limits && !limitsError}
        />
        <MetricCard
          label="Trades"
          value={tradesUsed != null ? `${fmtNum(tradesUsed, { dp: 0 })}/${fmtNum(tradesMax, { dp: 0 })}` : '--'}
          period="today"
          loading={!limits && !limitsError}
        />
        <MetricCard
          label="Hit rate 1h"
          value={fmtPct(stats?.hit_rate_1h, { asRatio: true, signed: false })}
          raw={stats?.hit_rate_1h}
          tone={stats?.hit_rate_1h != null ? deltaTone(stats.hit_rate_1h - 0.5) : undefined}
          period={`${fmtNum(stats?.graded_count, { dp: 0 })} graded`}
          loading={statsLoading && !stats}
        />
        <MetricCard
          label="Hit rate 24h"
          value={fmtPct(stats?.hit_rate_24h, { asRatio: true, signed: false })}
          raw={stats?.hit_rate_24h}
          tone={stats?.hit_rate_24h != null ? deltaTone(stats.hit_rate_24h - 0.5) : undefined}
          period={`${stats?.window_days ?? 7}d window`}
          loading={statsLoading && !stats}
        />
        <MetricCard
          label="Expectancy 1h"
          value={fmtPct(stats?.expectancy_1h, { asRatio: true })}
          raw={stats?.expectancy_1h}
          tone={deltaTone(stats?.expectancy_1h)}
          period="per signal"
          loading={statsLoading && !stats}
        />
        <MetricCard
          label="Agent latency"
          value={fmtLatency(agentLatency)}
          raw={agentLatency}
          period="slowest hop"
          loading={!health}
        />
      </div>

      {/* ---- kill-switch banner: the one state that must never be subtle ---- */}
      {killed && (
        <div
          role="alert"
          className="flex shrink-0 items-center gap-2 rounded-lg border border-hx-neg-500/40 bg-hx-neg-500/10 px-3 py-2"
        >
          <Icon name="kill" size={14} className="text-hx-neg-400" />
          <span className="text-hx-12 font-medium text-hx-neg-300">
            Kill switch engaged — no new orders will be released.
          </span>
        </div>
      )}

      {/* ---- price surface ---- */}
      <div className="min-h-0 flex-1">
        <ChartWorkspace symbol={symbol} height={300} />
      </div>

      {/* ---- strategy arms ---- */}
      <Panel className="shrink-0">
        <PanelHeader
          title="Graded cohorts"
          subtitle="per-horizon signal performance"
          actions={
            <div className="flex items-center gap-2">
              <StatusChip status={statsError ? 'offline' : 'connected'} label={statsError ? 'stats offline' : 'live'} />
              <Button size="xs" variant="subtle" onClick={refreshStats} icon="refresh">
                Refresh
              </Button>
            </div>
          }
        />
        <PanelBody pad={false} scroll={false}>
          {statsLoading && !stats ? (
            <div className="flex gap-2 p-2">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-[104px] w-[212px] rounded-lg" />
              ))}
            </div>
          ) : strategies.length ? (
            <div className="hx-scroll flex gap-2 overflow-x-auto p-2">
              {strategies.map((s) => (
                <StrategyCard
                  key={s.id}
                  strategy={s}
                  active={s.id === strategyId}
                  onSelect={onSelectStrategy}
                />
              ))}
            </div>
          ) : (
            <EmptyState
              icon="strategies"
              title="No graded signals yet"
              hint="Cohorts appear once recommendations have been graded at their horizon."
            />
          )}
        </PanelBody>
      </Panel>
    </div>
  );
}

export default DashboardModule;
