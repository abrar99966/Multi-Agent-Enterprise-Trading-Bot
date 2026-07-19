/**
 * DepthLadder — the price ladder half of a DOM (depth of market).
 *
 * Purely presentational: it renders the levels it is handed and knows nothing
 * about where they came from. OrderBook decides real-vs-modelled and labels the
 * panel; this component only honours the `synthetic` flag by hatching its size
 * bars, so a modelled book can never be mistaken for a live one even at a glance.
 *
 * Layout is the standard institutional DOM:
 *     [bid cum | bid size | PRICE | ask size | ask cum]
 * asks descending from the top, bids descending below, and the spread row
 * between them — which puts the touch (best bid / best ask) together in the
 * middle of the panel where the eye rests during execution.
 *
 * Size bars grow *outward from the price column* (bids right-to-left, asks
 * left-to-right) so both sides are read against the same centre axis.
 */
import React from 'react';
import { fmtNum, fmtQty, cx } from '../../ui';

/** Hatching = "this number was modelled, not received". A texture channel, so
    the distinction survives greyscale and colour-blindness (WCAG 1.4.1). */
const HATCH = {
  backgroundImage:
    'repeating-linear-gradient(45deg, rgba(255,255,255,0.14) 0 2px, transparent 2px 5px)',
};

const pct = (v, max) => (max > 0 ? Math.max(1.5, Math.min(100, (v / max) * 100)) : 0);

/**
 * One price level. `side` picks the anchor edge for both bars: the size bar
 * against the price column, the cumulative bar against the outer edge.
 */
function LadderRow({ side, level, dp, maxSize, maxCum, largeThreshold, synthetic, isTouch }) {
  const bid = side === 'bid';
  const isLarge = largeThreshold > 0 && level.size >= largeThreshold;

  const sizeBar = (
    <span
      aria-hidden="true"
      className={cx(
        'absolute inset-y-[3px] rounded-[2px] pointer-events-none',
        bid ? 'right-0 bg-hx-pos-500/25' : 'left-0 bg-hx-neg-500/25',
      )}
      style={{ width: `${pct(level.size, maxSize)}%`, ...(synthetic ? HATCH : null) }}
    />
  );

  const cumBar = (
    <span
      aria-hidden="true"
      className={cx(
        'absolute inset-y-[5px] rounded-[2px] pointer-events-none',
        bid ? 'left-0 bg-hx-pos-500/[0.10]' : 'right-0 bg-hx-neg-500/[0.10]',
      )}
      style={{ width: `${pct(level.cum, maxCum)}%` }}
    />
  );

  const cumCell = (
    <td className="relative px-1.5 text-hx-10 hx-mono hx-tnum text-hx-text-dim">
      {cumBar}
      <span className={cx('relative', bid ? 'block text-left' : 'block text-right')}>
        {fmtQty(level.cum)}
      </span>
    </td>
  );

  const sizeCell = (
    <td className="relative px-1.5 text-hx-11 hx-mono hx-tnum">
      {sizeBar}
      <span
        className={cx(
          'relative flex items-center gap-1',
          bid ? 'justify-end' : 'justify-start flex-row-reverse',
          isLarge ? 'text-hx-text-hi font-semibold' : 'text-hx-text-mid',
        )}
      >
        {/* Glyph, not just weight/colour — the block marker must survive greyscale. */}
        {isLarge && (
          <span className={cx('text-[8px] leading-none', bid ? 'text-hx-pos-300' : 'text-hx-neg-300')}>
            ◆
          </span>
        )}
        {fmtQty(level.size)}
      </span>
    </td>
  );

  const priceCell = (
    <td
      className={cx(
        'px-1.5 text-hx-11 hx-mono hx-tnum text-center font-medium',
        bid ? 'text-hx-pos-400' : 'text-hx-neg-400',
        isTouch && (bid ? 'bg-hx-pos-500/[0.07]' : 'bg-hx-neg-500/[0.07]'),
      )}
    >
      {fmtNum(level.price, { dp })}
    </td>
  );

  const empty = <td className="px-1.5" />;

  return (
    <tr
      style={{ height: 20 }}
      title={
        isLarge
          ? `Block: ${fmtQty(level.size)} at ${fmtNum(level.price, { dp })}`
          : undefined
      }
      className={cx(
        'transition-colors duration-75 hover:bg-white/[0.035]',
        isTouch && 'bg-white/[0.02]',
      )}
    >
      {bid ? cumCell : empty}
      {bid ? sizeCell : empty}
      {priceCell}
      {bid ? empty : sizeCell}
      {bid ? empty : cumCell}
    </tr>
  );
}

/** The centre rule: absolute spread, its bps cost, and the mid it implies. */
function SpreadRow({ spread, spreadBps, mid, dp }) {
  return (
    <tr style={{ height: 24 }} className="bg-white/[0.04]">
      <td colSpan={5} className="px-2 border-y border-hx-border-strong">
        <div className="flex items-center justify-between gap-2">
          <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Spread</span>
          <span className="flex items-center gap-2 hx-mono hx-tnum text-hx-11">
            <span className="text-hx-text-hi">{fmtNum(spread, { dp })}</span>
            <span className="text-hx-text-dim">
              {spreadBps != null ? `${fmtNum(spreadBps, { dp: 1 })} bps` : '--'}
            </span>
            <span className="text-hx-text-lo">
              mid <span className="text-hx-text-mid">{fmtNum(mid, { dp })}</span>
            </span>
          </span>
        </div>
      </td>
    </tr>
  );
}

export function DepthLadder({
  bids = [],          // best-first (highest price first)
  asks = [],          // best-first (lowest price first)
  dp = 2,
  spread,
  spreadBps,
  mid,
  largeThreshold = 0,
  synthetic = false,
  className = '',
}) {
  // One scale across both sides — per-side scaling would make a thin ask book
  // look as deep as a heavy bid book, which is exactly the read a DOM exists
  // to prevent.
  const maxSize = Math.max(1, ...bids.map((l) => l.size), ...asks.map((l) => l.size));
  const maxCum = Math.max(
    1,
    bids.length ? bids[bids.length - 1].cum : 0,
    asks.length ? asks[asks.length - 1].cum : 0,
  );

  // Asks render worst-price-first so the best ask lands adjacent to the spread row.
  const asksDesc = [...asks].reverse();

  return (
    <div className={cx('min-w-0', className)}>
      <table className="w-full border-collapse" style={{ tableLayout: 'fixed' }}>
        <caption className="sr-only">
          {synthetic
            ? 'Modelled order book ladder, derived from the last quote. Not live market depth.'
            : 'Order book depth ladder'}
        </caption>
        <colgroup>
          <col style={{ width: '17%' }} />
          <col style={{ width: '23%' }} />
          <col style={{ width: '20%' }} />
          <col style={{ width: '23%' }} />
          <col style={{ width: '17%' }} />
        </colgroup>

        <thead className="hx-sticky-head">
          <tr className="text-hx-10 uppercase tracking-wider text-hx-text-lo">
            <th scope="col" className="h-[22px] px-1.5 text-left font-medium">Cum</th>
            <th scope="col" className="h-[22px] px-1.5 text-right font-medium">Bid</th>
            <th scope="col" className="h-[22px] px-1.5 text-center font-medium">Price</th>
            <th scope="col" className="h-[22px] px-1.5 text-left font-medium">Ask</th>
            <th scope="col" className="h-[22px] px-1.5 text-right font-medium">Cum</th>
          </tr>
        </thead>

        <tbody>
          {asksDesc.map((level, i) => (
            <LadderRow
              key={`a-${level.price}-${i}`}
              side="ask"
              level={level}
              dp={dp}
              maxSize={maxSize}
              maxCum={maxCum}
              largeThreshold={largeThreshold}
              synthetic={synthetic}
              isTouch={i === asksDesc.length - 1}
            />
          ))}

          <SpreadRow spread={spread} spreadBps={spreadBps} mid={mid} dp={dp} />

          {bids.map((level, i) => (
            <LadderRow
              key={`b-${level.price}-${i}`}
              side="bid"
              level={level}
              dp={dp}
              maxSize={maxSize}
              maxCum={maxCum}
              largeThreshold={largeThreshold}
              synthetic={synthetic}
              isTouch={i === 0}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default DepthLadder;
