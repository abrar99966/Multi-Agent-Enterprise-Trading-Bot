/**
 * RiskIndicator — severity readout for limits, exposure, drawdown, VaR.
 *
 * severity: ok | elevated | high | critical  (see tokens.SEVERITY_META)
 * variants:
 *   bar    — labelled utilisation bar with a limit marker (default)
 *   gauge  — 4-segment stepped gauge, good for a compact header slot
 *   inline — icon + text only, for grid cells
 *
 * Severity is ALWAYS carried by an icon and a text label in addition to colour
 * (WCAG 1.4.1). The bar additionally encodes level by fill length, so a
 * greyscale screenshot still reads correctly.
 */
import React from 'react';
import { Icon } from './Icon';
import { SEVERITY_META, TONE_TEXT, TONE_SOLID, TONE_BG, severityTone, toSeverity, cx } from './tokens';

/** Derive a severity from a 0..1 utilisation ratio. */
export function ratioSeverity(ratio, { elevated = 0.6, high = 0.8, critical = 0.95 } = {}) {
  const r = Number(ratio);
  if (!Number.isFinite(r)) return 'ok';
  if (r >= critical) return 'critical';
  if (r >= high) return 'high';
  if (r >= elevated) return 'elevated';
  return 'ok';
}

export function RiskIndicator({
  severity,             // explicit severity; otherwise derived from `value`/`max`
  value,                // current utilisation (same unit as max)
  max,                  // limit
  label,
  valueText,            // pre-formatted right-hand readout
  variant = 'bar',
  showLabel = true,
  className = '',
  ...rest
}) {
  const ratio = Number.isFinite(Number(value)) && Number(max) ? Number(value) / Number(max) : null;
  const sev = toSeverity(severity ?? (ratio !== null ? ratioSeverity(ratio) : 'ok'));
  const tone = severityTone(sev);
  const meta = SEVERITY_META[sev];
  const pct = ratio === null ? 0 : Math.max(0, Math.min(1, ratio)) * 100;

  const a11y = {
    role: 'meter',
    'aria-valuenow': ratio === null ? undefined : Math.round(pct),
    'aria-valuemin': 0,
    'aria-valuemax': 100,
    'aria-label': `${label || 'Risk'}: ${meta.label}${ratio !== null ? `, ${Math.round(pct)}% of limit` : ''}`,
  };

  if (variant === 'inline') {
    return (
      <span className={cx('inline-flex items-center gap-1.5 whitespace-nowrap', className)} {...rest}>
        <Icon name={meta.icon} size={12} className={TONE_TEXT[tone]} />
        <span className={cx('text-hx-11 font-medium', TONE_TEXT[tone])}>{meta.label}</span>
        {valueText && <span className="text-hx-11 text-hx-text-lo hx-mono">{valueText}</span>}
      </span>
    );
  }

  if (variant === 'gauge') {
    return (
      <div className={cx('inline-flex items-center gap-2', className)} {...a11y} {...rest}>
        <span className="flex items-center gap-[3px]" aria-hidden="true">
          {['ok', 'elevated', 'high', 'critical'].map((step) => {
            // Fill every segment up to and including the active severity, so
            // level is readable as a count of lit bars, not just a hue.
            const lit = SEVERITY_META[step].rank <= meta.rank;
            return (
              <span
                key={step}
                className={cx(
                  'h-3 w-[5px] rounded-[1px]',
                  lit ? TONE_SOLID[tone] : 'bg-white/[0.08]',
                )}
              />
            );
          })}
        </span>
        {showLabel && (
          <span className={cx('inline-flex items-center gap-1 text-hx-11 font-medium', TONE_TEXT[tone])}>
            <Icon name={meta.icon} size={12} />
            {meta.label}
          </span>
        )}
      </div>
    );
  }

  // variant === 'bar'
  return (
    <div className={cx('flex flex-col gap-1 min-w-0', className)} {...rest}>
      {showLabel && (
        <div className="flex items-baseline justify-between gap-2 min-w-0">
          <span className="inline-flex items-center gap-1.5 min-w-0">
            <Icon name={meta.icon} size={12} className={cx('shrink-0', TONE_TEXT[tone])} />
            <span className="text-hx-11 text-hx-text-mid truncate">{label}</span>
          </span>
          <span className={cx('text-hx-11 font-medium hx-mono shrink-0', TONE_TEXT[tone])}>
            {valueText ?? (ratio !== null ? `${pct.toFixed(0)}%` : meta.label)}
          </span>
        </div>
      )}
      <div className={cx('relative h-1.5 w-full rounded-full overflow-hidden', TONE_BG[tone] || 'bg-white/[0.06]')} {...a11y}>
        <div
          className={cx('absolute inset-y-0 left-0 rounded-full transition-[width] duration-300', TONE_SOLID[tone])}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default RiskIndicator;
