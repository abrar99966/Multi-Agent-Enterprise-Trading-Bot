/**
 * MetricCard — compact KPI tile: label, big value, signed delta, optional
 * sparkline and trend period.
 *
 * Density: 64px tall by default. Deltas always carry a sign AND an arrow glyph
 * (▲/▼/→) so direction survives greyscale and colour-blindness — colour is the
 * redundant channel, never the only one.
 *
 * `flash` re-tints the value cell green/red for 700ms whenever it changes,
 * which is how a trader notices a tick without watching every tile.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import { Sparkline } from './Sparkline';
import { Skeleton } from './Skeleton';
import { TONE_TEXT, deltaTone, deltaArrow, cx } from './tokens';

/** useFlash — returns an animation class for one paint after `value` changes. */
export function useFlash(value, enabled = true) {
  const prev = useRef(value);
  const [cls, setCls] = useState('');

  useEffect(() => {
    if (!enabled) return;
    const before = prev.current;
    prev.current = value;
    if (before === undefined || before === null || before === value) return;
    const a = Number(value);
    const b = Number(before);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return;
    // Re-key the class each time so an identical consecutive flash restarts.
    setCls(a > b ? 'animate-hx-flash-pos' : 'animate-hx-flash-neg');
    const t = setTimeout(() => setCls(''), 720);
    return () => clearTimeout(t);
  }, [value, enabled]);

  return cls;
}

export function MetricCard({
  label,
  value,                 // pre-formatted string (use fmtCur/fmtPct from tokens)
  raw,                   // numeric value driving the flash, if `value` is a string
  delta,                 // number: signed change
  deltaText,             // pre-formatted delta, overrides the default rendering
  period = '',           // "24h", "since open", ...
  sparkline,             // number[] — renders inline, or pass `children` for a custom slot
  sparklineTone,
  icon,
  tone,                  // forces the value colour; defaults to neutral/hi
  loading = false,
  flash = false,
  onClick,
  className = '',
  children,              // custom sparkline / secondary slot
  ...rest
}) {
  const flashCls = useFlash(raw !== undefined ? raw : value, flash && !loading);
  const dTone = deltaTone(delta);
  const Tag = onClick ? 'button' : 'div';

  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      aria-busy={loading || undefined}
      className={cx(
        'group relative flex flex-col justify-between gap-1 min-w-0 text-left',
        'rounded-lg border border-hx-border-subtle bg-hx-bg-raised px-3 py-2 shadow-hx-panel',
        onClick && 'hx-focus cursor-pointer transition-colors hover:border-hx-border-strong hover:bg-white/[0.04]',
        className,
      )}
      {...rest}
    >
      {/* label row */}
      <div className="flex items-center gap-1.5 min-w-0">
        {icon && <Icon name={icon} size={12} className="text-hx-text-dim shrink-0" />}
        <span className="text-hx-10 font-medium uppercase tracking-wider text-hx-text-lo truncate">
          {label}
        </span>
      </div>

      {/* value + sparkline */}
      <div className="flex items-end justify-between gap-2 min-w-0">
        <div className="min-w-0">
          {loading ? (
            <Skeleton h={20} className="w-24 my-0.5" />
          ) : (
            <div
              className={cx(
                'text-[20px] leading-6 font-semibold hx-mono truncate rounded px-0.5 -mx-0.5',
                tone ? TONE_TEXT[tone] : 'text-hx-text-hi',
                flashCls,
              )}
            >
              {value ?? '--'}
            </div>
          )}
        </div>

        {!loading && (children || (sparkline && sparkline.length > 1)) && (
          <div className="shrink-0 opacity-80">
            {children || (
              <Sparkline
                values={sparkline}
                tone={sparklineTone || 'accent'}
                // No explicit tone → colour the line by its own direction.
                autoTone={!sparklineTone}
                width={64}
                height={20}
                filled
              />
            )}
          </div>
        )}
      </div>

      {/* delta row */}
      {(delta !== undefined || deltaText || period) && (
        <div className="flex items-center gap-1.5 min-w-0">
          {loading ? (
            <Skeleton h={9} className="w-16" />
          ) : (
            <>
              {(delta !== undefined || deltaText) && (
                <span className={cx('text-hx-11 font-medium hx-mono', TONE_TEXT[dTone])}>
                  <span aria-hidden="true">{deltaArrow(delta)}</span>{' '}
                  {deltaText ?? `${delta > 0 ? '+' : ''}${delta}`}
                </span>
              )}
              {period && <span className="text-hx-10 text-hx-text-dim truncate">{period}</span>}
            </>
          )}
        </div>
      )}
    </Tag>
  );
}

/** MetricRow — evenly distributes MetricCards across the top of a view. */
export function MetricRow({ children, className = '', cols }) {
  return (
    <div
      className={cx('grid gap-2', className)}
      style={{ gridTemplateColumns: cols ? `repeat(${cols}, minmax(0, 1fr))` : undefined }}
    >
      {children}
    </div>
  );
}

export default MetricCard;
