/**
 * PortfolioModule — the portfolio analytics workspace.
 *
 * Composition: a KPI strip over a 12-column panel grid (curve + allocation,
 * drawdown + treemap, positions + sectors). Every panel is independently
 * loading/error/empty-gated, so one dead endpoint degrades a single tile rather
 * than the view.
 *
 * ---- SHELL WIRING -------------------------------------------------------
 * Selection is prop-driven, NOT imported from a store module. WHY: the shell
 * (lib/ws/*) does not exist in the tree yet, and a hard import of a missing
 * module is a build error in Next, not a soft failure. The contract is:
 *
 *   <PortfolioModule
 *     selectedSymbol={store.selection.symbol}     // read
 *     onSelectSymbol={store.selectSymbol}         // write / publish
 *   />
 *
 * With neither prop supplied the module keeps selection in local state and
 * still works standalone. When the shell lands, wiring those two props is the
 * whole integration — nothing else in this directory needs to change.
 *
 * ---- OVERLAY SAFETY -----------------------------------------------------
 * This subtree deliberately sets no `transform`, `filter` or `perspective` on
 * any ancestor element. Those create a containing block that would clip the
 * shell's fixed-position Drawer/NotificationStack. Do not add `.gpu-layer` or
 * `translateZ(0)` here.
 */
import React, { useCallback, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Icon,
  MetricCard,
  Panel,
  PanelBody,
  PanelFooter,
  PanelHeader,
  PanelToolbar,
  StatusChip,
  Tooltip,
  cx,
  fmtCur,
  fmtNum,
  fmtPct,
  fmtTime,
} from '../../ui';
import { AllocationDonut } from './AllocationDonut';
import { DrawdownChart } from './DrawdownChart';
import { ExposureTreemap } from './ExposureTreemap';
import { PerformanceChart } from './PerformanceChart';
import { PositionsGrid } from './PositionsGrid';
import { SectorHeatmap } from './SectorHeatmap';
import { usePortfolioData } from './usePortfolioData';

export function PortfolioModule({
  selectedSymbol: selectedSymbolProp,
  onSelectSymbol,
  className = '',
}) {
  const d = usePortfolioData();

  /* Selection: controlled by the shell when props are supplied, local otherwise. */
  const [localSymbol, setLocalSymbol] = useState(null);
  const selectedSymbol = selectedSymbolProp !== undefined ? selectedSymbolProp : localSymbol;
  const selectSymbol = useCallback(
    (sym) => {
      // Toggle off when re-clicking the active row — a selected-but-invisible
      // symbol is a common way to lose track of what the other panels follow.
      const next = sym === selectedSymbol ? null : sym;
      if (onSelectSymbol) onSelectSymbol(next);
      if (selectedSymbolProp === undefined) setLocalSymbol(next);
    },
    [onSelectSymbol, selectedSymbol, selectedSymbolProp],
  );

  const [sectorFilter, setSectorFilter] = useState(null);
  const [groupBy, setGroupBy] = useState('sector');

  const toggleSector = useCallback((s) => setSectorFilter((cur) => (cur === s ? null : s)), []);

  const visiblePositions = useMemo(
    () => (sectorFilter ? d.positions.filter((p) => p.sector === sectorFilter) : d.positions),
    [d.positions, sectorFilter],
  );

  const treemapItems = useMemo(
    () =>
      visiblePositions.map((p) => ({
        symbol: p.symbol,
        sector: p.sector,
        value: p.exposure,
        changePct: p.changePct,
      })),
    [visiblePositions],
  );

  const { totals, currency, stats } = d;
  const statsQ = d.q.statsQ;

  /* Connection state — derived from whether the slow polls are erroring. */
  const feedStatus = useMemo(() => {
    if (d.q.watchlistQ.error || d.positionsError) return 'degraded';
    if (d.positionsLoading) return 'stale';
    return 'live';
  }, [d.q.watchlistQ.error, d.positionsError, d.positionsLoading]);

  return (
    <div className={cx('flex flex-col gap-2 min-h-0 min-w-0 p-2 bg-hx-bg-base', className)}>
      {/* ================= header ================= */}
      <header className="flex items-center justify-between gap-3 px-1 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Icon name="portfolio" size={15} className="text-hx-accent-400 shrink-0" />
          <h1 className="text-hx-13 font-semibold uppercase tracking-wide text-hx-text-hi">Portfolio</h1>
          <StatusChip status={feedStatus} showIcon />
          {d.positionsSource && (
            <Tooltip
              content={
                d.positionsSource === 'journal'
                  ? 'Positions come from the selected event journal (authoritative fills).'
                  : 'No journal available — positions reconstructed from the trade blotter using average-cost accounting.'
              }
              side="bottom"
            >
              <Badge tone={d.positionsSource === 'journal' ? 'accent' : 'warn'} size="xs" icon="info">
                {d.positionsSource === 'journal' ? 'Journal' : 'Derived'}
              </Badge>
            </Tooltip>
          )}
          {sectorFilter && (
            <Button size="xs" variant="subtle" icon="close" onClick={() => setSectorFilter(null)}>
              {sectorFilter}
            </Button>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {/* Journal picker — every projection below is scoped to this file. */}
          {d.journals.length > 0 && (
            <label className="flex items-center gap-1.5">
              <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Journal</span>
              <select
                value={d.journalName || ''}
                onChange={(e) => d.setJournalName(e.target.value)}
                className="hx-focus hx-mono text-hx-11 rounded border border-hx-border-subtle bg-hx-bg-raised px-1.5 py-0.5 text-hx-text-mid"
              >
                {d.journals.map((j) => (
                  <option key={j.name} value={j.name}>
                    {j.name} · {fmtNum(j.records, { dp: 0 })}
                    {j.chain_ok ? '' : ' ⚠'}
                  </option>
                ))}
              </select>
            </label>
          )}
          {totals.dataThrough && (
            <span className="hx-mono text-hx-10 text-hx-text-dim">
              through {fmtTime(totals.dataThrough, { mode: 'datetime' })}
            </span>
          )}
          <Button size="xs" variant="subtle" icon="refresh" onClick={d.refreshAll}>
            Refresh
          </Button>
        </div>
      </header>

      {/* ================= KPI strip ================= */}
      <div className="grid gap-2 grid-cols-2 md:grid-cols-4 2xl:grid-cols-7 shrink-0">
        <MetricCard
          label="Net liquidation"
          icon="portfolio"
          value={fmtCur(totals.netLiq, { ccy: currency, compact: true })}
          raw={totals.netLiq}
          flash
          period={totals.hasAccounts ? 'broker equity' : 'cash + exposure'}
          loading={d.q.accountsQ.loading && !d.q.accountsQ.data}
        />
        <MetricCard
          label="Cash"
          icon="orders"
          value={fmtCur(totals.cash, { ccy: currency, compact: true })}
          raw={totals.cash}
          period={totals.hasAccounts ? `${d.accounts.length} account(s)` : 'no broker linked'}
          loading={d.q.accountsQ.loading && !d.q.accountsQ.data}
        />
        <MetricCard
          label="Unrealised P&L"
          icon="analytics"
          value={fmtCur(totals.unrealized, { ccy: currency, signed: true, compact: true })}
          raw={totals.unrealized}
          tone={totals.unrealized > 0 ? 'pos' : totals.unrealized < 0 ? 'neg' : undefined}
          delta={totals.unrealized}
          deltaText={fmtCur(totals.unrealized, { ccy: currency, signed: true, compact: true })}
          period="open positions"
          flash
          loading={d.positionsLoading}
        />
        <MetricCard
          label="Realised P&L"
          icon="check"
          value={fmtCur(totals.realized, { ccy: currency, signed: true, compact: true })}
          raw={totals.realized}
          tone={totals.realized > 0 ? 'pos' : totals.realized < 0 ? 'neg' : undefined}
          period="journal to date"
          loading={d.positionsLoading}
        />
        <MetricCard
          label="Gross exposure"
          icon="markets"
          value={fmtCur(totals.grossExposure, { ccy: currency, compact: true })}
          raw={totals.grossExposure}
          period={`${totals.openCount} position${totals.openCount === 1 ? '' : 's'}`}
          loading={d.positionsLoading}
        />
        <MetricCard
          label="Max drawdown"
          icon="risk"
          value={fmtCur(totals.maxDrawdown, { ccy: currency, compact: true })}
          raw={totals.maxDrawdown}
          tone={totals.maxDrawdown < 0 ? 'neg' : undefined}
          period="realised curve"
          loading={d.positionsLoading}
        />
        <MetricCard
          label="Hit rate 1h"
          icon="spark"
          value={stats && stats.hasData ? fmtPct(stats.hit_rate_1h, { signed: false }) : '--'}
          raw={stats ? stats.hit_rate_1h : null}
          period={
            stats && stats.hasData
              ? `${fmtNum(stats.graded_count, { dp: 0 })} graded · ${fmtNum(stats.window_days, {
                  dp: 0,
                })}d`
              : 'not enough history'
          }
          loading={statsQ.loading && !statsQ.data}
        />
      </div>

      {/* ================= panel grid ================= */}
      <div className="grid gap-2 grid-cols-1 xl:grid-cols-12 min-h-0">
        {/* ---- equity curve ---- */}
        <Panel className="xl:col-span-8">
          <PanelHeader
            title="Realised P&L curve"
            subtitle={d.journalName || 'no journal'}
            icon="analytics"
          />
          <PanelBody scroll={false}>
            <PerformanceChart
              points={d.equityCurve}
              currency={currency}
              loading={d.positionsLoading}
              error={d.q.tradingQ.error}
              onRetry={d.q.tradingQ.refresh}
              height={196}
            />
          </PanelBody>
          {/* Grading stats live here rather than in their own panel: they
              describe the same question the curve does — "is the book right?" */}
          <PanelFooter className="gap-4">
            {stats && stats.hasData ? (
              <>
                <Stat label="Hit 1h" value={fmtPct(stats.hit_rate_1h, { signed: false })} />
                <Stat label="Hit 24h" value={fmtPct(stats.hit_rate_24h, { signed: false })} />
                <Stat
                  label="Expectancy 1h"
                  value={fmtPct(stats.expectancy_1h, { asRatio: false, dp: 3 })}
                />
                <Stat label="Graded" value={fmtNum(stats.graded_count, { dp: 0 })} />
                <span className="ml-auto hx-mono text-hx-10 text-hx-text-dim truncate">
                  {stats.byHorizon
                    ? `${Object.keys(stats.byHorizon).length} horizon(s)`
                    : 'horizon breakdown unavailable'}
                </span>
              </>
            ) : (
              <span className="text-hx-10 text-hx-text-dim truncate">
                {statsQ.error
                  ? "Grading stats unavailable — couldn't reach /performance/stats"
                  : stats && stats.message
                    ? stats.message
                    : 'Loading signal grading…'}
              </span>
            )}
          </PanelFooter>
        </Panel>

        {/* ---- allocation ---- */}
        <Panel className="xl:col-span-4">
          <PanelHeader title="Allocation" icon="dashboard" />
          <PanelBody scroll={false}>
            <AllocationDonut
              segments={d.allocation}
              currency={currency}
              loading={d.positionsLoading && d.q.accountsQ.loading}
              error={d.q.accountsQ.error && d.positionsError ? d.q.accountsQ.error : null}
              onRetry={d.refreshAll}
            />
          </PanelBody>
          <PanelFooter>
            <span className="text-hx-10 text-hx-text-dim">
              Instrument class inferred from ticker — backend exposes none per position.
            </span>
          </PanelFooter>
        </Panel>

        {/* ---- drawdown ---- */}
        <Panel className="xl:col-span-8">
          <PanelHeader title="Drawdown" subtitle="underwater, absolute" icon="risk" />
          <PanelBody scroll={false}>
            <DrawdownChart
              points={d.drawdownCurve}
              currency={currency}
              loading={d.positionsLoading}
              error={d.q.tradingQ.error}
              onRetry={d.q.tradingQ.refresh}
              height={148}
            />
          </PanelBody>
        </Panel>

        {/* ---- exposure treemap ---- */}
        <Panel className="xl:col-span-4">
          <PanelHeader title="Exposure" icon="markets" />
          <PanelToolbar>
            <div className="flex items-center gap-1">
              {['sector', 'symbol'].map((g) => (
                <button
                  key={g}
                  type="button"
                  aria-pressed={groupBy === g}
                  onClick={() => setGroupBy(g)}
                  className={cx(
                    'hx-focus rounded px-1.5 py-0.5 text-hx-10 uppercase tracking-wider transition-colors',
                    groupBy === g
                      ? 'bg-hx-accent-500/[0.16] text-hx-accent-300 font-semibold'
                      : 'text-hx-text-lo hover:text-hx-text-hi hover:bg-white/[0.05]',
                  )}
                >
                  {g}
                </button>
              ))}
            </div>
            <span className="hx-mono text-hx-10 text-hx-text-dim">
              {fmtCur(
                treemapItems.reduce((s, i) => s + i.value, 0),
                { ccy: currency, compact: true },
              )}
            </span>
          </PanelToolbar>
          <PanelBody scroll={false}>
            <ExposureTreemap
              items={treemapItems}
              groupBy={groupBy}
              currency={currency}
              selectedSymbol={selectedSymbol}
              onSelectSymbol={selectSymbol}
              loading={d.positionsLoading}
              error={d.positionsError}
              onRetry={d.refreshAll}
              height={232}
            />
          </PanelBody>
        </Panel>

        {/* ---- positions ---- */}
        <Panel className="xl:col-span-8 min-h-[280px]">
          <PanelHeader
            title="Open positions"
            subtitle={sectorFilter ? `filtered · ${sectorFilter}` : undefined}
            icon="orders"
            actions={
              <span className="hx-mono text-hx-10 text-hx-text-dim">
                {visiblePositions.length} / {d.positions.length}
              </span>
            }
          />
          <PanelBody pad={false} scroll={false}>
            <PositionsGrid
              positions={visiblePositions}
              currency={currency}
              selectedSymbol={selectedSymbol}
              onSelectSymbol={selectSymbol}
              loading={d.positionsLoading}
              error={d.positionsError}
              onRetry={d.refreshAll}
              maxHeight={320}
            />
          </PanelBody>
        </Panel>

        {/* ---- sector heatmap ---- */}
        <Panel className="xl:col-span-4">
          <PanelHeader
            title="Sector performance"
            icon="dashboard"
            actions={
              <Tooltip
                content="Notional-weighted daily move. Sector mapping is client-side — no endpoint returns a sector field."
                side="left"
              >
                <Icon name="info" size={12} className="text-hx-text-dim" />
              </Tooltip>
            }
          />
          <PanelBody>
            <SectorHeatmap
              sectors={d.sectors}
              currency={currency}
              selectedSector={sectorFilter}
              onSelectSector={toggleSector}
              loading={d.positionsLoading}
              error={d.positionsError}
              onRetry={d.refreshAll}
            />
          </PanelBody>
        </Panel>
      </div>
    </div>
  );
}

/** Compact label/value pair for panel footers. */
function Stat({ label, value }) {
  return (
    <span className="flex items-baseline gap-1.5 shrink-0">
      <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">{label}</span>
      <span className="hx-mono text-hx-11 text-hx-text-hi">{value}</span>
    </span>
  );
}

export default PortfolioModule;
