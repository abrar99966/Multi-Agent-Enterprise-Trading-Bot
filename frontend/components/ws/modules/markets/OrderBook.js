/**
 * OrderBook — the DOM: depth ladder, spread, cumulative liquidity, book totals,
 * and a recent-prints tape alongside.
 *
 * HONESTY BOUNDARY — read this before changing anything here.
 * This backend exposes no level-2 endpoint anywhere in its REST surface; the
 * market-data router returns last-trade quotes and OHLCV bars only. So the panel
 * is split into two clearly-separated halves:
 *
 *   LADDER — MODELLED from the last quote (price, spread band, volume). Marked
 *            with a warn badge in the header, a disclaimer strip above it,
 *            hatched size bars, and a footer that scopes every total as
 *            "modelled". It is deterministic (see buildSyntheticLadder) so it
 *            never animates like a live feed.
 *   TAPE   — REAL intraday bars from /market-data/intraday. Badged "real" and
 *            labelled with its own interval, because a tape of 5m bars is not a
 *            tape of executions and must not be read as one.
 *
 * If a depth feed is ever added, delete buildSyntheticLadder's call site and the
 * `synthetic` plumbing; DepthLadder itself needs no changes.
 */
import React, { useMemo, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import {
  Panel, PanelHeader, PanelToolbar, PanelBody, PanelFooter,
  Button, Badge, Icon, EmptyState, Skeleton,
  fmtNum, fmtQty, fmtCur, fmtPct, fmtTime, deltaArrow, deltaTone, TONE_TEXT, cx,
} from '../../ui';
import {
  apiBase, jget, MARKETS_CADENCE,
  normalizeQuote, normalizeSeries, buildSyntheticLadder,
} from './marketsApi';
import { DepthLadder } from './DepthLadder';

const DEPTH_CHOICES = [5, 10, 15];
const ROW_H = 20;
const HEAD_H = 22;
const SPREAD_H = 24;

/** Resolves to null without a request — useLivePoll must still be called
    unconditionally, so "no symbol" becomes an empty fetch rather than a
    skipped hook. */
const idleFetcher = () => Promise.resolve(null);

/** Book pressure. Modelled here, so every label that renders it says so. */
function ImbalanceBar({ imbalance }) {
  const bidPct = Math.round((imbalance ?? 0.5) * 100);
  return (
    <span className="flex items-center gap-1.5 min-w-0">
      <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo shrink-0">Imb</span>
      <span
        className="relative h-[6px] w-[54px] rounded-full overflow-hidden bg-hx-neg-500/25 shrink-0"
        role="img"
        aria-label={`Modelled book imbalance: ${bidPct}% bid, ${100 - bidPct}% ask`}
      >
        <span className="absolute inset-y-0 left-0 bg-hx-pos-500/60" style={{ width: `${bidPct}%` }} />
      </span>
      <span className="hx-mono hx-tnum text-hx-10 text-hx-text-mid shrink-0">
        {bidPct}/{100 - bidPct}
      </span>
    </span>
  );
}

/**
 * Recent prints. Real bars, newest first. Direction is carried by an arrow
 * glyph as well as colour so the tape reads in greyscale.
 */
function PrintsTape({ bars, interval, source, loading, error, height, onRetry }) {
  const rows = useMemo(() => {
    const out = [];
    for (let i = bars.length - 1; i >= 0 && out.length < 60; i -= 1) {
      const b = bars[i];
      const prev = i > 0 ? bars[i - 1].c : b.o;
      out.push({ ...b, dir: prev == null ? 0 : b.c - prev });
    }
    return out;
  }, [bars]);

  return (
    <div className="flex flex-col min-w-0 border-l border-hx-border-subtle">
      <div className="flex items-center justify-between gap-1 px-2 shrink-0 border-b border-hx-border-subtle" style={{ height: HEAD_H }}>
        <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo truncate">Prints</span>
        <Badge tone="pos" size="xs" dot>real</Badge>
      </div>

      <div className="overflow-auto hx-scroll min-h-0" style={{ maxHeight: height }}>
        {loading && (
          <div className="p-2 space-y-1.5">
            {Array.from({ length: 10 }).map((_, i) => (
              <Skeleton key={i} h={8} className="w-full" style={{ animationDelay: `${i * 40}ms` }} />
            ))}
          </div>
        )}

        {!loading && error && (
          <EmptyState
            variant="error"
            title="Prints unavailable"
            hint={String(error.message || error)}
            action={{ label: 'Retry', onClick: onRetry }}
          />
        )}

        {!loading && !error && rows.length === 0 && (
          <EmptyState title="No prints" hint="Market closed or no bars in range." />
        )}

        {!loading && !error && rows.length > 0 && (
          <table className="w-full border-collapse" style={{ tableLayout: 'fixed' }}>
            <caption className="sr-only">Recent intraday bars, newest first</caption>
            <colgroup>
              <col style={{ width: '38%' }} />
              <col style={{ width: '36%' }} />
              <col style={{ width: '26%' }} />
            </colgroup>
            <tbody>
              {rows.map((b, i) => (
                <tr key={`${b.tMs}-${i}`} style={{ height: ROW_H }} className="hover:bg-white/[0.035]">
                  <td className="px-1.5 text-hx-10 hx-mono hx-tnum text-hx-text-dim">
                    {fmtTime(b.tMs, { mode: 'hm' })}
                  </td>
                  <td className={cx('px-1.5 text-hx-11 hx-mono hx-tnum text-right', TONE_TEXT[deltaTone(b.dir)])}>
                    <span aria-hidden="true" className="text-[8px] mr-0.5">{deltaArrow(b.dir)}</span>
                    {fmtNum(b.c, { dp: 2 })}
                  </td>
                  <td className="px-1.5 text-hx-10 hx-mono hx-tnum text-right text-hx-text-lo">
                    {b.v != null ? fmtQty(b.v) : '--'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!loading && !error && rows.length > 0 && (
        <div className="px-2 py-1 shrink-0 border-t border-hx-border-subtle text-hx-10 text-hx-text-dim truncate">
          {interval || '--'} bars{source ? ` · ${source}` : ''}
        </div>
      )}
    </div>
  );
}

export function OrderBook({ symbol, className = '' }) {
  const [levels, setLevels] = useState(10);

  const quoteUrl = symbol
    ? `${apiBase()}/api/v1/market-data/quotes/${encodeURIComponent(symbol)}`
    : null;
  const seriesUrl = symbol
    ? `${apiBase()}/api/v1/market-data/intraday/${encodeURIComponent(symbol)}?range=1d&interval=5m`
    : null;

  const quoteFetcher = useMemo(() => (quoteUrl ? jget(quoteUrl) : idleFetcher), [quoteUrl]);
  const seriesFetcher = useMemo(() => (seriesUrl ? jget(seriesUrl) : idleFetcher), [seriesUrl]);

  const q = useLivePoll(quoteFetcher, MARKETS_CADENCE.quote, [quoteUrl]);
  const s = useLivePoll(seriesFetcher, MARKETS_CADENCE.intraday, [seriesUrl]);

  const quote = useMemo(() => normalizeQuote(q.data), [q.data]);
  const series = useMemo(() => normalizeSeries(s.data), [s.data]);

  // Rebuilds only when symbol/price/volume/levels change — the ladder is stable
  // between polls that didn't move the price, which is the point.
  const book = useMemo(
    () =>
      quote
        ? buildSyntheticLadder({
            symbol: quote.symbol,
            price: quote.price,
            volume: quote.volume,
            levels,
          })
        : null,
    [quote?.symbol, quote?.price, quote?.volume, levels], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // 2dp across the board: every tick band this backend serves (0.01 … 1.00) is
  // representable at 2dp, and a stable decimal count keeps the price column
  // from reflowing as the ladder walks across a band boundary.
  const dp = 2;
  const ladderHeight = HEAD_H + levels * ROW_H * 2 + SPREAD_H;

  const depthToggle = (
    <span className="flex items-center gap-0.5" role="group" aria-label="Ladder depth">
      {DEPTH_CHOICES.map((n) => (
        <Button
          key={n}
          size="xs"
          variant={n === levels ? 'primary' : 'subtle'}
          onClick={() => setLevels(n)}
          aria-pressed={n === levels}
        >
          {n}
        </Button>
      ))}
    </span>
  );

  /* ---- states before the book can render ---- */

  if (!symbol) {
    return (
      <Panel className={className}>
        <PanelHeader title="Order Book" icon="orders" />
        <PanelBody>
          <EmptyState
            title="No symbol selected"
            hint="Pick a row in the watchlist to load its book."
            icon="markets"
          />
        </PanelBody>
      </Panel>
    );
  }

  return (
    <Panel className={className} loading={q.loading}>
      <PanelHeader
        title="Order Book"
        subtitle={symbol}
        icon="orders"
        actions={
          <>
            <Badge tone="warn" size="xs" icon="alert">modelled</Badge>
            {depthToggle}
            <Button
              size="xs"
              variant="subtle"
              icon="refresh"
              iconOnly
              aria-label="Refresh book"
              loading={q.loading}
              onClick={() => { q.refresh(); s.refresh(); }}
            />
          </>
        }
      />

      <PanelToolbar>
        {q.loading && !quote ? (
          <Skeleton h={12} className="w-40" />
        ) : quote ? (
          <>
            <span className="flex items-baseline gap-2 min-w-0">
              <span className="hx-mono hx-tnum text-hx-14 font-semibold text-hx-text-hi truncate">
                {fmtCur(quote.price, { ccy: quote.currency })}
              </span>
              <span className={cx('hx-mono hx-tnum text-hx-11', TONE_TEXT[deltaTone(quote.changePct)])}>
                <span aria-hidden="true">{deltaArrow(quote.changePct)}</span>{' '}
                {fmtPct(quote.changePct, { asRatio: false })}
              </span>
            </span>
            <span className="flex items-center gap-2 shrink-0 text-hx-10 text-hx-text-dim">
              {quote.tsMs != null && <span className="hx-tnum">{fmtTime(quote.tsMs)}</span>}
              <span className="uppercase tracking-wider">{quote.source}</span>
            </span>
          </>
        ) : (
          <span className="text-hx-11 text-hx-text-dim">Quote unavailable</span>
        )}
      </PanelToolbar>

      <PanelBody pad={false}>
        {/* Error takes the whole body: without a quote there is no price to
            model a ladder around, so a partial render would be misleading. */}
        {q.error && !quote ? (
          <EmptyState
            variant="error"
            title="Could not load quote"
            hint={String(q.error.message || q.error)}
            action={{ label: 'Retry', onClick: q.refresh }}
          />
        ) : q.loading && !book ? (
          <div className="p-3 space-y-1.5">
            {Array.from({ length: 12 }).map((_, i) => (
              <Skeleton key={i} h={10} className="w-full" style={{ animationDelay: `${i * 35}ms` }} />
            ))}
          </div>
        ) : !book ? (
          <EmptyState
            title="No price for this symbol"
            hint="The provider returned a quote without a usable last price, so no ladder can be shown."
          />
        ) : (
          <div className="grid min-w-0" style={{ gridTemplateColumns: 'minmax(0,1fr) 148px' }}>
            <div className="min-w-0 flex flex-col">
              {/* The disclaimer sits ABOVE the ladder, inside its column — a
                  badge alone in the header is too easy to miss. */}
              <div className="flex items-start gap-1.5 px-2 py-1 border-b border-hx-warn-500/25 bg-hx-warn-500/[0.07]">
                <Icon name="alert" size={12} className="text-hx-warn-400 shrink-0 mt-px" />
                <span className="text-hx-10 leading-[13px] text-hx-text-mid">
                  <span className="font-semibold text-hx-warn-300">Modelled depth.</span>{' '}
                  No L2 feed is connected. Levels are derived from the last quote for layout
                  only — this is not live market depth and must not be traded against.
                </span>
              </div>
              <DepthLadder
                bids={book.bids}
                asks={book.asks}
                dp={dp}
                spread={book.spread}
                spreadBps={book.spreadBps}
                mid={book.mid}
                largeThreshold={book.largeThreshold}
                synthetic={book.synthetic}
              />
            </div>

            <PrintsTape
              bars={series.bars}
              interval={series.interval}
              source={series.source}
              loading={s.loading && !s.data}
              error={s.error}
              height={ladderHeight}
              onRetry={s.refresh}
            />
          </div>
        )}
      </PanelBody>

      {book && (
        <PanelFooter>
          <span className="flex items-center gap-3 min-w-0">
            <span className="hx-mono hx-tnum">
              <span className="text-hx-text-dim">B </span>
              <span className="text-hx-pos-400">{fmtQty(book.bidTotal)}</span>
            </span>
            <span className="hx-mono hx-tnum">
              <span className="text-hx-text-dim">A </span>
              <span className="text-hx-neg-400">{fmtQty(book.askTotal)}</span>
            </span>
            <ImbalanceBar imbalance={book.imbalance} />
          </span>
          <span className="text-hx-10 text-hx-text-dim truncate">
            {levels}×2 levels · modelled totals
          </span>
        </PanelFooter>
      )}
    </Panel>
  );
}

export default OrderBook;
