/**
 * StatusChip — connection/service state with a status dot and label.
 *
 * status: live (pulsing green) | connected | degraded | stale | offline | paused
 * Each status maps to a tone AND a distinct dot treatment (pulse / solid /
 * hollow / slashed), so status is legible without colour perception.
 */
import React from 'react';
import { Icon } from './Icon';
import { TONE_TEXT, TONE_SOLID, cx } from './tokens';

const STATUS = {
  live: { tone: 'pos', label: 'Live', pulse: true, fill: 'solid' },
  connected: { tone: 'pos', label: 'Connected', pulse: false, fill: 'solid' },
  degraded: { tone: 'warn', label: 'Degraded', pulse: false, fill: 'solid', icon: 'alert' },
  stale: { tone: 'warn', label: 'Stale', pulse: false, fill: 'hollow', icon: 'clock' },
  paused: { tone: 'neutral', label: 'Paused', pulse: false, fill: 'hollow', icon: 'pause' },
  offline: { tone: 'neg', label: 'Offline', pulse: false, fill: 'hollow', icon: 'close' },
  error: { tone: 'neg', label: 'Error', pulse: false, fill: 'solid', icon: 'alert' },
};

export const STATUS_KEYS = Object.keys(STATUS);

/** The dot alone — reusable in dense contexts (sidebar rails, grid cells). */
export function StatusDot({ status = 'live', className = '' }) {
  const s = STATUS[status] || STATUS.offline;
  const solid = TONE_SOLID[s.tone] || TONE_SOLID.neutral;
  return (
    <span className={cx('relative inline-flex h-2 w-2 shrink-0', className)}>
      {/* Halo is decorative; the core dot below always renders. */}
      {s.pulse && (
        <span
          className={cx('absolute inset-0 rounded-full animate-hx-pulse-dot', solid)}
          aria-hidden="true"
        />
      )}
      <span
        className={cx(
          'relative inline-block h-2 w-2 rounded-full',
          s.fill === 'solid' ? solid : cx('bg-transparent border-[1.5px]', TONE_TEXT[s.tone], 'border-current'),
        )}
      />
    </span>
  );
}

export function StatusChip({
  status = 'live',
  label,          // overrides the default label
  detail,         // e.g. "12ms" or "3 feeds" — rendered muted after the label
  showIcon = false,
  className = '',
  ...rest
}) {
  const s = STATUS[status] || STATUS.offline;
  const text = label || s.label;
  return (
    <span
      // role=status so screen readers announce transitions (live → offline)
      // without the user having to hunt for the indicator.
      role="status"
      aria-label={`${text}${detail ? `, ${detail}` : ''}`}
      className={cx(
        'inline-flex items-center gap-1.5 h-[20px] px-2 rounded border',
        'border-hx-border-subtle bg-white/[0.03] text-hx-11 font-medium whitespace-nowrap',
        className,
      )}
      {...rest}
    >
      <StatusDot status={status} />
      {showIcon && s.icon && <Icon name={s.icon} size={11} className={TONE_TEXT[s.tone]} />}
      <span className={TONE_TEXT[s.tone] || TONE_TEXT.neutral}>{text}</span>
      {detail && <span className="text-hx-text-dim hx-tnum">{detail}</span>}
    </span>
  );
}

export default StatusChip;
