/**
 * WatchlistPanel — the dense quote grid and the module's selection surface.
 *
 * Controlled by design: MarketsModule owns the poll and hands rows down, so the
 * watchlist and the movers list render from ONE /market-data/watchlist response
 * instead of two independent 20s polls of the same URL. Clicking a row publishes
 * the symbol upward; everything else in the workspace reacts to that.
 *
 * Columns beyond the core four ship hidden and are opt-in through the grid's
 * column chooser — a watchlist rail is narrow, and a trader who wants OHLC in it
 * should say so rather than have it crammed in by default.
 */
import React, { useMemo, useState } from 'react';
import {
  Panel, PanelHeader, PanelBody,
  DataGrid, Button, Icon, Sparkline, StatusChip, EmptyState, useFlash,
  fmtCur, fmtNum, fmtPct, fmtQty, deltaArrow, deltaTone, TONE_TEXT, cx,
} from '../../ui';
import { normalizeSymbolInput } from './marketsApi';

/**
 * Last-price cell. useFlash is a hook, so the flash has to live in its own
 * component — it cannot be called inside a column's render loop.
 */
function PriceCell({ price, ccy }) {
  const flash = useFlash(price, true);
  return (
    <span className={cx('inline-block rounded px-1 -mx-1', flash)}>
      {fmtCur(price, { ccy })}
    </span>
  );
}

/** Signed change with an arrow glyph — direction never rides on colour alone. */
function DeltaCell({ value, formatter }) {
  return (
    <span className={TONE_TEXT[deltaTone(value)]}>
      <span aria-hidden="true" className="text-[9px] mr-0.5">{deltaArrow(value)}</span>
      {formatter(value)}
    </span>
  );
}

/** Compact inline control shell — shared by the search and add-symbol inputs. */
function InputShell({ icon, children, className = '' }) {
  return (
    <div
      className={cx(
        'flex items-center gap-1.5 h-[22px] px-2 rounded shrink-0',
        'bg-white/[0.04] border border-hx-border-subtle',
        'focus-within:border-hx-accent-500/60',
        className,
      )}
    >
      <Icon name={icon} size={12} className="text-hx-text-dim shrink-0" />
      {children}
    </div>
  );
}

export function WatchlistPanel({
  quotes = [],
  missing = [],
  symbols = [],
  history,            // Map<symbol, number[]> — observed ticks, for the sparkline
  loading = false,
  error = null,
  onRefresh,
  selected,
  onSelect,
  onAdd,
  onRemove,
  feedStatus = 'live',
  feedDetail,
  className = '',
}) {
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState('');

  const rows = useMemo(() => {
    const t = query.trim().toUpperCase();
    if (!t) return quotes;
    return quotes.filter(
      (q) => q.symbol.includes(t) || String(q.name || '').toUpperCase().includes(t),
    );
  }, [quotes, query]);

  const submitAdd = (e) => {
    e.preventDefault();
    const sym = normalizeSymbolInput(draft);
    if (!sym) return;
    // Silently ignore duplicates rather than erroring — re-adding a symbol you
    // already track is a no-op, not a mistake worth a dialog.
    if (!symbols.includes(sym) && onAdd) onAdd(sym);
    setDraft('');
  };

  const columns = useMemo(
    () => [
      {
        key: 'symbol',
        header: 'Symbol',
        width: 104,
        render: (r) => (
          <span className="flex flex-col leading-tight min-w-0">
            <span className="hx-mono text-hx-12 font-semibold text-hx-text-hi truncate">
              {r.symbol}
            </span>
            <span className="text-hx-10 text-hx-text-dim truncate">
              {r.exchange || r.name}
            </span>
          </span>
        ),
      },
      {
        key: 'price',
        header: 'Last',
        width: 92,
        numeric: true,
        render: (r) => <PriceCell price={r.price} ccy={r.currency} />,
      },
      {
        key: 'changePct',
        header: 'Chg%',
        width: 78,
        numeric: true,
        render: (r) => (
          <DeltaCell value={r.changePct} formatter={(v) => fmtPct(v, { asRatio: false })} />
        ),
      },
      {
        key: 'trend',
        header: 'Trend',
        width: 78,
        sortable: false,
        exportable: false,
        align: 'right',
        render: (r) => {
          const vals = history?.get ? history.get(r.symbol) : null;
          return (
            <span className="inline-flex justify-end w-full">
              <Sparkline
                values={vals || []}
                autoTone
                width={68}
                height={18}
                aria-label={`${r.symbol} observed price trend this session`}
              />
            </span>
          );
        },
      },
      {
        key: 'change',
        header: 'Chg',
        width: 78,
        numeric: true,
        hidden: true,
        render: (r) => <DeltaCell value={r.change} formatter={(v) => fmtNum(v, { dp: 2 })} />,
      },
      {
        key: 'volume',
        header: 'Volume',
        width: 74,
        numeric: true,
        hidden: true,
        render: (r) => (r.volume != null ? fmtQty(r.volume) : '--'),
      },
      {
        key: 'open',
        header: 'Open',
        width: 74,
        numeric: true,
        hidden: true,
        render: (r) => fmtNum(r.open, { dp: 2 }),
      },
      {
        key: 'high',
        header: 'High',
        width: 74,
        numeric: true,
        hidden: true,
        render: (r) => fmtNum(r.high, { dp: 2 }),
      },
      {
        key: 'low',
        header: 'Low',
        width: 74,
        numeric: true,
        hidden: true,
        render: (r) => fmtNum(r.low, { dp: 2 }),
      },
      {
        key: 'source',
        header: 'Src',
        width: 62,
        hidden: true,
        render: (r) => (
          <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">{r.source}</span>
        ),
      },
      {
        key: 'remove',
        header: '',
        width: 28,
        sortable: false,
        exportable: false,
        align: 'center',
        render: (r) => (
          <Button
            size="xs"
            variant="subtle"
            icon="close"
            iconOnly
            aria-label={`Remove ${r.symbol} from watchlist`}
            // Without this the click also selects the row we just removed.
            onClick={(e) => { e.stopPropagation(); onRemove && onRemove(r.symbol); }}
            // Dimmed rather than hidden-until-hover: DataGrid's <tr> carries no
            // `group` class (and isn't ours to change), so a hover-reveal here
            // would simply never reveal.
            className="opacity-50 hover:opacity-100 focus-visible:opacity-100"
          />
        ),
      },
    ],
    [history, onRemove],
  );

  const gridToolbar = (
    <>
      <InputShell icon="search">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter"
          aria-label="Filter watchlist"
          className="w-[84px] bg-transparent outline-none text-hx-11 text-hx-text-hi placeholder:text-hx-text-dim"
        />
      </InputShell>

      <form onSubmit={submitAdd} className="flex items-center gap-1">
        <InputShell icon="plus">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Add symbol"
            aria-label="Add symbol to watchlist"
            maxLength={20}
            className="w-[78px] bg-transparent outline-none text-hx-11 hx-mono uppercase text-hx-text-hi placeholder:text-hx-text-dim placeholder:normal-case placeholder:font-hx-sans"
          />
        </InputShell>
        <Button
          type="submit"
          size="xs"
          variant="ghost"
          disabled={!normalizeSymbolInput(draft)}
          aria-label="Add symbol"
        >
          Add
        </Button>
      </form>
    </>
  );

  return (
    <Panel className={className} loading={loading}>
      <PanelHeader
        title="Watchlist"
        icon="markets"
        subtitle={quotes.length ? `${rows.length}/${symbols.length}` : undefined}
        actions={
          <>
            <StatusChip status={feedStatus} detail={feedDetail} />
            <Button
              size="xs"
              variant="subtle"
              icon="refresh"
              iconOnly
              aria-label="Refresh watchlist"
              loading={loading}
              onClick={onRefresh}
            />
          </>
        }
      />

      <PanelBody pad={false}>
        {/* A failed poll with cached rows still shows the rows — a stale book is
            more useful than an error page, so the error becomes a strip. */}
        {error && quotes.length > 0 && (
          <div className="flex items-center gap-1.5 px-2 py-1 border-b border-hx-neg-500/25 bg-hx-neg-500/[0.07]">
            <Icon name="alert" size={12} className="text-hx-neg-400 shrink-0" />
            <span className="text-hx-10 text-hx-text-mid truncate flex-1">
              Refresh failed — showing last good data. {String(error.message || error)}
            </span>
            <Button size="xs" variant="subtle" onClick={onRefresh}>Retry</Button>
          </div>
        )}

        {error && quotes.length === 0 && !loading ? (
          <EmptyState
            variant="error"
            title="Could not load watchlist"
            hint={String(error.message || error)}
            action={{ label: 'Retry', onClick: onRefresh }}
          />
        ) : (
          <DataGrid
            columns={columns}
            rows={rows}
            rowKey={(r) => r.symbol}
            loading={loading && quotes.length === 0}
            onRowClick={(r) => onSelect && onSelect(r.symbol)}
            selectedKey={selected}
            defaultSort={null}
            columnChooser
            exportName="watchlist"
            toolbar={gridToolbar}
            ariaLabel="Watchlist quotes"
            emptyTitle={query ? 'No match' : 'Watchlist empty'}
            emptyHint={
              query
                ? `Nothing matches "${query}".`
                : 'Add a symbol above to start tracking it.'
            }
            className="min-h-0"
          />
        )}
      </PanelBody>

      {/* Symbols that failed on every provider are dropped from the response with
          no error field, so a short grid looks like a bug unless we say otherwise. */}
      {missing.length > 0 && (
        <div className="flex items-center gap-1.5 px-3 h-[24px] shrink-0 border-t border-hx-border-subtle">
          <Icon name="info" size={11} className="text-hx-warn-400 shrink-0" />
          <span className="text-hx-10 text-hx-text-dim truncate" title={missing.join(', ')}>
            No quote for {missing.slice(0, 4).join(', ')}
            {missing.length > 4 ? ` +${missing.length - 4}` : ''}
          </span>
        </div>
      )}
    </Panel>
  );
}

export default WatchlistPanel;
