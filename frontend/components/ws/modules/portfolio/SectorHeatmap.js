/**
 * SectorHeatmap — notional-weighted daily move per sector.
 *
 * COLOUR-BLIND SAFETY is the design constraint here, and it is met on three
 * independent channels:
 *   1. luminance — tint alpha scales with |move|, so magnitude reads in greyscale
 *   2. glyph     — every tile carries ▲ / ▼ / →
 *   3. text      — the signed percentage is printed on every tile, always
 * Hue is therefore redundant. A viewer with full deuteranopia loses nothing.
 *
 * Sector attribution is client-side (see usePortfolioData.SECTORS) because no
 * endpoint on this backend returns a sector field; the panel says so in its hint.
 */
import React, { useMemo } from 'react';
import { TONE_HEX, cx, deltaArrow, fmtCur, fmtPct } from '../../ui';
import { PanelState, rgba } from './chartKit';

/** Full-strength tint at a 2% weighted move — typical daily sector dispersion. */
const FULL_SCALE_PCT = 2;

function tileStyle(changePct) {
  if (changePct == null) {
    return { backgroundColor: 'rgba(255,255,255,0.03)', borderColor: 'rgba(255,255,255,0.07)' };
  }
  const t = Math.min(1, Math.abs(changePct) / FULL_SCALE_PCT);
  const hex = changePct > 0 ? TONE_HEX.pos : changePct < 0 ? TONE_HEX.neg : TONE_HEX.neutral;
  return {
    backgroundColor: rgba(hex, 0.08 + 0.4 * t),
    borderColor: rgba(hex, 0.22 + 0.3 * t),
  };
}

export function SectorHeatmap({
  sectors = [],
  currency = 'INR',
  selectedSector = null,
  onSelectSector,
  loading = false,
  error = null,
  onRetry,
  className = '',
}) {
  const rows = useMemo(() => sectors.filter((s) => s && s.count > 0), [sectors]);
  const totalExposure = rows.reduce((a, s) => a + (s.exposure || 0), 0);

  return (
    <PanelState
      loading={loading}
      error={error}
      empty={!rows.length}
      onRetry={onRetry}
      emptyTitle="No sector data"
      emptyHint="Sectors are derived from open positions once quotes are available."
      height={180}
      className={className}
    >
      <div
        className={cx('grid gap-1.5 grid-cols-2 xl:grid-cols-3', className)}
        role="list"
        aria-label="Sector performance heatmap"
      >
        {rows.map((s) => {
          const st = tileStyle(s.changePct);
          const active = selectedSector === s.sector;
          const Tag = onSelectSector ? 'button' : 'div';
          return (
            <Tag
              key={s.sector}
              role="listitem"
              type={onSelectSector ? 'button' : undefined}
              onClick={onSelectSector ? () => onSelectSector(s.sector) : undefined}
              aria-label={`${s.sector}: ${
                s.changePct == null ? 'no quote' : fmtPct(s.changePct, { asRatio: false })
              }, ${s.count} position${s.count === 1 ? '' : 's'}, exposure ${fmtCur(s.exposure, {
                ccy: currency,
              })}`}
              className={cx(
                'flex flex-col justify-between gap-1 rounded-md border px-2 py-1.5 text-left min-w-0',
                'transition-colors duration-100',
                onSelectSector && 'hx-focus cursor-pointer hover:brightness-125',
                active && 'ring-1 ring-hx-accent-400',
              )}
              style={st}
            >
              <div className="flex items-center justify-between gap-1.5 min-w-0">
                <span className="text-hx-10 uppercase tracking-wider text-hx-text-mid truncate">
                  {s.sector}
                </span>
                {/* Position count doubles as the confidence cue for the tile. */}
                <span className="hx-mono text-hx-10 text-hx-text-dim shrink-0">{s.count}</span>
              </div>

              <div className="flex items-baseline gap-1 min-w-0">
                <span aria-hidden="true" className="text-hx-11 text-hx-text-hi shrink-0">
                  {s.changePct == null ? '·' : deltaArrow(s.changePct)}
                </span>
                <span className="hx-mono text-hx-13 font-semibold text-hx-text-hi truncate">
                  {s.changePct == null ? '--' : fmtPct(s.changePct, { asRatio: false })}
                </span>
              </div>

              <div className="flex items-baseline justify-between gap-1.5 min-w-0">
                <span className="hx-mono text-hx-10 text-hx-text-lo truncate">
                  {fmtCur(s.exposure, { ccy: currency, compact: true })}
                </span>
                <span className="hx-mono text-hx-10 text-hx-text-dim shrink-0">
                  {totalExposure > 0 ? fmtPct(s.exposure / totalExposure, { signed: false, dp: 0 }) : '--'}
                </span>
              </div>
            </Tag>
          );
        })}
      </div>
    </PanelState>
  );
}

export default SectorHeatmap;
