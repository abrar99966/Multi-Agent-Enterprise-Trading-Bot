/**
 * Button — the only interactive affordance in the workspace.
 *
 * variants: primary (cyan, one per view), ghost (bordered, the default for
 *           toolbars), subtle (borderless, for tertiary/inline actions),
 *           danger (destructive: kill-switch, cancel-all).
 * sizes:    xs (22px, inside grid rows), sm (26px, toolbars), md (32px, forms).
 * states:   loading (spinner replaces the icon, width held steady so toolbars
 *           don't reflow), disabled, iconOnly (square, requires `aria-label`).
 */
import React from 'react';
import { Icon } from './Icon';
import { cx } from './tokens';

const VARIANTS = {
  primary:
    'bg-hx-accent-500 text-hx-bg-base font-semibold border border-hx-accent-400 hover:bg-hx-accent-400 active:bg-hx-accent-600',
  ghost:
    'bg-white/[0.03] text-hx-text-hi border border-hx-border-subtle hover:bg-white/[0.07] hover:border-hx-border-strong active:bg-white/[0.04]',
  subtle:
    'bg-transparent text-hx-text-mid border border-transparent hover:bg-white/[0.06] hover:text-hx-text-hi',
  danger:
    'bg-hx-neg-500/[0.12] text-hx-neg-300 border border-hx-neg-500/40 hover:bg-hx-neg-500/20 hover:text-hx-neg-300 active:bg-hx-neg-500/25',
};

const SIZES = {
  xs: { box: 'h-[22px] px-2 gap-1 text-hx-11 rounded', icon: 14, only: 'w-[22px] px-0' },
  sm: { box: 'h-[26px] px-2.5 gap-1.5 text-hx-12 rounded-md', icon: 16, only: 'w-[26px] px-0' },
  md: { box: 'h-8 px-3 gap-1.5 text-hx-13 rounded-md', icon: 18, only: 'w-8 px-0' },
};

/** Indeterminate spinner sized to the button's icon slot. */
function Spinner({ size }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className="animate-spin shrink-0"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

export function Button({
  children,
  variant = 'ghost',
  size = 'sm',
  icon,          // icon name, rendered left of the label
  iconRight,
  loading = false,
  disabled = false,
  iconOnly = false,
  type = 'button',
  className = '',
  ...rest
}) {
  const s = SIZES[size] || SIZES.sm;
  const isOff = disabled || loading;

  return (
    <button
      type={type}
      disabled={isOff}
      // aria-busy tells AT the control is working rather than broken while the
      // label stays put (we never swap label text for "Loading...").
      aria-busy={loading || undefined}
      className={cx(
        'hx-focus inline-flex items-center justify-center select-none whitespace-nowrap',
        'transition-colors duration-100',
        'disabled:opacity-45 disabled:pointer-events-none',
        s.box,
        iconOnly && s.only,
        VARIANTS[variant] || VARIANTS.ghost,
        className,
      )}
      {...rest}
    >
      {loading ? (
        <Spinner size={s.icon} />
      ) : (
        icon && <Icon name={icon} size={s.icon} />
      )}
      {!iconOnly && children}
      {!iconOnly && !loading && iconRight && <Icon name={iconRight} size={s.icon} />}
    </button>
  );
}

/** Segmented control — a row of buttons where exactly one is active. */
export function ButtonGroup({ options = [], value, onChange, size = 'sm', className = '' }) {
  const s = SIZES[size] || SIZES.sm;
  return (
    <div
      role="group"
      className={cx('inline-flex items-center rounded-md border border-hx-border-subtle bg-white/[0.02] p-0.5 gap-0.5', className)}
    >
      {options.map((o) => {
        const val = typeof o === 'string' ? o : o.value;
        const label = typeof o === 'string' ? o : o.label;
        const active = val === value;
        return (
          <button
            key={val}
            type="button"
            aria-pressed={active}
            onClick={() => onChange && onChange(val)}
            className={cx(
              'hx-focus inline-flex items-center justify-center transition-colors duration-100',
              s.box,
              active
                ? 'bg-hx-accent-500/[0.16] text-hx-accent-300 font-semibold'
                : 'text-hx-text-lo hover:text-hx-text-hi hover:bg-white/[0.05]',
            )}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

export default Button;
