/**
 * PositionsGrid — open positions blotter.
 *
 * Row click publishes the symbol to the workspace selection so the chart,
 * markets and orders modules follow along. The grid is the module's primary
 * navigation surface, so it is fully keyboard-operable via DataGrid (↑/↓/Enter).
 *
 * Signed values carry an explicit +/- and a ▲/▼ glyph in addition to tone —
 * never colour alone.
 */
import React, { useMemo } from 'react';
import { Badge, DataGrid, TONE_TEXT, cx, deltaArrow, deltaTone, fmtCur, fmtNum, fmtPct, fmtQty } from '../../ui';

/** Signed numeric cell: arrow + sign + tone. */
function Signed({ value, format, dim = false }) {
  if (value == null || !Number.isFinite(Number(value))) {
    return <span className="text-hx-text-dim">--</span>;
  }
  const tone = deltaTone(value);
  return (
    <span className={cx('hx-mono whitespace-nowrap', dim ? 'text-hx-text-lo' : TONE_TEXT[tone])}>
      <span aria-hidden="true">{deltaArrow(value)}</span> {format(value)}
    </span>
  );
}

export function PositionsGrid({
  positions = [],
  currency = 'INR',
  selectedSymbol = null,
  onSelectSymbol,
  loading = false,
  error = null,
  onRetry,
  maxHeight,
  className = '',
}) {
  const columns = useMemo(
    () => [
      {
        key: 'symbol',
        header: 'Symbol',
        width: '15%',
        mono: true,
        render: (r) => (
          <span className="flex items-center gap-1.5 min-w-0">
            <span className="font-semibold text-hx-text-hi truncate">{r.symbol}</span>
            {/* Direction is a word, not a colour. */}
            {r.qty < 0 && (
              <Badge tone="warn" size="xs">
                Short
              </Badge>
            )}
            {r.paper && (
              <Badge tone="info" size="xs">
                Paper
              </Badge>
            )}
          </span>
        ),
      },
      {
        key: 'qty',
        header: 'Qty',
        width: '9%',
        numeric: true,
        accessor: (r) => r.qty,
        render: (r) => <span className="hx-mono">{fmtQty(r.qty, { signed: true })}</span>,
      },
      {
        key: 'avgPrice',
        header: 'Avg',
        width: '11%',
        numeric: true,
        accessor: (r) => r.avgPrice,
        render: (r) => fmtNum(r.avgPrice, { dp: 2 }),
      },
      {
        key: 'ltp',
        header: 'LTP',
        width: '11%',
        numeric: true,
        accessor: (r) => r.ltp,
        headerTitle: 'Last traded price from the live watchlist poll',
        render: (r) =>
          r.ltp == null ? <span className="text-hx-text-dim">--</span> : fmtNum(r.ltp, { dp: 2 }),
      },
      {
        key: 'unrealized',
        header: 'Unrealised',
        width: '13%',
        numeric: true,
        accessor: (r) => r.unrealized,
        render: (r) => <Signed value={r.unrealized} format={(v) => fmtCur(v, { ccy: currency, signed: true })} />,
      },
      {
        key: 'unrealizedPct',
        header: '%',
        width: '10%',
        numeric: true,
        accessor: (r) => r.unrealizedPct,
        render: (r) => <Signed value={r.unrealizedPct} format={(v) => fmtPct(v)} />,
      },
      {
        key: 'exposure',
        header: 'Exposure',
        width: '13%',
        numeric: true,
        accessor: (r) => r.exposure,
        render: (r) => fmtCur(r.exposure, { ccy: currency, compact: true }),
      },
      {
        key: 'strategy',
        header: 'Strategy',
        width: '18%',
        headerTitle: 'Most recent strategy_id that emitted an intent for this symbol',
        accessor: (r) => r.strategy || '',
        render: (r) =>
          r.strategy ? (
            <span className="hx-mono text-hx-11 text-hx-text-mid truncate">{r.strategy}</span>
          ) : (
            <span className="text-hx-text-dim">--</span>
          ),
      },
    ],
    [currency],
  );

  return (
    <DataGrid
      columns={columns}
      rows={positions}
      rowKey={(r) => r.symbol}
      loading={loading}
      selectedKey={selectedSymbol || undefined}
      onRowClick={onSelectSymbol ? (r) => onSelectSymbol(r.symbol) : undefined}
      defaultSort={{ key: 'exposure', dir: 'desc' }}
      exportName="helios-positions"
      columnChooser
      maxHeight={maxHeight}
      ariaLabel="Open positions"
      className={className}
      emptyTitle={error ? "Couldn't load positions" : 'No open positions'}
      emptyHint={
        error
          ? String((error && error.message) || error)
          : 'Positions are reconstructed from the selected journal, or from the trade blotter when no journal is available.'
      }
      emptyAction={onRetry ? { label: 'Retry', onClick: onRetry, icon: 'refresh' } : undefined}
    />
  );
}

export default PositionsGrid;
