/**
 * AnalyticsModule — transaction-cost analysis over GET /api/v1/dash/tca.
 *
 * Implementation shortfall is decomposed into delay / execution / fees, which is
 * the only decomposition the store actually persists; "opportunity cost" is NOT
 * a column in backend/app/tca/store.py, so it is not shown rather than faked.
 * The three components sum to total_is_bps.
 *
 * Every bps figure the endpoint aggregates is NOTIONAL-WEIGHTED, so client-side
 * roll-ups here weight by notional too — a plain mean would disagree with the
 * server's own headline number.
 */
import React, { useCallback, useMemo, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import {
  Panel, PanelHeader, PanelToolbar, PanelBody,
  DataGrid, Drawer, Tabs, TabPanel, Button, ButtonGroup, Badge, MetricCard, MetricRow,
  EmptyState, Skeleton, Icon, InfoTip,
  TONE_SOLID, TONE_TEXT, TONE_HEX,
  fmtNum, fmtCur, fmtQty, fmtTime, deltaArrow, deltaTone, cx,
} from '../../ui';
import { apiBase, jget, jpost, nsToMs, OPS_CADENCE, useControllable } from './opsApi';

/** Cost components in fixed display order. Tone is semantic, not decorative. */
const IS_PARTS = [
  { key: 'delay_bps', label: 'Delay', tone: 'warn', hint: 'Drift between the decision price and arrival at the venue.' },
  { key: 'execution_bps', label: 'Execution', tone: 'accent', hint: 'Slippage from arrival price to the actual fill price.' },
  { key: 'fees_bps', label: 'Fees', tone: 'info', hint: 'Commissions and charges, expressed in basis points of notional.' },
];

/** bps → readable string. Costs are small numbers where 2dp matters. */
const bps = (v, dp = 2) => (Number.isFinite(Number(v)) ? `${Number(v).toFixed(dp)} bps` : '--');

/* ---- shortfall decomposition -------------------------------------------- */

/**
 * Diverging stacked bar. Components can be negative (price moved in our favour),
 * so a conventional left-to-right stack would be wrong — positives stack right
 * of the zero axis, negatives left, and the axis stays fixed at centre.
 */
function ShortfallBar({ parts, total }) {
  const posSum = parts.reduce((s, p) => s + Math.max(0, p.value || 0), 0);
  const negSum = parts.reduce((s, p) => s + Math.abs(Math.min(0, p.value || 0)), 0);
  const scale = Math.max(posSum, negSum, Math.abs(Number(total) || 0)) || 1;

  let posOff = 0;
  let negOff = 0;
  const segs = parts.map((p) => {
    const v = Number(p.value) || 0;
    const w = (Math.abs(v) / scale) * 50; // each half of the track is 50%
    const seg = v >= 0
      ? { ...p, left: 50 + posOff, width: w, v }
      : { ...p, left: 50 - negOff - w, width: w, v };
    if (v >= 0) posOff += w; else negOff += w;
    return seg;
  });

  const totalPct = ((Number(total) || 0) / scale) * 50;

  return (
    <div className="flex flex-col gap-2">
      <div className="relative h-7 w-full rounded bg-white/[0.03] border border-hx-border-subtle overflow-hidden">
        {segs.map((s) => (
          s.width > 0.15 ? (
            <div
              key={s.key}
              className={cx('absolute inset-y-0', TONE_SOLID[s.tone])}
              style={{ left: `${s.left}%`, width: `${s.width}%`, opacity: 0.85 }}
              title={`${s.label}: ${bps(s.v)}`}
            />
          ) : null
        ))}
        {/* zero axis — the reference every segment is measured from */}
        <div className="absolute inset-y-0 left-1/2 w-px bg-white/25" aria-hidden="true" />
        {/* total marker, drawn over the stack as a verification tick */}
        <div
          className="absolute inset-y-0 w-[2px] bg-hx-text-hi"
          style={{ left: `calc(${50 + totalPct}% - 1px)` }}
          aria-hidden="true"
        />
      </div>

      <div className="flex items-center justify-between text-hx-10 text-hx-text-dim">
        <span>← favourable</span>
        <span>0</span>
        <span>adverse →</span>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {parts.map((p) => (
          <span key={p.key} className="inline-flex items-center gap-1.5">
            <span className={cx('h-2 w-2 rounded-[1px] shrink-0', TONE_SOLID[p.tone])} aria-hidden="true" />
            <span className="text-hx-11 text-hx-text-mid">{p.label}</span>
            <span className="text-hx-11 hx-mono hx-tnum text-hx-text-hi">{bps(p.value)}</span>
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-[2px] bg-hx-text-hi shrink-0" aria-hidden="true" />
          <span className="text-hx-11 text-hx-text-mid">Total</span>
          <span className="text-hx-11 hx-mono hx-tnum text-hx-text-hi">{bps(total)}</span>
        </span>
      </div>
    </div>
  );
}

/* ---- markout curve ------------------------------------------------------- */

/**
 * Post-fill markout by horizon. Uses a fixed internal coordinate space with a
 * stretched viewBox so it fills any panel width without measuring the DOM.
 */
function MarkoutCurve({ points, height = 160 }) {
  const W = 600;
  const H = height;
  const padL = 44;
  const padR = 12;
  const padT = 12;
  const padB = 26;

  if (!points.length) {
    return <EmptyState title="No markout data" hint="Fills in this store carry no markouts_bps horizons." />;
  }

  const vals = points.map((p) => p.value);
  let min = Math.min(...vals, 0);
  let max = Math.max(...vals, 0);
  if (max - min < 1e-9) { min -= 1; max += 1; }
  const padY = (max - min) * 0.12;
  min -= padY;
  max += padY;

  const x = (i) => padL + (points.length === 1 ? (W - padL - padR) / 2 : (i / (points.length - 1)) * (W - padL - padR));
  const y = (v) => padT + (1 - (v - min) / (max - min)) * (H - padT - padB);

  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(p.value).toFixed(1)}`).join(' ');
  const zeroY = y(0);
  const ticks = [max, (max + min) / 2, min];

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="w-full block"
      style={{ height }}
      role="img"
      aria-label={`Markout curve across ${points.length} horizons`}
    >
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={padL} y1={y(t)} x2={W - padR} y2={y(t)} stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
          <text x={padL - 6} y={y(t) + 3} textAnchor="end" fontSize="9" fill="#565f70" className="hx-mono">
            {t.toFixed(1)}
          </text>
        </g>
      ))}

      {/* Zero line is the decision boundary: above it the fill aged badly. */}
      <line x1={padL} y1={zeroY} x2={W - padR} y2={zeroY} stroke="rgba(255,255,255,0.28)" strokeWidth="1" strokeDasharray="3 3" />

      <path d={line} fill="none" stroke={TONE_HEX.accent} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />

      {points.map((p, i) => (
        <g key={p.key}>
          <circle cx={x(i)} cy={y(p.value)} r="2.75" fill={p.value >= 0 ? TONE_HEX.neg : TONE_HEX.pos} />
          <text x={x(i)} y={H - 8} textAnchor="middle" fontSize="9" fill="#7d8899" className="hx-mono">
            {p.key}
          </text>
        </g>
      ))}
    </svg>
  );
}

/* ---- fill quality histogram --------------------------------------------- */

function FillQuality({ rows }) {
  const buckets = useMemo(() => {
    const vals = rows.map((r) => Number(r.total_is_bps)).filter(Number.isFinite);
    if (!vals.length) return null;
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const n = 12;
    const span = max - min || 1;
    const out = Array.from({ length: n }, (_, i) => ({
      lo: min + (span * i) / n,
      hi: min + (span * (i + 1)) / n,
      count: 0,
    }));
    vals.forEach((v) => {
      const i = Math.min(n - 1, Math.floor(((v - min) / span) * n));
      out[i].count += 1;
    });
    return { out, min, max, total: vals.length };
  }, [rows]);

  if (!buckets) return <EmptyState title="No fills to profile" />;

  const peak = Math.max(...buckets.out.map((b) => b.count), 1);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-end gap-[3px] h-32">
        {buckets.out.map((b, i) => {
          // Adverse cost (>0) is the bad tail; tint it so the skew reads instantly.
          const adverse = b.lo >= 0;
          return (
            <div key={i} className="flex-1 flex flex-col justify-end items-center gap-1 min-w-0">
              <span className="text-hx-10 text-hx-text-dim hx-mono">{b.count || ''}</span>
              <div
                className={cx('w-full rounded-t-[2px]', adverse ? TONE_SOLID.neg : TONE_SOLID.pos)}
                style={{ height: `${(b.count / peak) * 100}%`, opacity: b.count ? 0.75 : 0.12, minHeight: b.count ? 2 : 2 }}
                title={`${b.lo.toFixed(1)} to ${b.hi.toFixed(1)} bps — ${b.count} fills`}
              />
            </div>
          );
        })}
      </div>
      <div className="flex items-center justify-between text-hx-10 text-hx-text-dim hx-mono">
        <span>{buckets.min.toFixed(1)} bps</span>
        <span className="text-hx-text-lo">total implementation shortfall · {buckets.total} fills</span>
        <span>{buckets.max.toFixed(1)} bps</span>
      </div>
    </div>
  );
}

/* ---- fill detail drawer -------------------------------------------------- */

/**
 * Compares a realised fill against the pre-trade impact model. The estimate is
 * fetched on demand (one POST per inspected fill) rather than for every row —
 * the endpoint is real computation and the grid can hold 10k rows.
 */
function FillDetail({ row, onSelectSymbol }) {
  const [est, setEst] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const side = String(row.side || '').toUpperCase();
  const canModel = (side === 'BUY' || side === 'SELL')
    && Number(row.qty) > 0
    && Number(row.decision_price) > 0;

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await jpost('/api/v1/execution/impact-estimate', {
        symbol: row.symbol,
        side,
        qty: Number(row.qty),
        reference_price: Number(row.decision_price),
        region: 'IN',
      });
      setEst(res?.estimate || null);
    } catch (e) {
      setError(e.message || 'Estimate failed');
    } finally {
      setBusy(false);
    }
  };

  const modeled = est ? Number(est.total_expected_cost_bps) : null;
  const actual = Number(row.total_is_bps);
  // Positive slippage = we paid more than the model predicted.
  const slip = modeled !== null && Number.isFinite(actual) ? actual - modeled : null;

  const Field = ({ label, value, mono = true }) => (
    <div className="min-w-0">
      <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo">{label}</div>
      <div className={cx('text-hx-12 text-hx-text-hi truncate', mono && 'hx-mono hx-tnum')}>{value}</div>
    </div>
  );

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => onSelectSymbol && onSelectSymbol(row.symbol)}
          className="hx-focus text-hx-14 font-semibold text-hx-accent-300 hover:text-hx-accent-400 rounded"
        >
          {row.symbol}
        </button>
        <Badge tone={side === 'BUY' ? 'pos' : 'neg'} size="xs">{side}</Badge>
        <Badge tone="neutral" size="xs">{row.strategy_id || 'unknown strategy'}</Badge>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Field label="Qty" value={fmtQty(row.qty)} />
        <Field label="Notional" value={fmtCur(row.notional, { ccy: 'INR', compact: true })} />
        <Field label="Filled" value={fmtTime(nsToMs(row.ts_fill), { mode: 'datetime' })} />
        <Field label="Decision" value={fmtNum(row.decision_price)} />
        <Field label="Arrival" value={fmtNum(row.arrival_price)} />
        <Field label="Fill" value={fmtNum(row.fill_price)} />
      </div>

      <div className="pt-2 border-t border-hx-border-subtle">
        <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo mb-2">Shortfall decomposition</div>
        <ShortfallBar
          parts={IS_PARTS.map((p) => ({ ...p, value: Number(row[p.key]) }))}
          total={row.total_is_bps}
        />
      </div>

      <div className="pt-2 border-t border-hx-border-subtle">
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">
            Versus impact model
          </span>
          <Button size="xs" icon="spark" loading={busy} disabled={!canModel} onClick={run}>
            {est ? 'Recompute' : 'Estimate'}
          </Button>
        </div>

        {!canModel && (
          <p className="text-hx-11 text-hx-text-dim">
            Needs a positive qty, decision price and a BUY/SELL side to model.
          </p>
        )}

        {error && (
          <div role="alert" className="flex items-start gap-2 p-2 rounded border border-hx-neg-500/30 bg-hx-neg-500/[0.12]">
            <Icon name="alert" size={12} className="text-hx-neg-400 shrink-0 mt-px" />
            <span className="text-hx-10 text-hx-neg-300">{error}</span>
          </div>
        )}

        {est && (
          <div className="flex flex-col gap-2">
            <div className="grid grid-cols-3 gap-2">
              <Field label="Modeled" value={bps(modeled)} />
              <Field label="Actual" value={bps(actual)} />
              <div className="min-w-0">
                <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Slippage</div>
                <div className={cx('text-hx-12 hx-mono hx-tnum truncate', TONE_TEXT[deltaTone(slip)])}>
                  <span aria-hidden="true">{deltaArrow(slip)}</span> {bps(slip)}
                </div>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <Field label="Spread cost" value={bps(est.spread_cost_bps)} />
              <Field label="Temp impact" value={bps(est.temporary_impact_bps)} />
              <Field label="Perm impact" value={bps(est.permanent_impact_bps)} />
              <Field label="Rec. algo" value={est.recommended_algo} mono={false} />
              <Field label="Urgency" value={fmtNum(est.recommended_urgency, { dp: 2 })} />
              <Field label="Duration" value={`${est.recommended_duration_min}m`} />
            </div>
            <p className="text-hx-10 text-hx-text-dim leading-relaxed">
              Slippage above zero means the fill cost more than the pre-trade model predicted.
              The model is re-run now against current defaults, not the parameters that were live at fill time.
            </p>
          </div>
        )}
      </div>

      {row.markouts_bps && Object.keys(row.markouts_bps).length > 0 && (
        <div className="pt-2 border-t border-hx-border-subtle">
          <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo mb-2">Markouts</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {Object.entries(row.markouts_bps).map(([k, v]) => (
              <span key={k} className="inline-flex items-center gap-1.5">
                <span className="text-hx-11 text-hx-text-lo hx-mono">{k}</span>
                <span className={cx('text-hx-11 hx-mono hx-tnum', TONE_TEXT[deltaTone(-Number(v))])}>
                  <span aria-hidden="true">{deltaArrow(v)}</span> {bps(v)}
                </span>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="text-hx-10 text-hx-text-dim hx-mono break-all pt-2 border-t border-hx-border-subtle">
        fill {row.fill_id} · order {row.order_id} · intent {row.intent_id}
      </div>
    </div>
  );
}

/* ---- module -------------------------------------------------------------- */

const DB_OPTIONS = [
  { value: '', label: 'Backtest' },
  { value: 'real', label: 'Real' },
];

export function AnalyticsModule({
  db,                 // store: selected TCA store name ('' = default bt.db)
  onDbChange,
  onSelectSymbol,     // store: publish symbol selection
  className = '',
}) {
  const [dbName, setDbName] = useControllable(db, onDbChange, '');
  const [limit, setLimit] = useState(500);
  const [tab, setTab] = useState('shortfall');
  const [detail, setDetail] = useState(null);

  const url = `${apiBase()}/api/v1/dash/tca?limit=${limit}${dbName ? `&db=${encodeURIComponent(dbName)}` : ''}`;
  const { data, error, loading, refresh } = useLivePoll(jget(url), OPS_CADENCE.tca, [url]);

  const rows = data?.rows || [];
  const agg = data?.aggregates || {};
  const nFills = Number(agg.n_fills) || 0;

  // Two degraded variants collapse to "no data" but must be worded differently:
  // a missing .db file omits by_strategy entirely; an empty one returns {}.
  const dbMissing = Boolean(data) && data.by_strategy === undefined;
  const hasMetrics = nFills > 0 && agg.total_is_bps !== undefined;

  /** Notional-weighted markout per horizon, matching the server's weighting. */
  const markouts = useMemo(() => {
    const acc = new Map();
    rows.forEach((r) => {
      const m = r.markouts_bps;
      if (!m || typeof m !== 'object') return;
      const w = Math.abs(Number(r.notional)) || 0;
      Object.entries(m).forEach(([k, v]) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return;
        const cur = acc.get(k) || { sum: 0, wsum: 0 };
        cur.sum += n * w;
        cur.wsum += w;
        acc.set(k, cur);
      });
    });
    return Array.from(acc.entries())
      .map(([key, { sum, wsum }]) => ({ key, value: wsum ? sum / wsum : 0 }))
      // Horizons are stringified numbers on this store; sort numerically where
      // possible so the curve reads left-to-right in time, not lexically.
      .sort((a, b) => {
        const na = parseFloat(a.key);
        const nb = parseFloat(b.key);
        if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
        return String(a.key).localeCompare(String(b.key));
      });
  }, [rows]);

  const strategies = useMemo(() => {
    const bs = data?.by_strategy || {};
    return Object.entries(bs).map(([id, v]) => {
      const notional = Number(v.notional) || 0;
      return {
        id,
        n: Number(v.n) || 0,
        notional,
        is_cost: Number(v.is_cost) || 0,
        // by_strategy carries cost in currency only; bps is derived so the
        // column is comparable across strategies of different size.
        is_bps: notional ? (Number(v.is_cost) / notional) * 10000 : null,
      };
    }).sort((a, b) => Math.abs(b.is_cost) - Math.abs(a.is_cost));
  }, [data]);

  const openDetail = useCallback((row) => {
    setDetail(row);
    if (onSelectSymbol && row?.symbol) onSelectSymbol(row.symbol);
  }, [onSelectSymbol]);

  const fillCols = useMemo(() => [
    { key: 'ts_fill', header: 'Time', width: 88, mono: true, render: (r) => fmtTime(nsToMs(r.ts_fill)) },
    { key: 'symbol', header: 'Symbol', width: 96, mono: true },
    {
      key: 'side',
      header: 'Side',
      width: 56,
      render: (r) => (
        <Badge tone={String(r.side).toUpperCase() === 'BUY' ? 'pos' : 'neg'} size="xs">
          {r.side}
        </Badge>
      ),
    },
    { key: 'qty', header: 'Qty', width: 72, numeric: true, render: (r) => fmtQty(r.qty) },
    { key: 'fill_price', header: 'Fill', width: 84, numeric: true, render: (r) => fmtNum(r.fill_price) },
    { key: 'notional', header: 'Notional', width: 90, numeric: true, render: (r) => fmtCur(r.notional, { ccy: 'INR', compact: true }) },
    { key: 'delay_bps', header: 'Delay', width: 72, numeric: true, render: (r) => fmtNum(r.delay_bps) },
    { key: 'execution_bps', header: 'Exec', width: 72, numeric: true, render: (r) => fmtNum(r.execution_bps) },
    { key: 'fees_bps', header: 'Fees', width: 66, numeric: true, render: (r) => fmtNum(r.fees_bps) },
    {
      key: 'total_is_bps',
      header: 'Total IS',
      width: 86,
      numeric: true,
      render: (r) => (
        // Cost is adverse when positive, so the tone is inverted versus P&L.
        <span className={TONE_TEXT[deltaTone(-Number(r.total_is_bps))]}>
          <span aria-hidden="true">{deltaArrow(r.total_is_bps)}</span> {fmtNum(r.total_is_bps)}
        </span>
      ),
    },
    { key: 'strategy_id', header: 'Strategy', width: 130 },
  ], []);

  const stratCols = useMemo(() => [
    { key: 'id', header: 'Strategy', width: 200, mono: true },
    { key: 'n', header: 'Fills', width: 70, numeric: true, render: (r) => fmtQty(r.n) },
    { key: 'notional', header: 'Notional', width: 110, numeric: true, render: (r) => fmtCur(r.notional, { ccy: 'INR', compact: true }) },
    {
      key: 'is_cost',
      header: 'IS cost',
      width: 110,
      numeric: true,
      render: (r) => (
        <span className={TONE_TEXT[deltaTone(-r.is_cost)]}>
          <span aria-hidden="true">{deltaArrow(r.is_cost)}</span> {fmtCur(Math.abs(r.is_cost), { ccy: 'INR' })}
        </span>
      ),
    },
    {
      key: 'is_bps',
      header: 'IS bps',
      width: 90,
      numeric: true,
      render: (r) => (
        <span className={TONE_TEXT[deltaTone(-r.is_bps)]}>
          {r.is_bps === null ? '--' : fmtNum(r.is_bps)}
        </span>
      ),
    },
  ], []);

  const tabs = [
    { id: 'shortfall', label: 'Shortfall', icon: 'analytics' },
    { id: 'markouts', label: 'Markouts', icon: 'markets' },
    { id: 'fills', label: 'Fills', icon: 'orders', count: rows.length || undefined },
    { id: 'strategies', label: 'Strategies', icon: 'strategies', count: strategies.length || undefined },
  ];

  return (
    <Panel className={cx('h-full', className)} loading={loading}>
      <PanelHeader
        title="Execution analytics"
        icon="analytics"
        subtitle={data?.db ? `${nFills} fills` : undefined}
        actions={
          <Button size="xs" variant="subtle" icon="refresh" iconOnly aria-label="Reload TCA" onClick={refresh} />
        }
      />

      <PanelToolbar>
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Store</span>
          <ButtonGroup size="xs" options={DB_OPTIONS} value={dbName} onChange={setDbName} />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Rows</span>
          <ButtonGroup
            size="xs"
            options={[{ value: 200, label: '200' }, { value: 500, label: '500' }, { value: 2000, label: '2k' }]}
            value={limit}
            onChange={setLimit}
          />
        </div>
      </PanelToolbar>

      <PanelBody pad={false} scroll={false} className="flex flex-col">
        {loading && !data && (
          <div className="p-3 space-y-2">
            <div className="grid grid-cols-6 gap-2">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} h={58} rounded="rounded-lg" />)}
            </div>
            <Skeleton h={160} rounded="rounded-lg" />
          </div>
        )}

        {!loading && error && (
          <EmptyState
            variant="error"
            size="lg"
            title="Could not load TCA"
            hint={error.message}
            action={{ label: 'Retry', onClick: refresh }}
          />
        )}

        {!error && data && !hasMetrics && (
          <EmptyState
            size="lg"
            title={dbMissing ? 'TCA store not found' : 'No fills recorded'}
            hint={
              dbMissing
                ? `No database for "${dbName || 'bt'}". Run a paper session or backtest to populate it.`
                : 'The store exists but holds no fills yet. Execution costs appear once orders fill.'
            }
            icon="analytics"
            action={{ label: 'Retry', onClick: refresh }}
          />
        )}

        {!error && data && hasMetrics && (
          <>
            <div className="p-3 shrink-0">
              <MetricRow cols={6}>
                <MetricCard label="Fills" value={fmtQty(nFills)} icon="orders" />
                <MetricCard
                  label="Total IS"
                  value={fmtNum(agg.total_is_bps)}
                  period="bps"
                  tone={Number(agg.total_is_bps) > 0 ? 'neg' : 'pos'}
                />
                <MetricCard label="IS cost" value={fmtCur(agg.total_is_cost, { ccy: 'INR', compact: true })} />
                <MetricCard label="Delay" value={fmtNum(agg.delay_bps)} period="bps" />
                <MetricCard label="Execution" value={fmtNum(agg.execution_bps)} period="bps" />
                <MetricCard label="Fees" value={fmtNum(agg.fees_bps)} period="bps" />
              </MetricRow>
            </div>

            <Tabs tabs={tabs} value={tab} onChange={setTab} idPrefix="hx-analytics" className="px-2 shrink-0" />

            <div className="flex-1 min-h-0 overflow-auto hx-scroll">
              <TabPanel id="shortfall" value={tab} idPrefix="hx-analytics" className="p-3 flex flex-col gap-4">
                <div>
                  <div className="flex items-center gap-1.5 mb-2">
                    <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">
                      Implementation shortfall — notional weighted
                    </span>
                    <InfoTip content="Delay + Execution + Fees = Total IS. Positive values are adverse: the trade cost more than the decision price implied." />
                  </div>
                  <ShortfallBar
                    parts={IS_PARTS.map((p) => ({ ...p, value: Number(agg[p.key]) }))}
                    total={agg.total_is_bps}
                  />
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                  {IS_PARTS.map((p) => {
                    const v = Number(agg[p.key]);
                    const share = Number(agg.total_is_bps) ? (v / Number(agg.total_is_bps)) * 100 : null;
                    return (
                      <div key={p.key} className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised p-3">
                        <div className="flex items-center gap-1.5">
                          <span className={cx('h-2 w-2 rounded-[1px]', TONE_SOLID[p.tone])} aria-hidden="true" />
                          <span className="text-hx-11 font-medium text-hx-text-mid">{p.label}</span>
                        </div>
                        <div className="text-[18px] leading-6 font-semibold hx-mono hx-tnum text-hx-text-hi mt-1">
                          {bps(v)}
                        </div>
                        <div className="text-hx-10 text-hx-text-dim mt-0.5">
                          {share === null ? 'no total' : `${share.toFixed(0)}% of total`}
                        </div>
                        <p className="text-hx-10 text-hx-text-lo leading-relaxed mt-1.5">{p.hint}</p>
                      </div>
                    );
                  })}
                </div>

                <div>
                  <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo mb-2">Fill quality distribution</div>
                  <FillQuality rows={rows} />
                </div>
              </TabPanel>

              <TabPanel id="markouts" value={tab} idPrefix="hx-analytics" className="p-3 flex flex-col gap-3">
                <div className="flex items-center gap-1.5">
                  <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">
                    Post-fill markout by horizon
                  </span>
                  <InfoTip content="Notional-weighted mean markout across the loaded fills. Above zero means price moved against the fill after execution." />
                </div>
                <MarkoutCurve points={markouts} />
                {markouts.length > 0 && (
                  <div className="flex flex-wrap gap-x-5 gap-y-1 pt-1">
                    {markouts.map((m) => (
                      <span key={m.key} className="inline-flex items-center gap-1.5">
                        <span className="text-hx-11 text-hx-text-lo hx-mono">{m.key}</span>
                        <span className={cx('text-hx-11 hx-mono hx-tnum', TONE_TEXT[deltaTone(-m.value)])}>
                          <span aria-hidden="true">{deltaArrow(m.value)}</span> {m.value.toFixed(2)}
                        </span>
                      </span>
                    ))}
                  </div>
                )}
              </TabPanel>

              <TabPanel id="fills" value={tab} idPrefix="hx-analytics" className="min-h-0">
                <DataGrid
                  columns={fillCols}
                  rows={rows}
                  rowKey={(r, i) => r.fill_id ?? i}
                  onRowClick={openDetail}
                  selectedKey={detail?.fill_id}
                  defaultSort={{ key: 'ts_fill', dir: 'desc' }}
                  exportName={`tca-fills-${dbName || 'bt'}`}
                  columnChooser
                  emptyTitle="No fills"
                  ariaLabel="Fills with transaction costs"
                />
              </TabPanel>

              <TabPanel id="strategies" value={tab} idPrefix="hx-analytics" className="min-h-0">
                <DataGrid
                  columns={stratCols}
                  rows={strategies}
                  rowKey={(r) => r.id}
                  defaultSort={{ key: 'is_cost', dir: 'desc' }}
                  exportName={`tca-strategies-${dbName || 'bt'}`}
                  emptyTitle="No per-strategy attribution"
                  emptyHint="This store reports no strategy breakdown."
                  ariaLabel="Cost attribution by strategy"
                />
              </TabPanel>
            </div>
          </>
        )}
      </PanelBody>

      <Drawer
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
        title={detail ? `${detail.symbol} — fill analysis` : ''}
        subtitle={detail ? fmtTime(nsToMs(detail.ts_fill), { mode: 'datetime' }) : ''}
        icon="analytics"
        size={520}
      >
        {detail && <FillDetail row={detail} onSelectSymbol={onSelectSymbol} />}
      </Drawer>
    </Panel>
  );
}

export default AnalyticsModule;
