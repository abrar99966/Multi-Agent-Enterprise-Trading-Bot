/**
 * MarketMovers — top gainers, losers and most-active, side by side.
 *
 * Derived entirely from the watchlist rows the module already polled: movers are
 * a *view* of the same payload, not a second data source, so this component
 * issues no request of its own. That keeps the endpoint at exactly one poll per
 * cadence and guarantees the movers can never disagree with the watchlist.
 *
 * Scope is therefore the user's watchlist, not the whole market — the header
 * says so, because "Top Gainers" over 10 tracked symbols means something very
 * different from "Top Gainers" over the NSE.
 */
import React, { useMemo } from 'react';
import {
  Panel, PanelHeader, PanelBody,
  Icon, EmptyState, Skeleton,
  fmtPct, fmtQty, deltaTone, TONE_TEXT, cx,
} from '../../ui';

/** One mover row. Compact 22px rhythm, whole row is the hit target. */
function MoverRow({ rank, quote, value, tone, selected, onSelect }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect && onSelect(quote.symbol)}
        aria-current={selected ? 'true' : undefined}
        className={cx(
          'hx-focus-inset w-full flex items-center gap-2 h-[22px] px-2 rounded-sm text-left',
          'transition-colors duration-75',
          selected ? 'bg-hx-accent-500/[0.10]' : 'hover:bg-white/[0.05]',
        )}
      >
        <span className="hx-mono hx-tnum text-hx-10 text-hx-text-dim w-3 shrink-0">{rank}</span>
        <span className="hx-mono text-hx-11 text-hx-text-hi truncate flex-1 min-w-0">
          {quote.symbol}
        </span>
        <span className={cx('hx-mono hx-tnum text-hx-11 shrink-0', tone && TONE_TEXT[tone])}>
          {value}
        </span>
      </button>
    </li>
  );
}

function MoverList({ title, icon, rows, selected, onSelect, emptyHint }) {
  return (
    <div className="flex flex-col min-w-0">
      <div className="flex items-center gap-1.5 px-2 h-[20px] shrink-0">
        <Icon name={icon} size={11} className="text-hx-text-dim shrink-0" />
        <span className="text-hx-10 font-medium uppercase tracking-wider text-hx-text-lo truncate">
          {title}
        </span>
      </div>

      {rows.length === 0 ? (
        <p className="px-2 py-2 text-hx-10 text-hx-text-dim leading-snug">{emptyHint}</p>
      ) : (
        <ul className="flex flex-col">
          {rows.map((r, i) => (
            <MoverRow
              key={r.quote.symbol}
              rank={i + 1}
              quote={r.quote}
              value={r.value}
              tone={r.tone}
              selected={selected === r.quote.symbol}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

export function MarketMovers({
  quotes = [],
  loading = false,
  error = null,
  selected,
  onSelect,
  limit = 6,
  className = '',
}) {
  const { gainers, losers, active } = useMemo(() => {
    const withPct = quotes.filter((q) => q.changePct != null);
    const withVol = quotes.filter((q) => q.volume != null && q.volume > 0);

    const byPct = [...withPct].sort((a, b) => b.changePct - a.changePct);

    return {
      // Only rows actually moving the right way — padding a gainers list with
      // flat or falling symbols would misreport the session.
      gainers: byPct
        .filter((q) => q.changePct > 0)
        .slice(0, limit)
        .map((q) => ({
          quote: q,
          value: fmtPct(q.changePct, { asRatio: false }),
          tone: deltaTone(q.changePct),
        })),
      losers: byPct
        .filter((q) => q.changePct < 0)
        .reverse()
        .slice(0, limit)
        .map((q) => ({
          quote: q,
          value: fmtPct(q.changePct, { asRatio: false }),
          tone: deltaTone(q.changePct),
        })),
      active: [...withVol]
        .sort((a, b) => b.volume - a.volume)
        .slice(0, limit)
        .map((q) => ({ quote: q, value: fmtQty(q.volume), tone: null })),
    };
  }, [quotes, limit]);

  return (
    <Panel className={className} loading={loading}>
      <PanelHeader
        title="Movers"
        icon="analytics"
        subtitle="watchlist scope"
      />
      <PanelBody pad={false} className="py-1">
        {loading && quotes.length === 0 ? (
          <div className="grid grid-cols-3 gap-2 p-2">
            {Array.from({ length: 3 }).map((_, c) => (
              <div key={c} className="space-y-1.5">
                {Array.from({ length: 4 }).map((__, r) => (
                  <Skeleton key={r} h={10} className="w-full" style={{ animationDelay: `${(c * 4 + r) * 40}ms` }} />
                ))}
              </div>
            ))}
          </div>
        ) : error && quotes.length === 0 ? (
          <EmptyState
            variant="error"
            title="Movers unavailable"
            hint={String(error.message || error)}
          />
        ) : quotes.length === 0 ? (
          <EmptyState title="No quotes" hint="Add symbols to the watchlist to see movers." />
        ) : (
          <div className="grid grid-cols-3 gap-x-2 divide-x divide-hx-border-subtle">
            <MoverList
              title="Gainers"
              icon="chevron-up"
              rows={gainers}
              selected={selected}
              onSelect={onSelect}
              emptyHint="Nothing up on the session."
            />
            <MoverList
              title="Losers"
              icon="chevron-down"
              rows={losers}
              selected={selected}
              onSelect={onSelect}
              emptyHint="Nothing down on the session."
            />
            <MoverList
              title="Most Active"
              icon="spark"
              rows={active}
              selected={selected}
              onSelect={onSelect}
              // Indices and several broker rows carry no volume at all, so an
              // empty list here is a provider gap, not a quiet market.
              emptyHint="Provider returned no volume for these symbols."
            />
          </div>
        )}
      </PanelBody>
    </Panel>
  );
}

export default MarketMovers;
