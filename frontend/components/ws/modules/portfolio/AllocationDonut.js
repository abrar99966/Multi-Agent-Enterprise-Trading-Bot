/**
 * AllocationDonut — capital split across cash / equity / options / futures /
 * other, drawn as a single inline SVG.
 *
 * WHY stroke-dasharray and not <path> arcs: a dashed circle needs no
 * trigonometry, degrades gracefully at any radius, and animates without a
 * layout pass. The arcs are decoration; the legend is the real, accessible
 * representation of the data — every slice is named and quantified there, so
 * the chart never depends on colour alone.
 */
import React, { useMemo, useState } from 'react';
import { TONE_HEX, TONE_TEXT, cx, fmtCur, fmtPct } from '../../ui';
import { PanelState, rgba } from './chartKit';

const VIEW = 120;
const R = 46;
const STROKE = 15;
const CIRC = 2 * Math.PI * R;

export function AllocationDonut({
  segments = [],
  currency = 'INR',
  loading = false,
  error = null,
  onRetry,
  size = 148,
  className = '',
}) {
  const [hovered, setHovered] = useState(null);

  const { arcs, total } = useMemo(() => {
    const clean = segments.filter((s) => Number.isFinite(Number(s.value)) && Number(s.value) > 0);
    const sum = clean.reduce((a, s) => a + Number(s.value), 0);
    let cursor = 0;
    const out = clean.map((s) => {
      const frac = sum > 0 ? Number(s.value) / sum : 0;
      const len = frac * CIRC;
      const arc = { ...s, value: Number(s.value), frac, len, offset: cursor };
      cursor += len;
      return arc;
    });
    return { arcs: out, total: sum };
  }, [segments]);

  return (
    <PanelState
      loading={loading}
      error={error}
      empty={!arcs.length}
      onRetry={onRetry}
      emptyTitle="No allocation"
      emptyHint="Connect a broker account or open a position to populate the capital split."
      height={size}
      className={className}
    >
      <div className="flex items-center gap-4 min-w-0">
        {/* ---- donut ---- */}
        <div className="relative shrink-0" style={{ width: size, height: size }}>
          <svg
            viewBox={`0 0 ${VIEW} ${VIEW}`}
            width={size}
            height={size}
            role="img"
            aria-label={`Allocation across ${arcs.length} categories, total ${fmtCur(total, {
              ccy: currency,
              compact: true,
            })}`}
          >
            {/* Track keeps the ring readable when one slice dominates. */}
            <circle
              cx={VIEW / 2}
              cy={VIEW / 2}
              r={R}
              fill="none"
              stroke="rgba(255,255,255,0.05)"
              strokeWidth={STROKE}
            />
            {/* -90deg so the first slice starts at 12 o'clock. */}
            <g transform={`rotate(-90 ${VIEW / 2} ${VIEW / 2})`}>
              {arcs.map((a) => {
                const dim = hovered && hovered !== a.key;
                return (
                  <circle
                    key={a.key}
                    cx={VIEW / 2}
                    cy={VIEW / 2}
                    r={R}
                    fill="none"
                    stroke={TONE_HEX[a.tone] || TONE_HEX.neutral}
                    strokeWidth={hovered === a.key ? STROKE + 3 : STROKE}
                    strokeDasharray={`${a.len.toFixed(3)} ${(CIRC - a.len).toFixed(3)}`}
                    strokeDashoffset={(-a.offset).toFixed(3)}
                    opacity={dim ? 0.28 : 1}
                    style={{ transition: 'opacity 120ms linear, stroke-width 120ms linear' }}
                    onMouseEnter={() => setHovered(a.key)}
                    onMouseLeave={() => setHovered(null)}
                  >
                    <title>{`${a.label}: ${fmtCur(a.value, { ccy: currency })} (${fmtPct(a.frac, {
                      signed: false,
                    })})`}</title>
                  </circle>
                );
              })}
            </g>
          </svg>

          {/* Centre readout: total, or the hovered slice. */}
          <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
            {(() => {
              const focus = hovered ? arcs.find((a) => a.key === hovered) : null;
              return (
                <>
                  <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">
                    {focus ? focus.label : 'Total'}
                  </span>
                  <span
                    className={cx(
                      'hx-mono text-hx-13 font-semibold',
                      focus ? TONE_TEXT[focus.tone] : 'text-hx-text-hi',
                    )}
                  >
                    {fmtCur(focus ? focus.value : total, { ccy: currency, compact: true })}
                  </span>
                  {focus && (
                    <span className="hx-mono text-hx-10 text-hx-text-lo">
                      {fmtPct(focus.frac, { signed: false })}
                    </span>
                  )}
                </>
              );
            })()}
          </div>
        </div>

        {/* ---- legend: the accessible source of truth ---- */}
        <ul className="flex-1 min-w-0 space-y-1">
          {arcs.map((a) => (
            <li key={a.key}>
              <button
                type="button"
                onMouseEnter={() => setHovered(a.key)}
                onMouseLeave={() => setHovered(null)}
                onFocus={() => setHovered(a.key)}
                onBlur={() => setHovered(null)}
                className={cx(
                  'hx-focus w-full flex items-center gap-2 rounded px-1 py-0.5 text-left transition-colors',
                  hovered === a.key ? 'bg-white/[0.05]' : 'hover:bg-white/[0.03]',
                )}
              >
                <span
                  aria-hidden="true"
                  className="h-2 w-2 rounded-[2px] shrink-0"
                  style={{ backgroundColor: TONE_HEX[a.tone] || TONE_HEX.neutral }}
                />
                <span className="text-hx-11 text-hx-text-mid truncate flex-1">{a.label}</span>
                <span className="hx-mono text-hx-11 text-hx-text-hi shrink-0">
                  {fmtPct(a.frac, { signed: false, dp: 1 })}
                </span>
                <span
                  className="hx-mono text-hx-10 text-hx-text-lo shrink-0 w-14 text-right"
                  style={{ borderLeft: `2px solid ${rgba(TONE_HEX[a.tone], 0.45)}`, paddingLeft: 6 }}
                >
                  {fmtCur(a.value, { ccy: currency, compact: true })}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </PanelState>
  );
}

export default AllocationDonut;
