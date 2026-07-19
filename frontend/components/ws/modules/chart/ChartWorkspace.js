/**
 * ChartWorkspace — chart + toolbar, bound to the live candle feed.
 *
 * Owns the timeframe/overlay state and the useCandles subscription; the canvas
 * below it stays purely presentational. Markers are supplied by the host (order
 * fills, strategy signals, AI recommendations) so this file never fetches
 * anything but price.
 */
import React, { useMemo, useState } from 'react';
import { useCandles } from '../../../../lib/useCandles';
import {
  Button,
  EmptyState,
  Icon,
  Panel,
  PanelBody,
  PanelHeader,
  Skeleton,
  StatusChip,
  TONE_TEXT,
  cx,
  deltaArrow,
  deltaTone,
  fmtNum,
  fmtPct,
} from '../../ui';
import { CandleChart } from './CandleChart';

/** Wire values the backend accepts for `range`/`interval`, per timeframe. */
export const TIMEFRAMES = [
  { id: '1m', label: '1m', range: '1d', interval: '1m' },
  { id: '5m', label: '5m', range: '5d', interval: '5m' },
  { id: '15m', label: '15m', range: '5d', interval: '15m' },
  { id: '1h', label: '1H', range: '1mo', interval: '60m' },
  { id: '1d', label: '1D', range: '1y', interval: '1d' },
];

function ToolbarToggle({ active, onClick, children, title }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={active}
      className={cx(
        'hx-focus rounded px-1.5 py-0.5 text-hx-10 font-medium transition-colors',
        active
          ? 'bg-hx-accent-500/15 text-hx-accent-300'
          : 'text-hx-text-dim hover:bg-white/5 hover:text-hx-text-mid',
      )}
    >
      {children}
    </button>
  );
}

export function ChartWorkspace({ symbol, markers = [], onSelectBar, height = 340, compact = false }) {
  const [tf, setTf] = useState('5m');
  const [overlays, setOverlays] = useState({ ema9: true, ema21: true, vwap: true });
  const frame = TIMEFRAMES.find((t) => t.id === tf) || TIMEFRAMES[1];

  const { loading, series, quote, source, error, reload } = useCandles(symbol, {
    range: frame.range,
    interval: frame.interval,
    enabled: Boolean(symbol),
  });

  // The quote endpoint has two provider shapes; normalise the two field spellings.
  const price = quote?.current_price ?? quote?.price ?? null;
  const changePct = quote?.change_pct ?? quote?.change_percent ?? null;
  const tone = deltaTone(changePct);

  const toggle = (k) => setOverlays((o) => ({ ...o, [k]: !o[k] }));

  const body = useMemo(() => {
    if (!symbol) {
      return (
        <EmptyState
          icon="markets"
          title="No instrument selected"
          hint="Pick a symbol from the watchlist or press Ctrl K to search."
        />
      );
    }
    if (loading && !series.length) {
      return (
        <div className="space-y-2 p-3">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-[240px] w-full" />
        </div>
      );
    }
    if (error) {
      return (
        <EmptyState
          icon="alert"
          title={error === 'timeout' ? 'Chart request timed out' : 'No price data'}
          hint={
            error === 'timeout'
              ? 'The backend is busy — a bulk ingest can starve the quote path.'
              : 'The provider returned an empty series for this instrument and timeframe.'
          }
          action={
            <Button size="xs" variant="subtle" onClick={reload}>
              Retry
            </Button>
          }
        />
      );
    }
    return (
      <CandleChart
        series={series}
        markers={markers}
        overlays={overlays}
        height={height}
        onHover={onSelectBar}
      />
    );
  }, [symbol, loading, series, error, reload, markers, overlays, height, onSelectBar]);

  return (
    <Panel className="flex h-full min-h-0 flex-col">
      <PanelHeader
        title={
          <span className="flex items-baseline gap-2">
            <span className="font-hx-mono text-hx-13 font-semibold text-hx-text-hi">
              {symbol || '—'}
            </span>
            {price != null && (
              <>
                <span className="font-hx-mono text-hx-12 text-hx-text-hi">{fmtNum(price)}</span>
                {changePct != null && (
                  <span className={cx('font-hx-mono text-hx-11', TONE_TEXT[tone])}>
                    {/* change_pct arrives already scaled (2.36 = 2.36%), not as a ratio */}
                    {deltaArrow(changePct)} {fmtPct(changePct, { asRatio: false })}
                  </span>
                )}
              </>
            )}
          </span>
        }
        actions={
          <div className="flex items-center gap-2">
            {source && (
              <StatusChip status={source === 'yahoo' ? 'stale' : 'connected'} label={source} />
            )}
            <div className="flex items-center gap-0.5 rounded bg-hx-bg-sunken p-0.5">
              {TIMEFRAMES.map((t) => (
                <ToolbarToggle
                  key={t.id}
                  active={t.id === tf}
                  onClick={() => setTf(t.id)}
                  title={`${t.range} @ ${t.interval}`}
                >
                  {t.label}
                </ToolbarToggle>
              ))}
            </div>
            {!compact && (
              <div className="flex items-center gap-0.5 rounded bg-hx-bg-sunken p-0.5">
                <ToolbarToggle active={overlays.ema9} onClick={() => toggle('ema9')} title="9-period EMA">
                  EMA9
                </ToolbarToggle>
                <ToolbarToggle active={overlays.ema21} onClick={() => toggle('ema21')} title="21-period EMA">
                  EMA21
                </ToolbarToggle>
                <ToolbarToggle active={overlays.vwap} onClick={() => toggle('vwap')} title="Session VWAP">
                  VWAP
                </ToolbarToggle>
              </div>
            )}
            <button
              type="button"
              onClick={reload}
              title="Refresh series"
              aria-label="Refresh series"
              className="hx-focus rounded p-1 text-hx-text-dim hover:text-hx-text-mid"
            >
              <Icon name="refresh" size={13} />
            </button>
          </div>
        }
      />
      <PanelBody className="min-h-0 flex-1 p-0">{body}</PanelBody>
    </Panel>
  );
}

export default ChartWorkspace;
