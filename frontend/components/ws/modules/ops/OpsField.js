/**
 * Small form controls shared by Logs, Replay and Settings.
 *
 * These are deliberately NOT promoted into components/ws/ui — that layer is
 * owned by the foundation and this module may not extend it. Kept minimal and
 * native (real <select>, real <input>) so keyboard behaviour and screen-reader
 * semantics come from the platform rather than being re-implemented.
 */
import React, { useId } from 'react';
import { Icon, cx } from '../../ui';

/** Native select with terminal chrome. Options: [{value,label,disabled}] or strings. */
export function Select({
  label,
  value,
  onChange,
  options = [],
  size = 'sm',
  className = '',
  disabled = false,
  placeholder,
  'aria-label': ariaLabel,
}) {
  const id = useId();
  const h = size === 'xs' ? 'h-[22px] text-hx-11' : 'h-[26px] text-hx-12';

  return (
    <span className={cx('inline-flex items-center gap-1.5 min-w-0', className)}>
      {label && (
        <label htmlFor={id} className="text-hx-10 uppercase tracking-wider text-hx-text-lo shrink-0">
          {label}
        </label>
      )}
      <span className="relative inline-flex min-w-0">
        <select
          id={id}
          value={value}
          disabled={disabled}
          aria-label={ariaLabel || label}
          onChange={(e) => onChange && onChange(e.target.value)}
          className={cx(
            'hx-focus appearance-none min-w-0 max-w-full pl-2 pr-6 rounded-md truncate',
            'bg-hx-bg-base border border-hx-border-subtle text-hx-text-hi',
            'hover:border-hx-border-strong transition-colors',
            'disabled:opacity-45 disabled:pointer-events-none',
            h,
          )}
        >
          {placeholder && <option value="">{placeholder}</option>}
          {options.map((o) => {
            const v = typeof o === 'string' ? o : o.value;
            const l = typeof o === 'string' ? o : o.label;
            return (
              <option key={v} value={v} disabled={typeof o === 'object' && o.disabled}>
                {l}
              </option>
            );
          })}
        </select>
        <Icon
          name="chevron-down"
          size={12}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 text-hx-text-dim pointer-events-none"
        />
      </span>
    </span>
  );
}

/** Search box with a clear affordance. Debouncing is the caller's concern. */
export function SearchInput({ value, onChange, placeholder = 'Search…', className = '', ariaLabel = 'Search' }) {
  return (
    <span className={cx('relative inline-flex items-center min-w-0', className)}>
      <Icon name="search" size={12} className="absolute left-2 text-hx-text-dim pointer-events-none" />
      <input
        type="search"
        value={value}
        aria-label={ariaLabel}
        placeholder={placeholder}
        onChange={(e) => onChange && onChange(e.target.value)}
        className={cx(
          'hx-focus h-[26px] w-full pl-7 pr-7 rounded-md text-hx-12',
          'bg-hx-bg-base border border-hx-border-subtle text-hx-text-hi',
          'placeholder:text-hx-text-dim hover:border-hx-border-strong transition-colors',
          '[&::-webkit-search-cancel-button]:appearance-none',
        )}
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="Clear search"
          className="hx-focus absolute right-1.5 h-4 w-4 inline-flex items-center justify-center rounded text-hx-text-dim hover:text-hx-text-hi"
        >
          <Icon name="close" size={11} />
        </button>
      )}
    </span>
  );
}

/**
 * Switch-style toggle. Uses a real checkbox input so it is focusable, spacebar
 * operable and announced with its checked state for free.
 */
export function Toggle({ checked, onChange, label, hint, disabled = false, className = '' }) {
  return (
    <label
      className={cx(
        'inline-flex items-center gap-2 min-w-0 cursor-pointer select-none',
        disabled && 'opacity-45 pointer-events-none',
        className,
      )}
    >
      <span className="relative inline-flex shrink-0">
        <input
          type="checkbox"
          checked={Boolean(checked)}
          disabled={disabled}
          onChange={(e) => onChange && onChange(e.target.checked)}
          className="hx-focus peer sr-only"
        />
        <span
          aria-hidden="true"
          className={cx(
            'h-[16px] w-[28px] rounded-full border transition-colors duration-100',
            'peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-hx-accent-400',
            checked ? 'bg-hx-accent-500/40 border-hx-accent-500' : 'bg-white/[0.05] border-hx-border-subtle',
          )}
        />
        <span
          aria-hidden="true"
          className={cx(
            'absolute top-[3px] h-[10px] w-[10px] rounded-full transition-all duration-100',
            checked ? 'left-[15px] bg-hx-accent-300' : 'left-[3px] bg-hx-text-lo',
          )}
        />
      </span>
      {label && (
        <span className="min-w-0">
          <span className="block text-hx-12 text-hx-text-mid truncate">{label}</span>
          {hint && <span className="block text-hx-10 text-hx-text-dim truncate">{hint}</span>}
        </span>
      )}
    </label>
  );
}

/** Numeric input for risk limits. Kept text-aligned right and mono for scanning. */
export function NumberInput({ value, onChange, min, max, step = 1, suffix, className = '', ariaLabel, disabled }) {
  return (
    <span className={cx('inline-flex items-center gap-1.5 min-w-0', className)}>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        aria-label={ariaLabel}
        onChange={(e) => onChange && onChange(e.target.value)}
        className={cx(
          'hx-focus h-[26px] w-full px-2 rounded-md text-hx-12 text-right hx-mono hx-tnum',
          'bg-hx-bg-base border border-hx-border-subtle text-hx-text-hi',
          'hover:border-hx-border-strong transition-colors disabled:opacity-45',
        )}
      />
      {suffix && <span className="text-hx-10 text-hx-text-dim shrink-0">{suffix}</span>}
    </span>
  );
}

/** Settings section: a titled block with optional description and header actions. */
export function Section({ title, description, icon, actions, children, className = '' }) {
  return (
    <section className={cx('flex flex-col gap-2.5', className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-1.5 text-hx-12 font-semibold uppercase tracking-wide text-hx-text-hi">
            {icon && <Icon name={icon} size={13} className="text-hx-text-lo" />}
            {title}
          </h3>
          {description && (
            <p className="text-hx-11 text-hx-text-lo leading-relaxed mt-1 max-w-[68ch]">{description}</p>
          )}
        </div>
        {actions && <div className="flex items-center gap-1.5 shrink-0">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

/** A labelled row inside a Section — label left, control right. */
export function Field({ label, hint, control, className = '' }) {
  return (
    <div className={cx('flex items-center justify-between gap-4 py-1.5 min-w-0', className)}>
      <div className="min-w-0">
        <div className="text-hx-12 text-hx-text-mid truncate">{label}</div>
        {hint && <div className="text-hx-10 text-hx-text-dim truncate">{hint}</div>}
      </div>
      <div className="shrink-0">{control}</div>
    </div>
  );
}

/** Keyboard key rendering for the shortcuts reference. */
export function KeyCap({ children }) {
  return (
    <kbd className="inline-flex items-center justify-center min-w-[20px] h-[20px] px-1.5 rounded border border-hx-border-strong bg-white/[0.05] text-hx-10 hx-mono text-hx-text-hi">
      {children}
    </kbd>
  );
}
