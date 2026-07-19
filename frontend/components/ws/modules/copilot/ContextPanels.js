/**
 * ContextPanels — the tabbed dock beneath the Copilot.
 *
 * Every tab re-scopes to the current selection. Where the backend genuinely has
 * no endpoint for a tab (Features, Sentiment), the panel says so plainly rather
 * than inventing plausible numbers — a trading desk that displays fabricated
 * telemetry is worse than one that displays none.
 */
import React, { useMemo, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import { CADENCE, apiBase, jget } from '../../../../lib/ws/api';
import {
  Badge,
  EmptyState,
  Panel,
  PanelBody,
  RiskIndicator,
  Skeleton,
  Tabs,
  TONE_TEXT,
  cx,
  deltaTone,
  fmtCur,
  fmtNum,
  fmtPct,
  fmtTime,
} from '../../ui';

const TABS = [
  { id: 'insights', label: 'Insights' },
  { id: 'risk', label: 'Risk' },
  { id: 'features', label: 'Features' },
  { id: 'news', label: 'News' },
  { id: 'sentiment', label: 'Sentiment' },
  { id: 'exposure', label: 'Exposure' },
];

function Row({ label, value, tone }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-hx-11 text-hx-text-lo">{label}</span>
      <span className={cx('hx-mono text-hx-11 tabular-nums', tone ? TONE_TEXT[tone] : 'text-hx-text-hi')}>
        {value}
      </span>
    </div>
  );
}

/* ---- tabs --------------------------------------------------------------- */

function InsightsTab({ symbol }) {
  const base = apiBase();
  const { data: macro, loading } = useLivePoll(jget(`${base}/api/v1/slowpath/macro`), CADENCE.performance);
  const { data: stats } = useLivePoll(
    jget(`${base}/api/v1/performance/stats?days=7&grade_now=false`),
    CADENCE.performance,
  );

  if (loading && !macro) return <Skeleton className="h-24 w-full" />;

  const regime = macro?.macro_regime;
  const curve = macro?.yield_curve;

  return (
    <div className="space-y-3">
      <section>
        <h4 className="mb-1 text-hx-10 uppercase tracking-wider text-hx-text-dim">Macro regime</h4>
        <div className="flex items-center gap-2">
          <Badge tone={regime === 'crisis' ? 'neg' : regime === 'stress' ? 'warn' : 'pos'}>
            {regime ? regime.toUpperCase() : 'CALM'}
          </Badge>
          {macro?.would_tighten_gross_to_pct != null && (
            <span className="text-hx-10 text-hx-text-dim">
              would tighten gross to {macro.would_tighten_gross_to_pct}%
            </span>
          )}
        </div>
        <div className="mt-1.5">
          <Row label="10Y-2Y spread" value={curve ? fmtNum(curve.spread_10y_2y) : '--'} tone={deltaTone(curve?.spread_10y_2y)} />
          <Row label="VIX" value={fmtNum(macro?.vix)} />
          <Row label="Curve inverted" value={curve?.inverted ? 'yes' : 'no'} tone={curve?.inverted ? 'neg' : 'pos'} />
        </div>
        <p className="mt-1 text-hx-10 text-hx-text-dim">
          Tighten-only advisory. Treasury needs no key; VIX requires FRED.
        </p>
      </section>

      <section>
        <h4 className="mb-1 text-hx-10 uppercase tracking-wider text-hx-text-dim">Signal quality</h4>
        <Row label="Hit rate 1h" value={fmtPct(stats?.hit_rate_1h, { asRatio: true, signed: false })} />
        <Row label="Hit rate 24h" value={fmtPct(stats?.hit_rate_24h, { asRatio: true, signed: false })} />
        {/* expectancy_1h is derived from actual_move_pct_1h — already a percentage,
            so it must not be scaled again like the 0..1 hit rates above. */}
        <Row label="Expectancy 1h" value={fmtPct(stats?.expectancy_1h, { asRatio: false })} tone={deltaTone(stats?.expectancy_1h)} />
        <Row label="Graded" value={fmtNum(stats?.graded_count, { dp: 0 })} />
        {symbol && stats?.per_symbol?.[symbol] && (
          <>
            <h4 className="mb-1 mt-2 text-hx-10 uppercase tracking-wider text-hx-text-dim">{symbol}</h4>
            <Row label="Signals" value={fmtNum(stats.per_symbol[symbol].total, { dp: 0 })} />
            <Row label="Hit rate" value={fmtPct(stats.per_symbol[symbol].hit_rate_1h, { asRatio: true, signed: false })} />
            <Row label="Avg move" value={fmtPct(stats.per_symbol[symbol].avg_move_pct, { asRatio: false })} />
          </>
        )}
      </section>
    </div>
  );
}

function RiskTab() {
  const { data, loading, error } = useLivePoll(jget(`${apiBase()}/api/v1/risk/limits`), CADENCE.risk);
  if (loading && !data) return <Skeleton className="h-24 w-full" />;
  if (error) return <EmptyState variant="error" title="Risk limits unavailable" hint={String(error.message || error)} />;

  const tradesUsed = data?.today_trade_count ?? 0;
  const tradesMax = data?.daily_max_trades ?? 0;
  const pnl = data?.today_realized_pnl_inr ?? 0;
  const lossCap = data?.daily_max_loss_inr ?? 0;
  // Loss consumed only counts negative P&L — profit does not buy extra budget.
  const lossUsed = Math.max(0, -pnl);

  return (
    <div className="space-y-3">
      {data?.kill_switch && (
        <div role="alert" className="rounded border border-hx-neg-500/40 bg-hx-neg-500/10 px-2 py-1.5 text-hx-11 text-hx-neg-300">
          Kill switch engaged — order release halted.
        </div>
      )}
      <RiskIndicator label="Daily loss budget" value={lossUsed} max={lossCap || 1} valueText={fmtCur(lossUsed, { ccy: 'INR' })} />
      <RiskIndicator label="Trade count" value={tradesUsed} max={tradesMax || 1} valueText={`${tradesUsed}/${tradesMax}`} />
      <div className="border-t border-hx-border-subtle pt-2">
        <Row label="Per-trade cap" value={fmtCur(data?.per_trade_max_inr, { ccy: 'INR' })} />
        <Row label="Realised P&L" value={fmtCur(pnl, { ccy: 'INR', signed: true })} tone={deltaTone(pnl)} />
        <Row label="Trades left" value={fmtNum(data?.today_remaining_trades, { dp: 0 })} />
        <Row label="Updated" value={data?.updated_at ? fmtTime(data.updated_at) : '--'} />
      </div>
    </div>
  );
}

function FeaturesTab({ symbol }) {
  // The 22-feature fabric vector is computed inside the engine and is not
  // exposed over REST. Saying so beats rendering a convincing fake.
  return (
    <EmptyState
      icon="analytics"
      title="Feature vector not exposed"
      hint={
        symbol
          ? `The fast-path fabric computes 22 features for ${symbol} in-process; no REST endpoint publishes them yet. They are visible in the journal projections under the AI dashboard.`
          : 'Select an instrument to scope this panel.'
      }
    />
  );
}

function NewsTab({ symbol }) {
  const { data, loading, error } = useLivePoll(
    symbol ? jget(`${apiBase()}/api/v1/market-data/news/${encodeURIComponent(symbol)}`) : () => Promise.resolve(null),
    CADENCE.performance,
    [symbol],
  );

  if (!symbol) return <EmptyState icon="markets" title="No instrument selected" hint="Pick a symbol to load its headlines." />;
  if (loading && !data) return <Skeleton className="h-24 w-full" />;
  if (error) return <EmptyState variant="error" title="News unavailable" hint={String(error.message || error)} />;

  const items = data?.news || [];
  if (!items.length) return <EmptyState icon="logs" title="No headlines" hint={`Nothing returned for ${symbol}.`} />;

  return (
    <ul className="space-y-2">
      {items.slice(0, 12).map((n, i) => (
        <li key={i} className="border-b border-hx-border-subtle pb-2 last:border-0">
          <p className="text-hx-11 leading-snug text-hx-text-mid">{n.title}</p>
          <p className="mt-0.5 flex items-center gap-2 text-hx-10 text-hx-text-dim">
            <span>{n.source || 'unknown'}</span>
            {n.published_at && <span>{fmtTime(n.published_at, { mode: 'rel' })}</span>}
          </p>
        </li>
      ))}
    </ul>
  );
}

function SentimentTab({ symbol }) {
  return (
    <EmptyState
      icon="info"
      title="Sentiment scoring unavailable"
      hint={
        'The free Finnhub company-news tier returns no per-article sentiment score, so the platform reports neutral rather than inferring one. A scored feed (paid /news-sentiment) would populate this panel.'
      }
    />
  );
}

function ExposureTab() {
  const { data, loading } = useLivePoll(jget(`${apiBase()}/api/v1/trades/history`), CADENCE.history);
  const positions = useMemo(() => {
    const trades = data?.trades || [];
    const bySym = new Map();
    for (const t of trades) {
      if (!t.symbol) continue;
      const side = String(t.side || '').toUpperCase() === 'SELL' ? -1 : 1;
      const qty = (Number(t.quantity) || 0) * side;
      const px = Number(t.executed_price ?? t.placed_price) || 0;
      const cur = bySym.get(t.symbol) || { symbol: t.symbol, qty: 0, notional: 0 };
      cur.qty += qty;
      cur.notional += Math.abs(qty) * px;
      bySym.set(t.symbol, cur);
    }
    return Array.from(bySym.values())
      .filter((p) => p.notional > 0)
      .sort((a, b) => b.notional - a.notional);
  }, [data]);

  if (loading && !data) return <Skeleton className="h-24 w-full" />;
  if (!positions.length) {
    return <EmptyState icon="portfolio" title="No exposure" hint="No executed trades in history yet." />;
  }
  const total = positions.reduce((s, p) => s + p.notional, 0) || 1;

  return (
    <div className="space-y-1.5">
      <p className="text-hx-10 text-hx-text-dim">Gross notional traded, derived from fill history.</p>
      {positions.slice(0, 10).map((p) => (
        <div key={p.symbol}>
          <div className="flex items-baseline justify-between">
            <span className="hx-mono text-hx-11 text-hx-text-hi">{p.symbol}</span>
            <span className="hx-mono text-hx-11 text-hx-text-mid">
              {fmtCur(p.notional, { ccy: 'INR', compact: true })}
            </span>
          </div>
          <div className="mt-0.5 h-1 overflow-hidden rounded bg-white/5">
            <div className="h-full bg-hx-accent-500/70" style={{ width: `${(p.notional / total) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/* ---- shell -------------------------------------------------------------- */

export function ContextPanels({ symbol, strategyId }) {
  const [tab, setTab] = useState('insights');

  const body = {
    insights: <InsightsTab symbol={symbol} />,
    risk: <RiskTab />,
    features: <FeaturesTab symbol={symbol} />,
    news: <NewsTab symbol={symbol} />,
    sentiment: <SentimentTab symbol={symbol} />,
    exposure: <ExposureTab />,
  }[tab];

  return (
    <Panel flush className="flex h-full min-h-0 flex-col rounded-none border-0 border-t border-hx-border-subtle">
      {/* Six tabs don't fit a 372px dock at every width, and a clipped final tab
          reads as a rendering bug. Scroll the strip instead of truncating it. */}
      <div className="hx-scroll shrink-0 overflow-x-auto">
        <Tabs tabs={TABS} value={tab} onChange={setTab} idPrefix="ctx" />
      </div>
      <PanelBody className="hx-scroll min-h-0 flex-1 overflow-y-auto p-3">{body}</PanelBody>
    </Panel>
  );
}

export default ContextPanels;
