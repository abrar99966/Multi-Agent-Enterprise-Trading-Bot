/**
 * EmptyState — what a panel shows instead of a blank void.
 *
 * Distinguishes three cases the user must be able to tell apart:
 *   default — no data yet / nothing matches
 *   error   — the fetch failed (offers the action as "Retry")
 *   filter  — data exists but the current filter hides it
 * Compact by default; `size="lg"` for full-panel takeovers.
 */
import React from 'react';
import { Icon } from './Icon';
import { Button } from './Button';
import { cx } from './tokens';

const VARIANTS = {
  default: { icon: 'search', tone: 'text-hx-text-dim' },
  error: { icon: 'alert', tone: 'text-hx-neg-400' },
  filter: { icon: 'filter', tone: 'text-hx-text-dim' },
  offline: { icon: 'close', tone: 'text-hx-warn-400' },
};

export function EmptyState({
  title = 'No data',
  hint,
  icon,                 // overrides the variant icon
  variant = 'default',
  action,               // { label, onClick, icon } or a ReactNode
  size = 'md',
  className = '',
}) {
  const v = VARIANTS[variant] || VARIANTS.default;
  const lg = size === 'lg';

  return (
    <div
      role={variant === 'error' ? 'alert' : 'status'}
      className={cx(
        'flex flex-col items-center justify-center text-center',
        lg ? 'py-12 px-6 gap-3' : 'py-8 px-4 gap-2',
        className,
      )}
    >
      <div
        className={cx(
          'flex items-center justify-center rounded-lg border border-hx-border-subtle bg-white/[0.02]',
          lg ? 'h-11 w-11' : 'h-8 w-8',
          v.tone,
        )}
      >
        <Icon name={icon || v.icon} size={lg ? 20 : 16} />
      </div>

      <div className={cx('font-semibold text-hx-text-mid', lg ? 'text-hx-14' : 'text-hx-13')}>{title}</div>

      {hint && (
        <div className={cx('text-hx-text-dim max-w-[38ch] leading-relaxed', lg ? 'text-hx-12' : 'text-hx-11')}>
          {hint}
        </div>
      )}

      {action &&
        (React.isValidElement(action) ? (
          <div className="mt-1">{action}</div>
        ) : (
          <Button
            size="sm"
            variant={variant === 'error' ? 'ghost' : 'subtle'}
            icon={action.icon || (variant === 'error' ? 'refresh' : undefined)}
            onClick={action.onClick}
            className="mt-1"
          >
            {action.label}
          </Button>
        ))}
    </div>
  );
}

export default EmptyState;
